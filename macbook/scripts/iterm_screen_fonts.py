#!/usr/bin/env python3
"""Match each iTerm2 window's font size to the display it sits on.

Runs as the LaunchAgent com.jacobr.iterm-screen-fonts from its own venv
(install_iterm_screen_fonts.zsh builds it). It reads window frames over iTerm2's
Python API at the configured interval. When a window settles on another display,
or gains a tab or pane, those sessions get that display's font size.

Cmd+= (or Cmd++) and Cmd+- resize every tab and pane of the focused window, not
just the focused session, and Cmd+0 puts the window back to its display's size.
iTerm's global key bindings call a function this follower registers;
--install-keys writes those bindings. A size picked that way also goes to tabs
the window gains, until the window moves to the other display.

Settings are declared in shared/settings/iterm-screen-fonts.json and saved in
~/.config/jacobr/iterm-screen-fonts.json. Valid edits are applied while running.

  iterm_screen_fonts.py                 follow windows (what the LaunchAgent runs)
  iterm_screen_fonts.py --list          print each window's display and fonts
  iterm_screen_fonts.py --install-keys  bind the window font keys
  iterm_screen_fonts.py --remove-keys   give them back to iTerm's View menu
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import json
from pathlib import Path
import plistlib
import subprocess
import sys
import time

import tool_settings

TOOL = "iterm-screen-fonts"
BUILT_IN = "built_in"
EXTERNAL = "external"
DISPLAY_KINDS = (BUILT_IN, EXTERNAL)
FOLLOWER_SETTING_NAMES = (BUILT_IN, EXTERNAL, "poll_seconds", "settle_polls")
ITERM_BUNDLE_ID = "com.googlecode.iterm2"
# The window font keys, by iTerm's "<character>-<modifier flags>" key-map key,
# with the step each one takes (0 means the display's size). iTerm runs a
# global key binding before the View menu item that owns the same keys.
WINDOW_FONT_KEYS = {
    "0x3d-0x100000": 1,   # Cmd+=
    "0x2b-0x120000": 1,   # Cmd+Shift+= (Cmd++)
    "0x2d-0x100000": -1,  # Cmd+-
    "0x30-0x100000": 0,   # Cmd+0
}
# The name of the RPC follow() registers; iTerm invokes RPCs by function name.
STEP_FUNCTION = "iterm_window_font_step"
INVOKE_SCRIPT_FUNCTION = 60  # iterm2.BindingAction.INVOKE_SCRIPT_FUNCTION
GLOBAL_KEY_MAP = "GlobalKeyMap"
# iTerm uses this map until the global key bindings are first edited.
DEFAULT_GLOBAL_KEY_MAP = Path("/Applications/iTerm.app/Contents/Resources/DefaultGlobalKeyMap.plist")
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


def follower_settings(values: dict[str, object]) -> tuple[dict[str, float], float, int]:
    """Project the shared settings record into the values used by the follower."""
    sizes = {kind: values[kind] for kind in DISPLAY_KINDS}
    return sizes, values["poll_seconds"], values["settle_polls"]


def font_with_size(font: str, size: float) -> str:
    """iTerm stores fonts as "<PostScript name> <size>", e.g. "Monaco 12"."""
    name = font.rsplit(" ", 1)[0] if " " in font else font
    return f"{name} {size:g}"


def font_size(font: str) -> float:
    return float(font.rsplit(" ", 1)[1])


def font_size_bounds() -> tuple[float, float]:
    """The smallest and largest size the display settings accept."""
    settings = [s for s in tool_settings.load_declaration(TOOL).settings if s.name in DISPLAY_KINDS]
    return min(s.minimum for s in settings), max(s.maximum for s in settings)


def stepped_size(size: float, step: int, bounds: tuple[float, float]) -> float:
    low, high = bounds
    return min(high, max(low, size + step))


def _window_font_binding(step: int) -> dict[str, object]:
    return {"Action": INVOKE_SCRIPT_FUNCTION, "Text": f"{STEP_FUNCTION}(session_id: id, step: {step})"}


def with_window_font_keys(key_map: dict[str, dict]) -> dict[str, dict]:
    merged = dict(key_map)
    merged.update({key: _window_font_binding(step) for key, step in WINDOW_FONT_KEYS.items()})
    return merged


def without_window_font_keys(key_map: dict[str, dict]) -> dict[str, dict]:
    """Drops only bindings that call STEP_FUNCTION, so a later rebinding by hand survives."""
    return {key: binding for key, binding in key_map.items()
            if not str(binding.get("Text", "")).startswith(f"{STEP_FUNCTION}(")}


@dataclass
class _WindowState:
    candidate: str | None = None
    polls: int = 0
    applied: str | None = None
    sessions: set[str] = field(default_factory=set)
    held: float | None = None  # a size picked with the window font keys


class WindowTracker:
    """Decides which sessions need a font change, given one observation per poll."""

    # Requiring consecutive observations keeps a boundary crossing from changing
    # fonts while the user is still dragging the window.
    def __init__(self, settle_polls: int = 2):
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
            state.applied, state.sessions, state.held = kind, set(session_ids), None
            return kind, set(session_ids)
        added = session_ids - state.sessions
        state.sessions = set(session_ids)
        return (kind, added) if added else (None, set())

    def applied_kind(self, window_id: str) -> str | None:
        state = self._windows.get(window_id)
        return state.applied if state else None

    def hold(self, window_id: str, size: float | None) -> None:
        """Give tabs the window gains this size instead of the display's, until it moves."""
        self._windows.setdefault(window_id, _WindowState()).held = size

    def size_for(self, window_id: str, kind: str, sizes: dict[str, float]) -> float:
        state = self._windows.get(window_id)
        return state.held if state and state.held is not None else sizes[kind]

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
                size = tracker.size_for(window.window_id, kind, sizes)
                count = await _apply_size(window, todo, size)
                log(f"window {window.window_id}: {kind} size {size:g} for {count} session(s)")
        except iterm2.RPCException as error:  # window closed mid-poll
            log(f"window {window.window_id}: skipped ({error})")
    tracker.forget_others(seen)


