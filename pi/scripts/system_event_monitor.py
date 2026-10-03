#!/usr/bin/env python3
"""Persistent Raspberry Pi power, USB, kernel, and resource monitor.

The daemon combines two sources of evidence:

* firmware and /proc sampling catches current throttling state and resource peaks;
* the kernel journal supplies timestamped power, USB, storage, OOM, and fault events.

Only passive interfaces are used.  The monitor never resets USB, changes a clock,
or changes power state.  Data is stored in SQLite for the van dashboard and the
``report``/``events`` analysis commands in this file.
"""

import argparse as argparse
import collections as collections
import datetime as dt
import hashlib as hashlib
import json as json
import math as math
import os as os
import queue as queue
import re as re
import signal as signal
import sqlite3 as sqlite3
import statistics as statistics
import subprocess as subprocess
import sys as sys
import threading as threading
import time as time

if __package__:
    from .system_monitor.common import (
        DEFAULT_DATABASE as DEFAULT_DATABASE,
        DEFAULT_SAMPLE_INTERVAL as DEFAULT_SAMPLE_INTERVAL,
        DEFAULT_ROLLUP_INTERVAL as DEFAULT_ROLLUP_INTERVAL,
        DEFAULT_RETENTION_DAYS as DEFAULT_RETENTION_DAYS,
        DEFAULT_SAMPLE_RETENTION_HOURS as DEFAULT_SAMPLE_RETENTION_HOURS,
        DEFAULT_CRASH_SAMPLE_LIMIT as DEFAULT_CRASH_SAMPLE_LIMIT,
        DEFAULT_CRASH_REPORT_DIRECTORY as DEFAULT_CRASH_REPORT_DIRECTORY,
        REPORT_VERSION as REPORT_VERSION,
        THROTTLE_FLAGS as THROTTLE_FLAGS,
        SEVERITY_RANK as SEVERITY_RANK,
        utc_timestamp as utc_timestamp,
        iso_time as iso_time,
        json_dumps as json_dumps,
        normalize_boot_id as normalize_boot_id,
        event_fingerprint as event_fingerprint,
    )
    from .system_monitor.store import EventStore as EventStore, decode_row_json as decode_row_json
    from .system_monitor.probes import (
        read_text as read_text,
        read_number as read_number,
        parse_cpu_list as parse_cpu_list,
        collect_thermal_sensors as collect_thermal_sensors,
        collect_cpu_frequency_policies as collect_cpu_frequency_policies,
        run_text as run_text,
        parse_throttled as parse_throttled,
        parse_meminfo as parse_meminfo,
        parse_pressure as parse_pressure,
        collect_pressure as collect_pressure,
        collect_vm_counters as collect_vm_counters,
        collect_display_state as collect_display_state,
        parse_cpu_stat as parse_cpu_stat,
        parse_network_counters as parse_network_counters,
        parse_disk_counters as parse_disk_counters,
        counter_rate as counter_rate,
        calculate_network_io as calculate_network_io,
        calculate_disk_io as calculate_disk_io,
        process_details as process_details,
        collect_network_counter_state as collect_network_counter_state,
        collect_block_labels as collect_block_labels,
        collect_disk_counter_state as collect_disk_counter_state,
        collect_usb_state as collect_usb_state,
        collect_mount_state as collect_mount_state,
        ResourceSampler as ResourceSampler,
    )
    from .system_monitor.journal import (
        classify_kernel_message as classify_kernel_message,
        parse_journal_record as parse_journal_record,
        redact_log_message as redact_log_message,
        generic_journal_record as generic_journal_record,
        read_journal_records as read_journal_records,
        journal_monotonic_seconds as journal_monotonic_seconds,
        journal_order_key as journal_order_key,
    )
    from .system_monitor.rollups import metric_value as metric_value, RollupAccumulator as RollupAccumulator
    from .system_monitor.daemon import SystemEventMonitor as SystemEventMonitor
    from .system_monitor.crash import (
        read_pstore_directory as read_pstore_directory,
        read_pstore as read_pstore,
        read_pstore_archive as read_pstore_archive,
        analyze_previous_boot as analyze_previous_boot,
        build_resource_evidence as build_resource_evidence,
        build_crash_report as build_crash_report,
        write_crash_report_file as write_crash_report_file,
        capture_previous_boot as capture_previous_boot,
        compare_crash_history as compare_crash_history,
    )
    from .system_monitor.report import (
        event_public as event_public,
        rollup_row_and_metrics as rollup_row_and_metrics,
        best_rollup_metric as best_rollup_metric,
        best_thermal_sensor_metrics as best_thermal_sensor_metrics,
        build_process_report as build_process_report,
        build_throttling_report as build_throttling_report,
        power_episodes as power_episodes,
        build_diagnosis as build_diagnosis,
        diagnosis_from_evidence as diagnosis_from_evidence,
        report_event_evidence as report_event_evidence,
        build_report as build_report,
        list_events as list_events,
    )
    from .system_monitor.cli import (
        format_bytes as format_bytes,
        print_report as print_report,
        print_crash_report as print_crash_report,
        positive_float as positive_float,
        build_parser as build_parser,
        main as main,
    )
