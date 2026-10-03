"""System event monitor daemon lifecycle."""

import collections
import json
import queue
import subprocess
import threading
import time

if __package__:
    from .common import (
        DEFAULT_SAMPLE_INTERVAL,
        DEFAULT_ROLLUP_INTERVAL,
        DEFAULT_RETENTION_DAYS,
        THROTTLE_FLAGS,
        utc_timestamp,
        event_fingerprint,
        normalize_boot_id,
    )
    from .probes import ResourceSampler, collect_usb_state, collect_mount_state, run_text
    from .journal import parse_journal_record
    from .rollups import RollupAccumulator, metric_value
else:
    from common import (
        DEFAULT_SAMPLE_INTERVAL,
        DEFAULT_ROLLUP_INTERVAL,
        DEFAULT_RETENTION_DAYS,
        THROTTLE_FLAGS,
        utc_timestamp,
        event_fingerprint,
        normalize_boot_id,
    )
    from probes import ResourceSampler, collect_usb_state, collect_mount_state, run_text
    from journal import parse_journal_record
    from rollups import RollupAccumulator, metric_value


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
