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

if __package__:
    from . import system_monitor
    from .system_monitor import (
        common,
        probes,
        journal,
        store,
        rollups,
        daemon,
        crash,
        report,
        cli,
    )
    from .system_monitor.common import (
        DEFAULT_DATABASE,
        DEFAULT_SAMPLE_INTERVAL,
        DEFAULT_ROLLUP_INTERVAL,
        DEFAULT_RETENTION_DAYS,
        DEFAULT_SAMPLE_RETENTION_HOURS,
        DEFAULT_CRASH_SAMPLE_LIMIT,
        DEFAULT_CRASH_REPORT_DIRECTORY,
        REPORT_VERSION,
        THROTTLE_FLAGS,
        SEVERITY_RANK,
        utc_timestamp,
        iso_time,
        json_dumps,
        normalize_boot_id,
        event_fingerprint,
    )
    from .system_monitor.store import EventStore, decode_row_json
    from .system_monitor.journal import (
        classify_kernel_message,
        parse_journal_record,
        redact_log_message,
        generic_journal_record,
        read_journal_records,
        journal_monotonic_seconds,
        journal_order_key,
    )
    from .system_monitor.probes import (
        read_text,
        read_number,
        parse_cpu_list,
        collect_thermal_sensors,
        collect_cpu_frequency_policies,
        run_text,
        parse_throttled,
        parse_meminfo,
        parse_pressure,
        collect_pressure,
        collect_vm_counters,
        collect_display_state,
        parse_cpu_stat,
        parse_network_counters,
        parse_disk_counters,
        counter_rate,
        calculate_network_io,
        calculate_disk_io,
        process_details,
        collect_network_counter_state,
        collect_block_labels,
        collect_disk_counter_state,
        collect_usb_state,
        collect_mount_state,
        ResourceSampler,
    )
else:
    import system_monitor
    from system_monitor import (
        common,
        probes,
        journal,
        store,
        rollups,
        daemon,
        crash,
        report,
        cli,
    )
    from system_monitor.common import (
        DEFAULT_DATABASE,
        DEFAULT_SAMPLE_INTERVAL,
        DEFAULT_ROLLUP_INTERVAL,
        DEFAULT_RETENTION_DAYS,
        DEFAULT_SAMPLE_RETENTION_HOURS,
        DEFAULT_CRASH_SAMPLE_LIMIT,
        DEFAULT_CRASH_REPORT_DIRECTORY,
        REPORT_VERSION,
        THROTTLE_FLAGS,
        SEVERITY_RANK,
        utc_timestamp,
        iso_time,
        json_dumps,
        normalize_boot_id,
        event_fingerprint,
    )
    from system_monitor.store import EventStore, decode_row_json
    from system_monitor.journal import (
        classify_kernel_message,
        parse_journal_record,
        redact_log_message,
        generic_journal_record,
        read_journal_records,
        journal_monotonic_seconds,
        journal_order_key,
    )
    from system_monitor.probes import (
        read_text,
        read_number,
        parse_cpu_list,
        collect_thermal_sensors,
        collect_cpu_frequency_policies,
        run_text,
        parse_throttled,
        parse_meminfo,
        parse_pressure,
        collect_pressure,
        collect_vm_counters,
        collect_display_state,
        parse_cpu_stat,
        parse_network_counters,
        parse_disk_counters,
        counter_rate,
        calculate_network_io,
        calculate_disk_io,
        process_details,
        collect_network_counter_state,
        collect_block_labels,
        collect_disk_counter_state,
        collect_usb_state,
        collect_mount_state,
        ResourceSampler,
    )


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
        same_boot = isinstance(previous, dict) and normalize_boot_id(
            previous.get("boot_id")
        ) == normalize_boot_id(boot_id)
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
        self.store.record_sample(sample)
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
        if boot_id and normalize_boot_id(boot_id) != normalize_boot_id(last_boot):
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


def read_pstore_directory(base, source):
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
        records.append(
            {
                "name": name,
                "source": source,
                "content": redact_log_message(content),
            }
        )
    return records


