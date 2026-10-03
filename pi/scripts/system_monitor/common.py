"""Shared monitor constants, time and JSON helpers, boot identity, and fingerprints."""

import datetime as dt
import hashlib
import json
import os
import re
import time

if __package__:
    pass
else:
    pass


DEFAULT_DATABASE = os.environ.get(
    "VANPI_MONITOR_DATABASE", "/var/lib/vanpi-monitor/events.sqlite3"
)
DEFAULT_SAMPLE_INTERVAL = 5.0
DEFAULT_ROLLUP_INTERVAL = 60.0
DEFAULT_RETENTION_DAYS = 90
DEFAULT_SAMPLE_RETENTION_HOURS = 48
DEFAULT_CRASH_SAMPLE_LIMIT = 360
DEFAULT_CRASH_REPORT_DIRECTORY = "/var/lib/vanpi-monitor/crash-reports"
REPORT_VERSION = 1

THROTTLE_FLAGS = (
    (0, "under_voltage", "Undervoltage"),
    (1, "frequency_capped", "Arm frequency capped"),
    (2, "throttled", "Throttling"),
    (3, "soft_temperature_limit", "Soft temperature limit"),
)

SEVERITY_RANK = {"info": 0, "warning": 1, "critical": 2}


def utc_timestamp():
    return time.time()


def iso_time(timestamp):
    return dt.datetime.fromtimestamp(timestamp, dt.timezone.utc).isoformat().replace(
        "+00:00", "Z"
    )


def json_dumps(value):
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def normalize_boot_id(value):
    """Normalize real systemd UUID boot IDs while preserving test/legacy labels."""
    text = str(value or "").strip().lower()
    if re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", text):
        return text.replace("-", "")
    if re.fullmatch(r"[0-9a-f]{32}", text):
        return text
    return text or None


def event_fingerprint(*parts):
    joined = "\x1f".join(str(part) for part in parts)
    return hashlib.sha256(joined.encode("utf-8", "replace")).hexdigest()
