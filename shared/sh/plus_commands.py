#!/usr/bin/python3
"""Print Jacob's + commands and user-added slash commands.

The + commands (`+cp`, `+id`, `+cmd`, ...) are defined once, in the
"## + commands" section of each machine's global instruction file, one
bullet per command whose first sentence is its summary. This prints each
command with that sentence, so the list can't drift from the definitions.
The list is alphabetical by command name (Jacob's preference), whatever
order the instruction file defines them in. A second, alphabetical "Slash
commands" block lists Claude command files and active mods' literal command
metadata, or Codex prompts when --file is under ~/.codex. Skills are not listed;
unreadable slash-command sources are omitted without losing the + commands.

`--hook` reads a Claude Code or Codex UserPromptSubmit event on stdin. When
the prompt is exactly `+cmd`, it blocks the prompt and returns the list as
the reason shown to the user, so no model turn runs. Selected standalone
commands also receive their full procedures as context, never execution or
a changed prompt. `--procedure +id` prints the same context for an explicit
fallback. Procedures live only in ~/config/shared/plus-commands/procedures.json,
not this public repository. Standard library only; Python 3.9+ on Mac/Pi.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
import sys
from pathlib import Path

import slash_commands

DEFAULT_FILE = Path.home() / ".claude/CLAUDE.md"
HEADING = "## + commands"
TRIGGER = "+cmd"
# The Mac Codex file indents its bullets by one space.
BULLET = re.compile(r"^ ?- `(\+[^`]+)`: (.*)$")
SENTENCE_END = re.compile(r"\.(\s|$)")
PROCEDURE_SOURCE = Path("config/shared/plus-commands/procedures.json")
COMMON_PROCEDURES = frozenset(("+id", "+ho", "+ss", "+lex", "+sa", "+rcp", "+art", "+prompt"))
VARIANT_COMMANDS = {
    "mac-claude": COMMON_PROCEDURES | {"+clp", "+x"},
    "mac-codex": COMMON_PROCEDURES | {"+clp"},
    "pi-claude": COMMON_PROCEDURES,
    "pi-codex": COMMON_PROCEDURES,
}
MAX_SOURCE_BYTES = 256 * 1024
MAX_PROCEDURE_BYTES = 16 * 1024
MAX_CONTEXT_BYTES = 64 * 1024
MAX_EVENT_BYTES = 1024 * 1024
UNSAFE_TEXT = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
# Match notify_on_finish.py's whitespace-only token boundaries, not punctuation.
COMMAND_TOKEN = re.compile(r"(?<!\S)\+[a-z]+(?=\s|$)")
FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
QUOTE_END = {"'": "'", '"': '"', "‘": "’", "“": "”"}


def procedure_variant(path: Path) -> str:
    if sys.platform not in ("darwin", "linux"):
        raise ValueError("procedures support only Darwin and Linux")
    host = "mac" if sys.platform == "darwin" else "pi"
    # Same lexical --file selection as slash_commands.listing; no prompt input.
    engine = "codex" if Path.home() / ".codex" in path.absolute().parents else "claude"
    return f"{host}-{engine}"


def unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def invalid_constant(value: str) -> None:
    raise ValueError("non-finite JSON number")


def load_procedures() -> dict[str, dict[str, str]]:
    source = Path.home() / PROCEDURE_SOURCE
    # Nonblocking open plus fstat also bounds a misconfigured FIFO/device source.
    with os.fdopen(os.open(source, os.O_RDONLY | os.O_NONBLOCK), "rb") as handle:
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            raise ValueError("procedure source must be a regular file")
        raw = handle.read(MAX_SOURCE_BYTES + 1)
    if len(raw) > MAX_SOURCE_BYTES:
        raise ValueError("procedure source exceeds 256 KiB")
    data = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_object,
                      parse_constant=invalid_constant)
    if not isinstance(data, dict) or set(data) != {"version", "variants"}:
        raise ValueError("procedure source needs exactly version and variants")
    if type(data["version"]) is not int or data["version"] != 1:
        raise ValueError("unsupported procedure source version (expected integer 1)")
    variants = data["variants"]
    if not isinstance(variants, dict) or set(variants) != set(VARIANT_COMMANDS):
        raise ValueError("procedure source needs exactly the four host/engine variants")
    for variant, expected in VARIANT_COMMANDS.items():
        entries = variants[variant]
        if not isinstance(entries, dict) or set(entries) != expected:
            raise ValueError(f"procedure command keys do not match {variant}")
        for text in entries.values():
            if (not isinstance(text, str) or not text.strip() or UNSAFE_TEXT.search(text)
                    or len(text.encode("utf-8")) > MAX_PROCEDURE_BYTES):
                raise ValueError(f"{variant} procedures must be nonempty safe text of at most 16 KiB")
    return variants


def procedure_context(path: Path, names: list[str]) -> str:
    """One selector/renderer for both hook delivery and explicit CLI fallback."""
    variant = procedure_variant(path)
    entries = load_procedures()[variant]
    blocks = []
    for name in dict.fromkeys(names):
        if name not in entries:
            raise ValueError(f"no procedure for {name!r} in {variant}")
        blocks.append(f"{name} procedure ({variant}):\n{entries[name]}")
    text = "\n\n".join(blocks)
    if len(text.encode("utf-8")) > MAX_CONTEXT_BYTES:
        raise ValueError("selected procedure context exceeds 64 KiB")
    return text


def unquoted_lines(prompt: str) -> list[str]:
    """Mask literals, fences and Markdown quotations without creating new tokens."""
    lines, fence, literal = [], "", ""
    for line in prompt.splitlines():
        marker = FENCE.match(line) if not literal else None
        if fence:
            if (marker and marker[1][0] == fence[0] and len(marker[1]) >= len(fence)
                    and not line[marker.end():].strip()):
                fence = ""
            continue
        if marker:
            fence = marker[1]
            continue
        if not literal and (line.lstrip().startswith(">") or line.startswith(("    ", "\t"))):
            continue
        masked, pos = list(line), 0
        while pos < len(line):
            char = line[pos]
            if literal:
                if line.startswith(literal, pos):
                    end = pos + len(literal)
                    literal = ""
                else:
                    end = min(pos + (2 if char == "\\" else 1), len(line))
                masked[pos:end] = "\0" * (end - pos)
                pos = end
                continue
            if char == "`":
                end = pos + 1
                while end < len(line) and line[end] == "`":
                    end += 1
                literal = line[pos:end]
            elif char in QUOTE_END and (pos == 0 or not line[pos - 1].isalnum()):
                literal, end = QUOTE_END[char], pos + 1
            else:
                pos += 1
                continue
            masked[pos:end] = "\0" * (end - pos)
            pos = end
        lines.append("".join(masked))
    return lines


def requested_procedures(prompt: str, path: Path) -> list[str]:
    # glossary.py consumes the entire +terms remainder, even another + token.
    if re.fullmatch(r"\+terms(?:\s.*)?", prompt.strip(), re.DOTALL):
        return []
    allowed = VARIANT_COMMANDS[procedure_variant(path)]
    selected = []
    for line in unquoted_lines(prompt):
        # Focus text can mention commands without invoking them. Select only
        # adjacent leading commands and an appended command-only suffix.
        matches = list(COMMAND_TOKEN.finditer(line))
        leading_end, boundary = 0, 0
        for match in matches:
            if line[boundary:match.start()].strip():
                break
            leading_end += 1
            boundary = match.end()
        trailing_start, boundary = len(matches), len(line)
        for match in reversed(matches[leading_end:]):
            if line[match.end():boundary].strip():
                break
            trailing_start -= 1
            boundary = match.start()
        selected.extend(match[0] for match in matches[:leading_end] + matches[trailing_start:]
                        if match[0] in allowed)
    return list(dict.fromkeys(selected))


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
    found.sort(key=lambda pair: pair[0].lower())
    width = max(len(name) for name, _ in found)
    rows = [f"  {name.ljust(width)}  {summary}" for name, summary in found]
    text = "\n".join(["+ commands", *rows, f'Full definitions: "+ commands" in {_home_relative(path)}'])
    slash_block = slash_commands.listing(path)
    return text + "\n\n" + slash_block if slash_block else text


def _home_relative(path: Path) -> str:
    try:
        return "~/" + str(path.relative_to(Path.home()))
    except ValueError:
        return str(path)


def hook(path: Path) -> int:
    try:
        raw = sys.stdin.buffer.read(MAX_EVENT_BYTES + 1)
        if len(raw) > MAX_EVENT_BYTES:
            return 0
        event = json.loads(raw.decode("utf-8"))
        if (not isinstance(event, dict)
                or event.get("hook_event_name", "UserPromptSubmit") != "UserPromptSubmit"
                or not isinstance(event.get("prompt"), str)):
            return 0
        prompt = event["prompt"]
        # 2951a716: only a bare +cmd may consume the user's model turn.
        if prompt.strip() == TRIGGER:
            text = listing(path)
            if text is not None:
                json.dump({"decision": "block", "reason": text}, sys.stdout)
            return 0
        names = requested_procedures(prompt, path)
        if names:
            text = procedure_context(path, names)
            json.dump({"hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit", "additionalContext": text,
            }}, sys.stdout, ensure_ascii=False)
    except (OSError, ValueError, RecursionError):
        # Data installs before the hook/globals. Missing or invalid data must
        # leave prompts intact; the explicit --procedure fallback fails closed.
        pass
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--file", type=Path, default=DEFAULT_FILE,
                    help="instruction file holding the '+ commands' section (default %(default)s)")
    modes = ap.add_mutually_exclusive_group()
    modes.add_argument("--hook", action="store_true",
                       help="answer bare +cmd or supply selected procedure context without execution")
    modes.add_argument("--procedure", action="append", metavar="+COMMAND",
                       help="print a full procedure for this host/engine (repeatable)")
    args = ap.parse_args(argv)
    path = args.file.expanduser()
    if args.hook:
        return hook(path)
    if args.procedure:
        try:
            text = procedure_context(path, args.procedure)
        except (OSError, ValueError, RecursionError) as error:
            print(f"plus_commands: cannot load procedure from {Path.home() / PROCEDURE_SOURCE}: "
                  f"{str(error).splitlines()[0]}", file=sys.stderr)
            return 1
    else:
        text = listing(path)
        if text is None:
            print(f"no '{HEADING}' section in {args.file}", file=sys.stderr)
            return 1
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