def read_pstore(sys_root="/sys"):
    return read_pstore_directory(os.path.join(sys_root, "fs", "pstore"), "pstore")


def read_pstore_archive(path="/var/lib/systemd/pstore"):
    return read_pstore_directory(path, "systemd-pstore archive")


def analyze_previous_boot(
    records,
    current_boot_started_at=None,
    pstore_records=None,
    previous_boot_id_hint=None,
):
    """Analyze already-filtered previous-boot journal records."""
    pstore_records = list(pstore_records or ())
    # Pi wall-clock timestamps can jump when NTP catches up after boot. The
    # journal's per-boot monotonic timestamp is authoritative for sequencing.
    ordered = sorted(records, key=journal_order_key)
    if not ordered:
        if pstore_records:
            return {
                "available": True,
                "level": "critical",
                "headline": "Persistent kernel crash evidence is available",
                "findings": [
                    f"The journal has no readable preceding-boot records, but pstore retained {len(pstore_records)} kernel crash record(s)."
                ],
                "previous_boot": {
                    "boot_id": normalize_boot_id(previous_boot_id_hint),
                    "started_at": None,
                    "ended_at": None,
                    "started_monotonic_seconds": None,
                    "ended_monotonic_seconds": None,
                    "duration_seconds": None,
                    "retained_log_started_at": None,
                    "retained_log_span_seconds": None,
                    "ended_cleanly": False,
                    "gap_to_current_boot_seconds": None,
                },
                "pstore": pstore_records,
                "timeline": [],
                "counts": {"pstore": len(pstore_records)},
            }
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

    boot_id = normalize_boot_id(
        next((item.get("boot_id") for item in ordered if item.get("boot_id")), None)
        or previous_boot_id_hint
    )
    started_at = ordered[0]["timestamp"]
    ended_at = ordered[-1]["timestamp"]
    started_monotonic = journal_monotonic_seconds(ordered[0])
    ended_monotonic = journal_monotonic_seconds(ordered[-1])
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
    timeline = sorted(unique.values(), key=journal_order_key, reverse=True)[:80]
    if started_monotonic is not None and ended_monotonic is not None:
        duration_seconds = max(0, ended_monotonic - started_monotonic)
    else:
        duration_seconds = max(0, ended_at - started_at)
    return {
        "available": True,
        "level": level,
        "headline": headline,
        "findings": findings,
        "previous_boot": {
            "boot_id": boot_id,
            "started_at": started_at,
            "ended_at": ended_at,
            "started_monotonic_seconds": started_monotonic,
            "ended_monotonic_seconds": ended_monotonic,
            "duration_seconds": round(duration_seconds, 2),
            "retained_log_started_at": started_at,
            "retained_log_span_seconds": round(max(0, ended_at - started_at), 2),
            "ended_cleanly": ended_cleanly,
            "gap_to_current_boot_seconds": round(gap, 2) if gap is not None else None,
        },
        "pstore": pstore_records,
        "timeline": timeline,
        "counts": dict(counts),
    }


