"""Per-disk I/O, temperature and SMART history for the Disks & Torrents sheet.

I/O comes from the system monitor's ``disk-io`` command, one record per disk a
minute. SMART comes from smart_watch.sh's world-readable reading log, one line
per mounted HDD every 10 minutes (pi/docs/monitoring/SMART.md); parked disks
are never queried, so their temperature stops while they are unmounted.
"""

__all__ = ["DiskStatsReader", "MAX_HISTORY_HOURS"]

from .van_dashboard_common import SMART_HISTORY_DIR, os, time
from .van_dashboard_disks import DiskManager
from .van_dashboard_integrations import SystemMonitorCommandError

MAX_HISTORY_HOURS = 30 * 24
# smart_watch.sh line: epoch passed reallocated pending uncorrectable temp hours cycles
SMART_COUNTERS = (
    "reallocated_sectors",
    "pending_sectors",
    "uncorrectable_sectors",
    "temperature_c",
    "power_on_hours",
    "power_cycles",
)
IO_BUCKET_FIELDS = (
    "start",
    "minutes",
    "read_bytes",
    "write_bytes",
    "peak_read_bytes_per_second",
    "peak_write_bytes_per_second",
    "busy_percent",
    "peak_busy_percent",
)
IO_LATEST_FIELDS = ("at", "read_bytes_per_second", "write_bytes_per_second", "busy_percent")


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0


def _checked_row(row, fields, what):
    if not isinstance(row, dict) or not all(_number(row.get(field)) for field in fields):
        raise SystemMonitorCommandError(f"system monitor returned invalid {what}")
    return {field: row[field] for field in fields}


def _checked_rows(rows, fields, what):
    if not isinstance(rows, list):
        raise SystemMonitorCommandError(f"system monitor returned invalid {what}")
    return [_checked_row(row, fields, what) for row in rows]


def parse_smart_line(line):
    """One smart_watch.sh reading, or None for a torn or foreign line."""
    tokens = line.split()
    if len(tokens) != 2 + len(SMART_COUNTERS) or not tokens[0].isdigit():
        return None
    passed = {"true": True, "false": False, "unknown": None}.get(tokens[1], "invalid")
    if passed == "invalid":
        return None
    counters = {}
    for name, token in zip(SMART_COUNTERS, tokens[2:]):
        # Only a temperature can fall below zero (a van parked in winter).
        digits = token[1:] if name == "temperature_c" and token.startswith("-") else token
        if token == "unknown":
            counters[name] = None
        elif digits.isdigit():
            counters[name] = int(token)
        else:
            return None
    return {"at": int(tokens[0]), "passed": passed, **counters}


class DiskStatsReader:
    def __init__(self, monitor, smart_history_dir=SMART_HISTORY_DIR, wall_clock=time.time):
        self.monitor = monitor
        self.smart_history_dir = smart_history_dir
        self.wall_clock = wall_clock

    def _smart_readings(self, label):
        if not DiskManager.LABEL_RE.fullmatch(label):
            raise ValueError("invalid disk label")
        try:
            with open(os.path.join(self.smart_history_dir, label), encoding="ascii", errors="replace") as handle:
                lines = handle.read().splitlines()
        except FileNotFoundError:
            return []
        readings = [reading for reading in map(parse_smart_line, lines) if reading]
        return sorted(readings, key=lambda reading: reading["at"])

    def latest(self, labels):
        """The last I/O minute and SMART reading of each label; a missing source is null."""
        payload = self.monitor.disk_io_latest()
        disks = payload.get("disks")
        if not isinstance(disks, dict):
            raise SystemMonitorCommandError("system monitor returned invalid disk I/O")
        result = {}
        for label in labels:
            io = disks.get(label)
            readings = self._smart_readings(label)
            result[label] = {
                "io": None if io is None else _checked_row(io, IO_LATEST_FIELDS, "disk I/O"),
                "smart": readings[-1] if readings else None,
            }
        return {
            "checked_at": int(self.wall_clock()),
            "io_sample_seconds": payload.get("sample_seconds"),
            "disks": result,
        }

    def history(self, label, hours):
        """Bucketed I/O and temperature over the last ``hours`` for one disk's graphs."""
        if not 1 <= hours <= MAX_HISTORY_HOURS:
            raise ValueError(f"hours must be from 1 to {MAX_HISTORY_HOURS}")
        payload = self.monitor.disk_io_history(label, hours)
        size = payload.get("bucket_seconds")
        since, until = payload.get("since"), payload.get("until")
        if not all(_number(value) for value in (size, since, until)) or not size:
            raise SystemMonitorCommandError("system monitor returned invalid disk history")
        every_reading = self._smart_readings(label)
        readings = [reading for reading in every_reading if since < reading["at"] <= until]
        # Temperature shares the I/O buckets, so a month stays a few hundred points.
        temperatures = {}
        for reading in readings:
            if reading["temperature_c"] is not None:
                start = (reading["at"] - 1) // size * size
                temperatures.setdefault(start, []).append(reading["temperature_c"])
        return {
            "label": label,
            "since": since,
            "until": until,
            "bucket_seconds": size,
            "io_sample_seconds": payload.get("sample_seconds"),
            "io": _checked_rows(payload.get("buckets"), IO_BUCKET_FIELDS, "disk history"),
            "temperature": [
                {
                    "start": start,
                    "readings": len(values),
                    "temperature_c": round(sum(values) / len(values), 1),
                    "max_temperature_c": max(values),
                }
                for start, values in sorted(temperatures.items())
            ],
            # The latest reading even when the disk sat parked through the whole range.
            "smart": every_reading[-1] if every_reading else None,
        }
