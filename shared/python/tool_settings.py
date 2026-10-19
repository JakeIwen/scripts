#!/usr/bin/env python3
"""Shared declarations, validation, and private storage for tool settings."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys
import tempfile
from typing import Any, Dict, List, Optional, Tuple


SETTING_TYPES = frozenset(("integer", "number", "boolean", "choice"))
_TOOL_NAME = re.compile(r"[a-z0-9][a-z0-9-]*\Z")
_SETTING_NAME = re.compile(r"[a-z][a-z0-9_]*\Z")
_DECLARATION_FIELDS = frozenset(("tool", "title", "description", "settings"))
_SETTING_FIELDS = frozenset(
    ("name", "type", "default", "description", "minimum", "maximum", "choices", "unit", "step")
)
_REQUIRED_SETTING_FIELDS = frozenset(("name", "type", "default", "description"))


class SettingsError(ValueError):
    """A declaration or saved value is invalid."""


class SettingsConflict(SettingsError):
    """A settings write was based on a stale revision."""


@dataclass(frozen=True)
class Setting:
    name: str
    type: str
    default: Any
    description: str
    minimum: Optional[float] = None
    maximum: Optional[float] = None
    choices: Optional[Tuple[str, ...]] = None
    unit: Optional[str] = None
    step: Optional[float] = None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "type": self.type,
            "default": self.default,
            "description": self.description,
            "minimum": self.minimum,
            "maximum": self.maximum,
            "choices": list(self.choices) if self.choices is not None else None,
            "unit": self.unit,
            "step": self.step,
        }


@dataclass(frozen=True)
class Declaration:
    tool: str
    title: str
    description: str
    settings: Tuple[Setting, ...]

    def defaults(self) -> Dict[str, Any]:
        return {setting.name: setting.default for setting in self.settings}

    def as_dict(self) -> Dict[str, Any]:
        return {
            "tool": self.tool,
            "title": self.title,
            "description": self.description,
            "settings": [setting.as_dict() for setting in self.settings],
        }


@dataclass(frozen=True)
class SettingsState:
    tool: str
    values: Dict[str, Any]
    overrides: Dict[str, Any]
    revision: str
    path: str
    error: Optional[str]

    def as_dict(self) -> Dict[str, Any]:
        return {
            "tool": self.tool,
            "values": dict(self.values),
            "overrides": dict(self.overrides),
            "revision": self.revision,
            "path": self.path,
            "error": self.error,
        }


@dataclass(frozen=True)
class ReloadResult:
    values: Dict[str, Any]
    changed: bool
    error: Optional[str]


def _declarations_dir() -> Path:
    configured = os.environ.get("JACOBR_SETTINGS_DIR")
    if configured:
        return Path(configured).expanduser()
    return Path(__file__).resolve().parent.parent / "settings"


def _values_dir() -> Path:
    configured = os.environ.get("JACOBR_CONFIG_DIR")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".config" / "jacobr"


def _valid_tool_name(tool: str) -> bool:
    return isinstance(tool, str) and _TOOL_NAME.fullmatch(tool) is not None


def _declaration_path(tool: str) -> Path:
    return _declarations_dir() / (tool + ".json")


def declaration_tools() -> List[str]:
    directory = _declarations_dir()
    if not directory.is_dir():
        return []
    return sorted(path.stem for path in directory.glob("*.json") if path.is_file())


def _problem(path: Path, message: str) -> SettingsError:
    return SettingsError("{}: {}".format(path, message))


def _json_object(path: Path) -> Dict[str, Any]:
    try:
        raw = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as error:
        raise _problem(path, "invalid UTF-8: {}".format(error)) from error
    except OSError as error:
        raise _problem(path, str(error)) from error
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as error:
        raise _problem(path, "invalid JSON: {}".format(error)) from error
    if not isinstance(data, dict):
        raise _problem(path, "expected a JSON object")
    return data


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _normalize_value(setting: Setting, value: Any) -> Tuple[Any, Optional[str]]:
    if setting.type == "boolean":
        if not isinstance(value, bool):
            return value, "{} must be a boolean (got {!r})".format(setting.name, value)
        return value, None
    if setting.type == "choice":
        if not isinstance(value, str):
            return value, "{} must be a choice string (got {!r})".format(setting.name, value)
        if setting.choices is not None and value not in setting.choices:
            return value, "{} must be one of {} (got {!r})".format(
                setting.name, ", ".join(setting.choices), value)
        return value, None
    if not _is_number(value):
        return value, "{} must be {} (got {!r})".format(
            setting.name, "an integer" if setting.type == "integer" else "a number", value)
    if setting.type == "integer":
        if isinstance(value, float) and not value.is_integer():
            return value, "{} must be an integer (got {!r})".format(setting.name, value)
        value = int(value)
    if setting.minimum is not None and value < setting.minimum:
        return value, _range_problem(setting, value)
    if setting.maximum is not None and value > setting.maximum:
        return value, _range_problem(setting, value)
    return value, None


def _range_problem(setting: Setting, value: Any) -> str:
    if setting.minimum is not None and setting.maximum is not None:
        return "{} must be between {:g} and {:g} (got {!r})".format(
            setting.name, setting.minimum, setting.maximum, value)
    if setting.minimum is not None:
        return "{} must be at least {:g} (got {!r})".format(setting.name, setting.minimum, value)
    return "{} must be at most {:g} (got {!r})".format(setting.name, setting.maximum, value)


def _setting_from_data(path: Path, index: int, data: Any) -> Setting:
    prefix = "settings[{}]".format(index)
    if not isinstance(data, dict):
        raise _problem(path, "{} must be an object".format(prefix))
    missing = sorted(_REQUIRED_SETTING_FIELDS - set(data))
    unknown = sorted(set(data) - _SETTING_FIELDS)
    if missing:
        raise _problem(path, "{} is missing {}".format(prefix, ", ".join(missing)))
    if unknown:
        raise _problem(path, "{} has unknown fields {}".format(prefix, ", ".join(unknown)))

    name = data["name"]
    setting_type = data["type"]
    description = data["description"]
    if not isinstance(name, str) or _SETTING_NAME.fullmatch(name) is None:
        raise _problem(path, "{}.name must match [a-z][a-z0-9_]*".format(prefix))
    if not isinstance(setting_type, str) or setting_type not in SETTING_TYPES:
        raise _problem(path, "{}.type must be one of {}".format(prefix, ", ".join(sorted(SETTING_TYPES))))
    if not isinstance(description, str):
        raise _problem(path, "{}.description must be a string".format(prefix))

    minimum = data.get("minimum")
    maximum = data.get("maximum")
    step = data.get("step")
    for field_name, candidate in (("minimum", minimum), ("maximum", maximum), ("step", step)):
        if candidate is not None and not _is_number(candidate):
            raise _problem(path, "{}.{} must be a finite number".format(prefix, field_name))
    if minimum is not None and maximum is not None and minimum > maximum:
        raise _problem(path, "{}.minimum must not exceed maximum".format(prefix))
    if step is not None and step <= 0:
        raise _problem(path, "{}.step must be greater than zero".format(prefix))
    if setting_type not in ("integer", "number") and any(
            candidate is not None for candidate in (minimum, maximum, step)):
        raise _problem(path, "{}.minimum, maximum, and step are only valid for numeric settings".format(prefix))

    unit = data.get("unit")
    if unit is not None and not isinstance(unit, str):
        raise _problem(path, "{}.unit must be a string".format(prefix))
    raw_choices = data.get("choices")
    choices = None
    if setting_type == "choice":
        if (not isinstance(raw_choices, list) or not raw_choices
                or any(not isinstance(choice, str) for choice in raw_choices)):
            raise _problem(path, "{}.choices must be a non-empty list of strings".format(prefix))
        if len(set(raw_choices)) != len(raw_choices):
            raise _problem(path, "{}.choices must be unique".format(prefix))
        choices = tuple(raw_choices)
    elif raw_choices is not None:
        raise _problem(path, "{}.choices is only valid for choice settings".format(prefix))

    setting = Setting(name, setting_type, data["default"], description,
                      minimum, maximum, choices, unit, step)
    normalized, error = _normalize_value(setting, setting.default)
    if error is not None:
        raise _problem(path, "{}.default is invalid: {}".format(prefix, error))
    if normalized != setting.default or type(normalized) is not type(setting.default):
        setting = Setting(name, setting_type, normalized, description,
                          minimum, maximum, choices, unit, step)
    return setting


def load_declaration(tool: str) -> Declaration:
    path = _declaration_path(tool)
    if not _valid_tool_name(tool):
        raise _problem(path, "tool name must match [a-z0-9][a-z0-9-]*")
    data = _json_object(path)
    missing = sorted(_DECLARATION_FIELDS - set(data))
    unknown = sorted(set(data) - _DECLARATION_FIELDS)
    if missing:
        raise _problem(path, "missing fields {}".format(", ".join(missing)))
    if unknown:
        raise _problem(path, "unknown fields {}".format(", ".join(unknown)))
    if data["tool"] != tool:
        raise _problem(path, "tool must equal file stem {!r}".format(tool))
    if not isinstance(data["title"], str) or not isinstance(data["description"], str):
        raise _problem(path, "title and description must be strings")
    if not isinstance(data["settings"], list):
        raise _problem(path, "settings must be a list")
    settings = tuple(_setting_from_data(path, index, item)
                     for index, item in enumerate(data["settings"]))
    names = [setting.name for setting in settings]
    duplicates = sorted(name for name in set(names) if names.count(name) > 1)
    if duplicates:
        raise _problem(path, "duplicate setting names {}".format(", ".join(duplicates)))
    return Declaration(tool, data["title"], data["description"], settings)


def validate(declaration: Declaration, values: Any) -> Dict[str, Any]:
    if not isinstance(values, dict):
        raise SettingsError("settings values must be a JSON object")
    by_name = {setting.name: setting for setting in declaration.settings}
    allowed = [setting.name for setting in declaration.settings]
    problems = []
    unknown = [str(name) for name in values if name not in by_name]
    if unknown:
        problems.append("unknown settings {} (allowed settings: {})".format(
            ", ".join(sorted(unknown)), ", ".join(allowed)))
    normalized = {}
    for setting in declaration.settings:
        if setting.name not in values:
            continue
        value, error = _normalize_value(setting, values[setting.name])
        if error is not None:
            problems.append(error)
        else:
            normalized[setting.name] = value
    if problems:
        raise SettingsError("; ".join(problems))
    return normalized


def values_path(tool: str) -> Path:
    if not _valid_tool_name(tool):
        raise SettingsError("tool name must match [a-z0-9][a-z0-9-]*")
    return _values_dir() / (tool + ".json")


def _revision(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _read_state(declaration: Declaration, path: Path) -> SettingsState:
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return SettingsState(declaration.tool, declaration.defaults(), {}, "missing", str(path), None)
    except OSError as error:
        message = "{}: {}".format(path, error)
        return SettingsState(declaration.tool, declaration.defaults(), {}, "missing", str(path), message)
    revision = _revision(raw)
    try:
        data = json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        message = "{}: invalid JSON: {}".format(path, error)
        return SettingsState(declaration.tool, declaration.defaults(), {}, revision, str(path), message)
    try:
        overrides = validate(declaration, data)
    except SettingsError as error:
        message = "{}: {}".format(path, error)
        return SettingsState(declaration.tool, declaration.defaults(), {}, revision, str(path), message)
    effective = declaration.defaults()
    effective.update(overrides)
    return SettingsState(declaration.tool, effective, overrides, revision, str(path), None)


def read_state(tool: str) -> SettingsState:
    declaration = load_declaration(tool)
    return _read_state(declaration, values_path(tool))


def load(tool: str) -> Dict[str, Any]:
    state = read_state(tool)
    if state.error is not None:
        raise SettingsError(state.error)
    return dict(state.values)


def _write_overrides(path: Path, overrides: Dict[str, Any]) -> None:
    descriptor, temporary_name = tempfile.mkstemp(prefix="." + path.name + ".", dir=str(path.parent))
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            descriptor = -1
            json.dump(overrides, output, indent=2, allow_nan=False)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary_name, str(path))
        os.chmod(path, 0o600)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass


def save(tool: str, values: Any, expected_revision: Optional[str] = None) -> SettingsState:
    declaration = load_declaration(tool)
    path = values_path(tool)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    lock_path = path.with_suffix(path.suffix + ".lock")
    with lock_path.open("a+b") as lock:
        os.fchmod(lock.fileno(), 0o600)
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        current = _read_state(declaration, path)
        if expected_revision is not None and expected_revision != current.revision:
            raise SettingsConflict(
                "{} changed: expected revision {}, found {}".format(path, expected_revision, current.revision))
        if current.error is not None:
            raise SettingsError(current.error)
        update = validate(declaration, values)
        merged = dict(current.overrides)
        merged.update(update)
        normalized = validate(declaration, merged)
        defaults = declaration.defaults()
        overrides = {setting.name: normalized[setting.name]
                     for setting in declaration.settings
                     if setting.name in normalized and normalized[setting.name] != defaults[setting.name]}
        _write_overrides(path, overrides)
        return _read_state(declaration, path)


def _file_signature(path: Path) -> Optional[Tuple[int, int, int]]:
    try:
        stat = path.stat()
    except FileNotFoundError:
        return None
    return stat.st_mtime_ns, stat.st_size, stat.st_ino


class Reloader:
    """Reload one tool's values when its private values file changes."""

    def __init__(self, tool: str):
        self.tool = tool
        self._path = values_path(tool)
        self._values = load(tool)
        self._signature = _file_signature(self._path)
        self._error = None  # type: Optional[str]

    def poll(self) -> ReloadResult:
        signature = _file_signature(self._path)
        if signature == self._signature:
            return ReloadResult(dict(self._values), False, self._error)
        state = read_state(self.tool)
        self._signature = signature
        if state.error is not None:
            self._error = state.error
            return ReloadResult(dict(self._values), False, state.error)
        changed = state.values != self._values
        self._values = dict(state.values)
        self._error = None
        return ReloadResult(dict(self._values), changed, None)


