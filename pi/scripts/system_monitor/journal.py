"""Kernel-journal classification, reading, redaction, and ordering."""

import json
import re
import subprocess

from .common import event_fingerprint, iso_time


def classify_kernel_message(message):
    """Return a normalized event description for a noteworthy kernel line."""
    text = str(message or "").strip()
    lower = text.lower()
    if not text:
        return None

    if "undervoltage detected" in lower or "under-voltage detected" in lower:
        return ("power", "undervoltage_started", "critical", "Pi input undervoltage detected")
    if "voltage normalised" in lower or "voltage normalized" in lower:
        return ("power", "undervoltage_cleared", "info", "Pi input voltage recovered")
    if re.search(r"over.?current", lower):
        return ("power", "usb_overcurrent", "critical", "USB over-current reported")
    if "soft temperature limit" in lower:
        severity = "info" if any(word in lower for word in ("clear", "normal")) else "warning"
        return ("thermal", "soft_temperature_limit", severity, "Soft temperature limit changed")
    if re.search(r"thermal.*thrott|thrott.*thermal|frequency.*capp", lower):
        return ("thermal", "thermal_throttle", "warning", "Thermal or frequency throttling reported")

    if "new usb device found" in lower:
        return ("usb", "usb_connected", "info", "USB device connected")
    if "usb disconnect" in lower:
        return ("usb", "usb_disconnected", "warning", "USB device disconnected")
    if re.search(r"reset (?:low|full|high|super)speed usb device", lower):
        return ("usb", "usb_reset", "warning", "USB device reset")
    usb_failure = (
        "device descriptor read",
        "device not responding",
        "device not accepting address",
        "unable to enumerate usb device",
        "attempt power cycle",
        "cannot enable. maybe the usb cable is bad",
        "invalid context state",
        "device offline error",
        "uas_eh_abort_handler",
    )
    if ("usb" in lower or "xhci" in lower or "uas" in lower) and any(
        marker in lower for marker in usb_failure
    ):
        return ("usb", "usb_error", "warning", "USB communication or enumeration failure")
    if "host controller not responding" in lower or "xhci host controller not responding" in lower:
        return ("usb", "usb_controller_error", "critical", "USB host controller stopped responding")

    storage_markers = (
        "i/o error",
        "buffer i/o error",
        "blk_update_request",
        "rejecting i/o",
        "device offline",
        "filesystem error",
        "ext4-fs error",
        "xfs error",
        "exfat-fs error",
        "remounting filesystem read-only",
    )
    if any(marker in lower for marker in storage_markers):
        return ("storage", "storage_io_error", "critical", "Storage or filesystem I/O error")
    if any(marker in lower for marker in ("out of memory", "oom-killer", "killed process")):
        return ("memory", "out_of_memory", "critical", "Out-of-memory action")
    if "watchdog" in lower and any(marker in lower for marker in ("lockup", "bite", "reset")):
        return ("kernel", "watchdog", "critical", "Kernel watchdog fault")
    if "blocked for more than" in lower or "hung task" in lower:
        return ("kernel", "hung_task", "critical", "Kernel task hung")
    if "kernel panic" in lower:
        return ("kernel", "kernel_panic", "critical", "Kernel panic")
    if re.search(r"warning: cpu:.* at ", lower):
        return ("kernel", "kernel_warning", "warning", "Kernel warning trace")
    if "segfault at" in lower:
        return ("kernel", "segfault", "warning", "Process segmentation fault")
    return None


def parse_journal_record(record):
    try:
        message = record.get("MESSAGE", "")
        classification = classify_kernel_message(message)
        if classification is None:
            return None
        timestamp = int(record["__REALTIME_TIMESTAMP"]) / 1_000_000
    except (KeyError, TypeError, ValueError):
        return None
    category, kind, severity, summary = classification
    boot_id = record.get("_BOOT_ID")
    monotonic = record.get("__MONOTONIC_TIMESTAMP", "")
    return {
        "timestamp": timestamp,
        "boot_id": boot_id,
        "category": category,
        "kind": kind,
        "severity": severity,
        "source": "kernel",
        "summary": summary,
        "message": str(message)[:4000],
        "fingerprint": event_fingerprint("journal", boot_id, monotonic, kind, message),
    }


def redact_log_message(message):
    """Keep crash evidence useful without returning URLs or token-like values."""
    value = str(message or "").replace("\x00", " ")[:4000]
    value = re.sub(r"https?://\S+", "[redacted URL]", value)
    value = re.sub(
        r"(?i)\b(token|password|passwd|secret|api[_-]?key)\s*[=:]\s*\S+",
        lambda match: f"{match.group(1)}=[redacted]",
        value,
    )
    return value


def generic_journal_record(record):
    try:
        timestamp = int(record["__REALTIME_TIMESTAMP"]) / 1_000_000
    except (KeyError, TypeError, ValueError):
        return None
    message = redact_log_message(record.get("MESSAGE", ""))
    if not message:
        return None
    try:
        priority = int(record.get("PRIORITY", 6))
    except (TypeError, ValueError):
        priority = 6
    source = (
        record.get("SYSLOG_IDENTIFIER")
        or record.get("_SYSTEMD_UNIT")
        or record.get("_COMM")
        or "journal"
    )
    return {
        "timestamp": timestamp,
        "timestamp_iso": iso_time(timestamp),
        "boot_id": record.get("_BOOT_ID"),
        "monotonic": record.get("__MONOTONIC_TIMESTAMP"),
        "priority": priority,
        "source": str(source)[:120],
        "message": message,
        "pid1": str(record.get("_PID", "")) == "1",
        "transport": record.get("_TRANSPORT"),
    }


def read_journal_records(boot=-1, kernel=False, lines=600, timeout=20):
    args = [
        "/usr/bin/journalctl",
        f"--boot={boot}",
        f"--lines={int(lines)}",
        "--output=json",
        "--no-pager",
    ]
    if kernel:
        args.insert(2, "--dmesg")
    try:
        result = subprocess.run(
            args, capture_output=True, text=True, timeout=timeout, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if result.returncode:
        return []
    records = []
    for line in result.stdout.splitlines():
        try:
            raw = json.loads(line)
        except ValueError:
            continue
        record = generic_journal_record(raw)
        if record:
            records.append(record)
    return records


def journal_monotonic_seconds(record):
    try:
        return int(record.get("monotonic")) / 1_000_000
    except (TypeError, ValueError):
        return None


def journal_order_key(record):
    monotonic = journal_monotonic_seconds(record)
    return (
        0 if monotonic is not None else 1,
        monotonic if monotonic is not None else record.get("timestamp", 0),
    )
