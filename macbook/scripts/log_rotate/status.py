"""The `tool_automation` status record the Mac Panel's Automations section shows."""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

import tool_automation

from .inventory import Snapshot
from .records import MB, STATE_DUE, STATE_REFUSED

# The agent runs hourly; three hours without a run means it is not running.
STALE_RUN_SECONDS = 3 * 3600
# A log this many times over its limit means rotation is failing for it.
OVERSIZE_FACTOR = 2
OK, IDLE, UNKNOWN, WARN, ERROR = tool_automation.STATUS_LEVELS


def megabytes(size: int) -> str:
    return "{:.1f} MB".format(size / MB)


def _warnings(snapshot: Snapshot, last_run: Dict[str, Any], now: float) -> List[str]:
    found = []  # type: List[str]
    for entry in snapshot.entries:
        if entry.state == STATE_DUE and entry.bytes > OVERSIZE_FACTOR * entry.policy.max_mb * MB:
            found.append("{} is {} (limit {} MB): rotation is failing".format(
                os.path.basename(entry.path), megabytes(entry.bytes), entry.policy.max_mb))
    totals = snapshot.totals()
    limit = snapshot.defaults["warn_total_mb"]
    if totals["bytes"] + totals["archived_bytes"] > limit * MB:
        found.append("logs and archives total {} (warn above {} MB)".format(
            megabytes(totals["bytes"] + totals["archived_bytes"]), limit))
    if last_run.get("errors"):
        found.append("{} rotation error(s) in the last run".format(last_run["errors"]))
    if isinstance(last_run.get("ts"), (int, float)) and now - last_run["ts"] > STALE_RUN_SECONDS:
        found.append("the rotator has not run for over {} hours".format(STALE_RUN_SECONDS // 3600))
    return found


def build_status(snapshot: Snapshot, last_run: Dict[str, Any], now: float) -> Dict[str, Any]:
    totals = snapshot.totals()
    warnings = _warnings(snapshot, last_run, now)
    summary = "{} logs · {} live · {} archived".format(
        totals["files"], megabytes(totals["bytes"]), megabytes(totals["archived_bytes"]))
    stamp = last_run.get("ts") if isinstance(last_run.get("ts"), (int, float)) else None
    if warnings:
        level = WARN
        summary += " · " + warnings[0]
    elif stamp is None:
        level, summary = UNKNOWN, summary + " · rotator has not run yet"
    else:
        level = OK
    fields = [tool_automation.status_field(
        "Last run", "never" if stamp is None else "{} rotated, {} errors".format(
            last_run.get("rotated", 0), last_run.get("errors", 0)), stamp,
        WARN if last_run.get("errors") else None)]
    stamps = [e.last_rotated for e in snapshot.entries if e.last_rotated]
    if stamps:
        fields.append(tool_automation.status_field("Last rotation", None, max(stamps)))
    fields.append(tool_automation.status_field("Live", megabytes(totals["bytes"])))
    fields.append(tool_automation.status_field("Archived", megabytes(totals["archived_bytes"])))
    live = snapshot.live_entries()
    if live:
        largest = max(live, key=lambda entry: entry.bytes)
        fields.append(tool_automation.status_field(
            "Largest", "{} ({})".format(os.path.basename(largest.path), megabytes(largest.bytes))))
    due = sum(1 for entry in snapshot.entries if entry.state == STATE_DUE)
    fields.append(tool_automation.status_field("Due now", str(due), None, WARN if due else None))
    refused = sum(1 for entry in snapshot.entries if entry.state == STATE_REFUSED)
    if refused:
        fields.append(tool_automation.status_field("Refused", str(refused)))
    for warning in warnings[1:]:
        fields.append(tool_automation.status_field("Warning", warning, None, WARN))
    return tool_automation.status_record(level, summary, fields)


def error_status(message: str) -> Dict[str, Any]:
    return tool_automation.status_record(ERROR, message[:190] or "log-rotate failed", [])