async def _step_window_font(app, tracker: WindowTracker, sizes: dict[str, float],
                            session_id: str, step: int) -> None:
    """Resize every session in the window holding session_id, from that session's size."""
    session = app.get_session_by_id(session_id)
    window, _tab = app.get_window_and_tab_for_session(session) if session else (None, None)
    if window is None:
        return
    if step:
        profile = await session.async_get_profile()
        size = held = stepped_size(font_size(profile.normal_font), step, font_size_bounds())
    else:
        kind = tracker.applied_kind(window.window_id)
        if kind is None:  # not settled on a display yet
            return
        size, held = sizes[kind], None
    session_ids = {s.session_id for tab in window.tabs for s in tab.sessions}
    count = await _apply_size(window, session_ids, size)
    tracker.hold(window.window_id, held)
    log(f"window {window.window_id}: font keys size {size:g} for {count} session(s)")


async def follow(connection) -> None:
    import iterm2

    app = await iterm2.async_get_app(connection)
    reloader = tool_settings.Reloader(TOOL)
    settings = reloader.poll().values
    sizes, poll_seconds, settle_polls = follower_settings(settings)
    tracker = WindowTracker(settle_polls)

    @iterm2.RPC
    async def iterm_window_font_step(session_id, step):  # named STEP_FUNCTION
        await _step_window_font(app, tracker, sizes, session_id, step)  # sizes as last reloaded

    await iterm_window_font_step.async_register(connection)
    ignored_error = None
    screens, screens_read = read_screens(), time.monotonic()
    log(f"following windows; sizes {sizes}; screens {[(s.name, s.kind) for s in screens]}")
    while True:
        reloaded = reloader.poll()
        if reloaded.error is not None:
            if reloaded.error != ignored_error:
                log(f"settings ignored, keeping {settings}: {reloaded.error}")
            ignored_error = reloaded.error
        elif reloaded.changed:
            ignored_error = None
            new_sizes, new_poll_seconds, new_settle_polls = follower_settings(reloaded.values)
            if new_sizes != sizes or new_settle_polls != settle_polls:
                tracker.settle_polls = new_settle_polls
                tracker.reset()
            settings = reloaded.values
            sizes = new_sizes
            poll_seconds = new_poll_seconds
            settle_polls = new_settle_polls
            log(f"settings reloaded; sizes {sizes}; poll_seconds {poll_seconds:g}; "
                f"settle_polls {settle_polls}")
        else:
            ignored_error = None
        if time.monotonic() - screens_read > SCREEN_REFRESH_SECONDS:
            screens, screens_read = read_screens(), time.monotonic()
        await asyncio.wait_for(_poll_once(app, tracker, screens, sizes), POLL_TIMEOUT_SECONDS)
        await asyncio.sleep(poll_seconds)


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


async def set_window_font_keys(connection, install: bool) -> None:
    import iterm2.rpc

    reply = await iterm2.rpc.async_get_preference(connection, GLOBAL_KEY_MAP)
    current = json.loads(reply.preferences_response.results[0].get_preference_result.json_value)
    if current is None:
        with DEFAULT_GLOBAL_KEY_MAP.open("rb") as source:
            current = plistlib.load(source)
    updated = with_window_font_keys(current) if install else without_window_font_keys(current)
    if updated != current:
        reply = await iterm2.rpc.async_set_preference(connection, GLOBAL_KEY_MAP, json.dumps(updated))
        status = reply.preferences_response.results[0].set_preference_result.status
        if status != iterm2.api_pb2.PreferencesResponse.Result.SetPreferenceResult.Status.Value("OK"):
            raise iterm2.RPCException(f"setting {GLOBAL_KEY_MAP} failed with status {status}")
    print(f"window font keys {'bound' if install else 'removed'}; {len(updated)} global key bindings")


def main(argv: list[str]) -> int:
    import iterm2

    if argv == ["--list"]:
        iterm2.run_until_complete(list_windows)
        return 0
    if argv in (["--install-keys"], ["--remove-keys"]):
        install = argv == ["--install-keys"]
        iterm2.run_until_complete(lambda connection: set_window_font_keys(connection, install))
        return 0
    if argv:
        print(__doc__, file=sys.stderr)
        return 2
    tool_settings.load(TOOL)  # fail at startup, not mid-run, on bad saved settings
    while not iterm_is_running():
        time.sleep(WAIT_FOR_ITERM_SECONDS)
    try:
        iterm2.run_until_complete(follow)
    except Exception as error:  # iTerm quit or restarted; launchd starts a fresh run
        log(f"disconnected: {type(error).__name__}: {error}")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
