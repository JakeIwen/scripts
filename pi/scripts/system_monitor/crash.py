"""Pstore, previous-boot analysis, and crash capture."""

import collections
import json
import os

if __package__:
    from .common import (
        DEFAULT_CRASH_REPORT_DIRECTORY,
        REPORT_VERSION,
        SEVERITY_RANK,
        normalize_boot_id,
        utc_timestamp,
        event_fingerprint,
    )
    from .journal import (
        classify_kernel_message,
        redact_log_message,
        read_journal_records,
        journal_monotonic_seconds,
        journal_order_key,
    )
    from .probes import read_text, ResourceSampler, collect_usb_state, collect_mount_state
    from .rollups import metric_value
else:
    from common import (
        DEFAULT_CRASH_REPORT_DIRECTORY,
        REPORT_VERSION,
        SEVERITY_RANK,
        normalize_boot_id,
        utc_timestamp,
        event_fingerprint,
    )
    from journal import (
        classify_kernel_message,
        redact_log_message,
        read_journal_records,
        journal_monotonic_seconds,
        journal_order_key,
    )
    from probes import read_text, ResourceSampler, collect_usb_state, collect_mount_state
    from rollups import metric_value


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


def _analysis_without_journal(pstore_records, previous_boot_id_hint):
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


def _classify_previous_boot(ordered):
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
    return ended_cleanly, classified, counts


def _previous_boot_findings(ended_cleanly, pstore_records, counts, gap):
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
    return level, headline, findings


def _previous_boot_timeline(ordered, classified):
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
    return timeline


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
        return _analysis_without_journal(pstore_records, previous_boot_id_hint)

    ended_cleanly, classified, counts = _classify_previous_boot(ordered)

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
    level, headline, findings = _previous_boot_findings(
        ended_cleanly, pstore_records, counts, gap
    )

    timeline = _previous_boot_timeline(ordered, classified)
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
