#!/usr/bin/python3
"""Send a reply longer than one terminal screen back to be moved to a page.

The global "Long replies" rule puts any reply past about 50 rows or 450 words
in a local page. Agents misjudge their own length while writing, so this
Claude Code and Codex Stop hook measures the finished reply and, past either
limit with no page link in it, blocks once with instructions. Only replies
Jacob reads in a terminal count: headless runs (`claude -p`, `codex exec`) and
subagents pass. Failures never block. Standard library only; supports Mac
/usr/bin/python3 3.9 and Pi Python 3.11.
"""

from __future__ import annotations

import json
import math
import os
import re
import sys

WORD_LIMIT = 450
ROW_LIMIT = 50
# The narrower of Jacob's two iTerm tab widths (104 and 114 columns, 2026-11-02).
COLUMNS = 104
# Both agents draw reply text two columns in, under the message marker.
GUTTER = 2
ART_SCRIPT = "~/.codex/skills/codex-artifact/scripts/agent_art.py"
PAGE_LINK = re.compile(r"file://\S*/\.agent/artifacts/")
FENCE = re.compile(r"^\s*(`{3,}|~{3,})")
WORD = re.compile(r"\S*[^\W_]\S*")
LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")
MARKUP = re.compile(r"\*\*|__|`|^\s*#{1,6}\s+")
LIST_ITEM = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")


def prose(message: str) -> list[str]:
    """Lines outside fenced blocks: the rule exempts commands and scripts
    Jacob must act on, and replies carry those in fences."""
    lines, fence = [], ""
    for line in message.splitlines():
        marker = FENCE.match(line)
        if marker:
            token = marker.group(1)
            if not fence:
                fence = token
            elif token[0] == fence[0] and len(token) >= len(fence):
                fence = ""
            continue
        if not fence:
            lines.append(line)
    return lines


def word_count(lines: list[str]) -> int:
    return sum(len(WORD.findall(line)) for line in lines)


def wrapped_rows(text: str, width: int, indent: int) -> int:
    if len(text) <= width:
        return 1
    # Wrapped lines of a list item hang under its text, not its marker.
    return 1 + math.ceil((len(text) - width) / max(width - indent, 20))


def row_count(lines: list[str], columns: int = COLUMNS) -> int:
    """Rendered rows, estimated: markdown markers dropped, long lines wrapped,
    tables given their top and bottom borders."""
    width, total, in_table = columns - GUTTER, 0, False
    for line in lines:
        table_row = line.lstrip().startswith("|")
        if table_row != in_table:
            total += 1
            in_table = table_row
        text = MARKUP.sub("", LINK.sub(r"\1", line)).rstrip()
        item = LIST_ITEM.match(text)
        indent = len(item.group(0)) if item else len(text) - len(text.lstrip())
        total += wrapped_rows(text, width, indent)
    return total + (1 if in_table else 0)


def codex_source(transcript: object) -> object:
    """The session_meta source Codex writes first in its rollout: "cli" for
    the TUI, "exec" for headless runs, a dict for subagents."""
    if not isinstance(transcript, str) or not transcript:
        return None
    try:
        with open(transcript, encoding="utf-8") as handle:
            first = json.loads(handle.readline())
    except (OSError, ValueError):
        return None
    payload = first.get("payload") if isinstance(first, dict) else None
    return payload.get("source") if isinstance(payload, dict) else None


def is_codex(event: dict) -> bool:
    # Payload, not environment: a codex exec started from a Claude shell
    # inherits Claude's variables.
    return "turn_id" in event


def attended(event: dict, env: dict) -> bool:
    if is_codex(event):
        return codex_source(event.get("transcript_path")) == "cli"
    state = env.get("CLAUDE_CODE_SESSION_ATTENDED")
    if state is not None:
        return state == "1"
    return env.get("CLAUDE_CODE_ENTRYPOINT") == "cli"


def reason(event: dict, words: int, rows: int) -> str:
    claude = not is_codex(event)
    agent_flag = " --agent claude" if claude else ""
    text = (
        f"Long replies rule: this reply measures about {words} words and {rows} rows, "
        f"past one terminal screen ({WORD_LIMIT} words or {ROW_LIMIT} rows; fenced blocks "
        f"don't count). Save the reply itself as a local page with "
        f"`python3 {ART_SCRIPT} save{agent_flag} --title \"<name>\"` (body on stdin; "
        f"no --hosted, no Artifact publish). Then send a short final message: anything "
        f"Jacob must act on, in full; a summary of up to five lines; the page's file:// "
        f"URL; and the Terms block, if any. Leave the reply in the terminal only if Jacob "
        f"explicitly asked for the answer there, or a script or parent agent reads it; "
        f"then reply in one line saying which."
    )
    if claude and sys.platform == "darwin":
        text += " If you armed +x this turn, arm it again as your last tool call."
    return text


def verdict(event: dict, env: dict) -> str | None:
    """The block reason for an over-long attended reply, else None."""
    message = event.get("last_assistant_message")
    if (event.get("hook_event_name") != "Stop" or event.get("stop_hook_active")
            or not isinstance(message, str) or PAGE_LINK.search(message)):
        return None
    lines = prose(message)
    words, rows = word_count(lines), row_count(lines)
    if words <= WORD_LIMIT and rows <= ROW_LIMIT:
        return None
    if not attended(event, env):
        return None
    return reason(event, words, rows)


def main() -> int:
    try:
        event = json.load(sys.stdin)
        if isinstance(event, dict):
            why = verdict(event, dict(os.environ))
            if why:
                json.dump({"decision": "block", "reason": why}, sys.stdout)
    except (OSError, ValueError, TypeError) as error:
        print(f"long_reply_guard: {str(error).splitlines()[0]}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
