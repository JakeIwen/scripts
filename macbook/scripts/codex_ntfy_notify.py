#!/usr/bin/env python3
"""Forward MacBook Codex turn-complete notifications through vanpi's ntfy sender."""

import json
import os
from pathlib import Path
import shlex
import sqlite3
import subprocess
import sys


SSH = "/usr/bin/ssh"
SSH_HOST = os.environ.get("CODEX_NTFY_SSH_HOST", "pi@vanpi.lan")
REMOTE_NTFY_SEND = "/home/pi/scripts/ntfy_send.sh"
CODEX_STATE = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")) / "state_5.sqlite"
MAX_MESSAGE_CHARS = 3500


def conversation_title(event: dict) -> str:
    for key in ("conversation-title", "thread-title"):
        title = event.get(key)
        if isinstance(title, str) and title.strip():
            return title.strip()

    thread_id = event.get("thread-id")
    if not thread_id or not CODEX_STATE.is_file():
        return ""
    try:
        with sqlite3.connect(f"file:{CODEX_STATE}?mode=ro", uri=True, timeout=1) as connection:
            row = connection.execute(
                "SELECT title FROM threads WHERE id = ?",
                (thread_id,),
            ).fetchone()
    except (OSError, sqlite3.Error):
        return ""
    return row[0].strip() if row and isinstance(row[0], str) else ""


def notification(event: dict) -> tuple[str, str]:
    cwd = event.get("cwd") or "unknown directory"
    project = Path(cwd).name or cwd
    assistant_message = (event.get("last-assistant-message") or "Turn complete").strip()
    message = f"{cwd}\n\n{assistant_message}"
    thread_title = conversation_title(event)
    if thread_title:
        message = f"{thread_title} - {message}"
    if len(message) > MAX_MESSAGE_CHARS:
        message = message[: MAX_MESSAGE_CHARS - 1].rstrip() + "…"
    return f"Codex ready — {project}", message


def remote_command(title: str, message: str) -> str:
    arguments = [
        "/usr/bin/env",
        "NTFY_TOPIC_VAR=NTFY_AGENT_URL",
        REMOTE_NTFY_SEND,
        title,
        message,
        "default",
        "robot",
    ]
    return " ".join(shlex.quote(argument) for argument in arguments)


def main() -> int:
    if len(sys.argv) != 2:
        print("codex_ntfy_notify: expected one JSON argument", file=sys.stderr)
        return 2

    try:
        event = json.loads(sys.argv[1])
    except json.JSONDecodeError as exc:
        print(f"codex_ntfy_notify: invalid JSON: {exc}", file=sys.stderr)
        return 2

    if event.get("type") != "agent-turn-complete":
        return 0

    title, message = notification(event)
    try:
        result = subprocess.run(
            [
                SSH,
                "-o",
                "BatchMode=yes",
                "-o",
                "ConnectTimeout=8",
                SSH_HOST,
                remote_command(title, message),
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except subprocess.TimeoutExpired:
        print("codex_ntfy_notify: notification timed out", file=sys.stderr)
        return 1
    if result.returncode != 0:
        detail = result.stderr.strip() or f"exit status {result.returncode}"
        print(f"codex_ntfy_notify: {detail}", file=sys.stderr)
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
