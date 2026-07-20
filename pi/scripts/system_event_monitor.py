#!/usr/bin/env python3
"""Persistent Raspberry Pi power, USB, kernel, and resource monitor.

The daemon combines two sources of evidence:

* firmware and /proc sampling catches current throttling state and resource peaks;
* the kernel journal supplies timestamped power, USB, storage, OOM, and fault events.

Only passive interfaces are used.  The monitor never resets USB, changes a clock,
or changes power state.  Data is stored in SQLite for the van dashboard and the
``report``/``events`` analysis commands in this file.
"""

import argparse
import collections
import datetime as dt
import hashlib
import json
import math
import os
import queue
import re
import signal
import sqlite3
import statistics
import subprocess
import sys
import threading
import time


DEFAULT_DATABASE = os.environ.get(
    "VANPI_MONITOR_DATABASE", "/var/lib/vanpi-monitor/events.sqlite3"
)
DEFAULT_SAMPLE_INTERVAL = 5.0
DEFAULT_ROLLUP_INTERVAL = 60.0
DEFAULT_RETENTION_DAYS = 90
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


def read_text(path, default=None):
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            return handle.read().strip()
    except OSError:
        return default


def read_number(path, divisor=1.0):
    raw = read_text(path)
    try:
        return float(raw) / divisor
    except (TypeError, ValueError):
        return None


def parse_cpu_list(value):
    """Expand Linux CPU-list syntax such as ``0-3,6`` into integer IDs."""
    cpus = []
    for part in re.split(r"[\s,]+", str(value or "")):
        part = part.strip()
        if not part:
            continue
        try:
            if "-" in part:
                start, end = (int(item) for item in part.split("-", 1))
                if end < start:
                    continue
                cpus.extend(range(start, end + 1))
            else:
                cpus.append(int(part))
        except ValueError:
            continue
    return sorted(set(cpus))


def collect_thermal_sensors(sys_root="/sys"):
    """Return every readable Linux thermal zone without inventing per-core data."""
    thermal_root = os.path.join(sys_root, "class", "thermal")
    try:
        zones = sorted(
            (
                name
                for name in os.listdir(thermal_root)
                if re.fullmatch(r"thermal_zone\d+", name)
            ),
            key=lambda name: int(name.removeprefix("thermal_zone")),
        )
    except OSError:
        return []

    online_cpus = parse_cpu_list(
        read_text(os.path.join(sys_root, "devices", "system", "cpu", "online"), "")
    )
    sensors = []
    for zone in zones:
        zone_root = os.path.join(thermal_root, zone)
        temperature = read_number(os.path.join(zone_root, "temp"), divisor=1000)
        if temperature is None or not math.isfinite(temperature):
            continue
        sensor_type = read_text(os.path.join(zone_root, "type"), zone) or zone
        sensor = {
            "zone": zone,
            "type": sensor_type,
            "temperature_c": round(temperature, 2),
        }
        if "cpu" in sensor_type.lower() and online_cpus:
            # Raspberry Pi exposes one cpu-thermal package/SoC sensor shared by
            # all cores. Listing its scope is more accurate than duplicating the
            # same reading and calling those values per-core temperatures.
            sensor["cpu_ids"] = online_cpus
            sensor["shared"] = len(online_cpus) > 1
        sensors.append(sensor)
    return sensors


def collect_cpu_frequency_policies(sys_root="/sys"):
    """Return live cpufreq policy state useful when interpreting throttling."""
    cpu_root = os.path.join(sys_root, "devices", "system", "cpu", "cpufreq")
    try:
        policies = sorted(
            (
                name
                for name in os.listdir(cpu_root)
                if re.fullmatch(r"policy\d+", name)
            ),
            key=lambda name: int(name.removeprefix("policy")),
        )
    except OSError:
        return []

    result = []
    for policy in policies:
        policy_root = os.path.join(cpu_root, policy)

        def mhz(filename):
            value = read_number(os.path.join(policy_root, filename))
            return round(value / 1000, 2) if value is not None else None

        result.append(
            {
                "policy": policy,
                "cpu_ids": parse_cpu_list(
                    read_text(os.path.join(policy_root, "related_cpus"), "")
                ),
                "current_mhz": mhz("scaling_cur_freq"),
                "minimum_mhz": mhz("cpuinfo_min_freq"),
                "maximum_mhz": mhz("cpuinfo_max_freq"),
                "governor": read_text(os.path.join(policy_root, "scaling_governor")),
            }
        )
    return result


def run_text(args, timeout=3):
    try:
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode:
        return None
    return result.stdout.strip()


def parse_throttled(value):
    """Decode Raspberry Pi ``vcgencmd get_throttled`` output or an integer."""
    if isinstance(value, str):
        match = re.search(r"(?:throttled=)?(0x[0-9a-fA-F]+|[0-9]+)", value)
        if not match:
            return None
        try:
            raw = int(match.group(1), 0)
        except ValueError:
            return None
    elif isinstance(value, int):
        raw = value
    else:
        return None

    current = []
    occurred = []
    for bit, key, _label in THROTTLE_FLAGS:
        if raw & (1 << bit):
            current.append(key)
        if raw & (1 << (bit + 16)):
            occurred.append(key)
    return {
        "raw": raw,
        "hex": f"0x{raw:x}",
        "current": current,
        "occurred": occurred,
    }


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


def event_fingerprint(*parts):
    joined = "\x1f".join(str(part) for part in parts)
    return hashlib.sha256(joined.encode("utf-8", "replace")).hexdigest()


