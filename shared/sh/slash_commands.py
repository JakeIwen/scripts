"""Discover user slash-command metadata without executing commands or mod hooks."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re


PLUGIN_DIRS = "CLAUDE_CODE_PLUGIN_DIRS"
METADATA_FIELDS = {"name", "description", "argumentHint"}
REGISTER_TOKENS = ["$", ".", "command", ".", "register", "(", "{"]
JS_TOKENS = re.compile(
    r"(?P<comment>//[^\n]*|/\*[\s\S]*?\*/)"
    r'''|(?P<string>"(?:\\[\s\S]|[^"\\\r\n])*"|'(?:\\[\s\S]|[^'\\\r\n])*'|`(?:\\[\s\S]|[^`\\])*`)'''
    r"|[A-Za-z_$][\w$]*|[^\s]"
)
JS_ESCAPES = re.compile(r"\\(\r?\n|u\{[0-9a-fA-F]+\}|u[0-9a-fA-F]{4}|x[0-9a-fA-F]{2}|.)")
ESCAPE_CHARACTERS = {"n": "\n", "r": "\r", "t": "\t", "b": "\b", "f": "\f", "v": "\v", "0": "\0"}
BRACKETS = {"{": "}", "[": "]", "(": ")"}


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, ValueError):
        return ""


def _json(path: Path) -> dict:
    try:
        value = json.loads(_read(path))
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def _description(text: str) -> str:
    lines = text.splitlines()
    if not lines or lines[0].lstrip("\ufeff") != "---":
        return ""
    try:
        end = lines.index("---", 1)
    except ValueError:
        return ""
    for index, line in enumerate(lines[1:end], 1):
        if not line.startswith("description:"):
            continue
        value = line.partition(":")[2].strip()
        if value in {"|", "|-", "|+", ">", ">-", ">+"}:
            body = []
            for continuation in lines[index + 1:end]:
                if continuation and not continuation[0].isspace():
                    break
                body.append(continuation.strip())
            return " ".join(" ".join(body).split())
        if value.startswith('"'):
            try:
                value, _ = json.JSONDecoder().raw_decode(value)
            except ValueError:
                return ""
        elif value.startswith("'"):
            match = re.fullmatch(r"'((?:[^']|'')*)'\s*(?:#.*)?", value)
            if not match:
                return ""
            value = match.group(1).replace("''", "'")
        else:
            value = re.split(r"\s+#", value, maxsplit=1)[0]
        return " ".join(value.split())
    return ""


def _markdown_commands(directory: Path, prefix: str) -> list[tuple[str, str]]:
    try:
        paths = sorted(directory.glob("*.md"))
    except OSError:
        return []
    found = []
    for path in paths:
        summary = _description(_read(path))
        if summary:
            found.append((prefix + path.stem, summary))
    return found


def _plugin_directories(home: Path) -> list[Path]:
    configured = os.environ.get(PLUGIN_DIRS)
    if configured is None:
        environment = _json(home / ".claude/settings.json").get("env", {})
        configured = environment.get(PLUGIN_DIRS, "") if isinstance(environment, dict) else ""
    if not isinstance(configured, str):
        return []
    directories = []
    for part in configured.split(os.pathsep):
        if part:
            try:
                directories.append(Path(part).expanduser())
            except RuntimeError:
                continue  # A stale ~user entry must not hide the other mods.
    return directories


def _string_literal(token: str) -> str:
    if len(token) < 2 or token[0] not in "\"'`" or token[-1] != token[0]:
        raise ValueError("not a string literal")
    if token[0] == "`" and "${" in token:
        raise ValueError("template interpolation is not static metadata")

    def unescape(match: re.Match) -> str:
        escaped = match.group(1)
        if escaped.startswith("u{"):
            return chr(int(escaped[2:-1], 16))
        if escaped.startswith(("u", "x")):
            return chr(int(escaped[1:], 16))
        if escaped in {"\n", "\r\n"}:
            return ""
        return ESCAPE_CHARACTERS.get(escaped, escaped)

    return JS_ESCAPES.sub(unescape, token[1:-1])


def _object_fields(tokens: list[str], start: int) -> dict[str, str]:
    """Read only top-level literal metadata; nested handler objects aren't metadata."""
    fields = {}
    stack = []
    member = []
    for token in tokens[start:]:
        if not stack and token in {",", "}"}:
            if member:
                key = member[0]
                if key[:1] in {"'", '"'}:
                    key = _string_literal(key)
                if key == ".":
                    raise ValueError("spread metadata is not static")
                if key in METADATA_FIELDS:
                    if len(member) != 3 or member[1] != ":":
                        raise ValueError("metadata must be a string literal")
                    fields[key] = _string_literal(member[2])
            if token == "}":
                return fields
            member = []
            continue
        if token in BRACKETS:
            stack.append(BRACKETS[token])
        elif token in BRACKETS.values():
            if not stack or stack.pop() != token:
                raise ValueError("unbalanced registration")
        member.append(token)
    raise ValueError("incomplete registration")


def _registrations(source: str) -> list[tuple[str, str]]:
    tokens = [match.group() for match in JS_TOKENS.finditer(source) if match.lastgroup != "comment"]
    found = []
    count = len(REGISTER_TOKENS)
    for index in range(len(tokens) - count + 1):
        if tokens[index:index + count] != REGISTER_TOKENS:
            continue
        try:
            fields = _object_fields(tokens, index + count)
        except (ValueError, OverflowError):
            continue
        name = fields.get("name", "").strip()
        summary = " ".join(fields.get("description", "").split())
        hint = " ".join(fields.get("argumentHint", "").split())
        if name and not any(character.isspace() for character in name) and summary:
            label = "/" + name + (" " + hint if hint else "")
            found.append((label, summary))
    return found


def _mod_commands(home: Path) -> list[tuple[str, str]]:
    found = []
    for directory in _plugin_directories(home):
        hooks = directory / "hooks"
        modules = _json(hooks / "hooks.json").get("modules", [])
        if not isinstance(modules, list):
            continue
        for module in modules:
            if isinstance(module, str):
                found.extend(_registrations(_read(hooks / module)))
    return found


def listing(instruction_file: Path) -> str:
    """Return the optional block for the instruction file's command provider."""
    home = Path.home()
    if home / ".codex" in instruction_file.absolute().parents:
        found = _markdown_commands(home / ".codex/prompts", "/prompts:")
    else:
        found = _markdown_commands(home / ".claude/commands", "/") + _mod_commands(home)
    if not found:
        return ""
    found = sorted(set(found), key=lambda command: command[0].lower())
    width = max(len(name) for name, _ in found)
    rows = [f"  {name.ljust(width)}  {summary}" for name, summary in found]
    return "\n".join(["Slash commands", *rows])
