#!/usr/bin/python3
"""Print Jacob's + commands from the global agent instructions.

The + commands (`+cp`, `+id`, `+cmd`, ...) are defined once, in the
"## + commands" section of each machine's global instruction file, one
bullet per command whose first sentence is its summary. This prints each
command with that sentence, so the list can't drift from the definitions.

`--hook` reads a Claude Code or Codex UserPromptSubmit event on stdin. When
the prompt is exactly `+cmd`, it blocks the prompt and returns the list as
the reason shown to the user, so no model turn runs; any other prompt
passes through untouched. Standard library only; runs on the MacBook
(/usr/bin/python3 3.9) and the Pi.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

DEFAULT_FILE = Path.home() / ".claude/CLAUDE.md"
HEADING = "## + commands"
TRIGGER = "+cmd"
# The Mac Codex file indents its bullets by one space.
BULLET = re.compile(r"^ ?- `(\+[^`]+)`: (.*)$")
SENTENCE_END = re.compile(r"\.(\s|$)")


def section(text: str) -> list[str]:
    lines = text.splitlines()
    try:
        start = next(i for i, line in enumerate(lines) if line.startswith(HEADING))
    except StopIteration:
        return []
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    return lines[start + 1:end]


def commands(text: str) -> list[tuple[str, str]]:
    """(name, summary) per bullet; the summary is the bullet's first sentence."""
    found = []
    body = None
    for line in section(text):
        match = BULLET.match(line)
        if match:
            found.append([match.group(1), ""])
            body = match.group(2)
        elif body is not None and line.startswith("  ") and line.strip():
            body += " " + line.strip()
        else:
            body = None
        if body is not None:
            end = SENTENCE_END.search(body)
            if end or not line.strip():
                found[-1][1] = body[:end.start() + 1] if end else body
            if end:
                body = None
    return [(name, _plain(summary)) for name, summary in found]


def _plain(summary: str) -> str:
    summary = summary.replace("`", "")
    return summary[:1].upper() + summary[1:]


def listing(path: Path) -> str | None:
    try:
        found = commands(path.read_text())
    except OSError:
        return None
    if not found:
        return None
    width = max(len(name) for name, _ in found)
    rows = [f"  {name.ljust(width)}  {summary}" for name, summary in found]
    return "\n".join(["+ commands", *rows, f'Full definitions: "+ commands" in {_home_relative(path)}'])


def _home_relative(path: Path) -> str:
    try:
        return "~/" + str(path.relative_to(Path.home()))
    except ValueError:
        return str(path)


def hook(path: Path) -> int:
    try:
        event = json.load(sys.stdin)
    except ValueError:
        return 0
    if str(event.get("prompt", "")).strip() != TRIGGER:
        return 0
    text = listing(path)
    if text is None:
        return 0  # let the model answer from the instructions instead
    json.dump({"decision": "block", "reason": text}, sys.stdout)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--file", type=Path, default=DEFAULT_FILE,
                    help="instruction file holding the '+ commands' section (default %(default)s)")
    ap.add_argument("--hook", action="store_true",
                    help="act as a UserPromptSubmit hook: answer a bare +cmd, pass anything else")
    args = ap.parse_args(argv)
    if args.hook:
        return hook(args.file.expanduser())
    text = listing(args.file.expanduser())
    if text is None:
        print(f"no '{HEADING}' section in {args.file}", file=sys.stderr)
        return 1
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
