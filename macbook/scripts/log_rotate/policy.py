"""Declared defaults, the private per-file config, and its revision-checked edits."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

import tool_settings

from . import eligibility
from .records import (DECLARED_POLICY_FIELDS, DEFAULT_ENABLED, EXIT_BUSY, EXIT_CONFLICT, EXIT_ERROR,
                      FILES_CONFIG_TOOL, POLICY_FIELDS, SOURCE_SCAN, THIRD_PARTY_LOG_DIR, TOOL,
                      LogRotateError, Policy)

MISSING_REVISION = "missing"
_TOP_LEVEL = ("policies", "extras", "ignored")


@dataclass
class FilesConfig:
    revision: str
    policies: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    extras: List[str] = field(default_factory=list)
    ignored: List[str] = field(default_factory=list)

    def data(self) -> Dict[str, Any]:
        return {"policies": {path: dict(values) for path, values in self.policies.items()},
                "extras": list(self.extras), "ignored": list(self.ignored)}


def config_path() -> Path:
    # Through tool_settings so JACOBR_CONFIG_DIR isolates it like every tool's values.
    return tool_settings.values_path(FILES_CONFIG_TOOL)


def load_defaults() -> Tuple[Dict[str, Any], Optional[str]]:
    """Effective declared defaults, plus the saved-values problem when they fall back."""
    try:
        state = tool_settings.read_state(TOOL)
    except tool_settings.SettingsError as error:
        raise LogRotateError(EXIT_ERROR, str(error)) from None
    defaults = dict(state.values)
    defaults["enabled"] = DEFAULT_ENABLED
    return defaults, state.error


def validate_fields(fields: Dict[str, Any]) -> Dict[str, Any]:
    """Range-check policy fields against the declaration, naming the bad field."""
    declaration = tool_settings.load_declaration(TOOL)
    checked = {}  # type: Dict[str, Any]
    for name, value in fields.items():
        if name == "enabled":
            if not isinstance(value, bool):
                raise LogRotateError(EXIT_ERROR, "enabled must be on or off", field=name)
            checked[name] = value
        elif name == "keep" and type(value) is int and value == 0:
            # Discarding needs the per-file action, never an unconfirmed global default.
            checked[name] = value
        elif name in DECLARED_POLICY_FIELDS:
            try:
                checked.update(tool_settings.validate(declaration, {name: value}))
            except tool_settings.SettingsError as error:
                raise LogRotateError(EXIT_ERROR, str(error), field=name) from None
        else:
            raise LogRotateError(EXIT_ERROR, "unknown policy field {}".format(name), field=name)
    return checked


def _path_list(raw: Any, name: str, source: Path) -> List[str]:
    if (not isinstance(raw, list) or not all(isinstance(item, str) and os.path.isabs(item) for item in raw)):
        raise LogRotateError(EXIT_ERROR, "{}: {} must be a list of absolute paths".format(source, name))
    return list(raw)


def _parse(raw: bytes, source: Path) -> FilesConfig:
    revision = hashlib.sha256(raw).hexdigest()
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as error:
        raise LogRotateError(EXIT_ERROR, "{}: invalid JSON: {}".format(source, error)) from None
    if not isinstance(data, dict):
        raise LogRotateError(EXIT_ERROR, "{}: expected a JSON object".format(source))
    unknown = sorted(set(data) - set(_TOP_LEVEL) - {"revision"})
    if unknown:
        raise LogRotateError(EXIT_ERROR, "{}: unknown keys {}".format(source, ", ".join(unknown)))
    policies = data.get("policies", {})
    if not isinstance(policies, dict):
        raise LogRotateError(EXIT_ERROR, "{}: policies must be an object".format(source))
    checked = {}  # type: Dict[str, Dict[str, Any]]
    for path, values in policies.items():
        if not os.path.isabs(path) or not isinstance(values, dict) or set(values) - set(POLICY_FIELDS):
            raise LogRotateError(EXIT_ERROR, "{}: bad policy for {!r}".format(source, path))
        try:
            checked[path] = validate_fields(values)
        except LogRotateError as error:
            raise LogRotateError(EXIT_ERROR, "{}: {}: {}".format(source, path, error.message)) from None
    return FilesConfig(revision, checked, _path_list(data.get("extras", []), "extras", source),
                       _path_list(data.get("ignored", []), "ignored", source))


def read_files_config() -> FilesConfig:
    path = config_path()
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return FilesConfig(MISSING_REVISION)
    except OSError as error:
        raise LogRotateError(EXIT_ERROR, "{}: {}".format(path, error)) from None
    return _parse(raw, path)


@contextmanager
def _config_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor = os.open(str(path.with_suffix(path.suffix + ".lock")), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            if error.errno in (errno.EACCES, errno.EAGAIN):
                raise LogRotateError(EXIT_BUSY, "Another log-rotate edit is in progress") from None
            raise
        yield
    finally:
        os.close(descriptor)


def update_files_config(expected_revision: str, mutate: Callable[[Dict[str, Any]], None]) -> FilesConfig:
    """Apply `mutate` to the config data if the file still has `expected_revision`."""
    path = config_path()
    with _config_lock(path):
        current = read_files_config()
        if current.revision != expected_revision:
            raise LogRotateError(
                EXIT_CONFLICT,
                "log-rotate config changed: expected revision {}, found {}".format(
                    expected_revision, current.revision),
                revision=current.revision)
        data = current.data()
        mutate(data)
        tool_settings.write_private_json(path, data)
        return read_files_config()


def effective_policy(defaults: Dict[str, Any], config: FilesConfig, path: str) -> Policy:
    own = config.policies.get(path, {})
    values = {name: own.get(name, defaults[name]) for name in POLICY_FIELDS}
    return Policy(values["max_mb"], values["keep"], values["compress"], values["enabled"], bool(own))


def is_unopted_third_party(source: str, path: str, home: str, config: FilesConfig) -> bool:
    """A scanned log under ~/Library/Logs that no per-file policy or extra has enabled."""
    return (source == SOURCE_SCAN and path.startswith(os.path.join(home, THIRD_PARTY_LOG_DIR) + os.sep)
            and path not in config.extras and "enabled" not in config.policies.get(path, {}))


def set_policy(data: Dict[str, Any], path: str, fields: Dict[str, Any]) -> None:
    data["policies"].setdefault(path, {}).update(fields)


def clear_policy(data: Dict[str, Any], path: str) -> None:
    data["policies"].pop(path, None)


def add_extra(data: Dict[str, Any], path: str) -> None:
    if path not in data["extras"]:
        data["extras"].append(path)
    if path in data["ignored"]:
        data["ignored"].remove(path)


def ignore(data: Dict[str, Any], path: str) -> None:
    if path not in data["ignored"]:
        data["ignored"].append(path)


def unignore(data: Dict[str, Any], path: str) -> None:
    if path in data["ignored"]:
        data["ignored"].remove(path)


def require_rotatable(path: str, home: str) -> None:
    """Policies and extras only make sense for files the rotator may touch."""
    refusal = eligibility.inspect(path, home).refusal
    if refusal is not None:
        raise LogRotateError(EXIT_ERROR, "refused: {} is {}".format(path, refusal), field="path")
