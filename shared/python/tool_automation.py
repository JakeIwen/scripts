#!/usr/bin/env python3
"""Declarations and the status record for automations the Mac Panel shows.

An automation is a background tool the panel can show and act on without
tool-specific panel code. Each declares, in shared/automations/<tool>.json, the
command that prints its status and the actions the panel may run. A status
command prints one JSON object built by `status_record`; the panel checks it
again with `validate_status`, because the record crosses a process boundary.

Runs on /usr/bin/python3 3.9 as well as the tools' own interpreters (stdlib only).
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Tuple

# Mirrors Level in macbook/mac_panel/static/status_cell.mjs, which renders these.
STATUS_LEVELS = ("ok", "idle", "unknown", "warn", "error")
# Declarations name checkout and home paths this way so they survive a move.
ARGV_PLACEHOLDERS = ("{repo}", "{home}")
# Actions run inside the panel's HTTP request, so they must finish promptly.
MAX_TIMEOUT_SECONDS = 120
MAX_FIELDS = 20
MAX_SUMMARY_CHARS = 200
MAX_LABEL_CHARS = 60
MAX_VALUE_CHARS = 500

_TOOL_NAME = re.compile(r"[a-z0-9][a-z0-9-]*\Z")
_ACTION_NAME = re.compile(r"[a-z][a-z0-9-]*\Z")
_SERVICE_LABEL = re.compile(r"com\.jacobr\.[A-Za-z0-9_.-]+\Z")
_DECLARATION_FIELDS = frozenset(("tool", "title", "description", "service", "settings", "status", "actions"))
_REQUIRED_DECLARATION_FIELDS = frozenset(("tool", "title", "description", "status", "actions"))
_COMMAND_FIELDS = frozenset(("argv", "timeout"))
_ACTION_FIELDS = frozenset(("name", "label", "description", "argv", "timeout", "confirm"))
_REQUIRED_ACTION_FIELDS = frozenset(("name", "label", "description", "argv", "timeout"))
_STATUS_FIELDS = frozenset(("level", "summary", "fields"))
_FIELD_FIELDS = frozenset(("label", "value", "time", "level"))


class AutomationError(ValueError):
    """A declaration or status record is invalid."""


@dataclass(frozen=True)
class Command:
    argv: Tuple[str, ...]
    timeout: int


@dataclass(frozen=True)
class Action:
    name: str
    label: str
    description: str
    command: Command
    confirm: bool

    def as_dict(self) -> Dict[str, Any]:
        """The page's view: the argv stays on the server."""
        return {"name": self.name, "label": self.label, "description": self.description,
                "confirm": self.confirm}


@dataclass(frozen=True)
class Declaration:
    tool: str
    title: str
    description: str
    service: Optional[str]
    settings: Optional[str]
    status: Command
    actions: Tuple[Action, ...]

    def action(self, name: str) -> Optional[Action]:
        return next((action for action in self.actions if action.name == name), None)

    def as_dict(self) -> Dict[str, Any]:
        """The page's view: the argv stays on the server."""
        return {"tool": self.tool, "title": self.title, "description": self.description,
                "service": self.service, "settings": self.settings,
                "actions": [action.as_dict() for action in self.actions]}


def _declarations_dir() -> Path:
    configured = os.environ.get("JACOBR_AUTOMATIONS_DIR")
    if configured:
        return Path(configured).expanduser()
    return Path(__file__).resolve().parent.parent / "automations"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def declaration_tools() -> List[str]:
    directory = _declarations_dir()
    if not directory.is_dir():
        return []
    return sorted(path.stem for path in directory.glob("*.json") if path.is_file())


def _problem(where: str, message: str) -> AutomationError:
    return AutomationError("{}: {}".format(where, message))


def _text(where: str, data: Dict[str, Any], key: str, limit: int) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise _problem(where, "{} must be a non-empty string of at most {} characters".format(key, limit))
    return value


def _expand(argument: str) -> str:
    return argument.replace("{repo}", str(_repo_root())).replace("{home}", str(Path.home()))


def _command(where: str, data: Dict[str, Any]) -> Command:
    argv = data.get("argv")
    if not isinstance(argv, list) or not argv or not all(isinstance(item, str) and item for item in argv):
        raise _problem(where, "argv must be a non-empty list of non-empty strings")
    expanded = tuple(_expand(item) for item in argv)
    if not os.path.isabs(expanded[0]):
        raise _problem(where, "argv must start with an absolute executable path")
    timeout = data.get("timeout")
    if type(timeout) is not int or not 1 <= timeout <= MAX_TIMEOUT_SECONDS:
        raise _problem(where, "timeout must be an integer from 1 to {}".format(MAX_TIMEOUT_SECONDS))
    return Command(expanded, timeout)


