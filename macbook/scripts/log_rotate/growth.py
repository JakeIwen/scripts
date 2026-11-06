"""Per-log growth rate (MB/day) from the sizes each rotation run observes."""
from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from .records import DAY_SECONDS, MB

# Runs closer together than this accumulate into the next window instead of
# producing a noisy rate from a few seconds of writing.
MIN_WINDOW_SECONDS = 300
# A rate this old describes a rotator that stopped running, not the log.
STALE_AFTER_SECONDS = 7 * DAY_SECONDS
SMOOTHING = 0.5


def advance(previous: Dict[str, Dict[str, Any]], observed: Dict[str, Tuple[int, int]],
            now: float) -> Dict[str, Dict[str, Any]]:
    """Next ledger from `observed` {path: (bytes before rotation, bytes after)}.

    A size below the last recorded one means the file was truncated since; what it
    holds now is then the lower bound of what was written.
    """
    ledger = {}  # type: Dict[str, Dict[str, Any]]
    for path, (before, after) in observed.items():
        record = previous.get(path)
        if not isinstance(record, dict):
            ledger[path] = {"bytes": after, "ts": now, "pending": 0, "mb_per_day": None}
            continue
        written = before - record["bytes"] if before >= record["bytes"] else before
        pending = record["pending"] + written
        rate, stamp = record["mb_per_day"], record["ts"]
        window = now - stamp
        if window >= MIN_WINDOW_SECONDS:
            fresh = pending / MB / (window / DAY_SECONDS)
            rate = fresh if rate is None else rate * (1 - SMOOTHING) + fresh * SMOOTHING
            pending, stamp = 0, now
        ledger[path] = {"bytes": after, "ts": stamp, "pending": pending, "mb_per_day": rate}
    return ledger


def rate_for(record: Any, now: float) -> Optional[float]:
    if not isinstance(record, dict) or record.get("mb_per_day") is None:
        return None
    if now - record.get("ts", 0) > STALE_AFTER_SECONDS:
        return None
    return round(float(record["mb_per_day"]), 3)
