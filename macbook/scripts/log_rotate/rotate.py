"""A rotation run: pick the logs that need it, rotate them, record what happened."""
from __future__ import annotations

import os
import time
from typing import Any, Dict, List, Optional, Tuple

from . import growth, state
from .copytruncate import rotate_file
from .inventory import Snapshot, is_due
from .records import EXIT_ERROR, STATE_DISABLED, STATE_MISSING, STATE_REFUSED, LogEntry, LogRotateError


def _own_log_last(entries: List[LogEntry], home: str) -> List[LogEntry]:
    """The rotator's log goes last, so the lines about this run land before it rotates."""
    own = str(state.state_dir(home) / state.LOG_FILE)
    return [e for e in entries if e.path != own] + [e for e in entries if e.path == own]


def _skip_reason(entry: LogEntry, size: int, only: bool, force: bool) -> Optional[str]:
    """Why `entry` is not rotated: a reason to report, "" to skip silently, None to rotate."""
    if entry.state == STATE_REFUSED:
        return "refused: {}".format(entry.note)
    if entry.state == STATE_DISABLED and not (only and force):
        return "disabled"
    if entry.state == STATE_MISSING:
        return "missing" if only else ""
    if size == 0:
        return "empty" if only or force else ""
    if not force and not is_due(size, entry.policy.max_mb):
        return "below threshold" if only else ""
    return None


def _current_size(path: str, fallback: int) -> int:
    try:
        return os.lstat(path).st_size
    except OSError:
        return fallback


def select_entry(snapshot: Snapshot, path: str) -> LogEntry:
    entry = snapshot.entry(path)
    if entry is None:
        raise LogRotateError(EXIT_ERROR, "not a known log: {}".format(path), field="path")
    return entry


def run_rotation(snapshot: Snapshot, home: str, only: Optional[str] = None, force: bool = False,
                 now: Optional[float] = None) -> Dict[str, Any]:
    """Rotate what is due (or `only` one log); the caller holds the run lock."""
    now = time.time() if now is None else now
    scope = [select_entry(snapshot, only)] if only else snapshot.entries
    rotated, skipped, errors = [], [], []  # type: List[Any], List[Any], List[Any]
    observed = {}  # type: Dict[str, Tuple[int, int]]
    stamps = state.read_json(home, state.ROTATED_FILE)
    for entry in _own_log_last(scope, home):
        size = _current_size(entry.path, entry.bytes)
        reason = _skip_reason(entry, size, bool(only), force)
        if reason is not None:
            if reason:
                skipped.append({"path": entry.path, "reason": reason})
            if entry.state not in (STATE_REFUSED, STATE_MISSING):
                observed[entry.path] = (size, size)
            continue
        warnings = []  # type: List[str]
        try:
            before, archive = rotate_file(entry.path, home, entry.policy.keep, entry.policy.compress, warnings)
        except Exception as error:
            errors.append({"path": entry.path, "error": str(error)})
            state.append_log(home, "error {}: {}".format(entry.path, error))
            observed[entry.path] = (size, size)
            continue
        finally:
            for warning in warnings:
                errors.append({"path": entry.path, "error": warning})
                state.append_log(home, "error {}: {}".format(entry.path, warning))
        rotated.append({"path": entry.path, "bytes_before": before, "archive": archive})
        stamps[entry.path] = round(now, 3)
        observed[entry.path] = (before, 0)
        state.append_log(home, "rotated {} {} bytes -> {}".format(entry.path, before, archive or "discarded"))
    _record(home, observed, stamps, only, now, len(rotated), len(errors))
    return {"rotated": rotated, "skipped": skipped, "errors": errors}


def _record(home: str, observed: Dict[str, Tuple[int, int]], stamps: Dict[str, Any],
            only: Optional[str], now: float, rotated: int, errors: int) -> None:
    previous = state.read_json(home, state.SIZES_FILE).get("paths")
    previous = previous if isinstance(previous, dict) else {}
    ledger = growth.advance(previous, observed, now)
    if only:
        # A single-log run says nothing about the others; keep their rows as they were.
        ledger = {**{k: v for k, v in previous.items() if k not in ledger}, **ledger}
    state.write_json(home, state.SIZES_FILE, {"paths": ledger})
    state.write_json(home, state.ROTATED_FILE, stamps)
    if only is None or errors:
        state.write_json(home, state.RUN_FILE, {"ts": round(now, 3), "rotated": rotated, "errors": errors})
        state.append_log(home, "run complete: {} rotated, {} errors".format(rotated, errors))
