#!/usr/bin/env python3
"""Compatibility re-exports for the video identity catalog."""

from __future__ import annotations

import contextlib
import hashlib
import json
import math
import os
import sqlite3
import tempfile
import threading
import time
import uuid
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any, Callable

__all__ = (
    "CatalogConflict",
    "CatalogError",
    "CatalogNotFound",
    "DEFAULT_BUSY_TIMEOUT_MS",
    "MediaAssetCatalog",
    "SCHEMA_VERSION",
    "ensure_pre_v2_backup",
)

from .catalog import (
    _backup_path as _backup_path,
    _quick_check as _quick_check,
    ensure_pre_v2_backup as ensure_pre_v2_backup,
    DEFAULT_BUSY_TIMEOUT_MS as DEFAULT_BUSY_TIMEOUT_MS,
    MediaAssetCatalog as MediaAssetCatalog,
)
from .catalog_values import (
    CatalogError as CatalogError,
    CatalogConflict as CatalogConflict,
    CatalogNotFound as CatalogNotFound,
    _json as _json,
    _json_or_none as _json_or_none,
    _digest as _digest,
    _finite_nonnegative as _finite_nonnegative,
    _required_text as _required_text,
    _optional_locator as _optional_locator,
    _path_key as _path_key,
    _new_id as _new_id,
)
from .schema import _MIGRATIONS as _MIGRATIONS, SCHEMA_VERSION as SCHEMA_VERSION
from .v1_bridge import _V1_UNTRUSTED_COVERAGE as _V1_UNTRUSTED_COVERAGE