def _action(where: str, data: Any) -> Action:
    if not isinstance(data, dict):
        raise _problem(where, "expected an object")
    if not _REQUIRED_ACTION_FIELDS <= data.keys() or data.keys() - _ACTION_FIELDS:
        raise _problem(where, "fields must be {} plus optional confirm".format(", ".join(sorted(_REQUIRED_ACTION_FIELDS))))
    name = data["name"]
    if not isinstance(name, str) or not _ACTION_NAME.fullmatch(name):
        raise _problem(where, "name must be lower-case letters, digits and hyphens")
    confirm = data.get("confirm", False)
    if not isinstance(confirm, bool):
        raise _problem(where, "confirm must be a boolean")
    return Action(name, _text(where, data, "label", MAX_LABEL_CHARS),
                  _text(where, data, "description", MAX_VALUE_CHARS),
                  _command(where, data), confirm)


def _optional_name(where: str, data: Dict[str, Any], key: str, pattern: "re.Pattern[str]") -> Optional[str]:
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise _problem(where, "{} is not a valid name".format(key))
    return value


def load_declaration(tool: str) -> Declaration:
    if not isinstance(tool, str) or not _TOOL_NAME.fullmatch(tool):
        raise AutomationError("invalid automation name {!r}".format(tool))
    path = _declarations_dir() / (tool + ".json")
    where = str(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as error:
        raise _problem(where, str(error)) from error
    if not isinstance(data, dict):
        raise _problem(where, "expected a JSON object")
    if not _REQUIRED_DECLARATION_FIELDS <= data.keys() or data.keys() - _DECLARATION_FIELDS:
        raise _problem(where, "fields must be {} plus optional service and settings".format(
            ", ".join(sorted(_REQUIRED_DECLARATION_FIELDS))))
    if data["tool"] != tool:
        raise _problem(where, "tool must match the file name")
    status = data["status"]
    if not isinstance(status, dict) or status.keys() != _COMMAND_FIELDS:
        raise _problem(where, "status must be an object with argv and timeout")
    actions = data["actions"]
    if not isinstance(actions, list):
        raise _problem(where, "actions must be a list")
    parsed = tuple(_action("{} actions[{}]".format(where, index), item) for index, item in enumerate(actions))
    if len({action.name for action in parsed}) != len(parsed):
        raise _problem(where, "action names must be unique")
    return Declaration(
        tool=tool,
        title=_text(where, data, "title", MAX_LABEL_CHARS),
        description=_text(where, data, "description", MAX_VALUE_CHARS),
        service=_optional_name(where, data, "service", _SERVICE_LABEL),
        settings=_optional_name(where, data, "settings", _TOOL_NAME),
        status=_command(where + " status", status),
        actions=parsed,
    )


def _status_field(where: str, data: Any) -> Dict[str, Any]:
    if not isinstance(data, dict) or "label" not in data or data.keys() - _FIELD_FIELDS:
        raise _problem(where, "a field has label plus optional value, time and level")
    label = _text(where, data, "label", MAX_LABEL_CHARS)
    value = data.get("value")
    if value is not None and (not isinstance(value, str) or len(value) > MAX_VALUE_CHARS):
        raise _problem(where, "value must be a string of at most {} characters".format(MAX_VALUE_CHARS))
    time = data.get("time")
    if time is not None and (type(time) not in (int, float) or not math.isfinite(time) or time < 0):
        raise _problem(where, "time must be epoch seconds")
    if value is None and time is None:
        raise _problem(where, "a field needs a value, a time or both")
    level = data.get("level")
    if level is not None and level not in STATUS_LEVELS:
        raise _problem(where, "level must be one of {}".format(", ".join(STATUS_LEVELS)))
    return {"label": label, "value": value, "time": time, "level": level}


def validate_status(value: Any) -> Dict[str, Any]:
    """Return the canonical record, or raise AutomationError naming the first problem."""
    where = "status"
    if not isinstance(value, dict) or value.keys() != _STATUS_FIELDS:
        raise _problem(where, "expected an object with level, summary and fields")
    if value["level"] not in STATUS_LEVELS:
        raise _problem(where, "level must be one of {}".format(", ".join(STATUS_LEVELS)))
    summary = _text(where, value, "summary", MAX_SUMMARY_CHARS)
    fields = value["fields"]
    if not isinstance(fields, list) or len(fields) > MAX_FIELDS:
        raise _problem(where, "fields must be a list of at most {}".format(MAX_FIELDS))
    return {"level": value["level"], "summary": summary,
            "fields": [_status_field("{} fields[{}]".format(where, index), item)
                       for index, item in enumerate(fields)]}


def status_field(label: str, value: Optional[str] = None, time: Optional[float] = None,
                 level: Optional[str] = None) -> Dict[str, Any]:
    return {"label": label, "value": value, "time": time, "level": level}


def status_record(level: str, summary: str, fields: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Build the record a status command prints, failing in the tool rather than the panel."""
    return validate_status({"level": level, "summary": summary, "fields": list(fields)})
