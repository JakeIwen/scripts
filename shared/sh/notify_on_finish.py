#!/usr/bin/python3
"""Send one +n completion alert from Claude Code or Codex hooks.

UserPromptSubmit arms the session; Stop consumes its flag and starts the
existing ntfy sender detached. `hold` keeps the flag while the tasks live
at the next Stop keep running (Codex: through that turn's continuations);
without a hold, Claude Stops still wait while background workers run.
Standard library only, Python 3.9+ on Mac/Pi.
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
# Labels from Claude Code 2.1.321's task-type table (local_agent "subagent",
# local_workflow "workflow", remote_agent "cloud session"): workers that end
# on their own and wake the session when they do. Any other label, a new or
# unknown one included, sends at once: an early alert beats a lost one.
DEFERRING_TASK_TYPES = ("subagent", "workflow", "cloud session")
LIVE_TASK_STATUSES = ("running", "pending")
HOLD_COMMAND = f"/usr/bin/python3 {Path(__file__).resolve()} hold"
CONTEXT = (
    "The +n completion hook is armed: a hook will send the alert when this turn "
    "ends. If workers or background tasks are still outstanding when you end "
    f"this turn, run `{HOLD_COMMAND}` as your last tool call. The alert then "
    "waits until every background task running at that Stop has ended, and "
    "goes at the first Stop after that (on Codex, at the next turn's Stop). "
    "A task you restart under a new id needs a new hold. "
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


def workers_running(event: dict) -> bool:
    """Claude's Stop payload lists in-flight background work. Only the
    DEFERRING_TASK_TYPES workers defer the alert: they always end and wake
    the session with a notification turn whose Stop sends. Shells and
    monitors can be servers kept on purpose, teammates persist, and internal
    tasks (memory, auto-mode scans) never wake the session, so waiting on
    them could lose the alert. Codex sends no list."""
    tasks = event.get("background_tasks")
    if not isinstance(tasks, list):
        return False
    return any(isinstance(task, dict) and task.get("type") in DEFERRING_TASK_TYPES
               and task.get("status") in LIVE_TASK_STATUSES for task in tasks)


def live_task_ids(event: dict) -> set | None:
    """Ids of every running or pending task in Claude's list, shells
    included. None (unknown, never shorter) when the list is missing, is not
    a list, or has any entry without a non-empty string id and status."""
    tasks = event.get("background_tasks")
    if not isinstance(tasks, list):
        return None
    live = set()
    for task in tasks:
        if not isinstance(task, dict):
            return None
        task_id, status = task.get("id"), task.get("status")
        if not (isinstance(task_id, str) and task_id and isinstance(status, str) and status):
            return None
        if status in LIVE_TASK_STATUSES:
            live.add(task_id)
    return live


def capture_hold(saved: dict, live: set | None) -> bool:
    """Start a Claude hold from a snapshot: remember its live tasks, wait
    for a readable one, or (nothing running) release it at once."""
    if live is None:
        saved["hold_pending"] = True
        return True
    if live:
        saved["hold_ids"] = sorted(live)
        return True
    return False


def keep_armed(saved: dict, event: dict) -> bool:
    """Whether this Stop leaves the flag armed instead of sending.

    An explicit `hold` is the agent saying its work is still running, so it
    outranks the worker whitelist. Its states:
    - Codex (payload has `turn_id`; it lists no tasks): `held_turn` keeps the
      flag through that turn's continuations (stop_hook_active), and the
      next Stop outside one sends.
    - Claude, first held Stop: a readable snapshot's live task ids, shells
      included, become `hold_ids`; an empty one releases the hold at once
      (nothing is running, so this Stop is the finish); an unreadable one
      leaves `hold_pending` until a later Stop's list is readable.
    - `hold_ids`: kept while any remembered task is live or the list is
      unreadable; released once a readable list shows them all gone.
    Boundaries, as intended: a repeated `hold` replaces the remembered
    snapshot; a shell restarted under a new id is not the held task, so the
    agent holds again after restarting it; a held task that never ends never
    sends, which is what the agent asked for. The two-day expiry counts
    inactivity, since every Stop that keeps the flag rewrites it.
    With no hold, only DEFERRING_TASK_TYPES workers defer."""
    codex = "turn_id" in event
    live = None if codex else live_task_ids(event)
    if saved.pop("held", False):
        for state in ("held_turn", "hold_ids", "hold_pending"):
            saved.pop(state, None)
        if codex:
            saved["held_turn"] = True
            return True
        if capture_hold(saved, live):
            return True
    elif saved.pop("hold_pending", False):
        if capture_hold(saved, live):
            return True
    elif "hold_ids" in saved:
        held = saved["hold_ids"]
        held = {item for item in held if isinstance(item, str)} if isinstance(held, list) else set()
        if live is None or live & held:
            return True
        saved.pop("hold_ids")
    elif saved.get("held_turn"):
        if event.get("stop_hook_active") is True:
            return True
        saved.pop("held_turn")
    return workers_running(event)


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
    if keep_armed(saved, event):
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
