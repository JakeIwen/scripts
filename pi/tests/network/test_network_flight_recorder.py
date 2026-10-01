"""Independent recorder acceptance cases.

All generated records below are explicitly synthetic. The separate observed
fixture preserves redacted real lines and original file/line provenance. Expected
counts and intervals are specified here from the scenario, not computed by the
correlator being tested. No test sends probes or modifies a live device.
"""

import datetime as dt
import gzip
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock
import contextlib
import importlib.util
import io
from types import SimpleNamespace

from pi.scripts.network_recorder import Store, export_incident, parse_syslog, report
from pi.scripts.network_recorder.parsers import parse_ubnt
from pi.scripts.network_recorder.parsers import observation
from pi.scripts.network_recorder.collector import Collector, FileImporter
from pi.scripts.network_recorder import collector as collector_module


BASE = dt.datetime(2026, 9, 29, 10, tzinfo=dt.timezone.utc).timestamp()
FIXTURES = Path(__file__).with_name("fixtures") / "network_flight_recorder"


def log_line(second, tag, message):
    stamp = dt.datetime.fromtimestamp(BASE + second, dt.timezone.utc).isoformat()
    return f"{stamp} 192.168.6.1 OpenWrt {tag}: {message}"


def path_message(gateway=1, public=2):
    return (
        f"state-change state=gateway-{gateway}-public-{public} "
        f"device=wl1-sta0 gateway=172.20.10.1 gateway_ok={gateway} "
        f"public1=1.1.1.1:{int(public > 0)} public2=208.67.222.222:{int(public > 1)}"
    )


def https_message(uplink="clientwan", state="online", google="204/0", cloudflare="204/0"):
    return f"interface={uplink} state={state} google={google} cloudflare={cloudflare}"


class RecorderAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = os.path.join(self.directory.name, "history.sqlite3")
        self.store = Store(self.path)
        self.addCleanup(lambda: self.store.close())
        self.offset = 0

    def event(self, second, tag, message, **changes):
        text = log_line(second, tag, message)
        value = parse_syslog(
            text,
            imported_at=BASE + 3600,
            provenance={"file": "synthetic.log", "offset": self.offset, "generation": "fixture-1"},
            backfill=True,
        )
        self.offset += len(text.encode()) + 1
        self.assertIsNotNone(value, text)
        value.update(changes)
        return value

    def ingest(self, *rows):
        return self.store.ingest([self.event(*row) for row in rows])

    def report(self, **kwargs):
        options = {"start": BASE - 1, "end": BASE + 600, "now": BASE + 60}
        options.update(kwargs)
        return report(self.path, **options)

    def test_existing_receipt_time_is_not_import_time_or_device_source_time(self):
        line = "2026-09-17T00:14:28.272559-06:00 192.168.6.1 OpenWrt clientwan-path: " + path_message()
        event = parse_syslog(line, imported_at=BASE, provenance={"file": "old.gz", "offset": 42, "generation": "archive"}, backfill=True)
        expected = dt.datetime(2026, 9, 17, 6, 14, 28, 272559, tzinfo=dt.timezone.utc).timestamp()
        self.assertEqual(event["received_at"], expected)
        self.assertEqual(event["time"], expected)
        self.assertEqual(event["imported_at"], BASE)
        self.assertIsNone(event["source_time"])
        self.assertIn("receiver", event["time_quality"])
        self.assertEqual(event["provenance"]["offset"], 42)
        self.assertEqual(event["provenance"]["receipt_time_raw"], "2026-09-17T00:14:28.272559-06:00")

    def test_receipt_timezone_metadata_does_not_change_replay_identity(self):
        event = self.event(10, "clientwan-path", path_message(public=0))
        original = dict(event, provenance=dict(event["provenance"]))
        original["provenance"].pop("receipt_time_raw")
        self.assertEqual(self.store.ingest([original]), 1)
        self.assertEqual(self.store.ingest([event]), 0)
        self.assertEqual(self.report()["counts"], {"events": 1, "incidents": 1})

    def test_structured_rfc3164_clock_corrections_do_not_replace_receipt_order(self):
        # Clearly synthetic: device wall clock is wrong, then corrected forward,
        # then backward. RFC3164 supplies neither year nor timezone itself.
        source_stamps = ("2019-07-01T00:00:00+00:00", "2026-09-29T11:00:00+00:00", "2026-09-29T09:00:00+00:00")
        parsed = []
        for second, source_stamp in zip((10, 20, 30), source_stamps):
            receipt = dt.datetime.fromtimestamp(BASE + second, dt.timezone.utc).isoformat()
            raw = {"schema_version": "1", "received_at": receipt,
                   "reported_at": source_stamp, "protocol_version": "0",
                   "source_ip": "192.168.6.1", "hostname": "OpenWrt",
                   "tag": "clientwan-path:", "message": " " + path_message()}
            event = parse_syslog(json.dumps(raw), imported_at=BASE + 3600,
                                 provenance={"file": "structured.jsonl", "offset": second, "generation": "fixture"}, backfill=True)
            self.assertIsNotNone(event)
            self.assertEqual(event["received_at"], BASE + second)
            self.assertEqual(event["time"], BASE + second)
            self.assertEqual(event["source_time"], dt.datetime.fromisoformat(source_stamp).timestamp())
            self.assertEqual(event["provenance"]["receipt_time_raw"], receipt)
            self.assertNotEqual(event["time_quality"], "exact")
            parsed.append(event)
        self.store.ingest(parsed)
        self.assertEqual(self.report()["counts"]["incidents"], 0)

    def test_ubnt_uses_current_boot_uptime_anchor_and_preserves_uncertainty(self):
        # An antenna's 2019 wall clock must not date an event that its uptime
        # puts 20 seconds before a verified current collector observation.
        event = parse_ubnt("uptime=80 connection failed at 2019-07-01 00:00:00",
                           boot_id="antenna-boot-one", uptime=100, received_at=BASE,
                           imported_at=BASE + 3600, provenance={"sequence": 1}, uncertainty=5)
        self.assertEqual(event["time"], BASE - 20)
        self.assertEqual(event["monotonic"], 80)
        self.assertEqual(event["boot_id"], "antenna-boot-one")
        self.assertEqual(event["received_at"], BASE)
        self.assertEqual(event["imported_at"], BASE + 3600)
        self.assertIsNone(event["source_time"])
        self.assertEqual(event["provenance"]["uncertainty_seconds"], 5)
        self.assertIsNone(parse_ubnt("uptime=101 connection failed", boot_id="new-boot",
                                   uptime=100, received_at=BASE, provenance={"sequence": 2}))

    def test_observed_redacted_samples_preserve_receipt_provenance(self):
        fixture = json.loads((FIXTURES / "observed_log_samples.json").read_text())
        self.assertEqual(fixture["fixture_kind"], "observed_redacted")
        for sample in fixture["records"]:
            with self.subTest(line=sample["line"]):
                event = parse_syslog(sample["text"], imported_at=BASE,
                                     provenance={"file": sample["path"], "offset": sample["line"], "generation": "observed"}, backfill=True)
                self.assertIsNotNone(event)
                self.assertIsNone(event["source_time"])
                self.assertLess(event["time"], BASE)
                self.assertEqual(event["device"], "OpenWrt")

    def test_resolver_browsing_records_are_not_retained(self):
        for text in (
            "query[A] ordinary-browsing.example from 192.168.6.23",
            "reply ordinary-browsing.example is 203.0.113.2",
            "cached ordinary-browsing.example is 203.0.113.2",
        ):
            with self.subTest(text=text):
                event = parse_syslog(log_line(10, "dnsmasq[1]", text), imported_at=BASE + 3600,
                                     provenance={"file": "synthetic.log", "offset": 1, "generation": "fixture"}, backfill=True)
                self.assertIsNone(event)

    def test_malformed_structured_records_do_not_crash_ingestion(self):
        base = {"received_at": "2026-09-29T10:00:00Z", "hostname": "OpenWrt",
                "tag": "clientwan-path:", "message": path_message()}
        for changes in ({"tag": 123}, {"message": []}, {"received_at": "not-a-date"}, {"hostname": {"invalid": True}}):
            with self.subTest(changes=changes):
                self.assertIsNone(parse_syslog(json.dumps(dict(base, **changes)), imported_at=BASE,
                                              provenance={"file": "bad.jsonl", "offset": 1, "generation": "fixture"}, backfill=True))

    def test_gateway_reachable_external_tests_fail_and_short_recovery(self):
        self.ingest((0, "clientwan-path", path_message()),
                    (10, "clientwan-path", path_message(public=0)),
                    (15, "clientwan-path", path_message()))
        data = self.report()
        self.assertEqual(data["counts"], {"events": 3, "incidents": 1})
        incident = data["incidents"][0]
        self.assertEqual(incident["domains"], ["upstream-tests"])
        self.assertEqual(incident["uplinks"], ["clientwan"])
        self.assertEqual((incident["onset"], incident["end"]), (BASE + 10, BASE + 15))
        self.assertEqual(incident["duration_seconds"], 5)
        self.assertEqual(incident["status"], "recovered")
        self.assertNotIn("provider is down", json.dumps(incident).lower())
        detail = export_incident(self.path, incident["id"], now=BASE + 60)
        self.assertEqual(detail["schema_version"], 1)
        self.assertTrue(detail["events"])
        self.assertIn("time_semantics", detail)
        self.assertIn("coverage", detail)
        self.assertTrue(detail["limitations"])
        self.assertTrue(all(event["provenance"] for event in detail["events"]))

    def test_gateway_failure_is_not_local_client_authentication_failure(self):
        self.ingest((10, "clientwan-path", path_message(gateway=0, public=0)),
                    (11, "hostapd", "wl0-ap0: STA 02:00:00:00:00:01 SAE authentication failed"),
                    (20, "clientwan-path", path_message()))
        data = self.report()
        self.assertEqual(data["counts"]["events"], 3)
        domains = {domain for incident in data["incidents"] for domain in incident["domains"]}
        self.assertEqual(domains, {"gateway-hotspot", "local-wifi"})
        self.assertEqual(data["counts"]["incidents"], 2)
        gateway = next(i for i in data["incidents"] if "gateway-hotspot" in i["domains"])
        self.assertEqual(gateway["status"], "recovered")
        wifi = next(i for i in data["incidents"] if "local-wifi" in i["domains"])
        self.assertNotEqual(wifi["status"], "recovered")

    def test_dns_error_is_distinct_from_ambiguous_https_timeout(self):
        self.ingest((10, "uplink-https", https_message(state="offline", google="000/6", cloudflare="000/6")),
                    (20, "uplink-https", https_message()),
                    (30, "uplink-https", https_message(state="offline", google="000/28", cloudflare="000/28")),
                    (40, "uplink-https", https_message()))
        data = self.report()
        self.assertEqual(data["counts"], {"events": 4, "incidents": 2})
        self.assertEqual({tuple(i["domains"]) for i in data["incidents"]}, {("dns",), ("https",)})
        dns = next(i for i in data["incidents"] if "dns" in i["domains"])
        self.assertIn("shared", json.dumps(dns).lower() + json.dumps(data["limitations"]).lower())
        self.assertEqual({i["duration_seconds"] for i in data["incidents"]}, {10})

    def test_overlapping_uplinks_preserve_separate_incidents_and_failover_evidence(self):
        self.ingest((10, "uplink-https", https_message(state="offline", google="000/28", cloudflare="000/28")),
                    (12, "mwan3-hotplug[42]", "Connection tracking flushed for interface 'clientwan' on action 'disconnected'"),
                    (15, "uplink-https", https_message("wan", "offline", "000/28", "000/28")),
                    (20, "uplink-https", https_message()),
                    (40, "uplink-https", https_message("wan")))
        data = self.report()
        self.assertEqual(data["counts"]["events"], 5)
        https = [i for i in data["incidents"] if "https" in i["domains"]]
        self.assertEqual(len(https), 2)
        durations = {tuple(i["uplinks"]): i["duration_seconds"] for i in https}
        self.assertEqual(durations, {("clientwan",): 10, ("wan",): 25})
        self.assertTrue(any("Connection tracking flushed" in e["message"] for e in data["events"]))

    def test_identical_text_at_distinct_offsets_is_not_deduplicated(self):
        first = self.event(10, "clientwan-path", path_message(public=0))
        second = self.event(10, "clientwan-path", path_message(public=0))
        self.assertEqual(self.store.ingest([first, second]), 2)
        self.assertEqual(self.store.ingest([first, second]), 0)
        self.assertEqual(self.report()["counts"], {"events": 2, "incidents": 1})

    def test_store_restart_preserves_ids_checkpoints_and_replay_counts(self):
        events = [self.event(10, "clientwan-path", path_message(public=0)),
                  self.event(20, "clientwan-path", path_message())]
        self.store.ingest(events, checkpoints={"fixture-reader": {"offset": 321, "generation": "one"}})
        before = self.report()
        self.store.close()
        self.store = Store(self.path)
        self.assertEqual(self.store.checkpoint("fixture-reader"), {"offset": 321, "generation": "one"})
        self.assertEqual(self.store.ingest(events), 0)
        after = self.report()
        self.assertEqual(after["counts"], before["counts"])
        self.assertEqual([i["id"] for i in after["incidents"]], [i["id"] for i in before["incidents"]])
        self.assertEqual([e["id"] for e in after["events"]], [e["id"] for e in before["events"]])

    def test_abrupt_collector_exit_recovers_committed_batch_and_incidents(self):
        self.store.close()
        line = log_line(10, "clientwan-path", path_message(public=0))
        script = """
import os, sys
from pi.scripts.network_recorder import Store, parse_syslog
store = Store(sys.argv[1])
events = [parse_syslog(sys.argv[2], imported_at=1790679600,
    provenance={'generation':'crash-fixture','offset':i}, backfill=True) for i in range(400)]
def stop_after_durable_batch():
    os._exit(23)
store._bound_storage = stop_after_durable_batch
store.ingest(events)
"""
        crashed = subprocess.run([sys.executable, "-c", script, self.path, line],
                                 cwd=str(Path(__file__).resolve().parents[3]),
                                 capture_output=True, text=True, timeout=5, check=False)
        self.assertEqual(crashed.returncode, 23, crashed.stderr)
        self.store = Store(self.path)
        self.assertEqual(self.report()["counts"], {"events": 250, "incidents": 1})
        events = [parse_syslog(line, imported_at=BASE + 3600,
                              provenance={"generation": "crash-fixture", "offset": index}, backfill=True)
                  for index in range(400)]
        self.assertEqual(self.store.ingest(events), 150)
        self.assertEqual(self.report()["counts"], {"events": 400, "incidents": 1})

    def test_partial_write_restart_rotation_gzip_and_replay_preserve_counts(self):
        path = Path(self.directory.name) / "dendelion.log"
        failure = log_line(10, "clientwan-path", path_message(public=0))
        recovery = log_line(20, "clientwan-path", path_message())
        # Two identical complete records are legitimate separate observations;
        # the last incomplete record must remain unacknowledged until newline.
        path.write_text(failure + "\n" + failure + "\n" + recovery)
        first = FileImporter(self.store).import_file(path)
        self.assertEqual(first["inserted"], 2)
        self.assertFalse(first["complete"])
        self.assertEqual(FileImporter(self.store).import_file(path)["inserted"], 0)
        self.store.close()
        self.store = Store(self.path)
        with path.open("a") as handle:
            handle.write("\n")
        self.assertEqual(FileImporter(self.store).import_file(path)["inserted"], 1)
        archive = path.with_name("dendelion.log.1")
        path.rename(archive)
        compressed = archive.with_suffix(".1.gz")
        compressed.write_bytes(gzip.compress(archive.read_bytes()))
        self.assertEqual(FileImporter(self.store).import_file(archive)["inserted"], 0)
        self.assertEqual(FileImporter(self.store).import_file(compressed)["inserted"], 0)
        self.assertEqual(FileImporter(self.store).import_file(compressed)["inserted"], 0)
        path.write_text(log_line(30, "clientwan-path", path_message(public=0)) + "\n" +
                        log_line(40, "clientwan-path", path_message()) + "\n")
        self.assertEqual(FileImporter(self.store).import_file(path)["inserted"], 2)
        data = self.report()
        self.assertEqual(data["counts"], {"events": 5, "incidents": 2})
        self.assertEqual({i["duration_seconds"] for i in data["incidents"]}, {10})

    def test_cli_import_returns_when_final_line_is_incomplete(self):
        path = Path(self.directory.name) / "partial.log"
        path.write_text(log_line(10, "clientwan-path", path_message(public=0)) + "\n" +
                        log_line(20, "clientwan-path", path_message()))
        cli = Path(__file__).resolve().parents[2] / "scripts" / "network_flight_recorder.py"
        result = subprocess.run([sys.executable, str(cli), "--database", self.path, "import", str(path)],
                                capture_output=True, text=True, timeout=5, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.report()["counts"], {"events": 1, "incidents": 1})

    def test_cli_report_and_export_work_with_all_socket_connections_blocked(self):
        self.ingest((10, "clientwan-path", path_message(public=0)),
                    (20, "clientwan-path", path_message()))
        blocker = Path(self.directory.name) / "sitecustomize.py"
        blocker.write_text("import socket\ndef blocked(*args, **kwargs):\n    raise OSError('fixture: network blocked')\nsocket.create_connection = blocked\nsocket.getaddrinfo = blocked\nsocket.socket.connect = blocked\n")
        env = dict(os.environ, PYTHONPATH=self.directory.name)
        cli = Path(__file__).resolve().parents[2] / "scripts" / "network_flight_recorder.py"
        prefix = [sys.executable, str(cli), "--database", self.path]
        interval = ["--start", str(BASE), "--end", str(BASE + 60)]
        structured = subprocess.run(prefix + ["report", "--json"] + interval,
                                    env=env, capture_output=True, text=True, timeout=5, check=False)
        self.assertEqual(structured.returncode, 0, structured.stderr)
        data = json.loads(structured.stdout)
        self.assertEqual(data["counts"], {"events": 2, "incidents": 1})
        readable = subprocess.run(prefix + ["report"] + interval,
                                 env=env, capture_output=True, text=True, timeout=5, check=False)
        self.assertEqual(readable.returncode, 0, readable.stderr)
        self.assertIn("Observation:", readable.stdout)
        self.assertIn("Hypothesis:", readable.stdout)
        bundle = subprocess.run(prefix + ["export", "--incident", data["incidents"][0]["id"], "--limit", "50"],
                                env=env, capture_output=True, text=True, timeout=5, check=False)
        self.assertEqual(bundle.returncode, 0, bundle.stderr)
        exported = json.loads(bundle.stdout)
        self.assertEqual(exported["schema_version"], 1)
        self.assertEqual(len(exported["events"]), 2)
        self.assertTrue(exported["time_semantics"])
        self.assertTrue(exported["coverage"])
        self.assertTrue(exported["limitations"])

    def test_rotation_inherits_verified_router_boot_session(self):
        path = Path(self.directory.name) / "dendelion.log"
        path.write_text(log_line(0, "kernel", "Linux version 6.6.0 fixture boot") + "\n" +
                        log_line(10, "clientwan-path", path_message(public=0)) + "\n")
        FileImporter(self.store).import_file(path)
        path.rename(path.with_name("dendelion.log.1"))
        path.write_text(log_line(20, "clientwan-path", path_message()) + "\n")
        FileImporter(self.store).import_file(path)
        data = self.report()
        self.assertEqual(data["counts"], {"events": 3, "incidents": 1})
        self.assertEqual(data["incidents"][0]["status"], "recovered")
        self.assertEqual(data["incidents"][0]["duration_seconds"], 10)

    def test_backward_receipt_clock_jump_breaks_incident_continuity(self):
        path = Path(self.directory.name) / "backward.log"
        path.write_text(log_line(100, "clientwan-path", path_message(public=0)) + "\n" +
                        log_line(90, "clientwan-path", path_message()) + "\n")
        FileImporter(self.store).import_file(path)
        data = self.report(now=BASE + 110)
        self.assertEqual(data["counts"]["events"], 3)
        self.assertTrue(any(e["kind"] == "receiver-clock-boundary" for e in data["events"]))
        self.assertEqual(data["incidents"][0]["status"], "unknown-end")
        self.assertIsNone(data["incidents"][0]["duration_seconds"])

    def test_forward_receipt_clock_jump_never_invents_long_outage_duration(self):
        path = Path(self.directory.name) / "forward.log"
        path.write_text(log_line(10, "clientwan-path", path_message(public=0)) + "\n" +
                        log_line(4000, "clientwan-path", path_message()) + "\n")
        FileImporter(self.store).import_file(path)
        data = self.report(end=BASE + 5000, now=BASE + 4020)
        self.assertEqual(data["incidents"][0]["status"], "unknown-end")
        self.assertIsNone(data["incidents"][0]["duration_seconds"])

    def test_rotation_after_clock_catches_up_does_not_resurrect_old_epoch(self):
        path = Path(self.directory.name) / "dendelion.log"
        path.write_text(log_line(100, "clientwan-path", path_message(public=0)) + "\n" +
                        log_line(90, "clientwan-path", path_message()) + "\n" +
                        log_line(95, "clientwan-path", path_message()) + "\n")
        FileImporter(self.store).import_file(path)
        path.rename(path.with_name("dendelion.log.1"))
        path.write_text(log_line(105, "clientwan-path", path_message(public=0)) + "\n" +
                        log_line(110, "clientwan-path", path_message()) + "\n")
        FileImporter(self.store).import_file(path)
        data = self.report(now=BASE + 120)
        self.assertEqual(data["counts"]["incidents"], 2)
        old = next(i for i in data["incidents"] if i["onset"] == BASE + 100)
        new = next(i for i in data["incidents"] if i["onset"] == BASE + 105)
        self.assertEqual(old["status"], "unknown-end")
        self.assertEqual(new["duration_seconds"], 5)

    def test_historical_clock_marker_does_not_close_current_live_incident(self):
        current = Path(self.directory.name) / "current.log"
        old = Path(self.directory.name) / "old.log"
        current.write_text(log_line(500, "clientwan-path", path_message(public=0)) + "\n")
        old.write_text(log_line(100, "clientwan-path", path_message(public=0)) + "\n" +
                       log_line(90, "clientwan-path", path_message()) + "\n")
        FileImporter(self.store).import_file(current, backfill=False)
        FileImporter(self.store).import_file(old, backfill=True)
        data = self.report(now=BASE + 520)
        live = next(i for i in data["incidents"] if i["onset"] == BASE + 500)
        self.assertEqual(live["status"], "ongoing")

    def test_structured_cutover_does_not_duplicate_legacy_receiver_copy(self):
        log_dir = Path(self.directory.name) / "logs"
        log_dir.mkdir()
        messages = [(10, path_message(public=0)), (20, path_message()), (30, path_message())]
        (log_dir / "dendelion.log").write_text("".join(log_line(second, "clientwan-path", message) + "\n" for second, message in messages))
        records = []
        for second, message in messages[1:]:
            stamp = dt.datetime.fromtimestamp(BASE + second, dt.timezone.utc).isoformat()
            records.append(json.dumps({"schema_version": "1", "received_at": stamp, "reported_at": stamp,
                                       "hostname": "OpenWrt", "tag": "clientwan-path:", "message": " " + message,
                                       "source_ip": "192.168.6.1", "protocol_version": "0"}))
        (log_dir / "network.jsonl").write_text("\n".join(records) + "\n")
        collector = Collector(self.store, log_dir=str(log_dir), ubnt_target="")
        collector.logs()
        collector.logs()
        self.assertEqual(self.report()["counts"], {"events": 3, "incidents": 1})

    def test_structured_cutover_uses_receipt_even_when_first_tag_is_not_collected(self):
        log_dir = Path(self.directory.name) / "logs"
        log_dir.mkdir()
        (log_dir / "dendelion.log").write_text(log_line(20, "clientwan-path", path_message(public=0)) + "\n")
        records = []
        for second, tag, message in ((10, "cron:", "unrelated periodic task"),
                                     (20, "clientwan-path:", path_message(public=0))):
            stamp = dt.datetime.fromtimestamp(BASE + second, dt.timezone.utc).isoformat()
            records.append(json.dumps({"schema_version": "1", "received_at": stamp, "reported_at": stamp,
                                       "hostname": "OpenWrt", "tag": tag, "message": " " + message,
                                       "source_ip": "192.168.6.1", "protocol_version": "0"}))
        (log_dir / "network.jsonl").write_text("\n".join(records) + "\n")
        Collector(self.store, log_dir=str(log_dir), ubnt_target="").logs()
        self.assertEqual(self.report()["counts"], {"events": 1, "incidents": 1})

    def test_handshake_completion_recovers_same_client_authentication_failure(self):
        self.ingest((10, "hostapd", "wl0-ap0: STA 02:00:00:00:00:01 SAE authentication failed"),
                    (20, "hostapd", "wl0-ap0: STA 02:00:00:00:00:01 WPA: pairwise key handshake completed (RSN)"))
        incident = self.report()["incidents"][0]
        self.assertEqual(incident["domains"], ["local-wifi"])
        self.assertEqual(incident["status"], "recovered")
        self.assertEqual(incident["duration_seconds"], 10)

    def test_system_monitor_usb_burst_does_not_hide_following_power_evidence(self):
        source = os.path.join(self.directory.name, "system-monitor.sqlite3")
        db = sqlite3.connect(source)
        db.executescript("""
            CREATE TABLE events (id INTEGER PRIMARY KEY, timestamp REAL, boot_id TEXT,
                category TEXT, kind TEXT, severity TEXT, summary TEXT, message TEXT);
            CREATE INDEX events_report_idx ON events(timestamp,kind,severity,category);
            CREATE TABLE resource_samples (timestamp REAL);
        """)
        db.executemany("INSERT INTO events VALUES (?,?,?,?,?,?,?,?)",
                       [(index, BASE + 10, "pi-boot", "usb", "usb_error", "warning", "USB error", "fixture USB noise")
                        for index in range(1, 3001)])
        db.execute("INSERT INTO events VALUES (?,?,?,?,?,?,?,?)",
                   (3001, BASE + 20, "pi-boot", "power", "undervoltage_started", "critical", "Undervoltage", "fixture undervoltage detected"))
        db.execute("INSERT INTO resource_samples VALUES (?)", (BASE + 30,))
        db.commit()
        db.close()
        self.store.set_checkpoint("system-monitor-id", 0)
        self.store.db.commit()
        Collector(self.store, monitor_db=source, ubnt_target="").monitor()
        data = self.report()
        self.assertEqual(data["counts"]["events"], 1)
        self.assertEqual(data["events"][0]["kind"], "undervoltage_started")
        self.assertEqual(self.store.checkpoint("system-monitor-id"), 3001)

    def test_unchanged_antenna_log_refreshes_coverage_without_reparse_or_ingest(self):
        collector = Collector(self.store, ubnt_target="fixture-antenna")
        first = subprocess.CompletedProcess([], 0, "antenna-boot-one\n100\nuptime=80 current connection healthy ssid=fixture\n", "")
        second = subprocess.CompletedProcess([], 0, "antenna-boot-one\n160\nuptime=80 current connection healthy ssid=fixture\n", "")
        with mock.patch.object(collector_module.subprocess, "run", return_value=first), \
                mock.patch.object(collector_module.time, "time", return_value=BASE + 100):
            collector.antenna()
        with mock.patch.object(collector_module.subprocess, "run", return_value=second), \
                mock.patch.object(collector_module.time, "time", return_value=BASE + 160), \
                mock.patch.object(collector_module, "parse_ubnt", wraps=collector_module.parse_ubnt) as parse, \
                mock.patch.object(self.store, "ingest", wraps=self.store.ingest) as ingest:
            collector.antenna()
        parse.assert_not_called()
        ingest.assert_not_called()
        data = self.report(now=BASE + 160)
        self.assertEqual(data["counts"]["events"], 1)
        source = next(row for row in data["coverage"] if row["source"] == "ubnt-manager")
        self.assertEqual(source["status"], "current")
        self.assertEqual(source["last_received_at"], BASE + 160)
        self.assertEqual(data["events"][0]["time"], BASE + 80)

    def test_same_antenna_log_after_new_boot_is_reparsed_and_retained(self):
        collector = Collector(self.store, ubnt_target="fixture-antenna")
        first = subprocess.CompletedProcess([], 0, "antenna-boot-one\n100\nuptime=80 current connection healthy ssid=fixture\n", "")
        second = subprocess.CompletedProcess([], 0, "antenna-boot-two\n100\nuptime=80 current connection healthy ssid=fixture\n", "")
        with mock.patch.object(collector_module.subprocess, "run", return_value=first), \
                mock.patch.object(collector_module.time, "time", return_value=BASE + 100):
            collector.antenna()
        with mock.patch.object(collector_module.subprocess, "run", return_value=second), \
                mock.patch.object(collector_module.time, "time", return_value=BASE + 200), \
                mock.patch.object(collector_module, "parse_ubnt", wraps=collector_module.parse_ubnt) as parse:
            collector.antenna()
        self.assertEqual(parse.call_count, 1)
        data = self.report(now=BASE + 200)
        self.assertEqual(data["counts"]["events"], 2)
        self.assertEqual({e["boot_id"] for e in data["events"]}, {"antenna-boot-one", "antenna-boot-two"})

    def test_active_log_precedes_bounded_backfill_and_reports_pending_archives(self):
        log_dir = Path(self.directory.name) / "logs"
        log_dir.mkdir()

        def structured(second, failed=False):
            stamp = dt.datetime.fromtimestamp(BASE + second, dt.timezone.utc).isoformat()
            return json.dumps({"schema_version": "1", "received_at": stamp, "reported_at": stamp,
                               "hostname": "OpenWrt", "tag": "clientwan-path:",
                               "message": " " + path_message(public=0 if failed else 2),
                               "source_ip": "192.168.6.1", "protocol_version": "0"}) + "\n"

        active = log_dir / "network.jsonl"
        active.write_text(structured(100, failed=True))
        os.utime(active, (BASE + 100, BASE + 100))
        for number in range(1, 5):
            archive = log_dir / f"network.jsonl.{number}"
            archive.write_text(structured(-number * 10) * 1000)
            os.utime(archive, (BASE - number * 100, BASE - number * 100))
        legacy = log_dir / "dendelion.log.1.gz"
        legacy.write_bytes(gzip.compress((log_line(-100, "clientwan-path", path_message()) + "\n").encode() * 3000))
        os.utime(legacy, (BASE - 1000, BASE - 1000))
        collector = Collector(self.store, log_dir=str(log_dir), ubnt_target="")
        calls = []
        import_file = collector.files.import_file

        def tracked(path, **kwargs):
            result = import_file(path, **kwargs)
            calls.append((str(path), kwargs, result))
            return result

        with mock.patch.object(collector.files, "import_file", side_effect=tracked), \
                mock.patch.object(collector_module.time, "time", return_value=BASE + 100):
            collector.logs()
        self.assertEqual(calls[0][0], str(active))
        self.assertTrue(all(call[1]["max_bytes"] <= 128 * 1024 for call in calls))
        self.assertLessEqual(sum(call[2]["read_bytes"] for call in calls), 512 * 1024 + 16384)
        data = self.report(start=BASE - 200, now=BASE + 100)
        self.assertTrue(any(event["time"] == BASE + 100 and event["state"] == "failure" for event in data["events"]))
        history = next(row for row in data["coverage"] if row["source"] == "historical-import")
        self.assertEqual(history["status"], "unknown")
        self.assertIn("unread", history["detail"].lower())
        self.assertTrue(history.get("pending_files") == 5 or "5 of 6 source files" in history["detail"])

    def test_live_priority_does_not_claim_recovery_across_backfilled_router_boot(self):
        log_dir = Path(self.directory.name) / "logs"
        log_dir.mkdir()

        def structured(second, tag, message):
            stamp = dt.datetime.fromtimestamp(BASE + second, dt.timezone.utc).isoformat()
            return json.dumps({"schema_version": "1", "received_at": stamp, "reported_at": stamp,
                               "hostname": "OpenWrt", "tag": tag, "message": " " + message,
                               "source_ip": "192.168.6.1", "protocol_version": "0"}) + "\n"

        archive = log_dir / "network.jsonl.1"
        archive.write_text(structured(10, "clientwan-path:", path_message(public=0)) +
                           structured(15, "kernel:", "Linux version 6.6.0 fixture boot"))
        os.utime(archive, (BASE - 1000, BASE - 1000))
        active = log_dir / "network.jsonl"
        active.write_text(structured(20, "clientwan-path:", path_message()))
        os.utime(active, (BASE + 20, BASE + 20))
        with mock.patch.object(collector_module.time, "time", return_value=BASE + 20):
            Collector(self.store, log_dir=str(log_dir), ubnt_target="").logs()
        data = self.report(now=BASE + 30)
        self.assertEqual(data["counts"], {"events": 3, "incidents": 1})
        self.assertEqual(data["incidents"][0]["status"], "unknown-end")
        self.assertIsNone(data["incidents"][0]["duration_seconds"])

    def test_boot_arriving_alone_invalidates_already_materialized_recovery(self):
        self.ingest((10, "clientwan-path", path_message(public=0)),
                    (20, "clientwan-path", path_message()))
        first = self.report()
        self.assertEqual(first["incidents"][0]["status"], "recovered")
        self.assertEqual(first["incidents"][0]["duration_seconds"], 10)
        self.ingest((15, "kernel", "Linux version 6.6.0 fixture boot"))
        corrected = self.report()
        self.assertEqual(corrected["counts"], {"events": 3, "incidents": 1})
        self.assertEqual(corrected["incidents"][0]["status"], "unknown-end")
        self.assertIsNone(corrected["incidents"][0]["end"])
        self.assertIsNone(corrected["incidents"][0]["duration_seconds"])

    def test_backfilled_boot_splits_failures_before_and_after_reboot(self):
        self.ingest((10, "clientwan-path", path_message(public=0)),
                    (17, "clientwan-path", path_message(public=0)),
                    (20, "clientwan-path", path_message()))
        self.assertEqual(self.report()["counts"]["incidents"], 1)
        self.ingest((15, "kernel", "Linux version 6.6.0 fixture boot"))
        data = self.report()
        self.assertEqual(data["counts"], {"events": 4, "incidents": 2})
        before = next(i for i in data["incidents"] if i["onset"] == BASE + 10)
        after = next(i for i in data["incidents"] if i["onset"] == BASE + 17)
        self.assertEqual(before["status"], "unknown-end")
        self.assertIsNone(before["duration_seconds"])
        self.assertEqual(after["status"], "recovered")
        self.assertEqual(after["duration_seconds"], 3)

    def test_backfill_rebuilds_in_source_order_without_inflated_counts(self):
        recovery = self.event(20, "clientwan-path", path_message())
        failure = self.event(10, "clientwan-path", path_message(public=0))
        self.store.ingest([recovery])
        self.store.ingest([failure])
        self.store.ingest([failure, recovery])
        data = self.report()
        self.assertEqual(data["counts"], {"events": 2, "incidents": 1})
        self.assertEqual(data["incidents"][0]["duration_seconds"], 10)
        self.assertEqual(data["incidents"][0]["status"], "recovered")
        bundle = export_incident(self.path, data["incidents"][0]["id"], now=BASE + 60)
        self.assertEqual(len(bundle["events"]), 2)
        self.assertEqual({e["state"] for e in bundle["events"]}, {"failure", "success"})

    def test_missing_observations_do_not_extend_failure_through_a_collection_gap(self):
        self.ingest((10, "clientwan-path", path_message(public=0)),
                    (400, "clientwan-path", path_message()))
        incident = self.report(now=BASE + 410)["incidents"][0]
        self.assertEqual(incident["status"], "unknown-end")
        self.assertIsNone(incident["end"])
        self.assertIsNone(incident["duration_seconds"])

    def test_reboot_session_does_not_close_previous_failure(self):
        failure = self.event(10, "clientwan-path", path_message(public=0), boot_id="boot-one", session="boot-one")
        recovered = self.event(20, "clientwan-path", path_message(), boot_id="boot-two", session="boot-two")
        self.store.ingest([failure, recovered])
        incident = self.report()["incidents"][0]
        self.assertEqual(incident["status"], "unknown-end")
        self.assertIsNone(incident["end"])

    def test_interval_inside_failure_keeps_earlier_onset(self):
        self.ingest((10, "clientwan-path", path_message(public=0)),
                    (60, "clientwan-path", path_message(public=0)),
                    (100, "clientwan-path", path_message()))
        data = self.report(start=BASE + 50, end=BASE + 80, now=BASE + 120)
        self.assertEqual(data["counts"]["events"], 1)
        self.assertEqual(data["counts"]["incidents"], 1)
        self.assertEqual(data["incidents"][0]["onset"], BASE + 10)

    def test_old_success_is_stale_not_current_healthy_coverage(self):
        self.ingest((0, "clientwan-path", path_message()))
        data = self.report(now=BASE + 3600)
        self.assertEqual(data["counts"]["incidents"], 0)
        self.assertTrue(data["coverage"])
        self.assertTrue(all(row["status"] != "healthy" for row in data["coverage"]))
        path = next(row for row in data["coverage"] if row["source"] == "clientwan:path")
        self.assertEqual(path["status"], "stale")
        self.assertEqual(path["last_event_at"], BASE)

    def test_unavailable_collector_is_not_invented_internet_failure(self):
        self.ingest((0, "clientwan-path", path_message()))
        self.store.coverage("openwrt", "OpenWrt", "unavailable", "fixture file unreadable",
                            last_received_at=BASE, now=BASE + 30)
        data = self.report(now=BASE + 40)
        self.assertEqual(data["counts"]["incidents"], 0)
        source = next(row for row in data["coverage"] if row["source"] == "openwrt")
        self.assertEqual(source["status"], "unavailable")
        self.assertIn("unreadable", source["detail"])

    def test_resumed_collector_gap_has_unknown_end_despite_current_coverage(self):
        self.store.set_checkpoint("recorder-heartbeat", {
            "time": BASE, "session": "previous", "boot": "previous",
        })
        self.store.db.commit()
        collector = Collector(self.store, ubnt_target="")
        with mock.patch.object(collector_module.time, "time", return_value=BASE + 200):
            collector.heartbeat()
        data = self.report(now=BASE + 201)
        coverage = next(row for row in data["coverage"] if row["source"] == "recorder")
        self.assertEqual(coverage["status"], "current")
        self.assertEqual(data["counts"]["incidents"], 1)
        incident = data["incidents"][0]
        self.assertEqual(incident["domains"], ["monitoring"])
        self.assertEqual(incident["status"], "unknown-end")
        self.assertIsNone(incident["end"])
        self.assertIsNone(incident["duration_seconds"])

    def test_discrete_resource_event_is_not_an_ongoing_failure(self):
        event = observation("system-monitor", "vanpi", "fixture one-shot OOM kill", BASE + 10,
                            {"row_id": 1}, domain="monitoring", kind="oom_kill",
                            state="failure", stream="system:oom_kill")
        self.store.ingest([event])
        incident = self.report(now=BASE + 11)["incidents"][0]
        self.assertEqual(incident["status"], "unknown-end")
        self.assertIsNone(incident["end"])
        self.assertIsNone(incident["duration_seconds"])

    def test_fresh_periodic_network_failure_remains_ongoing(self):
        self.ingest((10, "clientwan-path", path_message(public=0)))
        incident = self.report(now=BASE + 11)["incidents"][0]
        self.assertEqual(incident["domains"], ["upstream-tests"])
        self.assertEqual(incident["status"], "ongoing")
        self.assertIsNone(incident["end"])

    def test_discrete_antenna_attempt_failure_has_unknown_end(self):
        event = parse_ubnt("uptime=100 connection failed", boot_id="antenna-boot",
                           uptime=100, received_at=BASE + 10, provenance={"line": 1})
        self.store.ingest([event])
        incident = self.report(now=BASE + 11)["incidents"][0]
        self.assertEqual(incident["domains"], ["ubnt"])
        self.assertEqual(incident["status"], "unknown-end")
        self.assertIsNone(incident["end"])
        self.assertIsNone(incident["duration_seconds"])

    def test_discrete_shared_resolver_log_has_unknown_end(self):
        self.ingest((10, "dnsmasq[1]", "no servers found in /tmp/resolv.conf.d/resolv.conf.auto, will retry"))
        incident = self.report(now=BASE + 11)["incidents"][0]
        self.assertEqual(incident["domains"], ["dns"])
        self.assertEqual(incident["status"], "unknown-end")
        self.assertIsNone(incident["end"])
        self.assertIsNone(incident["duration_seconds"])

    def test_fresh_periodic_https_dns_failure_remains_ongoing(self):
        self.ingest((10, "uplink-https", https_message(state="offline", google="000/6", cloudflare="000/6")))
        incident = self.report(now=BASE + 11)["incidents"][0]
        self.assertEqual(incident["domains"], ["dns"])
        self.assertEqual(incident["status"], "ongoing")
        self.assertIsNone(incident["end"])

    def test_empty_database_has_explicit_unavailable_coverage(self):
        data = self.report()
        self.assertEqual(data["counts"], {"events": 0, "incidents": 0})
        self.assertEqual(data["events"], [])
        self.assertEqual(data["incidents"], [])
        self.assertTrue(data["coverage"])
        self.assertEqual({row["status"] for row in data["coverage"]}, {"unavailable"})

    def test_filters_keep_complete_range_counts(self):
        self.ingest((10, "uplink-https", https_message(state="offline", google="000/28", cloudflare="000/28")),
                    (20, "uplink-https", https_message()),
                    (30, "uplink-https", https_message("wan", "offline", "000/28", "000/28")),
                    (40, "uplink-https", https_message("wan")))
        data = self.report(limit=1, uplink="clientwan")
        self.assertEqual(data["counts"], {"events": 2, "incidents": 1})
        self.assertEqual(len(data["events"]), 1)
        self.assertEqual(data["incidents"][0]["uplinks"], ["clientwan"])

    def test_secret_fixture_is_redacted_before_storage_and_every_export(self):
        secrets = ["hunter-fixture", "bearer-fixture", "argument-fixture", "json-fixture", "url-fixture"]
        message = (
            "wl0-ap0: STA 02:00:00:00:00:01 SAE authentication failed password=hunter-fixture "
            "Authorization: Bearer bearer-fixture --password argument-fixture "
            '\"api_key\": \"json-fixture\" https://user:url-fixture@example.invalid/private?q=url-fixture'
        )
        self.ingest((10, "hostapd", message))
        data = self.report()
        exposed = json.dumps(data)
        for incident in data["incidents"]:
            exposed += json.dumps(export_incident(self.path, incident["id"], now=BASE + 60))
        connection = sqlite3.connect(self.path)
        self.addCleanup(connection.close)
        persisted = "\n".join(connection.iterdump())
        for secret in secrets:
            self.assertNotIn(secret, exposed)
            self.assertNotIn(secret, persisted)
        self.assertIn("02:00:00:00:00:01", exposed)
        self.assertTrue(data["incidents"])

    def test_each_credential_form_is_independently_redacted(self):
        # Each payload is separate: a broad Authorization redaction must not
        # accidentally hide a broken password/URL/argument redactor in a test.
        forms = [
            "password=credential-fixture",
            "Authorization: Bearer credential-fixture",
            "--password 'credential-fixture'",
            '\"api_key\": \"credential-fixture\"',
            "https://user:credential-fixture@example.invalid/path?q=credential-fixture",
            "curl --user fixtureuser:credential-fixture",
            "curl -u fixtureuser:credential-fixture",
            "curl --proxy-user fixtureuser:credential-fixture",
            "Cookie: session=credential-fixture",
        ]
        for index, payload in enumerate(forms):
            with self.subTest(payload=payload):
                event = self.event(index, "hostapd", "wl0-ap0: SAE authentication failed " + payload)
                self.assertNotIn("credential-fixture", json.dumps(event))

    def test_redaction_keeps_diagnostic_handshake_words(self):
        event = self.event(0, "hostapd", "wl0-ap0: STA 02:00:00:00:00:01 WPA: pairwise key handshake completed (RSN)")
        self.assertIn("pairwise key handshake completed", event["message"])

    def test_read_only_report_does_not_initialize_or_migrate_schema(self):
        self.ingest((0, "clientwan-path", path_message()))
        connection = sqlite3.connect(self.path)
        before = connection.execute("SELECT type,name,sql FROM sqlite_master ORDER BY name").fetchall()
        schema_version = connection.execute("PRAGMA schema_version").fetchone()
        self.report()
        self.assertEqual(connection.execute("PRAGMA schema_version").fetchone(), schema_version)
        self.assertEqual(connection.execute("SELECT type,name,sql FROM sqlite_master ORDER BY name").fetchall(), before)
        connection.close()

    def test_high_volume_counts_are_full_range_with_bounded_display(self):
        rows = [self.event(index / 100, "clientwan-path", path_message(public=0)) for index in range(10000)]
        started = time.perf_counter()
        self.assertEqual(self.store.ingest(rows), 10000)
        ingest_seconds = time.perf_counter() - started
        started = time.perf_counter()
        data = self.report(limit=25, now=BASE + 105)
        report_seconds = time.perf_counter() - started
        self.assertEqual(data["counts"], {"events": 10000, "incidents": 1})
        self.assertEqual(len(data["events"]), 25)
        self.assertLess(report_seconds, 3, f"10k full-range report took {report_seconds:.3f}s")
        # This direct bulk import is outside the daemon's per-pass/CPU budgets.
        # The supported Pi 4 measured 20.19s; 60s is a regression/hang guard,
        # not an ingestion SLO. Exact counts and report latency remain strict.
        self.assertLess(ingest_seconds, 60, f"10k ingestion took {ingest_seconds:.3f}s")

    def test_retention_event_and_age_limits_actually_delete_rows(self):
        path = os.path.join(self.directory.name, "retention.sqlite3")
        store = Store(path, max_events=1000, retention_days=1, max_bytes=8 * 1024 * 1024)
        self.addCleanup(store.close)
        rows = [self.event(index / 100, "clientwan-path", path_message(public=0)) for index in range(1500)]
        store.ingest(rows)
        store.prune(now=BASE + 60)
        data = report(path, start=BASE - 1, end=BASE + 60, now=BASE + 60)
        self.assertEqual(data["counts"]["events"], 1000)
        store.prune(now=BASE + 2 * 86400)
        self.assertEqual(report(path, start=BASE - 1, end=BASE + 60, now=BASE + 2 * 86400)["counts"],
                         {"events": 0, "incidents": 0})

    def test_disk_budget_includes_sqlite_sidecars_during_burst(self):
        path = os.path.join(self.directory.name, "bounded.sqlite3")
        budget = 4 * 1024 * 1024
        store = Store(path, max_events=20000, retention_days=1, max_bytes=budget)
        self.addCleanup(store.close)
        rows = [self.event(index / 100, "hostapd", "wl0-ap0: SAE authentication failed " + "x" * 1100)
                for index in range(4000)]
        store.ingest(rows)
        store.prune(now=BASE + 60)
        bytes_used = sum(os.path.getsize(path + suffix) for suffix in ("", "-wal", "-shm") if os.path.exists(path + suffix))
        self.assertLessEqual(bytes_used, budget)
        data = report(path, start=BASE - 1, end=BASE + 60, now=BASE + 60)
        self.assertGreater(data["counts"]["events"], 0)
        self.assertLess(data["counts"]["events"], 4000)


