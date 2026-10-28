#!/usr/bin/python3
"""Save a photo from SSH stdin and insert its path into an existing LLM prompt."""
from __future__ import annotations

import argparse
from datetime import datetime
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
from typing import AbstractSet, NamedTuple, Optional, Sequence


MAX_IMAGE_BYTES = 60 * 1024 * 1024
MAX_IMAGE_EDGE = 2048
HEIF_BRANDS = {b"heic", b"heix", b"hevc", b"hevx", b"mif1", b"msf1"}
IMAGE_EXTENSIONS = {"jpg", "png", "heic", "heif", "gif", "webp"}
LLM_COMMANDS = {"claude", "codex"}
# iTerm2 appends the foreground job to a tab name: 'scripts ("Kapture MCP Bridge")'.
JOB_SUFFIX = re.compile(r'\s*\("[^"]*"\)\s*$')


class Session(NamedTuple):
    tty: str
    name: str


def session_label(name: str) -> str:
    """The phone notification shows the tab title Jacob recognises, not the job."""
    return JOB_SUFFIX.sub("", name).strip()


def classify_image(data: bytes) -> str:
    """Return the inbox extension from the file signature, not its supplied name."""
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if data.startswith(b"\x89PNG"):
        return "png"
    if data[4:8] == b"ftyp" and data[8:12] in HEIF_BRANDS:
        return "heif" if data[8:12] in {b"mif1", b"msf1"} else "heic"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "gif"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "webp"
    raise ValueError("not an image")


def photo_filename(extension: str, now: Optional[datetime] = None) -> str:
    """Keep generated basenames safe for both a terminal prompt and AppleScript."""
    if extension not in IMAGE_EXTENSIONS:
        raise ValueError("unsupported image extension")
    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    return f"{stamp}-{secrets.token_hex(2)}.{extension}"


def _new_photo_file(directory: Path, extension: str) -> tuple[Path, int]:
    while True:
        path = directory / photo_filename(extension)
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            continue
        os.fchmod(fd, 0o600)
        return path, fd


def _save_photo(data: bytes, extension: str) -> Path:
    directory = Path.home() / ".agent" / "inbox"
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory.chmod(0o700)
    path, fd = _new_photo_file(directory, extension)
    with os.fdopen(fd, "wb") as destination:
        destination.write(data)
    return path


def _image_edge(path: Path) -> int:
    result = subprocess.run(
        ["/usr/bin/sips", "-g", "pixelWidth", "-g", "pixelHeight", str(path)],
        check=True, capture_output=True, text=True, timeout=30)
    dimensions = {}
    for line in result.stdout.splitlines():
        key, separator, value = line.strip().partition(": ")
        if separator and key in {"pixelWidth", "pixelHeight"}:
            dimensions[key] = int(value)
    if len(dimensions) != 2 or min(dimensions.values()) <= 0:
        raise ValueError("sips did not report valid image dimensions")
    return max(dimensions.values())


def _normalize_photo(path: Path) -> Path:
    converted = None
    try:
        edge = _image_edge(path)
        command = ["/usr/bin/sips"]
        if path.suffix in {".heic", ".heif"}:
            # Reserve a private, noncolliding destination before sips opens it.
            converted, fd = _new_photo_file(path.parent, "jpg")
            os.close(fd)
            command.extend(["-s", "format", "jpeg"])
        # sips -Z also enlarges small images, so only request a resize above the cap.
        if edge > MAX_IMAGE_EDGE:
            command.extend(["--resampleHeightWidthMax", str(MAX_IMAGE_EDGE)])
        elif converted is None:
            return path
        command.append(str(path))
        if converted is not None:
            command.extend(["--out", str(converted)])
        subprocess.run(command, check=True, capture_output=True, timeout=30)
        result = converted or path
        if result.stat().st_size == 0:
            raise OSError("sips produced an empty image")
        result.chmod(0o600)
        if converted is not None:
            path.unlink()
        return result
    except (OSError, ValueError, subprocess.SubprocessError):
        if converted is not None:
            try:
                converted.unlink()
            except OSError:
                pass
        return path


def _tty_name(tty: str) -> str:
    return tty.strip().removeprefix("/dev/")


