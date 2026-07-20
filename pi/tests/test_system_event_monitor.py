import json
import os
import tempfile
import unittest
from unittest import mock

from pi.scripts import system_event_monitor as monitor


class ThrottleDecodeTests(unittest.TestCase):
    def test_decodes_current_and_sticky_firmware_flags(self):
        self.assertEqual(
            monitor.parse_throttled("throttled=0x50000"),
            {
                "raw": 0x50000,
                "hex": "0x50000",
                "current": [],
                "occurred": ["under_voltage", "throttled"],
            },
        )
        decoded = monitor.parse_throttled(0xF000F)
        self.assertEqual(
            decoded["current"],
            ["under_voltage", "frequency_capped", "throttled", "soft_temperature_limit"],
        )
        self.assertEqual(decoded["occurred"], decoded["current"])
        self.assertIsNone(monitor.parse_throttled("not available"))


class KernelClassificationTests(unittest.TestCase):
    def test_classifies_power_usb_storage_and_kernel_faults(self):
        cases = {
            "hwmon hwmon2: Undervoltage detected!": ("power", "undervoltage_started"),
            "hwmon hwmon2: Voltage normalised": ("power", "undervoltage_cleared"),
            "usb 1-1.2-port2: unable to enumerate USB device": ("usb", "usb_error"),
            "usb 2-2: USB disconnect, device number 4": ("usb", "usb_disconnected"),
            "Buffer I/O error on dev sda1": ("storage", "storage_io_error"),
            "WARNING: CPU: 3 PID: 905 at drivers/gpu/test.c:4 test": (
                "kernel",
                "kernel_warning",
            ),
        }
        for message, expected in cases.items():
            with self.subTest(message=message):
                classified = monitor.classify_kernel_message(message)
                self.assertEqual(classified[:2], expected)
        self.assertIsNone(monitor.classify_kernel_message("ordinary kernel message"))

    def test_journal_event_has_stable_boot_monotonic_fingerprint(self):
        record = {
            "MESSAGE": "hwmon hwmon2: Undervoltage detected!",
            "__REALTIME_TIMESTAMP": "1700000000250000",
            "__MONOTONIC_TIMESTAMP": "123456",
            "_BOOT_ID": "boot-one",
        }
        first = monitor.parse_journal_record(record)
        second = monitor.parse_journal_record(dict(record))
        self.assertEqual(first["timestamp"], 1700000000.25)
        self.assertEqual(first["fingerprint"], second["fingerprint"])
        self.assertEqual(first["severity"], "critical")


class IoMetricTests(unittest.TestCase):
    def test_parses_and_rates_network_without_counting_virtual_interface_twice(self):
        previous = monitor.parse_network_counters(
            """eth0: 1000 10 0 1 0 0 0 0 500 5 0 0 0 0 0 0
tailscale0: 200 2 0 0 0 0 0 0 300 3 0 0 0 0 0 0"""
        )
        current = monitor.parse_network_counters(
            """eth0: 1500 14 0 1 0 0 0 0 700 7 0 0 0 0 0 0
tailscale0: 1000 8 0 0 0 0 0 0 1300 9 0 0 0 0 0 0"""
        )
        result = monitor.calculate_network_io(
            current, previous, elapsed=2, physical_names={"eth0"}
        )
        self.assertEqual(result["rx_bytes_per_second"], 250)
        self.assertEqual(result["tx_bytes_per_second"], 100)
        self.assertEqual([item["name"] for item in result["interfaces"]], ["eth0", "tailscale0"])
        self.assertFalse(result["interfaces"][1]["physical"])

    def test_parses_whole_disk_throughput_iops_and_busy_time(self):
        previous = monitor.parse_disk_counters(
            "8 0 sda 10 0 100 50 20 0 200 70 0 300 400"
        )["sda"]
        current = monitor.parse_disk_counters(
            "8 0 sda 14 0 104 60 26 0 208 90 1 800 900"
        )["sda"]
        previous.update({"sector_size": 512, "labels": ["movingparts"]})
        current.update({"sector_size": 512, "labels": ["movingparts"]})
        result = monitor.calculate_disk_io(
            {"sda": current}, {"sda": previous}, elapsed=2
        )
        self.assertEqual(result["read_bytes_per_second"], 1024)
        self.assertEqual(result["write_bytes_per_second"], 2048)
        self.assertEqual(result["read_iops"], 2)
        self.assertEqual(result["write_iops"], 3)
        self.assertEqual(result["busy_percent"], 25)
        self.assertEqual(result["devices"][0]["labels"], ["movingparts"])


