"""Private rotator state: the run lock, growth and rotation ledgers, and its own log."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import errno
import fcntl
import json
import os
from pathlib import Path
from typing import Any, Dict, Iterator

import tool_settings

from .records import EXIT_BUSY, LogRotateError

SIZES_FILE = "last_sizes.json"
ROTATED_FILE = "last_rotated.json"
RUN_FILE = "last_run.json"
LOG_FILE = "rotate.log"
LOCK_FILE = "lock"


def state_dir(home: str) -> Path:
    return Path(home) / ".local" / "state" / "log-rotate"


def ensure_state_dir(home: str) -> Path:
    directory = state_dir(home)
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    return directory


@contextmanager
def run_lock(home: str) -> Iterator[None]:
    """One rotation run at a time; a second one fails fast so launchd never queues them."""
    descriptor = os.open(str(ensure_state_dir(home) / LOCK_FILE), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            if error.errno in (errno.EACCES, errno.EAGAIN):
                raise LogRotateError(EXIT_BUSY, "Another log-rotate run is in progress") from None
            raise
        yield
    finally:
        os.close(descriptor)


def read_json(home: str, name: str) -> Dict[str, Any]:
    try:
        value = json.loads((state_dir(home) / name).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def write_json(home: str, name: str, value: Dict[str, Any]) -> None:
    tool_settings.write_private_json(ensure_state_dir(home) / name, value)


def append_log(home: str, line: str) -> None:
    """One timestamped line; callers pass paths and sizes, never log contents."""
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    path = ensure_state_dir(home) / LOG_FILE
    descriptor = os.open(str(path), os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        os.write(descriptor, "{} {}\n".format(stamp, line).encode("utf-8", "replace"))
    finally:
        os.close(descriptor)
