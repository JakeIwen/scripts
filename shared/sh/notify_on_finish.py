#!/usr/bin/python3
"""Send one +n completion alert from Claude Code or Codex hooks.

UserPromptSubmit arms the session; Stop consumes its flag and starts the
existing ntfy sender detached. `hold` skips one Stop while background work
continues. Standard library only, Python 3.9+ on Mac/Pi.
NOTIFY_ON_FINISH_STATE_DIR and NOTIFY_ON_FINISH_SENDER override the state
folder and Python sender path for isolated checks (no phone delivery).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time


TOKEN = re.compile(r"(^|\s)\+n(?=\s|$)")
MAX_AGE_SECONDS = 2 * 86400
MAX_MESSAGE_CHARS = 200
HOLD_COMMAND = f"/usr/bin/python3 {Path(__file__).resolve()} hold"
CONTEXT = (
    "The +n completion hook is armed: a hook will send the alert when this turn "
    "ends. If workers or background tasks are still outstanding when you end "
    f"this turn, run `{HOLD_COMMAND}` as your last tool call. The next Stop "
    "un-holds and keeps the flag without notifying; a later task-notification "
    "turn (without UserPromptSubmit) sends at its Stop unless you hold again. "
    "Do not send a completion notification yourself. Send your own alert only "
    "if the task stalls on Jacob mid-turn."
)


def prune(state_dir: Path) -> None:
    cutoff = time.time() - MAX_AGE_SECONDS
    for path in state_dir.glob("*.json"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
        except OSError:
            pass


def flag_path(state_dir: Path, session: str) -> Path:
    # Session ids are external input; hashing keeps every flag inside its folder.
    return state_dir / (hashlib.sha256(session.encode("utf-8")).hexdigest() + ".json")


def write_flag(flag: Path, saved: dict) -> None:
    with os.fdopen(os.open(flag, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as handle:
        json.dump(saved, handle)


def arm(flag: Path, event: dict) -> None:
    cwd = event.get("cwd")
    if not isinstance(cwd, str) or not cwd:
        cwd = os.getcwd()
    flag.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    write_flag(flag, {"cwd": cwd, "time": time.time()})
    json.dump({"hookSpecificOutput": {
        "hookEventName": "UserPromptSubmit", "additionalContext": CONTEXT,
    }}, sys.stdout)


def project_name(cwd: str) -> str:
    try:
        result = subprocess.run(
            ["/usr/bin/git", "-C", cwd, "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=1, check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            return Path(result.stdout.strip()).name
    except (OSError, subprocess.SubprocessError):
        pass
    return Path(cwd).name or cwd


def message_preview(message: object) -> str:
    if isinstance(message, str):
        for line in message.splitlines():
            if line.strip():
                text = line.strip().replace("`", "")
                text = re.sub(r"(\*\*|__)(.+?)\1", r"\2", text)
                text = re.sub(r"(?<!\w)([*_])(.+?)\1(?!\w)", r"\2", text)
                return text[:MAX_MESSAGE_CHARS]
    return "turn finished"


def finish(flag: Path, event: dict, sender: Path) -> None:
    # Atomic rename claims the request before delivery, so repeated/concurrent
    # Stop events cannot notify twice. Delivery failures are not retried.
    claimed = flag.with_suffix(".claimed.json")
    try:
        flag.rename(claimed)
    except FileNotFoundError:
        return
    try:
        saved = json.loads(claimed.read_text(encoding="utf-8"))
    finally:
        claimed.unlink(missing_ok=True)
    cwd = saved.get("cwd") if isinstance(saved, dict) else None
    if not isinstance(cwd, str) or not cwd:
        return
    if saved.pop("held", False):
        write_flag(flag, saved)
        return
    subprocess.Popen(
        [sys.executable, "-I", str(sender), "--send", project_name(cwd),
         message_preview(event.get("last_assistant_message")), "default"],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def hook(event: object, state_dir: Path, sender: Path) -> None:
    if not isinstance(event, dict):
        return
    prune(state_dir)
    session = event.get("session_id")
    if not isinstance(session, str) or not session:
        return
    flag = flag_path(state_dir, session)
    name = event.get("hook_event_name")
    prompt = event.get("prompt")
    if name == "UserPromptSubmit" and isinstance(prompt, str) and TOKEN.search(prompt):
        arm(flag, event)
    elif name == "Stop":
        finish(flag, event, sender)


def hold(state_dir: Path) -> int:
    session = os.environ.get("CODEX_THREAD_ID") or os.environ.get("CLAUDE_CODE_SESSION_ID")
    if not session:
        print("notify_on_finish: hold needs CLAUDE_CODE_SESSION_ID or CODEX_THREAD_ID", file=sys.stderr)
        return 1
    prune(state_dir)
    flag = flag_path(state_dir, session)
    if not flag.is_file():
        print("notify_on_finish: no +n flag exists for this session", file=sys.stderr)
        return 1
    saved = json.loads(flag.read_text(encoding="utf-8"))
    saved["held"] = True
    write_flag(flag, saved)
    print("+n held for the next Stop; completion notification remains armed.")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hook", action="store_true", help="read a hook event on stdin")
    parser.add_argument("action", nargs="?", choices=("hold",),
                        help="hold an armed session across the next Stop")
    args = parser.parse_args(argv)
    if not args.hook and not args.action:
        parser.print_help()
        return 0
    try:
        home = Path.home()
        state_dir = Path(os.environ.get("NOTIFY_ON_FINISH_STATE_DIR", home / ".agent/notify-on-finish"))
        if args.action == "hold":
            return hold(state_dir.expanduser())
        default_sender = (home / "dev/scripts/macbook/scripts/claude_ntfy_notify.py"
                          if sys.platform == "darwin" else home / "scripts/claude_ntfy_notify.py")
        sender = Path(os.environ.get("NOTIFY_ON_FINISH_SENDER", default_sender))
        hook(json.load(sys.stdin), state_dir.expanduser(), sender.expanduser())
    except (OSError, ValueError, TypeError, subprocess.SubprocessError) as error:
        print(f"notify_on_finish: {str(error).splitlines()[0]}", file=sys.stderr)
        return 1 if args.action == "hold" else 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