else:
    from system_monitor.common import (
        DEFAULT_DATABASE as DEFAULT_DATABASE,
        DEFAULT_SAMPLE_INTERVAL as DEFAULT_SAMPLE_INTERVAL,
        DEFAULT_ROLLUP_INTERVAL as DEFAULT_ROLLUP_INTERVAL,
        DEFAULT_RETENTION_DAYS as DEFAULT_RETENTION_DAYS,
        DEFAULT_SAMPLE_RETENTION_HOURS as DEFAULT_SAMPLE_RETENTION_HOURS,
        DEFAULT_CRASH_SAMPLE_LIMIT as DEFAULT_CRASH_SAMPLE_LIMIT,
        DEFAULT_CRASH_REPORT_DIRECTORY as DEFAULT_CRASH_REPORT_DIRECTORY,
        REPORT_VERSION as REPORT_VERSION,
        THROTTLE_FLAGS as THROTTLE_FLAGS,
        SEVERITY_RANK as SEVERITY_RANK,
        utc_timestamp as utc_timestamp,
        iso_time as iso_time,
        json_dumps as json_dumps,
        normalize_boot_id as normalize_boot_id,
        event_fingerprint as event_fingerprint,
    )
    from system_monitor.store import EventStore as EventStore, decode_row_json as decode_row_json
    from system_monitor.probes import (
        read_text as read_text,
        read_number as read_number,
        parse_cpu_list as parse_cpu_list,
        collect_thermal_sensors as collect_thermal_sensors,
        collect_cpu_frequency_policies as collect_cpu_frequency_policies,
        run_text as run_text,
        parse_throttled as parse_throttled,
        parse_meminfo as parse_meminfo,
        parse_pressure as parse_pressure,
        collect_pressure as collect_pressure,
        collect_vm_counters as collect_vm_counters,
        collect_display_state as collect_display_state,
        parse_cpu_stat as parse_cpu_stat,
        parse_network_counters as parse_network_counters,
        parse_disk_counters as parse_disk_counters,
        counter_rate as counter_rate,
        calculate_network_io as calculate_network_io,
        calculate_disk_io as calculate_disk_io,
        process_details as process_details,
        collect_network_counter_state as collect_network_counter_state,
        collect_block_labels as collect_block_labels,
        collect_disk_counter_state as collect_disk_counter_state,
        collect_usb_state as collect_usb_state,
        collect_mount_state as collect_mount_state,
        ResourceSampler as ResourceSampler,
    )
    from system_monitor.journal import (
        classify_kernel_message as classify_kernel_message,
        parse_journal_record as parse_journal_record,
        redact_log_message as redact_log_message,
        generic_journal_record as generic_journal_record,
        read_journal_records as read_journal_records,
        journal_monotonic_seconds as journal_monotonic_seconds,
        journal_order_key as journal_order_key,
    )
    from system_monitor.rollups import metric_value as metric_value, RollupAccumulator as RollupAccumulator
    from system_monitor.daemon import SystemEventMonitor as SystemEventMonitor
    from system_monitor.crash import (
        read_pstore_directory as read_pstore_directory,
        read_pstore as read_pstore,
        read_pstore_archive as read_pstore_archive,
        analyze_previous_boot as analyze_previous_boot,
        build_resource_evidence as build_resource_evidence,
        build_crash_report as build_crash_report,
        write_crash_report_file as write_crash_report_file,
        capture_previous_boot as capture_previous_boot,
        compare_crash_history as compare_crash_history,
    )
    from system_monitor.report import (
        event_public as event_public,
        rollup_row_and_metrics as rollup_row_and_metrics,
        best_rollup_metric as best_rollup_metric,
        best_thermal_sensor_metrics as best_thermal_sensor_metrics,
        build_process_report as build_process_report,
        build_throttling_report as build_throttling_report,
        power_episodes as power_episodes,
        build_diagnosis as build_diagnosis,
        diagnosis_from_evidence as diagnosis_from_evidence,
        report_event_evidence as report_event_evidence,
        build_report as build_report,
        list_events as list_events,
    )
    from system_monitor.cli import (
        format_bytes as format_bytes,
        print_report as print_report,
        print_crash_report as print_crash_report,
        positive_float as positive_float,
        build_parser as build_parser,
        main as main,
    )


if __name__ == "__main__":
    raise SystemExit(main())
