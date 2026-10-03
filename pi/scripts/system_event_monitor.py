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
    from .system_monitor.daemon import SystemEventMonitor
    from .system_monitor.crash import (
        read_pstore_directory,
        read_pstore,
        read_pstore_archive,
        analyze_previous_boot,
        build_resource_evidence,
        build_crash_report,
        write_crash_report_file,
        capture_previous_boot,
        compare_crash_history,
    )
    from .system_monitor.rollups import metric_value, RollupAccumulator
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
    from system_monitor.daemon import SystemEventMonitor
    from system_monitor.crash import (
        read_pstore_directory,
        read_pstore,
        read_pstore_archive,
        analyze_previous_boot,
        build_resource_evidence,
        build_crash_report,
        write_crash_report_file,
        capture_previous_boot,
        compare_crash_history,
    )
    from system_monitor.rollups import metric_value, RollupAccumulator
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