class CrashAnalysisTests(unittest.TestCase):
    @staticmethod
    def record(timestamp, message, **overrides):
        record = {
            "timestamp": timestamp,
            "timestamp_iso": monitor.iso_time(timestamp),
            "boot_id": "crashed-boot",
            "monotonic": str(int(timestamp * 1_000_000)),
            "priority": 6,
            "source": "kernel",
            "message": message,
            "pid1": False,
            "transport": "kernel",
        }
        record.update(overrides)
        return record

    def test_recognizes_a_clean_previous_boot(self):
        records = [
            self.record(100, "Linux version test"),
            self.record(
                200,
                "systemd-shutdown: Syncing filesystems and block devices.",
                source="systemd-shutdown",
                pid1=True,
            ),
        ]
        analysis = monitor.analyze_previous_boot(records, current_boot_started_at=205)
        self.assertTrue(analysis["available"])
        self.assertTrue(analysis["previous_boot"]["ended_cleanly"])
        self.assertEqual(analysis["level"], "good")

    def test_correlates_power_usb_storage_and_kernel_crash_evidence(self):
        records = [
            self.record(100, "Linux version test"),
            self.record(180, "hwmon hwmon2: Undervoltage detected!"),
            self.record(181, "usb 2-2: USB disconnect, device number 4"),
            self.record(182, "Buffer I/O error on dev sda1"),
            self.record(183, "Kernel panic - not syncing: fatal exception", priority=0),
        ]
        analysis = monitor.analyze_previous_boot(
            records,
            current_boot_started_at=190,
            pstore_records=[{"name": "dmesg-ramoops-0", "content": "panic"}],
        )
        self.assertEqual(analysis["level"], "critical")
        self.assertFalse(analysis["previous_boot"]["ended_cleanly"])
        self.assertEqual(analysis["counts"]["undervoltage_started"], 1)
        self.assertEqual(analysis["counts"]["usb_disconnected"], 1)
        self.assertEqual(analysis["counts"]["storage_io_error"], 1)
        self.assertEqual(analysis["counts"]["kernel_panic"], 1)
        self.assertTrue(analysis["timeline"])

    def test_redacts_urls_and_secrets_from_saved_log_text(self):
        redacted = monitor.redact_log_message(
            "request https://private.example/path token=do-not-store password:secret"
        )
        self.assertNotIn("private.example", redacted)
        self.assertNotIn("do-not-store", redacted)
        self.assertNotIn("password:secret", redacted)

    def test_uses_monotonic_journal_time_when_wall_clock_jumps(self):
        records = [
            self.record(500, "Linux version test", monotonic="1000000"),
            self.record(100, "final kernel message", monotonic="11000000"),
        ]
        analysis = monitor.analyze_previous_boot(records)
        self.assertEqual(analysis["previous_boot"]["duration_seconds"], 10)
        self.assertEqual(analysis["previous_boot"]["started_at"], 500)
        self.assertEqual(analysis["previous_boot"]["ended_at"], 100)


class CrashHistoryStoreTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.store = monitor.EventStore(os.path.join(self.tempdir.name, "events.sqlite3"))

    def tearDown(self):
        self.store.close()
        self.tempdir.cleanup()

    @staticmethod
    def report(boot_id, analyzed_at, headline="Abrupt restart", count=1):
        return {
            "ok": True,
            "generated_at": analyzed_at,
            "analysis": {
                "available": True,
                "level": "warning",
                "headline": headline,
                "findings": [headline],
                "counts": {"undervoltage_started": count},
                "timeline": [{"timestamp": analyzed_at - 1, "message": headline}],
                "pstore": [],
                "previous_boot": {
                    "boot_id": boot_id,
                    "started_at": analyzed_at - 100,
                    "ended_at": analyzed_at - 10,
                },
            },
        }

    def test_saved_analysis_is_upserted_per_boot_and_full_report_is_retained(self):
        self.assertTrue(self.store.save_crash_analysis(self.report("boot-one", 200)))
        self.assertTrue(
            self.store.save_crash_analysis(
                self.report("boot-one", 210, headline="Updated analysis", count=2)
            )
        )
        history = self.store.crash_history(full=True)
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["headline"], "Updated analysis")
        self.assertEqual(history[0]["counts"]["undervoltage_started"], 2)
        self.assertEqual(
            history[0]["report"]["analysis"]["timeline"][0]["message"],
            "Updated analysis",
        )

    def test_compares_current_analysis_with_a_different_saved_boot(self):
        previous = self.report("boot-old", 100, count=3)
        self.store.save_crash_analysis(previous)
        current = self.report("boot-new", 200, count=1)["analysis"]
        comparison = monitor.compare_crash_history(current, self.store.crash_history())
        self.assertEqual(comparison["previous_boot_id"], "boot-old")
        self.assertEqual(comparison["count_deltas"]["undervoltage_started"], -2)


class StoreAndDiagnosisTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.now = 1_700_100_000.0
        self.database = os.path.join(self.tempdir.name, "events.sqlite3")
        self.store = monitor.EventStore(self.database, clock=lambda: self.now)

    def tearDown(self):
        self.store.close()
        self.tempdir.cleanup()

    def insert(self, timestamp, kind, category="power", severity="warning"):
        return self.store.insert_event(
            timestamp=timestamp,
            boot_id="boot-one",
            category=category,
            kind=kind,
            severity=severity,
            source="kernel",
            summary=kind.replace("_", " "),
            message=kind,
            fingerprint=monitor.event_fingerprint(timestamp, kind),
            state={"capture": "test"},
        )

    def test_report_correlates_some_but_not_all_undervoltage_with_usb(self):
        # One episode overlaps hub enumeration; the second has no nearby USB event.
        self.insert(self.now - 500, "undervoltage_started", severity="critical")
        self.insert(self.now - 496, "usb_connected", category="usb")
        self.insert(self.now - 495, "usb_error", category="usb")
        self.insert(self.now - 494, "usb_overcurrent", category="power", severity="critical")
        self.insert(self.now - 480, "undervoltage_cleared", severity="info")
        self.insert(self.now - 200, "undervoltage_started", severity="critical")
        self.insert(self.now - 194, "undervoltage_cleared", severity="info")
        self.store.set_meta(
            "current",
            {
                "timestamp": self.now - 1,
                "cpu_percent": 12.5,
                "memory": {"used_percent": 40, "available_bytes": 1_000_000},
                "swap": {"used_percent": 0},
                "load": {"1m": 0.2},
                "temperature_c": 55,
                "arm_mhz": 1500,
                "root_filesystem": {"used_percent": 42},
                "network_io": {
                    "rx_bytes_per_second": 1024,
                    "tx_bytes_per_second": 512,
                    "interfaces": [],
                },
                "disk_io": {
                    "read_bytes_per_second": 2048,
                    "write_bytes_per_second": 4096,
                    "busy_percent": 8,
                    "devices": [],
                },
                "top_cpu": [],
                "top_memory": [],
                "throttle": {
                    "hex": "0x50000",
                    "current": [],
                    "occurred": ["under_voltage", "throttled"],
                },
            },
        )
        report = monitor.build_report(self.store, hours=1, now=self.now)
        evidence = report["diagnosis"]["evidence"]
        self.assertEqual(report["diagnosis"]["headline"], "Pi input undervoltage is confirmed")
        self.assertEqual(evidence["undervoltage_episodes"], 2)
        self.assertEqual(evidence["undervoltage_near_usb"], 1)
        self.assertEqual(evidence["undervoltage_without_usb"], 1)
        self.assertEqual(evidence["usb_failures"], 1)
        self.assertEqual(evidence["usb_overcurrent_events"], 1)
        self.assertIn("actual throttling occurred", " ".join(report["diagnosis"]["findings"]))
        self.assertIn("over-current", " ".join(report["diagnosis"]["findings"]))
        self.assertFalse(report["status"]["stale"])
        self.assertEqual(report["peaks"]["network_rx_bytes_per_second"]["value"], 1024)
        self.assertEqual(report["peaks"]["disk_write_bytes_per_second"]["value"], 4096)

    def test_duplicate_journal_event_is_ignored(self):
        self.assertTrue(self.insert(self.now - 10, "undervoltage_started"))
        self.assertFalse(self.insert(self.now - 10, "undervoltage_started"))
        count = self.store.connection.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        self.assertEqual(count, 1)

    def test_read_only_store_can_generate_report_without_writing(self):
        self.store.set_meta("current", {"timestamp": self.now, "throttle": {}})
        readonly = monitor.EventStore(self.database, clock=lambda: self.now, read_only=True)
        try:
            report = monitor.build_report(readonly, hours=24, now=self.now)
        finally:
            readonly.close()
        self.assertTrue(report["ok"])
        self.assertTrue(report["status"]["available"])

    def test_flight_samples_are_durable_ordered_and_pruned_separately(self):
        old = {
            "timestamp": self.now - 72 * 3600,
            "uptime_seconds": 10,
            "boot_id": "01234567-89ab-cdef-0123-456789abcdef",
            "cpu_percent": 1,
        }
        recent = {
            "timestamp": self.now - 5,
            "uptime_seconds": 20,
            "boot_id": "0123456789abcdef0123456789abcdef",
            "cpu_percent": 2,
        }
        self.store.record_sample(old)
        self.store.record_sample(recent)
        samples = self.store.flight_samples(recent["boot_id"])
        self.assertEqual([item["cpu_percent"] for item in samples], [1, 2])
        self.assertEqual(samples[-1]["boot_id"], recent["boot_id"])
        self.assertEqual(self.store.connection.execute("PRAGMA synchronous").fetchone()[0], 2)
        self.store.prune(sample_retention_hours=5 / 3600)
        samples = self.store.flight_samples(recent["boot_id"])
        self.assertEqual([item["cpu_percent"] for item in samples], [2])


