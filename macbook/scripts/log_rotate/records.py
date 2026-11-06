"""Wire records, vocabulary and exit codes shared by every log_rotate module."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

TOOL = "log-rotate"
# Per-file policies, extras and ignores live apart from the declared defaults: the
# tool_settings file for TOOL only accepts declared setting names.
FILES_CONFIG_TOOL = "log-rotate-files"
LAUNCHD_LABEL = "com.jacobr.log-rotate"

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2
EXIT_CONFLICT = 3
EXIT_BUSY = 75

# One megabyte is 1048576 bytes everywhere (limits, sizes, totals); the Mac Panel's
# logs_format.mjs keeps the matching constant.
MB = 1024 * 1024
DAY_SECONDS = 86400

# Inventory sources; `launchagent:<Label>` and `crontab:<job id>` carry a suffix.
SOURCE_LAUNCHAGENT = "launchagent"
SOURCE_CRONTAB = "crontab"
SOURCE_SCAN = "scan"
SOURCE_EXTRA = "extra"

# Entry states, in the order that decides them (a refused file is never anything else).
STATE_OK = "ok"
STATE_DUE = "due"
STATE_DISABLED = "disabled"
STATE_MISSING = "missing"
STATE_REFUSED = "refused"

# Other apps' logs live here: found by the scan they start out unrotated, and a
# per-file policy (or adding the path as an extra) opts a file in.
THIRD_PARTY_LOG_DIR = "Library/Logs"
THIRD_PARTY_NOTE = "third-party app log; enable per file"

# Editable per-file policy fields (the first three are declared; enabled is per file only).
DECLARED_POLICY_FIELDS = ("max_mb", "keep", "compress")
POLICY_FIELDS = DECLARED_POLICY_FIELDS + ("enabled",)
DEFAULT_ENABLED = True


class LogRotateError(Exception):
    """A failure with the exit code and optional wire details the CLI reports."""

    def __init__(self, code: int, message: str, **details: Any) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details


@dataclass(frozen=True)
class Policy:
    max_mb: int
    keep: int
    compress: bool
    enabled: bool
    override: bool

    def as_dict(self) -> Dict[str, Any]:
        return {"max_mb": self.max_mb, "keep": self.keep, "compress": self.compress,
                "enabled": self.enabled, "override": self.override}


@dataclass
class LogEntry:
    path: str
    source: str
    bytes: int
    archived_bytes: int
    archives: int
    mtime: Optional[float]
    growth_mb_per_day: Optional[float]
    policy: Policy
    last_rotated: Optional[float]
    state: str
    note: Optional[str] = None

    def as_dict(self) -> Dict[str, Any]:
        value = {"path": self.path, "source": self.source, "bytes": self.bytes,
                 "archived_bytes": self.archived_bytes, "archives": self.archives,
                 "mtime": self.mtime, "growth_mb_per_day": self.growth_mb_per_day,
                 "policy": self.policy.as_dict(), "last_rotated": self.last_rotated,
                 "state": self.state}
        if self.note is not None:
            value["note"] = self.note
        return value
