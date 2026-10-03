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
    from .system_monitor.report import (
        event_public,
        rollup_row_and_metrics,
        best_rollup_metric,
        best_thermal_sensor_metrics,
        build_process_report,
        build_throttling_report,
        power_episodes,
        build_diagnosis,
        diagnosis_from_evidence,
        report_event_evidence,
        build_report,
        list_events,
    )
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
    from system_monitor.report import (
        event_public,
        rollup_row_and_metrics,
        best_rollup_metric,
        best_thermal_sensor_metrics,
        build_process_report,
        build_throttling_report,
        power_episodes,
        build_diagnosis,
        diagnosis_from_evidence,
        report_event_evidence,
        build_report,
        list_events,
    )
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
