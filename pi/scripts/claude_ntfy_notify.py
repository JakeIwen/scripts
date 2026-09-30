#!/usr/bin/env python3
"""Forward vanpi Claude Code notifications to Jacob's ntfy topic.

Claude Code counterpart to codex_ntfy_notify.py. Wired as a Stop and
Notification hook in ~/.claude/settings.json; reads the hook payload from
stdin, then re-executes itself detached so delivery never delays the turn.
"""

import json
import os
from pathlib import Path
import subprocess
import sys


NTFY_SEND = "/home/pi/scripts/ntfy_send.sh"
MAX_MESSAGE_CHARS = 3500
MAX_TITLE_CHARS = 200


def last_assistant_message(transcript_path: str) -> str:
    if not transcript_path or not Path(transcript_path).is_file():
        return ""
    text = ""
    try:
        with open(transcript_path, encoding="utf-8") as transcript:
            for line in transcript:
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if entry.get("type") != "assistant":
                    continue
                parts = [
                    item.get("text", "")
                    for item in entry.get("message", {}).get("content", [])
                    if isinstance(item, dict) and item.get("type") == "text"
                ]
                candidate = "\n".join(part for part in parts if part).strip()
                if candidate:
                    text = candidate
    except OSError:
        return ""
    return text


def notification(event: dict) -> tuple[str, str, str]:
    cwd = event.get("cwd") or "unknown directory"
    project = Path(cwd).name or cwd
    if event.get("hook_event_name") == "Notification":
        title = f"{project}: attention"
        body = (event.get("message") or "Claude needs attention").strip()
        priority = "high"
    else:
        title = project
        body = last_assistant_message(event.get("transcript_path", "")) or "Turn complete"
        priority = "default"
    if len(title) > MAX_TITLE_CHARS:
        title = title[: MAX_TITLE_CHARS - 1].rstrip() + "…"
    message = f"{body}\n\n{cwd}"
    if len(message) > MAX_MESSAGE_CHARS:
        message = message[: MAX_MESSAGE_CHARS - 1].rstrip() + "…"
    return title, message, priority


def send(title: str, message: str, priority: str) -> int:
    env = os.environ.copy()
    env["NTFY_TOPIC_VAR"] = "NTFY_AGENT_URL"
    try:
        result = subprocess.run(
            [NTFY_SEND, title, message, priority, "robot"],
            env=env,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except subprocess.TimeoutExpired:
        print("claude_ntfy_notify: notification timed out", file=sys.stderr)
        return 1
    if result.returncode != 0:
        detail = result.stderr.strip() or f"exit status {result.returncode}"
        print(f"claude_ntfy_notify: {detail}", file=sys.stderr)
    return result.returncode


def main() -> int:
    if len(sys.argv) == 5 and sys.argv[1] == "--send":
        return send(sys.argv[2], sys.argv[3], sys.argv[4])

    try:
        event = json.load(sys.stdin)
    except json.JSONDecodeError as exc:
        print(f"claude_ntfy_notify: invalid JSON: {exc}", file=sys.stderr)
        return 2

    if event.get("hook_event_name") not in ("Stop", "Notification"):
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
