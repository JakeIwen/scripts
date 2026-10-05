"""Shared scalar values for the video identity catalog."""

from __future__ import annotations

import hashlib
import json
import math
import os
import uuid
from typing import Any



class CatalogError(RuntimeError):
    """Base class for catalog failures."""


class CatalogConflict(CatalogError):
    """Raised when two durable identities contradict one another."""


class CatalogNotFound(CatalogError):
    """Raised when a requested catalog object does not exist."""


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _json_or_none(value: Any) -> str | None:
    return None if value is None else _json(value)


def _digest(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _finite_nonnegative(value: float | int | None, name: str) -> float | None:
    if value is None:
        return None
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise ValueError(f"{name} must be a finite non-negative number")
    return number


def _required_text(value: Any, name: str) -> str:
    text = str(value).strip()
    if not text:
        raise ValueError(f"{name} must not be empty")
    return text


def _optional_locator(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip().casefold()
    return text or None


def _path_key(path: str | os.PathLike[str]) -> str:
    value = os.path.expanduser(os.fspath(path))
    if not value:
        raise ValueError("path must not be empty")
    return os.path.normpath(os.path.abspath(value))


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"