def build_resource_evidence(samples):
    if not samples:
        return {"available": False, "sample_count": 0, "tail": [], "peaks": {}}
    metric_paths = {
        "cpu_percent": (("cpu_percent",), False),
        "memory_percent": (("memory", "used_percent"), False),
        "swap_percent": (("swap", "used_percent"), False),
        "load1": (("load", "1m"), False),
        "temperature_c": (("temperature_c",), False),
        "minimum_arm_mhz": (("arm_mhz",), True),
        "disk_busy_percent": (("disk_io", "busy_percent"), False),
        "network_rx_bytes_per_second": (("network_io", "rx_bytes_per_second"), False),
        "network_tx_bytes_per_second": (("network_io", "tx_bytes_per_second"), False),
        "disk_read_bytes_per_second": (("disk_io", "read_bytes_per_second"), False),
        "disk_write_bytes_per_second": (("disk_io", "write_bytes_per_second"), False),
    }
    peaks = {}
    for name, (path, prefer_min) in metric_paths.items():
        candidates = [
            (metric_value(sample, path), sample)
            for sample in samples
            if metric_value(sample, path) is not None
        ]
        if not candidates:
            peaks[name] = {"value": None, "at": None, "uptime_seconds": None}
            continue
        value, sample = (min if prefer_min else max)(
            candidates, key=lambda pair: pair[0]
        )
        peaks[name] = {
            "value": value,
            "at": sample.get("timestamp"),
            "uptime_seconds": sample.get("uptime_seconds"),
        }
        if name == "cpu_percent":
            peaks[name]["top_process"] = (sample.get("top_cpu") or [None])[0]
        elif name == "memory_percent":
            peaks[name]["top_process"] = (sample.get("top_memory") or [None])[0]
    first_uptime = samples[0].get("uptime_seconds")
    last_uptime = samples[-1].get("uptime_seconds")
    span = (
        max(0, last_uptime - first_uptime)
        if isinstance(first_uptime, (int, float))
        and isinstance(last_uptime, (int, float))
        else None
    )
    return {
        "available": True,
        "sample_count": len(samples),
        "boot_id": normalize_boot_id(samples[-1].get("boot_id")),
        "first_uptime_seconds": first_uptime,
        "last_uptime_seconds": last_uptime,
        "span_seconds": round(span, 2) if span is not None else None,
        "peaks": peaks,
        "last_sample": samples[-1],
        "tail": samples,
    }


def build_crash_report(
    store=None,
    clock=utc_timestamp,
    general_lines=4000,
    kernel_lines=2000,
    claim_pstore=False,
):
    general = read_journal_records(boot=-1, kernel=False, lines=general_lines)
    kernel = read_journal_records(boot=-1, kernel=True, lines=kernel_lines)
    unique = {}
    for item in general + kernel:
        key = (item.get("boot_id"), item.get("monotonic"), item["message"])
        unique[key] = item
    uptime_raw = (read_text("/proc/uptime", "") or "").split()
    try:
        current_boot_started_at = clock() - float(uptime_raw[0])
    except (IndexError, ValueError):
        current_boot_started_at = None
    journal_records = list(unique.values())
    analysis = analyze_previous_boot(journal_records, current_boot_started_at, [])
    previous_boot_id = (analysis.get("previous_boot") or {}).get("boot_id")
    live_pstore = read_pstore() + read_pstore_archive()
    if not previous_boot_id and store and live_pstore:
        previous_boot_id = normalize_boot_id(store.get_meta("last_boot_id"))
    if store and previous_boot_id and claim_pstore:
        store.claim_pstore_records(previous_boot_id, live_pstore)
    assigned_pstore = store.pstore_for_boot(previous_boot_id) if store else []
    # With no store (library/diagnostic use), report the files directly. With a
    # store, only attach records permanently claimed to this boot so an old
    # ramoops archive is never misattributed to every later crash.
    pstore_records = assigned_pstore if store else live_pstore
    if pstore_records:
        analysis = analyze_previous_boot(
            journal_records,
            current_boot_started_at,
            pstore_records,
            previous_boot_id_hint=previous_boot_id,
        )
    samples = store.flight_samples(previous_boot_id) if store and previous_boot_id else []
    resource_evidence = build_resource_evidence(samples)
    analysis["resource_evidence"] = resource_evidence
    if resource_evidence["available"]:
        span = resource_evidence.get("span_seconds")
        span_text = f" spanning {span / 60:.1f} minutes" if span is not None else ""
        analysis["findings"].append(
            f"The durable flight recorder retained {len(samples)} detailed sample(s){span_text} from the end of that boot."
        )
    return {
        "ok": True,
        "version": REPORT_VERSION,
        "generated_at": clock(),
        "current_boot_id": read_text("/proc/sys/kernel/random/boot_id"),
        "analysis": analysis,
    }