def _parse_cli_value(setting: Setting, text: str) -> Any:
    if setting.type == "choice":
        return text
    if setting.type == "boolean":
        lowered = text.lower()
        if lowered == "true":
            return True
        if lowered == "false":
            return False
        raise SettingsError("{} must be true or false".format(setting.name))
    try:
        return json.loads(text)
    except json.JSONDecodeError as error:
        raise SettingsError("{} is not a valid {}".format(setting.name, setting.type)) from error


def _print_json(value: Any) -> None:
    print(json.dumps(value, indent=2, sort_keys=False))


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("list")
    show_parser = subparsers.add_parser("show")
    show_parser.add_argument("tool")
    set_parser = subparsers.add_parser("set")
    set_parser.add_argument("tool")
    set_parser.add_argument("assignments", nargs="+")
    args = parser.parse_args(argv)
    try:
        if args.command == "list":
            for tool in declaration_tools():
                print(tool)
            return 0
        if args.command == "show":
            _print_json(read_state(args.tool).as_dict())
            return 0
        declaration = load_declaration(args.tool)
        by_name = {setting.name: setting for setting in declaration.settings}
        updates = {}
        for assignment in args.assignments:
            if "=" not in assignment:
                raise SettingsError("assignment must be key=value: {!r}".format(assignment))
            key, text = assignment.split("=", 1)
            if key not in by_name:
                raise SettingsError("unknown setting {} (allowed settings: {})".format(
                    key, ", ".join(by_name)))
            updates[key] = _parse_cli_value(by_name[key], text)
        _print_json(save(args.tool, updates).as_dict())
        return 0
    except SettingsError as error:
        print("error: {}".format(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
