#!/usr/bin/env python3
"""Forward Codex turn-complete notifications to Jacob's ntfy topic."""

import json
import os
from pathlib import Path
import subprocess
import sys


NTFY_SEND = "/home/pi/scripts/ntfy_send.sh"
MAX_MESSAGE_CHARS = 3500


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

    cwd = event.get("cwd") or "unknown directory"
    project = Path(cwd).name or cwd
    assistant_message = (event.get("last-assistant-message") or "Turn complete").strip()
    if len(assistant_message) > MAX_MESSAGE_CHARS:
        assistant_message = assistant_message[: MAX_MESSAGE_CHARS - 1].rstrip() + "…"

    title = f"Codex ready — {project}"
    message = f"{cwd}\n\n{assistant_message}"

    env = os.environ.copy()
    env["NTFY_TOPIC_VAR"] = "NTFY_AGENT_URL"
    try:
        result = subprocess.run(
            [NTFY_SEND, title, message, "default", "robot"],
            env=env,
            capture_output=True,
            text=True,
            timeout=20,
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
