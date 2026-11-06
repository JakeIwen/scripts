"""What needs attention in the logs: the one definition behind `status` and the panel's Logs section."""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

import tool_automation

from .inventory import Snapshot
from .records import MB, LogReason

# The agent runs hourly; three hours without a run means it is not running.
STALE_RUN_SECONDS = 3 * 3600
# A log this many times over its limit means rotation is failing for it.
OVERSIZE_FACTOR = 2
_OK, _IDLE, _UNKNOWN, WARN, ERROR = tool_automation.STATUS_LEVELS


def megabytes(size: int) -> str:
    return "{:.1f} MB".format(size / MB)


def _problem(level: str, text: str, path: Optional[str] = None) -> Dict[str, Any]:
    return {"level": level, "text": text, **({"path": path} if path else {})}


def find_problems(snapshot: Snapshot, last_run: Dict[str, Any], now: float) -> List[Dict[str, Any]]:
    """Each {level, text, path?}; `path` names the log the problem is about."""
    found = []  # type: List[Dict[str, Any]]
    for entry in snapshot.entries:
        name = os.path.basename(entry.path)
        if entry.reason in (LogReason.UNRECORDED_ARCHIVES,):
            found.append(_problem(WARN, "{}: {}".format(name, entry.note), entry.path))
        if entry.policy.enabled and entry.bytes > OVERSIZE_FACTOR * entry.policy.max_mb * MB:
            found.append(_problem(ERROR, "{} is {} (limit {} MB): rotation is failing".format(
                name, megabytes(entry.bytes), entry.policy.max_mb), entry.path))
    totals = snapshot.totals()
    limit = snapshot.defaults["warn_total_mb"]
    if totals["bytes"] + totals["archived_bytes"] > limit * MB:
        found.append(_problem(WARN, "logs and archives total {} (warn above {} MB)".format(
            megabytes(totals["bytes"] + totals["archived_bytes"]), limit)))
    if last_run.get("errors"):
        found.append(_problem(WARN, "{} rotation error(s) in the last run".format(last_run["errors"])))
    if isinstance(last_run.get("ts"), (int, float)) and now - last_run["ts"] > STALE_RUN_SECONDS:
        found.append(_problem(WARN, "the rotator has not run for over {} hours".format(STALE_RUN_SECONDS // 3600)))
    return found