class RecorderWaitTests(unittest.TestCase):
    def test_preemption_crossing_deadline_never_passes_negative_delay(self):
        clock = mock.Mock(side_effect=[0.9, 2.0])
        slept = []

        def sleep(delay):
            self.assertGreaterEqual(delay, 0)
            slept.append(delay)

        collector_module.wait_until(1.0, lambda: False, clock=clock, sleeper=sleep)
        self.assertEqual(clock.call_count, 2)
        self.assertEqual(len(slept), 1)
        self.assertAlmostEqual(slept[0], 0.1)

    def test_expired_deadline_returns_without_sleep(self):
        sleeper = mock.Mock()
        collector_module.wait_until(1.0, lambda: False, clock=lambda: 2.0, sleeper=sleeper)
        sleeper.assert_not_called()

    def test_wait_sleeps_in_positive_bounded_intervals(self):
        now = 0.0
        slept = []

        def sleep(delay):
            nonlocal now
            self.assertGreater(delay, 0)
            self.assertLessEqual(delay, 0.5)
            slept.append(delay)
            now += delay

        collector_module.wait_until(1.2, lambda: False, clock=lambda: now, sleeper=sleep)
        self.assertEqual(len(slept), 3)
        self.assertAlmostEqual(sum(slept), 1.2)

    def test_stop_before_or_during_wait_prevents_further_sleep(self):
        clock, sleeper = mock.Mock(), mock.Mock()
        collector_module.wait_until(100, lambda: True, clock=clock, sleeper=sleeper)
        clock.assert_not_called()
        sleeper.assert_not_called()
        stopped = False
        clock = mock.Mock(return_value=0)
        delays = []

        def stop_after_sleep(delay):
            nonlocal stopped
            delays.append(delay)
            stopped = True

        collector_module.wait_until(100, lambda: stopped, clock=clock, sleeper=stop_after_sleep)
        self.assertEqual(delays, [0.5])
        clock.assert_called_once()

    def test_collector_loop_delegates_interruptible_wait(self):
        collector = Collector(mock.Mock(), ubnt_target="")
        collector.once = mock.Mock()

        def wait(deadline, stopped):
            self.assertEqual(deadline, 105)
            self.assertFalse(stopped())
            collector.stopped = True
            self.assertTrue(stopped())

        with mock.patch.object(collector_module, "wait_until", side_effect=wait) as waiting, \
                mock.patch.object(collector_module.time, "monotonic", return_value=100), \
                mock.patch.object(collector_module.time, "sleep", side_effect=AssertionError("loop bypassed wait helper")), \
                mock.patch.object(collector_module.signal, "signal"):
            collector.run()
        waiting.assert_called_once()
        collector.once.assert_called_once()

    def test_storage_loop_delegates_interruptible_wait(self):
        from pi.scripts.network_recorder import storage
        config = SimpleNamespace(flash_logs=Path("/fixture/flash"), spool_dir=Path("/fixture/ram"))
        store = mock.Mock()
        manager = mock.Mock(store=store, mode="flash", replaying=False)
        manager.tick.return_value = store
        collector = mock.Mock()
        handlers = {}

        def wait(deadline, stopped):
            self.assertEqual(deadline, 105)
            self.assertFalse(stopped())
            handlers[storage.signal.SIGTERM](None, None)
            self.assertTrue(stopped())

        with mock.patch.object(storage, "storage_lock", return_value=contextlib.nullcontext()), \
                mock.patch.object(storage, "MountGuard"), \
                mock.patch.object(storage, "StorageManager", return_value=manager), \
                mock.patch.object(storage, "Collector", return_value=collector), \
                mock.patch.object(storage, "wait_until", side_effect=wait) as waiting, \
                mock.patch.object(storage.time, "monotonic", return_value=100), \
                mock.patch.object(storage.time, "sleep", side_effect=AssertionError("loop bypassed wait helper")), \
                mock.patch.object(storage.signal, "signal", side_effect=lambda signum, handler: handlers.__setitem__(signum, handler)):
            storage.run_storage(config)
        waiting.assert_called_once()
        collector.once.assert_called_once()
        manager.close.assert_called_once()

    def test_cli_error_location_excludes_exception_message_arguments_and_paths(self):
        script_dir = Path(__file__).resolve().parents[2] / "scripts"
        spec = importlib.util.spec_from_file_location("wait_error_cli_fixture", script_dir / "network_flight_recorder.py")
        cli = importlib.util.module_from_spec(spec)
        with mock.patch.object(sys, "path", [str(script_dir)] + sys.path):
            spec.loader.exec_module(cli)

        def fail(*args, **kwargs):
            raise ValueError("password=error-message-secret-fixture")

        with mock.patch.object(cli, "resolve_database", side_effect=fail), \
                mock.patch.object(sys, "stderr", new_callable=io.StringIO) as output:
            result = cli.main(["--database", "/fixture/argument-secret-fixture.sqlite3", "report"])
        self.assertEqual(result, 1)
        data = json.loads(output.getvalue())
        self.assertEqual(data["error_type"], "ValueError")
        self.assertEqual(set(data["location"]), {"file", "function", "line"})
        self.assertEqual(data["location"]["file"], Path(__file__).name)
        self.assertEqual(data["location"]["function"], "fail")
        self.assertIsInstance(data["location"]["line"], int)
        self.assertNotIn("error-message-secret-fixture", output.getvalue())
        self.assertNotIn("argument-secret-fixture", output.getvalue())
        self.assertNotIn(str(script_dir), output.getvalue())


if __name__ == "__main__":
    unittest.main()