def write_crash_report_file(report, output_directory=DEFAULT_CRASH_REPORT_DIRECTORY):
    previous_boot_id = normalize_boot_id(
        ((report.get("analysis") or {}).get("previous_boot") or {}).get("boot_id")
    )
    if not previous_boot_id:
        return None
    os.makedirs(output_directory, mode=0o750, exist_ok=True)
    destination = os.path.join(output_directory, f"boot-{previous_boot_id}.json")
    temporary = os.path.join(
        output_directory, f".boot-{previous_boot_id}.{os.getpid()}.tmp"
    )
    try:
        with open(temporary, "w", encoding="utf-8") as handle:
            json.dump(report, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o640)
        os.replace(temporary, destination)
        directory_fd = os.open(output_directory, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        try:
            os.unlink(temporary)
        except OSError:
            pass
    return destination


def capture_previous_boot(store, output_directory=DEFAULT_CRASH_REPORT_DIRECTORY):
    """Run once near startup and persist the preceding boot's evidence."""
    report = build_crash_report(store=store, claim_pstore=True)
    history = store.crash_history(limit=20)
    report["comparison"] = compare_crash_history(report["analysis"], history)
    report["saved"] = store.save_crash_analysis(report)
    report["report_path"] = write_crash_report_file(report, output_directory)

    current_sample = ResourceSampler().sample()
    store.record_sample(current_sample)
    boot_id = current_sample.get("boot_id")
    previous_boot_id = (
        (report.get("analysis") or {}).get("previous_boot") or {}
    ).get("boot_id")
    store.insert_event(
        timestamp=current_sample["timestamp"],
        boot_id=boot_id,
        category="system",
        kind="boot_crash_evidence_captured",
        severity="info",
        source="boot-hook",
        summary="Previous-boot crash evidence captured",
        message=f"previous_boot_id={previous_boot_id or 'unavailable'}",
        fingerprint=event_fingerprint("boot-crash-capture", boot_id),
        state={
            **current_sample,
            "capture": "boot_hook",
            "usb_devices": collect_usb_state(),
            "mounts": collect_mount_state(),
            "previous_boot_id": previous_boot_id,
            "previous_boot_level": (report.get("analysis") or {}).get("level"),
        },
    )
    return report


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
    current_resource = (analysis.get("resource_evidence") or {}).get("peaks") or {}
    previous_resource = previous.get("resource_peaks") or {}
    resource_deltas = {}
    for key in sorted(set(current_resource) | set(previous_resource)):
        current_value = (current_resource.get(key) or {}).get("value")
        previous_value = (previous_resource.get(key) or {}).get("value")
        if isinstance(current_value, (int, float)) and isinstance(
            previous_value, (int, float)
        ):
            resource_deltas[key] = round(current_value - previous_value, 2)
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
        "resource_peak_deltas": resource_deltas,
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
    return diagnosis_from_evidence(
        current, episodes, correlated, len(usb_failures), len(usb_overcurrents), len(storage_errors)
    )


def diagnosis_from_evidence(current, episodes, correlated, usb_failures, usb_overcurrents, storage_errors):
    """One diagnosis policy shared by in-memory and indexed database evidence."""
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
                f"The kernel also reported {usb_overcurrents} USB over-current change event(s), which is direct evidence of a USB power-path disturbance."
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
            f"The kernel recorded {usb_failures} USB reset, disconnect, or communication failure event(s)."
        )
        findings.append(
            "This pattern points more directly at a hub, cable, port, enclosure, or device data path, though it cannot rule out a brief power disturbance missed before monitoring began."
        )
    elif usb_overcurrents:
        level = "warning"
        headline = "USB over-current signaling was recorded"
        findings.append(
            f"The kernel reported {usb_overcurrents} USB over-current change event(s), which keeps the hub, attached devices, and USB/Pi power path in scope."
        )
    else:
        level = "good" if current else "unknown"
        headline = "No power or USB fault evidence in this range" if current else "Monitor has no current sample"
        findings.append(
            "No timestamped undervoltage, USB reset/disconnect, or USB communication failure was found in the selected range."
        )

    if storage_errors:
        findings.append(
            f"There are also {storage_errors} storage I/O error event(s); verify filesystem and device health before trusting affected disks."
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
            "usb_failures": usb_failures,
            "usb_overcurrent_events": usb_overcurrents,
            "storage_errors": storage_errors,
            "episodes": episodes,
        },
    }


