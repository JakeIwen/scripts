"""Find a Claude Code conversation's display name in its transcript.

Claude appends metadata rows to the session JSONL: `custom-title`
(`customTitle`, set by /rename or --name, always paired with an `agent-name`
row), `agent-name` (`agentName`) and the generated `ai-title` (`aiTitle`).
Claude re-appends them near the end of the file as the session goes on, so the
newest row of each kind is found by reading backwards in chunks. A user title
beats a generated one; within a kind the latest row wins.
"""

import glob
import json
import os
import re
import sys

CHUNK_BYTES = 256 * 1024
MAX_SCAN_BYTES = 16 * 1024 * 1024
# Preference order: explicit user names first, then the generated title.
KINDS = (("custom-title", "customTitle"), ("agent-name", "agentName"), ("ai-title", "aiTitle"))
MARKERS = tuple(f'"{field}"'.encode() for _, field in KINDS)
SESSION_ID = re.compile(r"[0-9A-Za-z-]{8,64}")


def clean_title(value, limit: int) -> str:
    text = " ".join(value.split()) if isinstance(value, str) else ""
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _title_from_line(line: bytes):
    if not any(marker in line for marker in MARKERS):
        return None
    try:
        row = json.loads(line)
    except ValueError:
        return None
    if not isinstance(row, dict):
        return None
    for kind, field in KINDS:
        if row.get("type") == kind:
            title = clean_title(row.get(field), 10_000)
            return (kind, title) if title else None
    return None


def transcript_title(path: str, limit: int = 60) -> str:
    """Return the conversation name, or "" when none is recorded or readable."""
    latest = {}
    try:
        with open(path, "rb") as handle:
            end = handle.seek(0, os.SEEK_END)
            floor = max(0, end - MAX_SCAN_BYTES)
            carry = b""
            while end > floor and "custom-title" not in latest:
                start = max(floor, end - CHUNK_BYTES)
                handle.seek(start)
                lines = (handle.read(end - start) + carry).split(b"\n")
                # The first piece may be a cut line; carry it into the next chunk.
                carry, lines = (lines[0], lines[1:]) if start > floor else (b"", lines)
                for line in reversed(lines):
                    found = _title_from_line(line)
                    if found:
                        latest.setdefault(*found)
                end = start
    except OSError:
        return ""
    for kind, _ in KINDS:
        if kind in latest:
            return clean_title(latest[kind], limit)
    return ""


def event_transcript(event: dict) -> str:
    """Transcript path from a hook payload, falling back to the session id."""
    path = event.get("transcript_path")
    if isinstance(path, str) and path:
        return path
    session = event.get("session_id")
    if isinstance(session, str) and SESSION_ID.fullmatch(session):
        pattern = os.path.expanduser(f"~/.claude/projects/*/{session}.jsonl")
        try:
            matches = sorted(glob.glob(pattern), key=os.path.getmtime, reverse=True)
        except OSError:
            return ""
        if matches:
            return matches[0]
    return ""


def main() -> int:
    """Read a hook payload and print its title for the standalone host adapters."""
    try:
        event = json.load(sys.stdin)
    except (ValueError, OSError):
        return 0
    if isinstance(event, dict):
        print(transcript_title(event_transcript(event)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