class FlightRecorderTests(unittest.TestCase):
    def test_pressure_parser_and_resource_summary_keep_crash_precursors(self):
        pressure = monitor.parse_pressure(
            "some avg10=1.25 avg60=0.50 avg300=0.10 total=12345\n"
            "full avg10=0.25 avg60=0.10 avg300=0.01 total=99"
        )
        self.assertEqual(pressure["some"]["avg10"], 1.25)
        self.assertEqual(pressure["full"]["total"], 99)
        samples = [
            {
                "timestamp": 100,
                "uptime_seconds": 50,
                "boot_id": "boot-one",
                "cpu_percent": 10,
                "memory": {"used_percent": 40},
                "top_cpu": [{"name": "idle"}],
                "top_memory": [{"name": "idle"}],
                "disk_io": {"busy_percent": 2},
                "network_io": {},
                "load": {"1m": 1},
                "swap": {"used_percent": 0},
                "temperature_c": 50,
                "arm_mhz": 1500,
            },
            {
                "timestamp": 110,
                "uptime_seconds": 60,
                "boot_id": "boot-one",
                "cpu_percent": 95,
                "memory": {"used_percent": 94},
                "top_cpu": [{"name": "worker"}],
                "top_memory": [{"name": "worker"}],
                "disk_io": {"busy_percent": 99},
                "network_io": {},
                "load": {"1m": 9},
                "swap": {"used_percent": 20},
                "temperature_c": 70,
                "arm_mhz": 600,
            },
        ]
        evidence = monitor.build_resource_evidence(samples)
        self.assertEqual(evidence["span_seconds"], 10)
        self.assertEqual(evidence["peaks"]["cpu_percent"]["value"], 95)
        self.assertEqual(
            evidence["peaks"]["cpu_percent"]["top_process"]["name"], "worker"
        )
        self.assertEqual(evidence["peaks"]["minimum_arm_mhz"]["value"], 600)

    def test_boot_capture_saves_database_and_atomic_json_copy(self):
        with tempfile.TemporaryDirectory() as tempdir:
            store = monitor.EventStore(os.path.join(tempdir, "events.sqlite3"))
            report = {
                "ok": True,
                "version": 1,
                "generated_at": 200,
                "current_boot_id": "boot-current",
                "analysis": {
                    "available": True,
                    "level": "warning",
                    "headline": "Abrupt restart",
                    "findings": [],
                    "previous_boot": {
                        "boot_id": "boot-old",
                        "started_at": 100,
                        "ended_at": 190,
                    },
                    "timeline": [],
                    "counts": {},
                    "pstore": [],
                    "resource_evidence": {
                        "available": False,
                        "sample_count": 0,
                        "tail": [],
                        "peaks": {},
                    },
                },
            }
            current_sample = {
                "timestamp": 201,
                "uptime_seconds": 5,
                "boot_id": "boot-current",
                "cpu_percent": None,
            }
            sampler = mock.Mock()
            sampler.sample.return_value = current_sample
            output = os.path.join(tempdir, "reports")
            with mock.patch.object(monitor, "build_crash_report", return_value=report), mock.patch.object(
                monitor, "ResourceSampler", return_value=sampler
            ), mock.patch.object(monitor, "collect_usb_state", return_value=[]), mock.patch.object(
                monitor, "collect_mount_state", return_value=[]
            ):
                captured = monitor.capture_previous_boot(store, output)
            self.assertTrue(captured["saved"])
            self.assertTrue(os.path.isfile(captured["report_path"]))
            with open(captured["report_path"], encoding="utf-8") as handle:
                on_disk = json.load(handle)
            self.assertEqual(on_disk["analysis"]["previous_boot"]["boot_id"], "boot-old")
            self.assertEqual(len(store.crash_history()), 1)
            kinds = [
                row[0]
                for row in store.connection.execute("SELECT kind FROM events").fetchall()
            ]
            self.assertEqual(kinds, ["boot_crash_evidence_captured"])
            store.close()