def report_event_evidence(store, since):
    """Count the full range using a covering index; fetch only power transitions.

    Event rows contain large state snapshots. Reading all those table pages for
    a USB storm used to dominate every dashboard refresh, even with LIMIT 100.
    """
    grouped = store.grouped_event_counts(since)
    counts, severities, categories = (collections.Counter() for _ in range(3))
    latest = {}
    usb_failures = 0
    for row in grouped:
        kind, severity, category, count, last_seen = row
        counts[kind] += count
        severities[severity] += count
        categories[category] += count
        latest[kind] = max(latest.get(kind, last_seen), last_seen)
        if category == "usb" and kind in (
            "usb_error", "usb_controller_error", "usb_reset", "usb_disconnected"
        ):
            usb_failures += count
    power_events = store.power_transition_rows(since)
    episodes = power_episodes(power_events)
    correlated = []
    for episode in episodes:
        nearby = store.usb_count_between(
            max(since, episode["started_at"] - 15), episode["started_at"] + 15,
        )[0]
        if nearby:
            correlated.append({"started_at": episode["started_at"], "usb_events": nearby})
    return counts, severities, categories, latest, episodes, correlated, usb_failures


def build_report(store, hours=24, limit=100, now=None):
    now = store.clock() if now is None else now
    since = now - float(hours) * 3600
    event_rows = store.report_event_rows(since, limit)
    # Counts and diagnosis still cover every event, independent of display limit.
    counts, severity, category, latest, episodes, correlated, usb_failures = report_event_evidence(store, since)
    events = [event_public(row) for row in event_rows]
    rollup_rows = store.report_rollup_rows(since)
    # A rollup's JSON contains every metric. Decode it once rather than once per
    # peak, thermal zone, and process aggregation; this matters on the Pi for
    # 7- and 30-day reports.
    decoded_rollups = [
        (row, decode_row_json(row, "metrics_json") or {}) for row in rollup_rows
    ]
    current = store.get_meta("current")
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

    diagnosis = diagnosis_from_evidence(
        current, episodes, correlated, usb_failures,
        counts.get("usb_overcurrent", 0), category.get("storage", 0),
    )
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
            "events": sum(counts.values()),
            "shown_events": len(events),
            "by_severity": dict(severity),
            "by_category": dict(category),
            "by_kind": dict(counts),
        },
        "peaks": peaks,
        "throttling": build_throttling_report(
            current or {}, counts,
            [{"kind": kind, "timestamp": timestamp} for kind, timestamp in latest.items()],
        ),
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
    rows = store.filtered_event_rows(since, limit, category, severity)
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

    capture_parser = subparsers.add_parser(
        "boot-capture",
        help="persist previous-boot logs and final flight-recorder samples",
    )
    capture_parser.add_argument(
        "--output-directory", default=DEFAULT_CRASH_REPORT_DIRECTORY
    )
    capture_parser.add_argument("--json", action="store_true")

    history_parser = subparsers.add_parser(
        "crash-history", help="list saved preceding-boot analyses"
    )
    history_parser.add_argument("--limit", type=int, default=20)
    history_parser.add_argument("--full", action="store_true")
    history_parser.add_argument("--json", action="store_true")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    writable = args.command_name in ("run", "boot-capture") or (
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
        if args.command_name == "boot-capture":
            report = capture_previous_boot(store, args.output_directory)
            if args.json:
                print(json.dumps(report, indent=2, sort_keys=True))
            else:
                analysis = report["analysis"]
                destination = report.get("report_path") or "no report file"
                print(
                    f"{analysis['headline']} [{analysis['level']}]; "
                    f"saved={report.get('saved', False)}; {destination}"
                )
            return 0
        if args.command_name == "crash-report":
            report = build_crash_report(
                store=store, claim_pstore=bool(args.save and not store.read_only)
            )
            history_before = store.crash_history(limit=20)
            report["comparison"] = compare_crash_history(
                report["analysis"], history_before
            )
            report["saved"] = store.save_crash_analysis(report) if args.save else False
            report["history"] = store.crash_history(limit=20)
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
