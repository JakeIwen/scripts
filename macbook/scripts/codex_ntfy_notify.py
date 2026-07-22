#!/usr/bin/env python3
"""Forward MacBook Codex turn-complete notifications through vanpi's ntfy sender."""

import json
import os
from pathlib import Path
import shlex
import subprocess
import sys


SSH = "/usr/bin/ssh"
SSH_HOST = os.environ.get("CODEX_NTFY_SSH_HOST", "pi@vanpi.lan")
REMOTE_NTFY_SEND = "/home/pi/scripts/ntfy_send.sh"
CODEX_DIR = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
SESSION_INDEX = CODEX_DIR / "session_index.jsonl"
MAX_MESSAGE_CHARS = 3500
MAX_TITLE_CHARS = 200


def conversation_title(event: dict) -> str:
    thread_id = event.get("thread-id")
    if not thread_id or not SESSION_INDEX.is_file():
        return ""
    title = ""
    try:
        with SESSION_INDEX.open(encoding="utf-8") as index:
            for line in index:
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                candidate = entry.get("thread_name")
                if entry.get("id") == thread_id and isinstance(candidate, str) and candidate.strip():
                    title = candidate.strip()
    except OSError:
        return ""
    return title


def notification(event: dict) -> tuple[str, str]:
    cwd = event.get("cwd") or "unknown directory"
    project = Path(cwd).name or cwd
    assistant_message = (event.get("last-assistant-message") or "Turn complete").strip()
    thread_title = conversation_title(event)
    title = thread_title or project
    if len(title) > MAX_TITLE_CHARS:
        title = title[: MAX_TITLE_CHARS - 1].rstrip() + "…"
    message = f"{assistant_message}\n\n{cwd}"
    if len(message) > MAX_MESSAGE_CHARS:
        message = message[: MAX_MESSAGE_CHARS - 1].rstrip() + "…"
    return title, message


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