class RollupTests(unittest.TestCase):
    @staticmethod
    def sample(timestamp, cpu, memory, temperature, process):
        return {
            "timestamp": timestamp,
            "boot_id": "boot-one",
            "cpu_percent": cpu,
            "memory": {"used_percent": memory, "available_bytes": 1000 - memory},
            "swap": {"used_percent": 0},
            "load": {"1m": cpu / 25},
            "temperature_c": temperature,
            "root_filesystem": {"used_percent": 45},
            "arm_mhz": 1500 - cpu,
            "network_io": {
                "rx_bytes_per_second": cpu * 100,
                "tx_bytes_per_second": cpu * 50,
                "interfaces": [
                    {
                        "name": "eth0",
                        "physical": True,
                        "rx_bytes_per_second": cpu * 100,
                        "tx_bytes_per_second": cpu * 50,
                    }
                ],
            },
            "disk_io": {
                "read_bytes_per_second": cpu * 200,
                "write_bytes_per_second": cpu * 300,
                "busy_percent": cpu,
                "devices": [
                    {
                        "name": "sda",
                        "labels": ["movingparts"],
                        "read_bytes_per_second": cpu * 200,
                        "write_bytes_per_second": cpu * 300,
                        "busy_percent": cpu,
                    }
                ],
            },
            "top_cpu": [{"name": process, "cpu_percent": cpu, "rss_bytes": 10}],
            "top_memory": [{"name": process, "rss_bytes": memory * 100}],
        }

    def test_preserves_peak_value_time_and_process(self):
        accumulator = monitor.RollupAccumulator(interval=60)
        accumulator.add(self.sample(100, 10, 30, 50, "idle"))
        accumulator.add(self.sample(160, 92, 55, 67, "worker"))
        self.assertTrue(accumulator.ready(160))
        rollup = accumulator.flush()
        self.assertEqual(rollup["cpu_peak"], 92)
        self.assertEqual(rollup["metrics"]["cpu"]["at"], 160)
        self.assertEqual(rollup["metrics"]["cpu"]["top_process"]["name"], "worker")
        self.assertEqual(rollup["temperature_peak"], 67)
        self.assertEqual(rollup["arm_mhz_min"], 1408)
        self.assertEqual(rollup["metrics"]["network_rx"]["peak"], 9200)
        self.assertEqual(rollup["metrics"]["network_rx"]["top_interface"]["name"], "eth0")
        self.assertEqual(rollup["metrics"]["disk_write"]["peak"], 27600)
        self.assertEqual(rollup["metrics"]["disk_busy"]["top_device"]["name"], "sda")


class FirmwareTransitionTests(unittest.TestCase):
    def test_sticky_flags_are_logged_once_per_boot(self):
        with tempfile.TemporaryDirectory() as tempdir:
            store = monitor.EventStore(os.path.join(tempdir, "events.sqlite3"))
            service = monitor.SystemEventMonitor(store)
            sample = {
                "timestamp": 1000,
                "boot_id": "boot-one",
                "throttle": monitor.parse_throttled(0x50000),
            }
            service.current = sample
            service.context_cache = {"usb_devices": [], "mounts": [], "failed_units": []}
            service.reconcile_firmware(sample)
            service.reconcile_firmware(sample)
            kinds = [
                row[0]
                for row in store.connection.execute(
                    "SELECT kind FROM events ORDER BY kind"
                ).fetchall()
            ]
            store.close()
        self.assertEqual(
            kinds,
            ["firmware_throttled_occurred", "firmware_under_voltage_occurred"],
        )


if __name__ == "__main__":
    unittest.main()