class EventStore:
    def __init__(self, path=DEFAULT_DATABASE, clock=utc_timestamp, read_only=False):
        self.path = path
        self.clock = clock
        self.read_only = read_only
        if read_only:
            uri_path = os.path.abspath(path).replace("?", "%3f").replace("#", "%23")
            self.connection = sqlite3.connect(
                f"file:{uri_path}?mode=ro", uri=True, timeout=10
            )
        else:
            parent = os.path.dirname(path) or "."
            os.makedirs(parent, exist_ok=True)
            self.connection = sqlite3.connect(path, timeout=10)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA busy_timeout=5000")
        if not read_only:
            self.connection.execute("PRAGMA journal_mode=WAL")
            self.connection.execute("PRAGMA synchronous=NORMAL")
            self._create_schema()
            try:
                os.chmod(path, 0o640)
            except OSError:
                pass

    def _create_schema(self):
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL NOT NULL,
                boot_id TEXT,
                category TEXT NOT NULL,
                kind TEXT NOT NULL,
                severity TEXT NOT NULL,
                source TEXT NOT NULL,
                summary TEXT NOT NULL,
                message TEXT NOT NULL,
                fingerprint TEXT NOT NULL UNIQUE,
                state_json TEXT
            );
            CREATE INDEX IF NOT EXISTS events_timestamp_idx ON events(timestamp DESC);
            CREATE INDEX IF NOT EXISTS events_kind_timestamp_idx ON events(kind, timestamp DESC);
            CREATE TABLE IF NOT EXISTS resource_rollups (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                period_start REAL NOT NULL,
                period_end REAL NOT NULL,
                boot_id TEXT,
                sample_count INTEGER NOT NULL,
                cpu_peak REAL,
                memory_peak REAL,
                swap_peak REAL,
                load1_peak REAL,
                temperature_peak REAL,
                root_used_peak REAL,
                arm_mhz_min REAL,
                metrics_json TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS rollups_period_idx
                ON resource_rollups(period_end DESC);
            CREATE TABLE IF NOT EXISTS crash_analyses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                previous_boot_id TEXT NOT NULL UNIQUE,
                analyzed_at REAL NOT NULL,
                previous_boot_started_at REAL,
                previous_boot_ended_at REAL,
                level TEXT NOT NULL,
                headline TEXT NOT NULL,
                report_json TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS crash_analyses_time_idx
                ON crash_analyses(analyzed_at DESC);
            PRAGMA user_version=2;
            """
        )
        self.connection.commit()

    def close(self):
        self.connection.close()

    def get_meta(self, key, default=None):
        row = self.connection.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        if row is None:
            return default
        try:
            return json.loads(row["value"])
        except (TypeError, ValueError):
            return default

    def set_meta(self, key, value, commit=True):
        self.connection.execute(
            "INSERT INTO meta(key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, json_dumps(value)),
        )
        if commit:
            self.connection.commit()

    def insert_event(
        self,
        timestamp,
        boot_id,
        category,
        kind,
        severity,
        source,
        summary,
        message,
        fingerprint,
        state=None,
    ):
        cursor = self.connection.execute(
            """
            INSERT OR IGNORE INTO events(
                timestamp, boot_id, category, kind, severity, source,
                summary, message, fingerprint, state_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                float(timestamp),
                boot_id,
                category,
                kind,
                severity,
                source,
                summary,
                message,
                fingerprint,
                json_dumps(state) if state is not None else None,
            ),
        )
        self.connection.commit()
        return cursor.rowcount == 1

    def insert_rollup(self, rollup):
        self.connection.execute(
            """
            INSERT INTO resource_rollups(
                period_start, period_end, boot_id, sample_count, cpu_peak,
                memory_peak, swap_peak, load1_peak, temperature_peak,
                root_used_peak, arm_mhz_min, metrics_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                rollup["period_start"],
                rollup["period_end"],
                rollup.get("boot_id"),
                rollup["sample_count"],
                rollup.get("cpu_peak"),
                rollup.get("memory_peak"),
                rollup.get("swap_peak"),
                rollup.get("load1_peak"),
                rollup.get("temperature_peak"),
                rollup.get("root_used_peak"),
                rollup.get("arm_mhz_min"),
                json_dumps(rollup["metrics"]),
            ),
        )
        self.connection.commit()

    def prune(self, retention_days=DEFAULT_RETENTION_DAYS):
        cutoff = self.clock() - float(retention_days) * 86400
        deleted = self.connection.execute(
            "DELETE FROM resource_rollups WHERE period_end < ?", (cutoff,)
        ).rowcount
        self.connection.commit()
        return deleted

    def save_crash_analysis(self, report):
        analysis = report.get("analysis") or {}
        previous_boot = analysis.get("previous_boot") or {}
        boot_id = previous_boot.get("boot_id")
        if not analysis.get("available") or not boot_id:
            return False
        self.connection.execute(
            """
            INSERT INTO crash_analyses(
                previous_boot_id, analyzed_at, previous_boot_started_at,
                previous_boot_ended_at, level, headline, report_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(previous_boot_id) DO UPDATE SET
                analyzed_at = excluded.analyzed_at,
                previous_boot_started_at = excluded.previous_boot_started_at,
                previous_boot_ended_at = excluded.previous_boot_ended_at,
                level = excluded.level,
                headline = excluded.headline,
                report_json = excluded.report_json
            """,
            (
                boot_id,
                report["generated_at"],
                previous_boot.get("started_at"),
                previous_boot.get("ended_at"),
                analysis.get("level", "unknown"),
                analysis.get("headline", "Crash analysis"),
                json_dumps(report),
            ),
        )
        self.connection.commit()
        return True

    def crash_history(self, limit=20, full=False):
        rows = self.connection.execute(
            "SELECT * FROM crash_analyses ORDER BY analyzed_at DESC LIMIT ?",
            (max(1, min(int(limit), 100)),),
        ).fetchall()
        history = []
        for row in rows:
            try:
                report = json.loads(row["report_json"])
            except (TypeError, ValueError):
                report = {}
            analysis = report.get("analysis") or {}
            item = {
                "id": row["id"],
                "previous_boot_id": row["previous_boot_id"],
                "analyzed_at": row["analyzed_at"],
                "level": row["level"],
                "headline": row["headline"],
                "previous_boot": analysis.get("previous_boot"),
                "findings": analysis.get("findings", []),
                "counts": analysis.get("counts", {}),
                "pstore_records": len(analysis.get("pstore", [])),
            }
            if full:
                item["report"] = report
            history.append(item)
        return history


def parse_meminfo(proc_root="/proc"):
    values = {}
    for line in (read_text(os.path.join(proc_root, "meminfo"), "") or "").splitlines():
        match = re.match(r"([^:]+):\s+(\d+)(?:\s+kB)?", line)
        if match:
            values[match.group(1)] = int(match.group(2)) * 1024
    total = values.get("MemTotal", 0)
    available = values.get("MemAvailable", values.get("MemFree", 0))
    swap_total = values.get("SwapTotal", 0)
    swap_free = values.get("SwapFree", 0)
    return {
        "total_bytes": total,
        "available_bytes": available,
        "used_bytes": max(0, total - available),
        "used_percent": round((total - available) * 100 / total, 2) if total else None,
        "swap_total_bytes": swap_total,
        "swap_used_bytes": max(0, swap_total - swap_free),
        "swap_used_percent": (
            round((swap_total - swap_free) * 100 / swap_total, 2) if swap_total else 0.0
        ),
    }


def parse_cpu_stat(proc_root="/proc"):
    line = (read_text(os.path.join(proc_root, "stat"), "") or "").splitlines()
    if not line or not line[0].startswith("cpu "):
        return None
    try:
        fields = [int(value) for value in line[0].split()[1:]]
    except ValueError:
        return None
    total = sum(fields)
    idle = (fields[3] if len(fields) > 3 else 0) + (fields[4] if len(fields) > 4 else 0)
    return total, idle


def parse_network_counters(value):
    """Parse /proc/net/dev into monotonically increasing interface counters."""
    counters = {}
    for line in str(value or "").splitlines():
        if ":" not in line:
            continue
        name, raw_fields = line.split(":", 1)
        fields = raw_fields.split()
        if len(fields) < 16:
            continue
        try:
            numbers = [int(field) for field in fields]
        except ValueError:
            continue
        counters[name.strip()] = {
            "rx_bytes": numbers[0],
            "rx_packets": numbers[1],
            "rx_errors": numbers[2],
            "rx_drops": numbers[3],
            "tx_bytes": numbers[8],
            "tx_packets": numbers[9],
            "tx_errors": numbers[10],
            "tx_drops": numbers[11],
        }
    return counters


def parse_disk_counters(value):
    """Parse /proc/diskstats into monotonically increasing block counters."""
    counters = {}
    for line in str(value or "").splitlines():
        fields = line.split()
        if len(fields) < 14:
            continue
        try:
            numbers = [int(field) for field in fields[3:]]
        except ValueError:
            continue
        counters[fields[2]] = {
            "reads": numbers[0],
            "sectors_read": numbers[2],
            "read_milliseconds": numbers[3],
            "writes": numbers[4],
            "sectors_written": numbers[6],
            "write_milliseconds": numbers[7],
            "in_flight": numbers[8],
            "io_milliseconds": numbers[9],
        }
    return counters


def counter_rate(current, previous, elapsed):
    if previous is None or elapsed is None or elapsed <= 0 or current < previous:
        return None
    return (current - previous) / elapsed


def calculate_network_io(current, previous, elapsed, physical_names=()):
    physical_names = set(physical_names)
    interfaces = []
    for name, counters in current.items():
        if name == "lo":
            continue
        old = previous.get(name, {}) if previous else {}
        item = {
            "name": name,
            "physical": name in physical_names,
            "rx_bytes_per_second": counter_rate(
                counters["rx_bytes"], old.get("rx_bytes"), elapsed
            ),
            "tx_bytes_per_second": counter_rate(
                counters["tx_bytes"], old.get("tx_bytes"), elapsed
            ),
            "rx_packets_per_second": counter_rate(
                counters["rx_packets"], old.get("rx_packets"), elapsed
            ),
            "tx_packets_per_second": counter_rate(
                counters["tx_packets"], old.get("tx_packets"), elapsed
            ),
            "errors": max(0, counters["rx_errors"] + counters["tx_errors"]),
            "drops": max(0, counters["rx_drops"] + counters["tx_drops"]),
        }
        interfaces.append(item)
    interfaces.sort(
        key=lambda item: (
            not item["physical"],
            -((item["rx_bytes_per_second"] or 0) + (item["tx_bytes_per_second"] or 0)),
            item["name"],
        )
    )
    physical = [item for item in interfaces if item["physical"]]

    def total(key):
        values = [item[key] for item in physical if item[key] is not None]
        return round(sum(values), 2) if values else None

    return {
        "rx_bytes_per_second": total("rx_bytes_per_second"),
        "tx_bytes_per_second": total("tx_bytes_per_second"),
        "rx_packets_per_second": total("rx_packets_per_second"),
        "tx_packets_per_second": total("tx_packets_per_second"),
        "interfaces": interfaces,
    }


def calculate_disk_io(current, previous, elapsed):
    devices = []
    for name, counters in current.items():
        old = previous.get(name, {}) if previous else {}
        sector_size = counters.get("sector_size", 512)
        read_sectors = counter_rate(
            counters["sectors_read"], old.get("sectors_read"), elapsed
        )
        written_sectors = counter_rate(
            counters["sectors_written"], old.get("sectors_written"), elapsed
        )
        io_ms = counter_rate(
            counters["io_milliseconds"], old.get("io_milliseconds"), elapsed
        )
        item = {
            "name": name,
            "labels": list(counters.get("labels", ())),
            "read_bytes_per_second": (
                round(read_sectors * sector_size, 2) if read_sectors is not None else None
            ),
            "write_bytes_per_second": (
                round(written_sectors * sector_size, 2)
                if written_sectors is not None
                else None
            ),
            "read_iops": counter_rate(counters["reads"], old.get("reads"), elapsed),
            "write_iops": counter_rate(counters["writes"], old.get("writes"), elapsed),
            "busy_percent": min(100.0, round(io_ms / 10, 2)) if io_ms is not None else None,
            "in_flight": counters["in_flight"],
        }
        devices.append(item)
    devices.sort(
        key=lambda item: -(
            (item["read_bytes_per_second"] or 0) + (item["write_bytes_per_second"] or 0)
        )
    )

    def total(key):
        values = [item[key] for item in devices if item[key] is not None]
        return round(sum(values), 2) if values else None

    busy_devices = [item for item in devices if item["busy_percent"] is not None]
    busiest = max(busy_devices, key=lambda item: item["busy_percent"]) if busy_devices else None
    return {
        "read_bytes_per_second": total("read_bytes_per_second"),
        "write_bytes_per_second": total("write_bytes_per_second"),
        "read_iops": total("read_iops"),
        "write_iops": total("write_iops"),
        "busy_percent": busiest["busy_percent"] if busiest else None,
        "busiest_device": busiest["name"] if busiest else None,
        "devices": devices,
    }


def process_details(proc_root="/proc"):
    details = []
    page_size = os.sysconf("SC_PAGE_SIZE")
    try:
        names = os.listdir(proc_root)
    except OSError:
        return details
    for name in names:
        if not name.isdigit():
            continue
        stat = read_text(os.path.join(proc_root, name, "stat"))
        if not stat:
            continue
        end = stat.rfind(")")
        start = stat.find("(")
        if start < 0 or end < start:
            continue
        fields = stat[end + 2 :].split()
        if len(fields) < 22:
            continue
        try:
            ticks = int(fields[11]) + int(fields[12])
            start_ticks = int(fields[19])
            rss_bytes = max(0, int(fields[21])) * page_size
        except (ValueError, IndexError):
            continue
        # Never retain /proc/<pid>/cmdline: command arguments frequently contain
        # tokens, passwords, or private URLs.  The kernel comm name and PID are
        # sufficient to identify a peak without copying secrets into the DB/UI.
        command = stat[start + 1 : end]
        details.append(
            {
                "pid": int(name),
                "key": f"{name}:{start_ticks}",
                "name": stat[start + 1 : end],
                "command": command[:240],
                "ticks": ticks,
                "rss_bytes": rss_bytes,
            }
        )
    return details


def collect_network_counter_state(proc_root="/proc", sys_root="/sys"):
    all_counters = parse_network_counters(
        read_text(os.path.join(proc_root, "net", "dev"), "")
    )
    selected = {}
    physical = []
    for name, counters in all_counters.items():
        interface_type = read_text(
            os.path.join(sys_root, "class", "net", name, "type")
        )
        # ARPHRD_ETHER (1) covers Ethernet/Wi-Fi/bridges. Tailscale uses
        # ARPHRD_NONE (65534). Exclude CAN (280) and loopback from this view.
        if name == "lo" or interface_type not in (None, "1", "65534"):
            continue
        selected[name] = counters
        if interface_type == "1" and os.path.exists(
            os.path.join(sys_root, "class", "net", name, "device")
        ):
            physical.append(name)
    return selected, physical


def collect_block_labels(dev_root="/dev", sys_root="/sys"):
    labels_by_device = collections.defaultdict(list)
    label_dir = os.path.join(dev_root, "disk", "by-label")
    try:
        labels = os.listdir(label_dir)
    except OSError:
        return labels_by_device
    for label in labels:
        try:
            block_name = os.path.basename(os.path.realpath(os.path.join(label_dir, label)))
        except OSError:
            continue
        sys_path = os.path.join(sys_root, "class", "block", block_name)
        if os.path.exists(os.path.join(sys_path, "partition")):
            parent = os.path.basename(os.path.dirname(os.path.realpath(sys_path)))
        else:
            parent = block_name
        if parent:
            labels_by_device[parent].append(label)
    for values in labels_by_device.values():
        values.sort()
    return labels_by_device


def collect_disk_counter_state(proc_root="/proc", sys_root="/sys", dev_root="/dev"):
    all_counters = parse_disk_counters(read_text(os.path.join(proc_root, "diskstats"), ""))
    labels = collect_block_labels(dev_root, sys_root)
    try:
        whole_devices = os.listdir(os.path.join(sys_root, "block"))
    except OSError:
        whole_devices = []
    selected = {}
    for name in whole_devices:
        if not re.match(r"^(?:sd[a-z]+|mmcblk\d+|nvme\d+n\d+|vd[a-z]+|xvd[a-z]+)$", name):
            continue
        counters = all_counters.get(name)
        if counters is None:
            continue
        sector_size = read_number(
            os.path.join(sys_root, "block", name, "queue", "hw_sector_size")
        )
        selected[name] = {
            **counters,
            "sector_size": int(sector_size or 512),
            "labels": labels.get(name, []),
        }
    return selected


def collect_usb_state(sys_root="/sys"):
    base = os.path.join(sys_root, "bus", "usb", "devices")
    devices = []
    try:
        names = sorted(os.listdir(base))
    except OSError:
        return devices
    for name in names:
        path = os.path.join(base, name)
        vendor = read_text(os.path.join(path, "idVendor"))
        product_id = read_text(os.path.join(path, "idProduct"))
        if vendor is None or product_id is None:
            continue
        devices.append(
            {
                "path": name,
                "id": f"{vendor}:{product_id}",
                "manufacturer": read_text(os.path.join(path, "manufacturer")),
                "product": read_text(os.path.join(path, "product")),
                "speed_mbps": read_number(os.path.join(path, "speed")),
            }
        )
    return devices


def collect_mount_state(proc_root="/proc"):
    mounts = []
    for line in (read_text(os.path.join(proc_root, "mounts"), "") or "").splitlines():
        fields = line.split()
        if len(fields) < 4 or not fields[0].startswith("/dev/"):
            continue
        mounts.append(
            {
                "device": fields[0],
                "mountpoint": fields[1].replace("\\040", " "),
                "filesystem": fields[2],
                "read_only": "ro" in fields[3].split(","),
            }
        )
    return mounts


class ResourceSampler:
    def __init__(
        self,
        proc_root="/proc",
        sys_root="/sys",
        dev_root="/dev",
        clock=utc_timestamp,
        monotonic=time.monotonic,
        command=run_text,
    ):
        self.proc_root = proc_root
        self.sys_root = sys_root
        self.dev_root = dev_root
        self.clock = clock
        self.monotonic = monotonic
        self.command = command
        self.previous_cpu = None
        self.previous_processes = {}
        self.previous_process_at = None
        self.previous_network = {}
        self.previous_disks = {}
        self.previous_io_at = None
        try:
            self.ticks_per_second = os.sysconf("SC_CLK_TCK")
        except (OSError, ValueError):
            self.ticks_per_second = 100
        self.cpu_count = max(1, os.cpu_count() or 1)

    def _root_usage(self):
        try:
            stat = os.statvfs("/")
        except OSError:
            return {"used_percent": None, "free_bytes": None, "total_bytes": None}
        total = stat.f_blocks * stat.f_frsize
        available = stat.f_bavail * stat.f_frsize
        used = total - stat.f_bfree * stat.f_frsize
        return {
            "used_percent": round(used * 100 / total, 2) if total else None,
            "free_bytes": available,
            "total_bytes": total,
        }

    def _process_usage(self, now_mono):
        processes = process_details(self.proc_root)
        elapsed = None if self.previous_process_at is None else now_mono - self.previous_process_at
        top_cpu = []
        if elapsed and elapsed > 0:
            for item in processes:
                previous_ticks = self.previous_processes.get(item["key"])
                if previous_ticks is None:
                    continue
                delta = max(0, item["ticks"] - previous_ticks)
                item["cpu_percent"] = round(
                    delta * 100 / self.ticks_per_second / elapsed, 2
                )
                top_cpu.append(item)
        top_cpu.sort(key=lambda item: item.get("cpu_percent", 0), reverse=True)
        top_memory = sorted(processes, key=lambda item: item["rss_bytes"], reverse=True)
        self.previous_processes = {item["key"]: item["ticks"] for item in processes}
        self.previous_process_at = now_mono

        def public(item, include_cpu=False):
            value = {
                "pid": item["pid"],
                "name": item["name"],
                "command": item["command"],
                "rss_bytes": item["rss_bytes"],
            }
            if include_cpu:
                value["cpu_percent"] = item.get("cpu_percent", 0.0)
            return value

        return (
            [public(item, True) for item in top_cpu[:5]],
            [public(item) for item in top_memory[:5]],
        )

    def sample(self):
        now = self.clock()
        now_mono = self.monotonic()
        cpu = parse_cpu_stat(self.proc_root)
        cpu_percent = None
        if cpu is not None and self.previous_cpu is not None:
            total_delta = cpu[0] - self.previous_cpu[0]
            idle_delta = cpu[1] - self.previous_cpu[1]
            if total_delta > 0:
                cpu_percent = round(
                    max(0.0, min(100.0, (total_delta - idle_delta) * 100 / total_delta)),
                    2,
                )
        self.previous_cpu = cpu
        top_cpu, top_memory = self._process_usage(now_mono)
        io_elapsed = None if self.previous_io_at is None else now_mono - self.previous_io_at
        network_counters, physical_networks = collect_network_counter_state(
            self.proc_root, self.sys_root
        )
        disk_counters = collect_disk_counter_state(
            self.proc_root, self.sys_root, self.dev_root
        )
        network_io = calculate_network_io(
            network_counters, self.previous_network, io_elapsed, physical_networks
        )
        disk_io = calculate_disk_io(disk_counters, self.previous_disks, io_elapsed)
        self.previous_network = network_counters
        self.previous_disks = disk_counters
        self.previous_io_at = now_mono
        memory = parse_meminfo(self.proc_root)
        try:
            load_values = [float(value) for value in os.getloadavg()]
        except (OSError, AttributeError):
            raw_load = (read_text(os.path.join(self.proc_root, "loadavg"), "0 0 0") or "0 0 0").split()
            load_values = [float(value) for value in raw_load[:3]]

        throttle_output = self.command(["/usr/bin/vcgencmd", "get_throttled"], timeout=3)
        throttle = parse_throttled(throttle_output)
        uptime_raw = (read_text(os.path.join(self.proc_root, "uptime"), "") or "").split()
        try:
            uptime = float(uptime_raw[0])
        except (IndexError, ValueError):
            uptime = None
        thermal_sensors = collect_thermal_sensors(self.sys_root)
        cpu_sensors = [
            sensor for sensor in thermal_sensors if "cpu" in sensor["type"].lower()
        ]
        primary_temperature = (cpu_sensors or thermal_sensors or [{}])[0].get(
            "temperature_c"
        )
        frequency_policies = collect_cpu_frequency_policies(self.sys_root)
        arm_khz = read_number(
            os.path.join(
                self.sys_root,
                "devices",
                "system",
                "cpu",
                "cpufreq",
                "policy0",
                "scaling_cur_freq",
            )
        )
        return {
            "timestamp": now,
            "timestamp_iso": iso_time(now),
            "boot_id": read_text(os.path.join(self.proc_root, "sys", "kernel", "random", "boot_id")),
            "uptime_seconds": uptime,
            "cpu_percent": cpu_percent,
            "cpu_count": self.cpu_count,
            "load": {"1m": load_values[0], "5m": load_values[1], "15m": load_values[2]},
            "memory": {
                "used_percent": memory["used_percent"],
                "used_bytes": memory["used_bytes"],
                "available_bytes": memory["available_bytes"],
                "total_bytes": memory["total_bytes"],
            },
            "swap": {
                "used_percent": memory["swap_used_percent"],
                "used_bytes": memory["swap_used_bytes"],
                "total_bytes": memory["swap_total_bytes"],
            },
            # Keep the legacy primary value for thresholds and old dashboard
            # clients while also reporting every thermal zone with its scope.
            "temperature_c": primary_temperature,
            "thermal_sensors": thermal_sensors,
            "arm_mhz": round(arm_khz / 1000, 2) if arm_khz is not None else None,
            "cpu_frequency_policies": frequency_policies,
            "throttle": throttle,
            "root_filesystem": self._root_usage(),
            "top_cpu": top_cpu,
            "top_memory": top_memory,
            "network_io": network_io,
            "disk_io": disk_io,
        }


def metric_value(sample, path):
    value = sample
    for key in path:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value if isinstance(value, (int, float)) and math.isfinite(value) else None


class RollupAccumulator:
    METRICS = {
        "cpu": ("cpu_percent",),
        "memory": ("memory", "used_percent"),
        "swap": ("swap", "used_percent"),
        "load1": ("load", "1m"),
        "temperature": ("temperature_c",),
        "root_used": ("root_filesystem", "used_percent"),
        "arm_mhz": ("arm_mhz",),
        "network_rx": ("network_io", "rx_bytes_per_second"),
        "network_tx": ("network_io", "tx_bytes_per_second"),
        "disk_read": ("disk_io", "read_bytes_per_second"),
        "disk_write": ("disk_io", "write_bytes_per_second"),
        "disk_busy": ("disk_io", "busy_percent"),
    }

    def __init__(self, interval=DEFAULT_ROLLUP_INTERVAL):
        self.interval = interval
        self.samples = []
        self.period_start = None

    def add(self, sample):
        if self.period_start is None:
            self.period_start = sample["timestamp"]
        self.samples.append(sample)

    def ready(self, now):
        return self.period_start is not None and now - self.period_start >= self.interval

    def flush(self):
        if not self.samples:
            return None
        metrics = {}
        for name, path in self.METRICS.items():
            candidates = [
                (metric_value(sample, path), sample)
                for sample in self.samples
                if metric_value(sample, path) is not None
            ]
            if not candidates:
                metrics[name] = {"peak": None, "average": None, "at": None}
                continue
            if name == "arm_mhz":
                peak_value, peak_sample = min(candidates, key=lambda pair: pair[0])
            else:
                peak_value, peak_sample = max(candidates, key=lambda pair: pair[0])
            entry = {
                "peak": round(peak_value, 2),
                "average": round(statistics.fmean(value for value, _sample in candidates), 2),
                "at": peak_sample["timestamp"],
            }
            if name == "cpu":
                entry["top_process"] = (peak_sample.get("top_cpu") or [None])[0]
            if name == "memory":
                entry["top_process"] = (peak_sample.get("top_memory") or [None])[0]
                entry["available_min_bytes"] = min(
                    sample["memory"]["available_bytes"] for _value, sample in candidates
                )
            if name in ("network_rx", "network_tx"):
                interface_key = (
                    "rx_bytes_per_second" if name == "network_rx" else "tx_bytes_per_second"
                )
                interfaces = peak_sample.get("network_io", {}).get("interfaces", ())
                eligible = [
                    item
                    for item in interfaces
                    if item.get("physical") and item.get(interface_key) is not None
                ]
                entry["top_interface"] = (
                    max(eligible, key=lambda item: item[interface_key]) if eligible else None
                )
            if name in ("disk_read", "disk_write", "disk_busy"):
                device_key = {
                    "disk_read": "read_bytes_per_second",
                    "disk_write": "write_bytes_per_second",
                    "disk_busy": "busy_percent",
                }[name]
                devices = peak_sample.get("disk_io", {}).get("devices", ())
                eligible = [item for item in devices if item.get(device_key) is not None]
                entry["top_device"] = (
                    max(eligible, key=lambda item: item[device_key]) if eligible else None
                )
            metrics[name] = entry
        thermal_metrics = []
        sensor_keys = sorted(
            {
                (sensor.get("zone"), sensor.get("type"))
                for sample in self.samples
                for sensor in sample.get("thermal_sensors", ())
                if sensor.get("zone") and sensor.get("type")
            }
        )
        for zone, sensor_type in sensor_keys:
            candidates = []
            cpu_ids = []
            shared = False
            for sample in self.samples:
                sensor = next(
                    (
                        item
                        for item in sample.get("thermal_sensors", ())
                        if item.get("zone") == zone and item.get("type") == sensor_type
                    ),
                    None,
                )
                value = sensor.get("temperature_c") if sensor else None
                if isinstance(value, (int, float)) and math.isfinite(value):
                    candidates.append((value, sample["timestamp"]))
                    cpu_ids = sensor.get("cpu_ids") or cpu_ids
                    shared = bool(sensor.get("shared", shared))
            if not candidates:
                continue
            peak, at = max(candidates, key=lambda pair: pair[0])
            thermal_metrics.append(
                {
                    "zone": zone,
                    "type": sensor_type,
                    "cpu_ids": cpu_ids,
                    "shared": shared,
                    "peak": round(peak, 2),
                    "average": round(
                        statistics.fmean(value for value, _timestamp in candidates), 2
                    ),
                    "at": at,
                }
            )
        metrics["thermal_sensors"] = thermal_metrics
        last = self.samples[-1]
        rollup = {
            "period_start": self.period_start,
            "period_end": last["timestamp"],
            "boot_id": last.get("boot_id"),
            "sample_count": len(self.samples),
            "cpu_peak": metrics["cpu"]["peak"],
            "memory_peak": metrics["memory"]["peak"],
            "swap_peak": metrics["swap"]["peak"],
            "load1_peak": metrics["load1"]["peak"],
            "temperature_peak": metrics["temperature"]["peak"],
            "root_used_peak": metrics["root_used"]["peak"],
            "arm_mhz_min": metrics["arm_mhz"]["peak"],
            "metrics": metrics,
        }
        self.samples = []
        self.period_start = None
        return rollup


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


class SystemEventMonitor:
    def __init__(
        self,
        store,
        sampler=None,
        sample_interval=DEFAULT_SAMPLE_INTERVAL,
        rollup_interval=DEFAULT_ROLLUP_INTERVAL,
        retention_days=DEFAULT_RETENTION_DAYS,
        clock=utc_timestamp,
        monotonic=time.monotonic,
    ):
        self.store = store
        self.sampler = sampler or ResourceSampler(clock=clock, monotonic=monotonic)
        self.sample_interval = sample_interval
        self.retention_days = retention_days
        self.clock = clock
        self.monotonic = monotonic
        self.rollup = RollupAccumulator(rollup_interval)
        self.stop_event = threading.Event()
        self.journal_queue = queue.Queue()
        self.journal_thread = None
        self.journal_process = None
        self.journal_lock = threading.Lock()
        self.current = None
        self.context_cache = None
        self.context_cached_at = 0
        self.threshold_active = {}
        self.threshold_counts = collections.Counter()
        self.last_failed_check = 0
        self.failed_units = []

    def _event_state(self, historical=False):
        if historical:
            return {
                "capture": "journal_backfill",
                "state_available": False,
                "note": "The monitor was not yet running at this event time.",
            }
        now = self.monotonic()
        if self.context_cache is None or now - self.context_cached_at >= 10:
            self.context_cache = {
                "usb_devices": collect_usb_state(),
                "mounts": collect_mount_state(),
                "failed_units": list(self.failed_units),
            }
            self.context_cached_at = now
        state = dict(self.current or {})
        state.update(self.context_cache)
        state["capture"] = "live"
        state["state_available"] = bool(self.current)
        return state

    def insert_normalized_event(self, event, historical=False):
        return self.store.insert_event(state=self._event_state(historical), **event)

    def backfill_journal(self):
        try:
            result = subprocess.run(
                [
                    "/usr/bin/journalctl",
                    "--dmesg",
                    "--boot=0",
                    "--output=json",
                    "--no-pager",
                ],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            self._monitor_error("journal_backfill_error", f"Could not read kernel journal: {error}")
            return 0
        if result.returncode:
            detail = (result.stderr or "journalctl failed").strip()[-500:]
            self._monitor_error("journal_backfill_error", detail)
            return 0
        inserted = 0
        for line in result.stdout.splitlines():
            try:
                record = json.loads(line)
            except ValueError:
                continue
            event = parse_journal_record(record)
            if event and self.insert_normalized_event(event, historical=True):
                inserted += 1
        return inserted

    def _journal_follow(self):
        while not self.stop_event.is_set():
            try:
                process = subprocess.Popen(
                    [
                        "/usr/bin/journalctl",
                        "--dmesg",
                        "--boot=0",
                        "--follow",
                        "--lines=0",
                        "--output=json",
                        "--no-pager",
                    ],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    bufsize=1,
                )
            except OSError as error:
                self.journal_queue.put(("error", str(error)))
                self.stop_event.wait(10)
                continue
            with self.journal_lock:
                self.journal_process = process
            assert process.stdout is not None
            for line in process.stdout:
                if self.stop_event.is_set():
                    break
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                event = parse_journal_record(record)
                if event:
                    self.journal_queue.put(("event", event))
            stderr = ""
            if process.stderr is not None:
                stderr = process.stderr.read().strip()[-500:]
            returncode = process.wait()
            with self.journal_lock:
                if self.journal_process is process:
                    self.journal_process = None
            if not self.stop_event.is_set():
                self.journal_queue.put(
                    ("error", stderr or f"journalctl follower exited {returncode}")
                )
                self.stop_event.wait(5)

    def start_journal_follow(self):
        if self.journal_thread is None:
            self.journal_thread = threading.Thread(
                target=self._journal_follow, name="kernel-journal", daemon=True
            )
            self.journal_thread.start()

    def stop(self):
        self.stop_event.set()
        with self.journal_lock:
            process = self.journal_process
        if process is not None and process.poll() is None:
            process.terminate()

    def _monitor_error(self, kind, message):
        now = self.clock()
        boot_id = self.current.get("boot_id") if self.current else None
        self.store.insert_event(
            timestamp=now,
            boot_id=boot_id,
            category="monitor",
            kind=kind,
            severity="warning",
            source="monitor",
            summary="System monitor input failed",
            message=str(message)[:1000],
            fingerprint=event_fingerprint(kind, boot_id, int(now // 300)),
            state=self._event_state(),
        )

    def drain_journal(self):
        count = 0
        while True:
            try:
                item_type, payload = self.journal_queue.get_nowait()
            except queue.Empty:
                break
            if item_type == "event":
                count += int(self.insert_normalized_event(payload))
            else:
                self._monitor_error("journal_follow_error", payload)
        return count

    def _firmware_event(self, key, transition, timestamp, boot_id):
        labels = {item[1]: item[2] for item in THROTTLE_FLAGS}
        active = transition == "active"
        category = "power" if key == "under_voltage" else "thermal"
        if key in ("frequency_capped", "throttled"):
            category = "throttle"
        if transition == "occurred":
            summary = f"Firmware says {labels[key].lower()} occurred earlier this boot"
            severity = "warning"
        elif active:
            summary = f"{labels[key]} active"
            severity = "critical" if key in ("under_voltage", "throttled") else "warning"
        else:
            summary = f"{labels[key]} cleared"
            severity = "info"
        event = {
            "timestamp": timestamp,
            "boot_id": boot_id,
            "category": category,
            "kind": f"firmware_{key}_{transition}",
            "severity": severity,
            "source": "firmware",
            "summary": summary,
            "message": summary,
            "fingerprint": event_fingerprint(
                "firmware", boot_id, key, transition, int(timestamp * 10)
            ),
        }
        self.insert_normalized_event(event)

    def reconcile_firmware(self, sample):
        throttle = sample.get("throttle")
        if throttle is None:
            self._monitor_error("vcgencmd_error", "vcgencmd get_throttled returned no usable value")
            return
        boot_id = sample.get("boot_id")
        previous = self.store.get_meta("last_throttle")
        same_boot = isinstance(previous, dict) and previous.get("boot_id") == boot_id
        previous_current = set(previous.get("current", ())) if same_boot else set()
        previous_occurred = set(previous.get("occurred", ())) if same_boot else set()
        current = set(throttle["current"])
        occurred = set(throttle["occurred"])
        for key in sorted(current - previous_current):
            self._firmware_event(key, "active", sample["timestamp"], boot_id)
        for key in sorted(previous_current - current):
            self._firmware_event(key, "cleared", sample["timestamp"], boot_id)
        for key in sorted(occurred - previous_occurred - current):
            self._firmware_event(key, "occurred", sample["timestamp"], boot_id)
        self.store.set_meta(
            "last_throttle",
            {"boot_id": boot_id, "current": sorted(current), "occurred": sorted(occurred)},
        )

    def _threshold_event(self, key, active, severity, summary, value, sample):
        previous = self.threshold_active.get(key, False)
        if active == previous:
            return
        self.threshold_active[key] = active
        now = sample["timestamp"]
        state_word = "started" if active else "cleared"
        self.store.insert_event(
            timestamp=now,
            boot_id=sample.get("boot_id"),
            category="resource",
            kind=f"{key}_{state_word}",
            severity=severity if active else "info",
            source="sampler",
            summary=summary if active else f"{summary} cleared",
            message=f"{key}={value}",
            fingerprint=event_fingerprint("threshold", sample.get("boot_id"), key, state_word, int(now)),
            state=self._event_state(),
        )

    def evaluate_thresholds(self, sample):
        checks = (
            ("high_cpu", metric_value(sample, ("cpu_percent",)), 95, 80, "warning", "Sustained CPU saturation"),
            ("high_memory", metric_value(sample, ("memory", "used_percent")), 90, 80, "critical", "High memory use"),
            ("high_swap", metric_value(sample, ("swap", "used_percent")), 75, 50, "warning", "High swap use"),
            ("high_temperature", metric_value(sample, ("temperature_c",)), 80, 75, "critical", "High SoC temperature"),
            ("root_disk_full", metric_value(sample, ("root_filesystem", "used_percent")), 90, 85, "critical", "Root filesystem nearly full"),
            ("high_load", metric_value(sample, ("load", "1m")), sample.get("cpu_count", 1) * 2, sample.get("cpu_count", 1) * 1.25, "warning", "High system load"),
            ("high_disk_io", metric_value(sample, ("disk_io", "busy_percent")), 98, 80, "warning", "Sustained disk saturation"),
        )
        for key, value, enter, clear, severity, summary in checks:
            if value is None:
                continue
            if not self.threshold_active.get(key):
                self.threshold_counts[key] = self.threshold_counts[key] + 1 if value >= enter else 0
                # CPU/load need three samples; capacity/thermal hazards should be immediate.
                required = 3 if key in ("high_cpu", "high_load", "high_disk_io") else 1
                if self.threshold_counts[key] >= required:
                    self._threshold_event(key, True, severity, summary, value, sample)
            elif value <= clear:
                self.threshold_counts[key] = 0
                self._threshold_event(key, False, severity, summary, value, sample)

    def check_failed_units(self, sample):
        now = self.monotonic()
        if now - self.last_failed_check < 60:
            return
        self.last_failed_check = now
        output = run_text(
            ["/bin/systemctl", "--failed", "--no-legend", "--plain"], timeout=5
        )
        if output is None:
            return
        current = sorted(
            {
                line.split()[0]
                for line in output.splitlines()
                if line.split() and line.split()[0] != "0"
            }
        )
        previous = set(self.failed_units)
        self.failed_units = current
        for unit in sorted(set(current) - previous):
            timestamp = sample["timestamp"]
            self.store.insert_event(
                timestamp=timestamp,
                boot_id=sample.get("boot_id"),
                category="service",
                kind="systemd_unit_failed",
                severity="warning",
                source="systemd",
                summary=f"Systemd unit failed: {unit}",
                message=unit,
                fingerprint=event_fingerprint("failed-unit", sample.get("boot_id"), unit, timestamp),
                state=self._event_state(),
            )
        self.context_cache = None

    def take_sample(self):
        sample = self.sampler.sample()
        self.current = sample
        self.store.set_meta("current", sample)
        self.reconcile_firmware(sample)
        self.evaluate_thresholds(sample)
        self.check_failed_units(sample)
        self.rollup.add(sample)
        if self.rollup.ready(sample["timestamp"]):
            self.store.insert_rollup(self.rollup.flush())
        return sample

    def initialize_boot(self):
        sample = self.take_sample()
        boot_id = sample.get("boot_id")
        last_boot = self.store.get_meta("last_boot_id")
        if boot_id and boot_id != last_boot:
            now = sample["timestamp"]
            self.store.insert_event(
                timestamp=now,
                boot_id=boot_id,
                category="system",
                kind="boot_observed",
                severity="info",
                source="monitor",
                summary="System boot observed",
                message=f"boot_id={boot_id}",
                fingerprint=event_fingerprint("boot", boot_id),
                state=self._event_state(),
            )
            self.store.set_meta("last_boot_id", boot_id)
        return sample

    def run(self):
        self.initialize_boot()
        imported = self.backfill_journal()
        print(f"system-event-monitor: imported {imported} current-boot kernel events", flush=True)
        self.start_journal_follow()
        next_sample = self.monotonic() + self.sample_interval
        next_prune = self.monotonic() + 3600
        while not self.stop_event.wait(0.5):
            self.drain_journal()
            now_mono = self.monotonic()
            if now_mono >= next_sample:
                self.take_sample()
                next_sample = now_mono + self.sample_interval
            if now_mono >= next_prune:
                self.store.prune(self.retention_days)
                next_prune = now_mono + 86400
        self.drain_journal()
        pending_rollup = self.rollup.flush()
        if pending_rollup:
            self.store.insert_rollup(pending_rollup)
        if self.journal_thread:
            self.journal_thread.join(timeout=3)


def decode_row_json(row, key):
    try:
        return json.loads(row[key]) if row[key] else None
    except (TypeError, ValueError):
        return None


def event_public(row, include_state=True):
    event = {
        "id": row["id"],
        "timestamp": row["timestamp"],
        "timestamp_iso": iso_time(row["timestamp"]),
        "boot_id": row["boot_id"],
        "category": row["category"],
        "kind": row["kind"],
        "severity": row["severity"],
        "source": row["source"],
        "summary": row["summary"],
        "message": row["message"],
    }
    if include_state:
        event["state"] = decode_row_json(row, "state_json")
    return event


def rollup_row_and_metrics(item):
    """Accept a DB row or a predecoded ``(row, metrics)`` report entry."""
    if isinstance(item, tuple) and len(item) == 2 and isinstance(item[1], dict):
        return item
    return item, decode_row_json(item, "metrics_json") or {}


def best_rollup_metric(rows, metric_name, prefer_min=False):
    candidates = []
    for item in rows:
        _row, metrics = rollup_row_and_metrics(item)
        entry = metrics.get(metric_name) or {}
        value = entry.get("peak")
        if isinstance(value, (int, float)):
            candidates.append((value, entry))
    if not candidates:
        return {"value": None, "at": None}
    value, entry = (min if prefer_min else max)(candidates, key=lambda pair: pair[0])
    result = {"value": value, "at": entry.get("at"), "average": entry.get("average")}
    for extra in ("top_process", "available_min_bytes", "top_interface", "top_device"):
        if extra in entry:
            result[extra] = entry.get(extra)
    return result


def best_thermal_sensor_metrics(rows, current=None):
    """Combine per-zone peaks stored in rollup JSON with the live sample."""
    sensors = {}

    def consider(sensor, value_key, timestamp_key):
        value = sensor.get(value_key)
        if not isinstance(value, (int, float)) or not math.isfinite(value):
            return
        key = (sensor.get("zone"), sensor.get("type"))
        if not all(key):
            return
        candidate = {
            "zone": key[0],
            "type": key[1],
            "cpu_ids": sensor.get("cpu_ids") or [],
            "shared": bool(sensor.get("shared")),
            "value": round(value, 2),
            "at": sensor.get(timestamp_key),
        }
        if sensor.get("average") is not None:
            candidate["average"] = sensor.get("average")
        if key not in sensors or value > sensors[key]["value"]:
            sensors[key] = candidate

    for item in rows:
        _row, metrics = rollup_row_and_metrics(item)
        for sensor in metrics.get("thermal_sensors") or ():
            if isinstance(sensor, dict):
                consider(sensor, "peak", "at")
    if isinstance(current, dict):
        for sensor in current.get("thermal_sensors") or ():
            if not isinstance(sensor, dict):
                continue
            live = dict(sensor)
            live["at"] = current.get("timestamp")
            consider(live, "temperature_c", "at")
    return sorted(sensors.values(), key=lambda sensor: (sensor["type"], sensor["zone"]))


def build_process_report(rows, current=None, limit=12):
    """Aggregate one-minute CPU/memory peak leaders across process restarts."""
    offenders = {}
    for item in rows:
        row, metrics = rollup_row_and_metrics(item)
        for resource in ("cpu", "memory"):
            metric = metrics.get(resource) or {}
            process = metric.get("top_process")
            if not isinstance(process, dict):
                continue
            name = str(process.get("name") or process.get("command") or "").strip()
            if not name:
                continue
            entry = offenders.setdefault(
                name,
                {
                    "name": name,
                    "cpu_peak_count": 0,
                    "memory_peak_count": 0,
                    "max_cpu_percent": None,
                    "max_rss_bytes": None,
                    "last_seen_at": None,
                    "pids": set(),
                },
            )
            entry[f"{resource}_peak_count"] += 1
            timestamp = metric.get("at") or row["period_end"]
            if entry["last_seen_at"] is None or timestamp > entry["last_seen_at"]:
                entry["last_seen_at"] = timestamp
                entry["latest_pid"] = process.get("pid")
            if isinstance(process.get("pid"), int):
                entry["pids"].add(process["pid"])
            cpu_percent = process.get("cpu_percent")
            if isinstance(cpu_percent, (int, float)) and (
                entry["max_cpu_percent"] is None
                or cpu_percent > entry["max_cpu_percent"]
            ):
                entry["max_cpu_percent"] = cpu_percent
            rss_bytes = process.get("rss_bytes")
            if isinstance(rss_bytes, (int, float)) and (
                entry["max_rss_bytes"] is None or rss_bytes > entry["max_rss_bytes"]
            ):
                entry["max_rss_bytes"] = rss_bytes

    public = []
    for entry in offenders.values():
        item = {key: value for key, value in entry.items() if key != "pids"}
        item["pid_count"] = len(entry["pids"])
        item["peak_count"] = item["cpu_peak_count"] + item["memory_peak_count"]
        public.append(item)
    public.sort(
        key=lambda item: (
            -item["peak_count"],
            -(item["max_cpu_percent"] or 0),
            -(item["max_rss_bytes"] or 0),
            item["name"].lower(),
        )
    )
    current = current if isinstance(current, dict) else {}
    return {
        "rollup_count": len(rows),
        "current_cpu": (current.get("top_cpu") or [])[:5],
        "current_memory": (current.get("top_memory") or [])[:5],
        "repeat_offenders": public[: max(1, min(int(limit), 50))],
    }


def build_throttling_report(current, counts, events):
    throttle = current.get("throttle") if isinstance(current, dict) else None
    throttle = throttle if isinstance(throttle, dict) else {}
    active = set(throttle.get("current") or ())
    occurred = set(throttle.get("occurred") or ())
    last_by_kind = {}
    for event in events:
        kind = event.get("kind")
        if kind and kind.startswith("firmware_"):
            last_by_kind[kind] = event.get("timestamp")
    flags = []
    for _bit, key, label in THROTTLE_FLAGS:
        active_kind = f"firmware_{key}_active"
        cleared_kind = f"firmware_{key}_cleared"
        occurred_kind = f"firmware_{key}_occurred"
        flags.append(
            {
                "key": key,
                "label": label,
                "active": key in active,
                "occurred_since_boot": key in occurred,
                "active_transitions": counts.get(active_kind, 0),
                "cleared_transitions": counts.get(cleared_kind, 0),
                "sticky_observations": counts.get(occurred_kind, 0),
                "last_active_at": last_by_kind.get(active_kind),
                "last_cleared_at": last_by_kind.get(cleared_kind),
            }
        )
    return {
        "available": bool(throttle),
        "raw": throttle.get("raw"),
        "hex": throttle.get("hex"),
        "active": sorted(active),
        "occurred_since_boot": sorted(occurred),
        "flags": flags,
    }


def power_episodes(events):
    starts = [event for event in events if event["kind"] == "undervoltage_started"]
    clears_by_boot = collections.defaultdict(list)
    for event in events:
        if event["kind"] == "undervoltage_cleared":
            clears_by_boot[event["boot_id"]].append(event["timestamp"])
    for values in clears_by_boot.values():
        values.sort()
    episodes = []
    for start in sorted(starts, key=lambda event: event["timestamp"]):
        end = next(
            (
                timestamp
                for timestamp in clears_by_boot[start["boot_id"]]
                if timestamp >= start["timestamp"]
            ),
            None,
        )
        episodes.append(
            {
                "started_at": start["timestamp"],
                "ended_at": end,
                "duration_seconds": round(end - start["timestamp"], 2) if end else None,
            }
        )
    return episodes


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


def read_pstore(sys_root="/sys"):
    base = os.path.join(sys_root, "fs", "pstore")
    records = []
    try:
        names = sorted(os.listdir(base))
    except OSError:
        return records
    for name in names[:20]:
        path = os.path.join(base, name)
        if not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf-8", errors="replace") as handle:
                content = handle.read(65536)
        except OSError:
            continue
        records.append({"name": name, "content": redact_log_message(content)})
    return records


def analyze_previous_boot(records, current_boot_started_at=None, pstore_records=None):
    """Analyze already-filtered previous-boot journal records."""
    pstore_records = list(pstore_records or ())
    ordered = sorted(records, key=lambda item: item["timestamp"])
    if not ordered:
        return {
            "available": False,
            "level": "unknown",
            "headline": "No previous-boot journal is available",
            "findings": [
                "The journal has no readable records for boot -1, so there is not enough retained evidence to analyze a preceding crash."
            ],
            "previous_boot": None,
            "pstore": pstore_records,
            "timeline": [],
            "counts": {},
        }

    clean_patterns = (
        "reached target shutdown.target",
        "systemd-shutdown",
        "shutting down",
        "powering off",
        "rebooting system",
    )
    fatal_patterns = (
        "kernel panic",
        "out of memory",
        "oom-killer",
        "killed process",
        "segfault",
        "core dumped",
        "blocked for more than",
        "hung task",
        "emergency mode",
    )
    shutdown_tail = ordered[-120:]
    ended_cleanly = any(
        any(pattern in item["message"].lower() for pattern in clean_patterns)
        and (
            item.get("pid1")
            or item.get("transport") == "kernel"
            or "systemd" in str(item.get("source", "")).lower()
        )
        for item in shutdown_tail
    )
    classified = []
    counts = collections.Counter()
    for item in ordered:
        classification = classify_kernel_message(item["message"])
        if classification:
            category, kind, severity, summary = classification
            enriched = {
                **item,
                "category": category,
                "kind": kind,
                "severity": severity,
                "summary": summary,
            }
            classified.append(enriched)
            counts[category] += 1
            counts[kind] += 1
        elif any(pattern in item["message"].lower() for pattern in fatal_patterns):
            classified.append(
                {
                    **item,
                    "category": "system",
                    "kind": "fatal_log",
                    "severity": "critical" if item["priority"] <= 3 else "warning",
                    "summary": "Potential crash precursor",
                }
            )
            counts["fatal_log"] += 1

    boot_id = next((item.get("boot_id") for item in ordered if item.get("boot_id")), None)
    started_at = ordered[0]["timestamp"]
    ended_at = ordered[-1]["timestamp"]
    gap = (
        max(0, current_boot_started_at - ended_at)
        if isinstance(current_boot_started_at, (int, float))
        else None
    )
    findings = []
    if ended_cleanly:
        level = "good"
        headline = "Previous boot shows a normal shutdown path"
        findings.append(
            "Normal shutdown/reboot markers are present, so the preceding restart does not look like an abrupt power loss or kernel crash."
        )
    else:
        level = "warning"
        headline = "Previous boot ended without clean shutdown evidence"
        findings.append(
            "No normal shutdown marker appears near the end of the retained journal. This is consistent with power loss, a hard reset, a kernel lockup, or incomplete journal persistence."
        )
    if pstore_records:
        level = "critical"
        headline = "Persistent kernel crash evidence is available"
        findings.append(
            f"The kernel pstore contains {len(pstore_records)} persistent crash record(s), which survive a reboot and are stronger evidence than a missing shutdown marker alone."
        )
    if counts["kernel_panic"] or counts["watchdog"] or counts["hung_task"]:
        level = "critical"
        findings.append(
            "The previous boot contains an explicit kernel panic, watchdog, or hung-task signature."
        )
    if counts["out_of_memory"]:
        findings.append(
            f"Out-of-memory handling appeared {counts['out_of_memory']} time(s) before the reboot."
        )
    if counts["undervoltage_started"]:
        findings.append(
            f"The previous boot recorded {counts['undervoltage_started']} undervoltage event(s)."
        )
    usb_faults = sum(
        counts[kind]
        for kind in ("usb_error", "usb_controller_error", "usb_reset", "usb_disconnected")
    )
    if usb_faults or counts["usb_overcurrent"]:
        findings.append(
            f"USB evidence near that boot includes {usb_faults} reset/disconnect/error event(s) and {counts['usb_overcurrent']} over-current event(s)."
        )
    if counts["storage_io_error"]:
        level = "critical"
        findings.append(
            f"Storage or filesystem I/O errors appeared {counts['storage_io_error']} time(s)."
        )
    if gap is not None:
        findings.append(
            f"The retained previous-boot log ends {gap:.1f} seconds before the estimated start of the current boot."
        )

    # Add a small amount of safe PID-1/kernel context surrounding the final
    # records, without returning arbitrary application logs or command lines.
    final_context = [
        {
            **item,
            "category": "context",
            "kind": "boot_tail",
            "severity": "critical" if item["priority"] <= 3 else "info",
            "summary": "Final boot log",
        }
        for item in ordered
        if item.get("pid1") or item.get("transport") == "kernel"
    ][-20:]
    combined = classified + final_context
    unique = {}
    for item in combined:
        key = (item["timestamp"], item["message"])
        previous = unique.get(key)
        if previous is None or SEVERITY_RANK.get(item["severity"], 0) > SEVERITY_RANK.get(
            previous["severity"], 0
        ):
            unique[key] = item
    timeline = sorted(unique.values(), key=lambda item: item["timestamp"], reverse=True)[:80]
    return {
        "available": True,
        "level": level,
        "headline": headline,
        "findings": findings,
        "previous_boot": {
            "boot_id": boot_id,
            "started_at": started_at,
            "ended_at": ended_at,
            "duration_seconds": round(max(0, ended_at - started_at), 2),
            "retained_log_started_at": started_at,
            "retained_log_span_seconds": round(max(0, ended_at - started_at), 2),
            "ended_cleanly": ended_cleanly,
            "gap_to_current_boot_seconds": round(gap, 2) if gap is not None else None,
        },
        "pstore": pstore_records,
        "timeline": timeline,
        "counts": dict(counts),
    }


def build_crash_report(clock=utc_timestamp):
    general = read_journal_records(boot=-1, kernel=False, lines=800)
    kernel = read_journal_records(boot=-1, kernel=True, lines=800)
    unique = {}
    for item in general + kernel:
        key = (item.get("boot_id"), item.get("monotonic"), item["message"])
        unique[key] = item
    uptime_raw = (read_text("/proc/uptime", "") or "").split()
    try:
        current_boot_started_at = clock() - float(uptime_raw[0])
    except (IndexError, ValueError):
        current_boot_started_at = None
    analysis = analyze_previous_boot(
        list(unique.values()), current_boot_started_at, read_pstore()
    )
    return {
        "ok": True,
        "version": REPORT_VERSION,
        "generated_at": clock(),
        "current_boot_id": read_text("/proc/sys/kernel/random/boot_id"),
        "analysis": analysis,
    }


def compare_crash_history(analysis, history):
    current_boot_id = (analysis.get("previous_boot") or {}).get("boot_id")
    previous = next(
        (item for item in history if item.get("previous_boot_id") != current_boot_id),
        None,
    )
    if previous is None:
        return None
    current_counts = analysis.get("counts") or {}
    previous_counts = previous.get("counts") or {}
    keys = (
        "undervoltage_started",
        "usb_overcurrent",
        "usb_error",
        "usb_reset",
        "usb_disconnected",
        "storage_io_error",
        "out_of_memory",
        "kernel_panic",
        "watchdog",
        "hung_task",
        "fatal_log",
    )
    return {
        "previous_boot_id": previous.get("previous_boot_id"),
        "previous_analyzed_at": previous.get("analyzed_at"),
        "previous_level": previous.get("level"),
        "previous_headline": previous.get("headline"),
        "level_changed": previous.get("level") != analysis.get("level"),
        "count_deltas": {
            key: int(current_counts.get(key, 0)) - int(previous_counts.get(key, 0))
            for key in keys
            if current_counts.get(key, 0) or previous_counts.get(key, 0)
        },
    }


def build_diagnosis(events, current):
    episodes = power_episodes(events)
    usb_events = [event for event in events if event["category"] == "usb"]
    usb_failures = [
        event
        for event in usb_events
        if event["kind"] in ("usb_error", "usb_controller_error", "usb_reset", "usb_disconnected")
    ]
    usb_overcurrents = [event for event in events if event["kind"] == "usb_overcurrent"]
    storage_errors = [event for event in events if event["category"] == "storage"]
    correlated = []
    for episode in episodes:
        nearby = [
            event
            for event in usb_events
            if abs(event["timestamp"] - episode["started_at"]) <= 15
        ]
        if nearby:
            correlated.append({"started_at": episode["started_at"], "usb_events": len(nearby)})
    uncorrelated = max(0, len(episodes) - len(correlated))
    throttle = (current or {}).get("throttle") or {}
    current_flags = set(throttle.get("current", ()))
    occurred_flags = set(throttle.get("occurred", ()))
    findings = []
    next_steps = []

    if episodes:
        level = "critical" if "under_voltage" in current_flags else "warning"
        headline = "Pi input undervoltage is confirmed"
        duration = sum(item["duration_seconds"] or 0 for item in episodes)
        findings.append(
            f"The kernel recorded {len(episodes)} undervoltage episode(s) totaling at least {duration:.1f} seconds in this range."
        )
        if "throttled" in occurred_flags:
            findings.append(
                "Firmware also reports that actual throttling occurred during this boot, so the voltage drops affected performance rather than being log noise."
            )
        if correlated:
            findings.append(
                f"{len(correlated)} undervoltage episode(s) occurred within 15 seconds of logged USB activity; hub inrush, back-powering, a downstream device, or total USB load is a plausible trigger."
            )
        if usb_overcurrents:
            findings.append(
                f"The kernel also reported {len(usb_overcurrents)} USB over-current change event(s), which is direct evidence of a USB power-path disturbance."
            )
        if uncorrelated:
            findings.append(
                f"{uncorrelated} episode(s) had no nearby logged USB activity. The evidence therefore does not isolate the powered hub; the Pi supply, cable, connector, and upstream 5 V path remain suspects."
            )
        next_steps.extend(
            (
                "After safely unmounting affected storage, reboot with the powered hub disconnected to clear the sticky firmware history and establish a baseline.",
                "Repeat with a known-good short Pi power cable/supply, then add the powered hub with no downstream devices and add devices one at a time.",
                "Treat USB errors without undervoltage as data-path/device evidence; treat undervoltage without USB activity as Pi input-power-path evidence.",
            )
        )
    elif current_flags:
        level = (
            "critical"
            if current_flags.intersection(("under_voltage", "throttled"))
            else "warning"
        )
        headline = "Firmware throttling or power limiting is active"
        findings.append(
            "The live firmware word reports active limiting: "
            + ", ".join(sorted(current_flags))
            + "."
        )
    elif "under_voltage" in occurred_flags or "throttled" in occurred_flags:
        level = "warning"
        headline = "Firmware has sticky power/throttle history"
        findings.append(
            "Firmware reports a power or throttle event earlier this boot, but no timestamped kernel undervoltage start is present in the selected range."
        )
    elif usb_failures:
        level = "warning"
        headline = "USB faults are present without confirmed undervoltage"
        findings.append(
            f"The kernel recorded {len(usb_failures)} USB reset, disconnect, or communication failure event(s)."
        )
        findings.append(
            "This pattern points more directly at a hub, cable, port, enclosure, or device data path, though it cannot rule out a brief power disturbance missed before monitoring began."
        )
    elif usb_overcurrents:
        level = "warning"
        headline = "USB over-current signaling was recorded"
        findings.append(
            f"The kernel reported {len(usb_overcurrents)} USB over-current change event(s), which keeps the hub, attached devices, and USB/Pi power path in scope."
        )
    else:
        level = "good" if current else "unknown"
        headline = "No power or USB fault evidence in this range" if current else "Monitor has no current sample"
        findings.append(
            "No timestamped undervoltage, USB reset/disconnect, or USB communication failure was found in the selected range."
        )

    if storage_errors:
        findings.append(
            f"There are also {len(storage_errors)} storage I/O error event(s); verify filesystem and device health before trusting affected disks."
        )
        level = "critical"
    if current_flags:
        findings.append("Active firmware flags: " + ", ".join(sorted(current_flags)) + ".")

    return {
        "level": level,
        "headline": headline,
        "findings": findings,
        "next_steps": next_steps,
        "evidence": {
            "undervoltage_episodes": len(episodes),
            "undervoltage_seconds": round(
                sum(item["duration_seconds"] or 0 for item in episodes), 2
            ),
            "undervoltage_near_usb": len(correlated),
            "undervoltage_without_usb": uncorrelated,
            "usb_failures": len(usb_failures),
            "usb_overcurrent_events": len(usb_overcurrents),
            "storage_errors": len(storage_errors),
            "episodes": episodes,
        },
    }


def build_report(store, hours=24, limit=100, now=None):
    now = store.clock() if now is None else now
    since = now - float(hours) * 3600
    event_rows = store.connection.execute(
        "SELECT * FROM events WHERE timestamp >= ? ORDER BY timestamp DESC LIMIT ?",
        (since, max(1, min(int(limit), 500))),
    ).fetchall()
    # Diagnosis and counts must use every event in range, independent of display limit.
    all_event_rows = store.connection.execute(
        """
        SELECT id, timestamp, boot_id, category, kind, severity, source, summary, message
        FROM events WHERE timestamp >= ? ORDER BY timestamp ASC
        """,
        (since,),
    ).fetchall()
    events = [event_public(row) for row in event_rows]
    all_events = [event_public(row, include_state=False) for row in all_event_rows]
    rollup_rows = store.connection.execute(
        "SELECT * FROM resource_rollups WHERE period_end >= ? ORDER BY period_end ASC",
        (since,),
    ).fetchall()
    # A rollup's JSON contains every metric. Decode it once rather than once per
    # peak, thermal zone, and process aggregation; this matters on the Pi for
    # 7- and 30-day reports.
    decoded_rollups = [
        (row, decode_row_json(row, "metrics_json") or {}) for row in rollup_rows
    ]
    current = store.get_meta("current")
    counts = collections.Counter(event["kind"] for event in all_events)
    severity = collections.Counter(event["severity"] for event in all_events)
    category = collections.Counter(event["category"] for event in all_events)
    peaks = {
        "cpu_percent": best_rollup_metric(decoded_rollups, "cpu"),
        "memory_percent": best_rollup_metric(decoded_rollups, "memory"),
        "swap_percent": best_rollup_metric(decoded_rollups, "swap"),
        "load1": best_rollup_metric(decoded_rollups, "load1"),
        "temperature_c": best_rollup_metric(decoded_rollups, "temperature"),
        "root_used_percent": best_rollup_metric(decoded_rollups, "root_used"),
        "minimum_arm_mhz": best_rollup_metric(decoded_rollups, "arm_mhz", prefer_min=True),
        "network_rx_bytes_per_second": best_rollup_metric(decoded_rollups, "network_rx"),
        "network_tx_bytes_per_second": best_rollup_metric(decoded_rollups, "network_tx"),
        "disk_read_bytes_per_second": best_rollup_metric(decoded_rollups, "disk_read"),
        "disk_write_bytes_per_second": best_rollup_metric(decoded_rollups, "disk_write"),
        "disk_busy_percent": best_rollup_metric(decoded_rollups, "disk_busy"),
    }
    current_age = None
    if isinstance(current, dict) and isinstance(current.get("timestamp"), (int, float)):
        current_age = max(0, now - current["timestamp"])
        current_candidates = {
            "cpu_percent": metric_value(current, ("cpu_percent",)),
            "memory_percent": metric_value(current, ("memory", "used_percent")),
            "swap_percent": metric_value(current, ("swap", "used_percent")),
            "load1": metric_value(current, ("load", "1m")),
            "temperature_c": metric_value(current, ("temperature_c",)),
            "root_used_percent": metric_value(current, ("root_filesystem", "used_percent")),
            "network_rx_bytes_per_second": metric_value(
                current, ("network_io", "rx_bytes_per_second")
            ),
            "network_tx_bytes_per_second": metric_value(
                current, ("network_io", "tx_bytes_per_second")
            ),
            "disk_read_bytes_per_second": metric_value(
                current, ("disk_io", "read_bytes_per_second")
            ),
            "disk_write_bytes_per_second": metric_value(
                current, ("disk_io", "write_bytes_per_second")
            ),
            "disk_busy_percent": metric_value(current, ("disk_io", "busy_percent")),
        }
        for key, value in current_candidates.items():
            if value is not None and (
                peaks[key]["value"] is None or value > peaks[key]["value"]
            ):
                peaks[key] = {"value": value, "at": current["timestamp"]}
                if key == "cpu_percent":
                    peaks[key]["top_process"] = (current.get("top_cpu") or [None])[0]
                if key == "memory_percent":
                    peaks[key]["top_process"] = (current.get("top_memory") or [None])[0]
                    peaks[key]["available_min_bytes"] = current.get("memory", {}).get(
                        "available_bytes"
                    )
                if key in (
                    "network_rx_bytes_per_second",
                    "network_tx_bytes_per_second",
                ):
                    interface_key = (
                        "rx_bytes_per_second"
                        if key == "network_rx_bytes_per_second"
                        else "tx_bytes_per_second"
                    )
                    interfaces = [
                        item
                        for item in current.get("network_io", {}).get("interfaces", ())
                        if item.get("physical") and item.get(interface_key) is not None
                    ]
                    peaks[key]["top_interface"] = (
                        max(interfaces, key=lambda item: item[interface_key])
                        if interfaces
                        else None
                    )
                if key in (
                    "disk_read_bytes_per_second",
                    "disk_write_bytes_per_second",
                    "disk_busy_percent",
                ):
                    device_key = {
                        "disk_read_bytes_per_second": "read_bytes_per_second",
                        "disk_write_bytes_per_second": "write_bytes_per_second",
                        "disk_busy_percent": "busy_percent",
                    }[key]
                    devices = [
                        item
                        for item in current.get("disk_io", {}).get("devices", ())
                        if item.get(device_key) is not None
                    ]
                    peaks[key]["top_device"] = (
                        max(devices, key=lambda item: item[device_key]) if devices else None
                    )

    peaks["thermal_sensors"] = best_thermal_sensor_metrics(decoded_rollups, current)
    legacy_temperature = peaks["temperature_c"]
    if peaks["thermal_sensors"] and legacy_temperature.get("value") is not None:
        primary_sensor = next(
            (
                sensor
                for sensor in peaks["thermal_sensors"]
                if "cpu" in sensor["type"].lower()
            ),
            peaks["thermal_sensors"][0],
        )
        if legacy_temperature["value"] > primary_sensor["value"]:
            primary_sensor.update(
                {
                    "value": legacy_temperature["value"],
                    "at": legacy_temperature.get("at"),
                    "average": legacy_temperature.get("average"),
                }
            )

    diagnosis = build_diagnosis(all_events, current)
    return {
        "ok": True,
        "version": REPORT_VERSION,
        "generated_at": now,
        "range": {"hours": float(hours), "since": since, "until": now},
        "status": {
            "available": current is not None,
            "stale": current_age is None or current_age > max(30, DEFAULT_SAMPLE_INTERVAL * 4),
            "sample_age_seconds": round(current_age, 2) if current_age is not None else None,
            "current": current,
        },
        "summary": {
            "events": len(all_events),
            "shown_events": len(events),
            "by_severity": dict(severity),
            "by_category": dict(category),
            "by_kind": dict(counts),
        },
        "peaks": peaks,
        "throttling": build_throttling_report(current or {}, counts, all_events),
        "processes": build_process_report(decoded_rollups, current),
        "diagnosis": diagnosis,
        "events": events,
    }


def format_bytes(value):
    if value is None:
        return "n/a"
    size = float(value)
    for suffix in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(size) < 1024 or suffix == "TiB":
            return f"{size:.1f} {suffix}"
        size /= 1024


def print_report(report):
    diagnosis = report["diagnosis"]
    print(f"{diagnosis['headline']} [{diagnosis['level']}]")
    for finding in diagnosis["findings"]:
        print(f"- {finding}")
    print("\nResource peaks")
    for label, key, suffix in (
        ("CPU", "cpu_percent", "%"),
        ("Memory", "memory_percent", "%"),
        ("Swap", "swap_percent", "%"),
        ("Load (1m)", "load1", ""),
        ("Temperature", "temperature_c", " C"),
        ("Root used", "root_used_percent", "%"),
        ("Minimum Arm clock", "minimum_arm_mhz", " MHz"),
        ("Disk busy", "disk_busy_percent", "%"),
    ):
        metric = report["peaks"][key]
        value = "n/a" if metric["value"] is None else f"{metric['value']}{suffix}"
        when = f" at {iso_time(metric['at'])}" if metric.get("at") else ""
        print(f"- {label}: {value}{when}")
    for label, key in (
        ("Network receive", "network_rx_bytes_per_second"),
        ("Network transmit", "network_tx_bytes_per_second"),
        ("Disk read", "disk_read_bytes_per_second"),
        ("Disk write", "disk_write_bytes_per_second"),
    ):
        metric = report["peaks"][key]
        value = "n/a" if metric["value"] is None else f"{format_bytes(metric['value'])}/s"
        when = f" at {iso_time(metric['at'])}" if metric.get("at") else ""
        print(f"- {label}: {value}{when}")
    print("\nRecent events")
    for event in report["events"][:25]:
        print(
            f"- {iso_time(event['timestamp'])} {event['severity'].upper()} "
            f"{event['summary']}: {event['message']}"
        )


def print_crash_report(report):
    analysis = report["analysis"]
    print(f"{analysis['headline']} [{analysis['level']}]")
    for finding in analysis.get("findings", []):
        print(f"- {finding}")
    comparison = report.get("comparison")
    if comparison:
        print("\nComparison with the preceding saved crash")
        print(
            f"- Previous assessment: {comparison.get('previous_headline')} "
            f"[{comparison.get('previous_level')}]"
        )
        for kind, delta in comparison.get("count_deltas", {}).items():
            print(f"- {kind}: {delta:+d}")
    print("\nRelevant previous-boot timeline")
    for item in analysis.get("timeline", [])[:40]:
        print(
            f"- {item.get('timestamp_iso', iso_time(item['timestamp']))} "
            f"{str(item.get('severity', 'info')).upper()} "
            f"{item.get('source', 'journal')}: {item.get('message', '')}"
        )


def list_events(store, hours, limit, category=None, severity=None, include_state=False):
    since = store.clock() - hours * 3600
    clauses = ["timestamp >= ?"]
    values = [since]
    if category:
        clauses.append("category = ?")
        values.append(category)
    if severity:
        clauses.append("severity = ?")
        values.append(severity)
    values.append(max(1, min(limit, 1000)))
    rows = store.connection.execute(
        f"SELECT * FROM events WHERE {' AND '.join(clauses)} ORDER BY timestamp DESC LIMIT ?",
        values,
    ).fetchall()
    return [event_public(row, include_state=include_state) for row in rows]


def positive_float(value):
    parsed = float(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return parsed


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", "--db", default=DEFAULT_DATABASE)
    subparsers = parser.add_subparsers(dest="command_name", required=True)

    run_parser = subparsers.add_parser("run", help="run the persistent monitor")
    run_parser.add_argument("--sample-interval", type=positive_float, default=DEFAULT_SAMPLE_INTERVAL)
    run_parser.add_argument("--rollup-interval", type=positive_float, default=DEFAULT_ROLLUP_INTERVAL)
    run_parser.add_argument("--retention-days", type=int, default=DEFAULT_RETENTION_DAYS)

    report_parser = subparsers.add_parser("report", help="analyze events and resource peaks")
    report_parser.add_argument("--hours", type=positive_float, default=24)
    report_parser.add_argument("--limit", type=int, default=100)
    report_parser.add_argument("--json", action="store_true")

    events_parser = subparsers.add_parser("events", help="list normalized events")
    events_parser.add_argument("--hours", type=positive_float, default=24)
    events_parser.add_argument("--limit", type=int, default=100)
    events_parser.add_argument("--category")
    events_parser.add_argument("--severity", choices=tuple(SEVERITY_RANK))
    events_parser.add_argument("--state", action="store_true", help="include captured system state")
    events_parser.add_argument("--json", action="store_true")

    crash_parser = subparsers.add_parser(
        "crash-report", help="analyze the journal from the preceding boot"
    )
    crash_parser.add_argument(
        "--save", action="store_true", help="save or update this boot analysis"
    )
    crash_parser.add_argument("--json", action="store_true")

    history_parser = subparsers.add_parser(
        "crash-history", help="list saved preceding-boot analyses"
    )
    history_parser.add_argument("--limit", type=int, default=20)
    history_parser.add_argument("--full", action="store_true")
    history_parser.add_argument("--json", action="store_true")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    writable = args.command_name == "run" or (
        args.command_name == "crash-report" and args.save
    )
    try:
        store = EventStore(args.database, read_only=not writable)
    except (OSError, sqlite3.Error) as error:
        print(f"system-event-monitor: could not open database: {error}", file=sys.stderr)
        return 1
    try:
        if args.command_name == "run":
            monitor = SystemEventMonitor(
                store,
                sample_interval=args.sample_interval,
                rollup_interval=args.rollup_interval,
                retention_days=args.retention_days,
            )

            def stop_monitor(_signum, _frame):
                monitor.stop()

            signal.signal(signal.SIGTERM, stop_monitor)
            signal.signal(signal.SIGINT, stop_monitor)
            monitor.run()
            return 0
        if args.command_name == "report":
            report = build_report(store, hours=args.hours, limit=args.limit)
            if args.json:
                print(json.dumps(report, indent=2, sort_keys=True))
            else:
                print_report(report)
            return 0
        if args.command_name == "crash-report":
            report = build_crash_report()
            report["saved"] = store.save_crash_analysis(report) if args.save else False
            history = store.crash_history(limit=20)
            report["history"] = history
            report["comparison"] = compare_crash_history(report["analysis"], history)
            if args.json:
                print(json.dumps(report, indent=2, sort_keys=True))
            else:
                print_crash_report(report)
            return 0
        if args.command_name == "crash-history":
            history = store.crash_history(limit=args.limit, full=args.full)
            payload = {"ok": True, "history": history}
            if args.json:
                print(json.dumps(payload, indent=2, sort_keys=True))
            else:
                for item in history:
                    print(
                        f"{iso_time(item['analyzed_at'])} "
                        f"{item['level'].upper():8} {item['headline']} "
                        f"[{item['previous_boot_id']}]"
                    )
            return 0
        if args.command_name == "events":
            events = list_events(
                store,
                args.hours,
                args.limit,
                category=args.category,
                severity=args.severity,
                include_state=args.state,
            )
            if args.json:
                print(json.dumps({"ok": True, "events": events}, indent=2, sort_keys=True))
            else:
                for event in events:
                    print(
                        f"{event['timestamp_iso']} {event['severity'].upper():8} "
                        f"{event['category']}/{event['kind']} {event['message']}"
                    )
            return 0
        return 2
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
