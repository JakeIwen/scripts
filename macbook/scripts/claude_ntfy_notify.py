#!/usr/bin/env python3
"""Forward MacBook Claude Code notifications through vanpi's ntfy sender.

Wired as the Notification hook in ~/.claude/settings.json: reads the hook
payload from stdin, then re-executes itself detached so delivery never delays
the session. `--send <title> <message> <priority>` sends one alert directly.
Tries vanpi.lan first, then bare vanpi (LAN probe, else Tailscale).
"""

import json
import os
from pathlib import Path
import shlex
import subprocess
import sys


SSH = "/usr/bin/ssh"
SSH_HOSTS = (
    [os.environ["CLAUDE_NTFY_SSH_HOST"]]
    if os.environ.get("CLAUDE_NTFY_SSH_HOST")
    else ["pi@vanpi.lan", "pi@vanpi"]
)
REMOTE_NTFY_SEND = "/home/pi/scripts/ntfy_send.sh"
MAX_MESSAGE_CHARS = 3500
MAX_TITLE_CHARS = 200


def notification(event: dict) -> tuple[str, str, str]:
    cwd = event.get("cwd") or "unknown directory"
    project = Path(cwd).name or cwd
    title = f"{project}: attention"
    body = (event.get("message") or "Claude needs attention").strip()
    priority = "high"
    if len(title) > MAX_TITLE_CHARS:
        title = title[: MAX_TITLE_CHARS - 1].rstrip() + "…"
    message = f"{body}\n\n{cwd}"
    if len(message) > MAX_MESSAGE_CHARS:
        message = message[: MAX_MESSAGE_CHARS - 1].rstrip() + "…"
    return title, message, priority


def remote_command(title: str, message: str, priority: str) -> str:
    arguments = [
        "/usr/bin/env",
        "NTFY_TOPIC_VAR=NTFY_AGENT_URL",
        REMOTE_NTFY_SEND,
        title,
        message,
        priority,
        "robot",
    ]
    return " ".join(shlex.quote(argument) for argument in arguments)


def send(title: str, message: str, priority: str) -> int:
    for host in SSH_HOSTS:
        try:
            result = subprocess.run(
                [
                    SSH,
                    "-o",
                    "BatchMode=yes",
                    "-o",
                    "ConnectTimeout=5",
                    host,
                    remote_command(title, message, priority),
                ],
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )
        except subprocess.TimeoutExpired:
            continue
        if result.returncode == 0:
            return 0
    print("claude_ntfy_notify: delivery failed on all hosts", file=sys.stderr)
    return 1


def main() -> int:
    if len(sys.argv) == 5 and sys.argv[1] == "--send":
        return send(sys.argv[2], sys.argv[3], sys.argv[4])

    try:
        event = json.load(sys.stdin)
    except json.JSONDecodeError as exc:
        print(f"claude_ntfy_notify: invalid JSON: {exc}", file=sys.stderr)
        return 2

    if event.get("hook_event_name") != "Notification":
        return 0

    title, message, priority = notification(event)
    subprocess.Popen(
        [sys.executable, os.path.abspath(__file__), "--send", title, message, priority],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