def choose_target(sessions: Sequence[Session], current_tty: Optional[str],
                  llm_ttys: AbstractSet[str]) -> Optional[Session]:
    """Prefer the current LLM session, otherwise require exactly one candidate."""
    eligible = {_tty_name(tty) for tty in llm_ttys if tty and "?" not in tty}
    candidates = [session for session in sessions if _tty_name(session.tty) in eligible]
    current = _tty_name(current_tty or "")
    for session in candidates:
        if _tty_name(session.tty) == current:
            return session
    return candidates[0] if len(candidates) == 1 else None


def _llm_ttys() -> dict[str, str]:
    try:
        result = subprocess.run(["/bin/ps", "-axo", "tty=,comm="], check=True,
                                capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return {}
    tools = {}
    for line in result.stdout.splitlines():
        fields = line.strip().split(maxsplit=1)
        if len(fields) != 2:
            continue
        tty, command = fields
        tool = Path(command).name
        if "?" not in tty and tool in LLM_COMMANDS:
            tools.setdefault(_tty_name(tty), tool)
    return tools


def _iterm_running() -> bool:
    try:
        result = subprocess.run(["/usr/bin/pgrep", "-x", "iTerm2"],
                                capture_output=True, timeout=5)
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _iterm_sessions() -> tuple[list[Session], Optional[str]]:
    if not _iterm_running():
        return [], None
    script = '''if application "iTerm2" is not running then return ""
set resultText to ""
tell application "iTerm2"
try
set resultText to (tty of current session of current window) & linefeed
on error
set resultText to linefeed
end try
repeat with w in windows
repeat with t in tabs of w
repeat with s in sessions of t
set resultText to resultText & (tty of s) & (ASCII character 9) & (name of s) & linefeed
end repeat
end repeat
end repeat
end tell
return resultText'''
    try:
        result = subprocess.run(["/usr/bin/osascript", "-e", script], check=True,
                                capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return [], None
    lines = result.stdout.splitlines()
    if not lines:
        return [], None
    sessions = []
    for line in lines[1:]:
        tty, separator, name = line.partition("\t")
        if separator and tty.strip():
            sessions.append(Session(_tty_name(tty), session_label(name)))
    return sessions, _tty_name(lines[0]) or None


def applescript_string(value: str) -> str:
    """Quote a literal without letting a path change the AppleScript program."""
    escaped = (value.replace("\\", "\\\\").replace('"', '\\"')
               .replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t"))
    return '"' + escaped + '"'


def _write_path(session: Session, path: Path) -> bool:
    if not _iterm_running():
        return False
    tty = applescript_string("/dev/" + _tty_name(session.tty))
    text = applescript_string(str(path) + " ")
    script = f'''if application "iTerm2" is not running then return ""
tell application "iTerm2"
repeat with w in windows
repeat with t in tabs of w
repeat with s in sessions of t
if tty of s is {tty} then
tell s to write text {text} newline no
return "sent"
end if
end repeat
end repeat
end repeat
end tell
return ""'''
    try:
        result = subprocess.run(["/usr/bin/osascript", "-e", script], check=True,
                                capture_output=True, text=True, timeout=5)
        return result.stdout.strip() == "sent"
    except (OSError, subprocess.SubprocessError):
        return False


def _deliver_photo(path: Path, dry_run: bool) -> str:
    tools = _llm_ttys()
    sessions, current_tty = _iterm_sessions()
    target = choose_target(sessions, current_tty, set(tools))
    if target is None:
        candidates = [session.name for session in sessions if _tty_name(session.tty) in tools]
        if len(candidates) > 1:
            return f"saved, several LLM tabs ({', '.join(candidates)}): {path}"
        return f"saved, no LLM tab in front: {path}"
    label = f"{target.name} ({tools[_tty_name(target.tty)]})"
    if dry_run:
        return f"would send to {label}: {path}"
    if _write_path(target, path):
        return f"→ {label}: {path}"
    return f"saved, could not write to {label}: {path}"


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="save without writing to iTerm2")
    arguments = parser.parse_args(argv)
    try:
        data = sys.stdin.buffer.read(MAX_IMAGE_BYTES + 1)
        if not data:
            raise ValueError("empty image")
        if len(data) > MAX_IMAGE_BYTES:
            raise ValueError("image exceeds 60 MB")
        extension = classify_image(data)
        path = _save_photo(data, extension)
    except (OSError, ValueError) as error:
        print(" ".join(str(error).splitlines()))
        return 1
    path = _normalize_photo(path)
    print(_deliver_photo(path, arguments.dry_run))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
