#!/usr/bin/env python3
"""Match each iTerm2 window's font size to the display it sits on.

Runs as the LaunchAgent com.jacobr.iterm-screen-fonts from its own venv
(install_iterm_screen_fonts.zsh builds it). Once a second it reads window
frames over iTerm2's Python API. When a window settles on another display, or
gains a tab or pane, those sessions get that display's font size. A manual
Cmd+/Cmd- change sticks until the window moves again.

Sizes default to DEFAULT_SIZES; ~/.config/iterm-screen-fonts.json overrides
them, e.g. {"built_in": 13, "external": 15}, and is re-read when it changes.

  iterm_screen_fonts.py          follow windows (what the LaunchAgent runs)
  iterm_screen_fonts.py --list   print each window's display and fonts
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import json
from pathlib import Path
import subprocess
import sys
import time

BUILT_IN = "built_in"
EXTERNAL = "external"
DISPLAY_KINDS = (BUILT_IN, EXTERNAL)
DEFAULT_SIZES = {BUILT_IN: 13, EXTERNAL: 15}
CONFIG_PATH = Path.home() / ".config" / "iterm-screen-fonts.json"
ITERM_BUNDLE_ID = "com.googlecode.iterm2"
POLL_SECONDS = 1.0
# A window must report the same display on this many consecutive polls before
# its fonts change, so dragging across the boundary doesn't flip them mid-move.
SETTLE_POLLS = 2
SCREEN_REFRESH_SECONDS = 15.0
# A poll that hangs means iTerm went away without closing the socket cleanly.
POLL_TIMEOUT_SECONDS = 20.0
WAIT_FOR_ITERM_SECONDS = 5.0

# NSScreen frames share Cocoa's bottom-left origin with iTerm's window frames.
# A mirror set (such as the built-in panel plus a hidden mirror) is one screen.
SCREENS_JXA = """
ObjC.import("AppKit");
const out = [];
const screens = $.NSScreen.screens;
for (let i = 0; i < screens.count; i++) {
  const s = screens.objectAtIndex(i);
  const f = s.frame;
  out.push({name: ObjC.unwrap(s.localizedName), x: f.origin.x, y: f.origin.y,
            w: f.size.width, h: f.size.height});
}
JSON.stringify(out);
"""


@dataclass(frozen=True)
class Screen:
    kind: str
    name: str
    x: float
    y: float
    w: float
    h: float

    def overlap(self, x: float, y: float, w: float, h: float) -> float:
        dx = min(self.x + self.w, x + w) - max(self.x, x)
        dy = min(self.y + self.h, y + h) - max(self.y, y)
        return dx * dy if dx > 0 and dy > 0 else 0.0


def parse_screens(raw_json: str) -> list[Screen]:
    """Built-in panels are named "Built-in ..." by macOS; anything else is external."""
    screens = []
    for item in json.loads(raw_json):
        name = str(item["name"])
        kind = BUILT_IN if name.startswith("Built-in") else EXTERNAL
        screens.append(Screen(kind, name, float(item["x"]), float(item["y"]),
                              float(item["w"]), float(item["h"])))
    return screens


def read_screens() -> list[Screen]:
    result = subprocess.run(["/usr/bin/osascript", "-l", "JavaScript", "-e", SCREENS_JXA],
                            check=True, capture_output=True, text=True, timeout=10)
    return parse_screens(result.stdout)


def display_kind_for(frame: tuple[float, float, float, float], screens: list[Screen]) -> str | None:
    """The kind of the screen holding most of the window, or None if it is on none."""
    best, best_area = None, 0.0
    for screen in screens:
        area = screen.overlap(*frame)
        if area > best_area:
            best, best_area = screen, area
    return best.kind if best else None


def load_sizes(path: Path = CONFIG_PATH) -> dict[str, float]:
    """Defaults merged with the optional config file, validated as a trust boundary."""
    sizes: dict[str, float] = dict(DEFAULT_SIZES)
    if not path.exists():
        return sizes
    data = json.loads(path.read_text())
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a JSON object")
    for key, value in data.items():
        if key not in DISPLAY_KINDS:
            raise ValueError(f"{path}: unknown key {key!r}; use {', '.join(DISPLAY_KINDS)}")
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 4 <= value <= 72:
            raise ValueError(f"{path}: {key} must be a font size between 4 and 72")
        sizes[key] = value
    return sizes


def font_with_size(font: str, size: float) -> str:
    """iTerm stores fonts as "<PostScript name> <size>", e.g. "Monaco 12"."""
    name = font.rsplit(" ", 1)[0] if " " in font else font
    return f"{name} {size:g}"


@dataclass
class _WindowState:
    candidate: str | None = None
    polls: int = 0
    applied: str | None = None
    sessions: set[str] = field(default_factory=set)


class WindowTracker:
    """Decides which sessions need a font change, given one observation per poll."""

    def __init__(self, settle_polls: int = SETTLE_POLLS):
        self.settle_polls = settle_polls
        self._windows: dict[str, _WindowState] = {}

    def observe(self, window_id: str, kind: str | None, session_ids: set[str]) -> tuple[str | None, set[str]]:
        """Returns (display kind, session ids to resize), or (None, set()) for nothing to do."""
        state = self._windows.setdefault(window_id, _WindowState())
        if kind != state.candidate:
            state.candidate, state.polls = kind, 0
        state.polls += 1
        if kind is None or state.polls < self.settle_polls:
            return None, set()
        if state.applied != kind:
            state.applied, state.sessions = kind, set(session_ids)
            return kind, set(session_ids)
        added = session_ids - state.sessions
        state.sessions = set(session_ids)
        return (kind, added) if added else (None, set())

    def forget_others(self, window_ids: set[str]) -> None:
        for window_id in list(self._windows):
            if window_id not in window_ids:
                del self._windows[window_id]

    def reset(self) -> None:
        self._windows.clear()


def log(message: str) -> None:
    print(time.strftime("%Y-%m-%d %H:%M:%S"), message, flush=True)


def iterm_is_running() -> bool:
    """Asks Launch Services; unlike `tell application`, this never launches iTerm."""
    script = f'application id "{ITERM_BUNDLE_ID}" is running'
    result = subprocess.run(["/usr/bin/osascript", "-e", script],
                            capture_output=True, text=True, timeout=10)
    return result.stdout.strip() == "true"


def _frame_tuple(frame) -> tuple[float, float, float, float]:
    return (frame.origin.x, frame.origin.y, frame.size.width, frame.size.height)


async def _apply_size(window, session_ids: set[str], size: float) -> int:
    import iterm2

    before = await window.async_get_frame()
    changed = 0
    for tab in window.tabs:
        for session in tab.sessions:
            if session.session_id not in session_ids:
                continue
            profile = await session.async_get_profile()
            change = iterm2.LocalWriteOnlyProfile()
            change.set_normal_font(font_with_size(profile.normal_font, size))
            if profile.use_non_ascii_font and profile.non_ascii_font:
                change.set_non_ascii_font(font_with_size(profile.non_ascii_font, size))
            await session.async_set_profile_properties(change)
            changed += 1
    # Keep the window exactly where and how big it was, whatever iTerm's
    # "Adjust window when changing font size" setting says.
    if _frame_tuple(await window.async_get_frame()) != _frame_tuple(before):
        await window.async_set_frame(before)
    return changed


async def _poll_once(app, tracker: WindowTracker, screens: list[Screen], sizes: dict[str, float]) -> None:
    import iterm2

    seen = set()
    for window in list(app.terminal_windows):
        seen.add(window.window_id)
        try:
            frame = await window.async_get_frame()
            session_ids = {s.session_id for tab in window.tabs for s in tab.sessions}
            kind, todo = tracker.observe(window.window_id, display_kind_for(_frame_tuple(frame), screens), session_ids)
            if kind:
                count = await _apply_size(window, todo, sizes[kind])
                log(f"window {window.window_id}: {kind} size {sizes[kind]:g} for {count} session(s)")
        except iterm2.RPCException as error:  # window closed mid-poll
            log(f"window {window.window_id}: skipped ({error})")
    tracker.forget_others(seen)


async def follow(connection) -> None:
    import iterm2

    app = await iterm2.async_get_app(connection)
    tracker = WindowTracker()
    sizes = load_sizes()
    config_mtime = CONFIG_PATH.stat().st_mtime if CONFIG_PATH.exists() else None
    screens, screens_read = read_screens(), time.monotonic()
    log(f"following windows; sizes {sizes}; screens {[(s.name, s.kind) for s in screens]}")
    while True:
        mtime = CONFIG_PATH.stat().st_mtime if CONFIG_PATH.exists() else None
        if mtime != config_mtime:
            config_mtime = mtime
            try:
                sizes = load_sizes()
                tracker.reset()  # re-apply everywhere with the new sizes
                log(f"config reloaded; sizes {sizes}")
            except ValueError as error:
                log(f"config ignored, keeping {sizes}: {error}")
        if time.monotonic() - screens_read > SCREEN_REFRESH_SECONDS:
            screens, screens_read = read_screens(), time.monotonic()
        await asyncio.wait_for(_poll_once(app, tracker, screens, sizes), POLL_TIMEOUT_SECONDS)
        await asyncio.sleep(POLL_SECONDS)


async def list_windows(connection) -> None:
    import iterm2

    app = await iterm2.async_get_app(connection)
    screens = read_screens()
    for window in app.terminal_windows:
        frame = _frame_tuple(await window.async_get_frame())
        kind = display_kind_for(frame, screens)
        print(f"window {window.window_id}: {kind}, frame {tuple(round(v) for v in frame)}")
        for tab in window.tabs:
            for session in tab.sessions:
                profile = await session.async_get_profile()
                name = await session.async_get_variable("name")
                print(f"  {profile.normal_font:<14} {name}")


def main(argv: list[str]) -> int:
    import iterm2

    if argv == ["--list"]:
        iterm2.run_until_complete(list_windows)
        return 0
    if argv:
        print(__doc__, file=sys.stderr)
        return 2
    load_sizes()  # fail at startup, not mid-run, on a bad config
    while not iterm_is_running():
        time.sleep(WAIT_FOR_ITERM_SECONDS)
    try:
        iterm2.run_until_complete(follow)
    except Exception as error:  # iTerm quit or restarted; launchd starts a fresh run
        log(f"disconnected: {type(error).__name__}: {error}")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
