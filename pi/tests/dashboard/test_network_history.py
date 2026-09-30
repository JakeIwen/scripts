"""HTTP/CLI boundary checks; core evidence correctness is tested separately."""

import concurrent.futures
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from werkzeug.datastructures import MultiDict

from pi.apps.van_dashboard import van_dashboard as dashboard
from pi.apps.van_dashboard.van_dashboard_history import (
    NetworkHistoryClient,
    NetworkHistoryError,
    network_history_query,
)
from pi.scripts.network_recorder.parsers import observation
from pi.scripts.network_recorder.store import Store


class HistoryQueryTests(unittest.TestCase):
    def test_preserves_literal_search_and_explicit_epoch_window(self):
        query = network_history_query(MultiDict({
            "start": "1700000000", "end": "1700000060", "search": "--token=$(noop)", "uplink": "clientwan"
        }))
        self.assertEqual(query, {
            "start": 1700000000.0, "end": 1700000060.0,
            "search": "--token=$(noop)", "uplink": "clientwan",
        })

    def test_rejects_unbounded_ambiguous_or_invalid_ranges(self):
        for values in (
            [("hours", "6"), ("hours", "24")], {"limit": "100000"}, {"hours": "2"},
            {"start": "1"}, {"start": "1", "end": "2", "hours": "6"},
            {"start": "nan", "end": "2"}, {"start": "1", "end": "inf"},
            {"start": "2", "end": "1"}, {"start": "1", "end": "3000000"},
            {"search": "x" * 201}, {"device": "router\nsecret"},
        ):
            with self.subTest(values=values), self.assertRaises(ValueError):
                network_history_query(MultiDict(values))


class HistoryClientTests(unittest.TestCase):
    def test_default_auto_selector_and_explicit_overrides(self):
        command = mock.Mock(return_value=SimpleNamespace(
            returncode=0, stdout='{"ok":true}', stderr=""
        ))
        with mock.patch.dict(os.environ, {}, clear=True):
            client = NetworkHistoryClient(command=command)
            client.report({"hours": 6})
            self.assertEqual(command.call_args.args[0][2:4], ["--database", "auto"])
        with mock.patch.dict(os.environ, {"VAN_DASHBOARD_NETWORK_RECORDER_DB": "/fixture/custom.sqlite3"}):
            self.assertEqual(NetworkHistoryClient().database, "/fixture/custom.sqlite3")
            self.assertEqual(NetworkHistoryClient(database="/explicit.sqlite3").database, "/explicit.sqlite3")

    def test_concurrent_readers_share_cached_report_without_mutable_aliases(self):
        command = mock.Mock(return_value=SimpleNamespace(
            returncode=0, stdout=json.dumps({"ok": True, "events": [{"id": 1}]}), stderr=""
        ))
        client = NetworkHistoryClient(tool="/recorder.py", database="/history.sqlite", command=command)
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            outputs = list(pool.map(lambda _: client.report({"hours": 6}), range(12)))
        self.assertEqual(command.call_count, 1)
        outputs[0]["events"].clear()
        self.assertEqual(outputs[1]["events"], [{"id": 1}])
        arguments = command.call_args.args[0]
        self.assertEqual(arguments[1:], [
            "/recorder.py", "--database", "/history.sqlite", "report", "--limit", "200", "--json", "--hours=6"
        ])
        self.assertEqual(command.call_args.kwargs["timeout"], 15)

    def test_failed_reader_never_exposes_unredacted_command_output(self):
        for result in (
            SimpleNamespace(returncode=1, stdout="", stderr="password=secret"),
            SimpleNamespace(returncode=0, stdout="Authorization: secret", stderr=""),
            SimpleNamespace(returncode=0, stdout='{"ok":false,"message":"password=secret"}', stderr=""),
        ):
            command = mock.Mock(return_value=result)
            client = NetworkHistoryClient(command=command)
            for _ in range(2):
                with self.assertRaises(NetworkHistoryError) as caught:
                    client.report({"hours": 6})
                self.assertNotIn("secret", str(caught.exception))
            self.assertEqual(command.call_count, 1)

    def test_timeout_does_not_reflect_arguments(self):
        command = mock.Mock(side_effect=subprocess.TimeoutExpired(["password=secret"], 15))
        with self.assertRaises(NetworkHistoryError) as caught:
            NetworkHistoryClient(command=command).report({"hours": 6})
        self.assertNotIn("secret", str(caught.exception))

    def test_incident_id_cannot_be_an_option_or_a_path(self):
        command = mock.Mock()
        client = NetworkHistoryClient(command=command)
        for value in ("../db", "--help", "x?token=value", "x\nsecret", "a" * 101):
            with self.assertRaises(ValueError):
                client.incident(value)
        command.assert_not_called()


class HistoryRouteTests(unittest.TestCase):
    def setUp(self):
        self.client = dashboard.app.test_client()

    def test_history_is_read_only_and_does_not_cache_http_responses(self):
        payload = {"ok": True, "schema_version": 1}
        with mock.patch.object(dashboard, "network_history") as history:
            history.report.return_value = payload
            response = self.client.get("/api/network-history?hours=24&uplink=clientwan")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json, payload)
            self.assertEqual(response.headers["Cache-Control"], "no-store")
            history.report.assert_called_once_with({"hours": 24, "uplink": "clientwan"})
            self.assertEqual(self.client.post("/api/network-history").status_code, 405)

    def test_rejects_invalid_query_before_read(self):
        with mock.patch.object(dashboard, "network_history") as history:
            response = self.client.get("/api/network-history?hours=6&hours=24")
            self.assertEqual(response.status_code, 400)
            history.report.assert_not_called()

    def test_unavailable_recorder_is_503_not_old_healthy_state(self):
        with mock.patch.object(dashboard.network_history, "report", side_effect=NetworkHistoryError("Recorder unavailable")):
            response = self.client.get("/api/network-history")
            self.assertEqual(response.status_code, 503)
            self.assertFalse(response.json["ok"])

    def test_incident_endpoint_is_bounded_and_rejects_extra_parameters(self):
        with mock.patch.object(dashboard, "network_history") as history:
            history.incident.return_value = {"ok": True, "incident": {"id": "inc-123"}}
            self.assertEqual(self.client.get("/api/network-history/incidents/inc-123").status_code, 200)
            history.incident.assert_called_once_with("inc-123")
            self.assertEqual(self.client.get("/api/network-history/incidents/inc-123?limit=100000").status_code, 400)


class HistoryIntegrationTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform.startswith("linux") and Path("/dev/shm").is_dir(),
                         "Actual storage guard requires Linux tmpfs; exercised in isolated Pi suite")
    def test_actual_auto_cli_uses_ram_and_rejects_an_unmounted_flash_shadow(self):
        from pi.scripts.network_recorder.storage import StorageConfig, StorageManager

        root = Path(__file__).resolve().parents[3]
        with tempfile.TemporaryDirectory(prefix="network-history-auto-", dir="/dev/shm") as directory:
            fixture = Path(directory)
            mountpoint = fixture / "absent-flash"
            mountpoint.mkdir()
            flash_root = mountpoint / "vanpi-network"
            runtime = fixture / "runtime"
            runtime.mkdir()
            configuration = fixture / "storage.json"
            configuration.write_text(json.dumps({
                "schema_version": 1, "mountpoint": str(mountpoint),
                "uuid": "fixture-device-does-not-exist", "fstype": "exfat",
                "flash_root": str(flash_root), "runtime_root": str(runtime),
            }))
            manager = StorageManager(StorageConfig(
                mountpoint=mountpoint, uuid="fixture-device-does-not-exist",
                flash_root=flash_root, runtime_root=runtime,
            ))
            try:
                manager.open()
                self.assertEqual(manager.mode, "ram")
                self.assertFalse(flash_root.exists(), "Missing flash must not create a shadow data directory")
                manager.store.ingest([
                    observation("synthetic-ram", "fixture-router", message, timestamp,
                                {"fixture_record": sequence}, uplink="wan", stream="fixture:auto",
                                domain="https", state=state)
                    for sequence, timestamp, state, message in (
                        (1, 1700000000, "failure", "fixture HTTPS failed"),
                        (2, 1700000030, "success", "fixture HTTPS recovered"),
                    )
                ])
                manager.record_coverage()
            finally:
                manager.close()

            def reader():
                return NetworkHistoryClient(tool=str(root / "pi/scripts/network_flight_recorder.py"), database="auto")

            with mock.patch.dict(os.environ, {"VANPI_NETWORK_STORAGE_CONFIG": str(configuration)}):
                with mock.patch.object(dashboard, "network_history", reader()):
                    client = dashboard.app.test_client()
                    response = client.get("/api/network-history?start=1699999999&end=1700000040")
                    self.assertEqual(response.status_code, 200, response.json)
                    self.assertEqual(response.json["counts"], {"events": 2, "incidents": 1})
                    storage = next(row for row in response.json["coverage"] if row["source"] == "storage")
                    self.assertEqual(storage["status"], "unknown")
                    self.assertIn("lost on reboot", storage["detail"])
                    incident_id = response.json["incidents"][0]["id"]
                    detail = client.get(f"/api/network-history/incidents/{incident_id}")
                    self.assertEqual(detail.status_code, 200, detail.json)
                    self.assertTrue(all(event["record_id"] for event in detail.json["events"]))

                # A valid old database underneath an absent mount is never a
                # substitute for the verified external filesystem.
                shadow = Store(flash_root / "events.sqlite3")
                shadow.close()
                status_path = runtime / "storage-status.json"
                status = json.loads(status_path.read_text())
                status.update(mode="flash", active_database=str(flash_root / "events.sqlite3"))
                status_path.write_text(json.dumps(status))
                with mock.patch.object(dashboard, "network_history", reader()):
                    failed = dashboard.app.test_client().get("/api/network-history")
                    self.assertEqual(failed.status_code, 503)
                    self.assertFalse(failed.json["ok"])
                configuration.unlink()
                with mock.patch.object(dashboard, "network_history", reader()):
                    missing = dashboard.app.test_client().get("/api/network-history")
                    self.assertEqual(missing.status_code, 503)

    def test_actual_cli_report_and_incident_export_through_flask(self):
        root = Path(__file__).resolve().parents[3]
        with tempfile.TemporaryDirectory(prefix="network-history-http-") as directory:
            database = Path(directory) / "events.sqlite3"
            store = Store(database)
            events = [observation(
                "synthetic", "fixture-router", message, timestamp,
                {"fixture_record": sequence},
                uplink="clientwan", stream="fixture:clientwan", domain="upstream-tests", state=state,
            ) for sequence, timestamp, state, message in (
                (1, 1700000000, "failure", "gateway answered; external tests failed token=fixture-secret"),
                (2, 1700000030, "success", "gateway and external tests answered"),
            )]
            store.ingest(events)
            store.close()
            before = database.stat().st_mtime_ns
            reader = NetworkHistoryClient(tool=str(root / "pi/scripts/network_flight_recorder.py"), database=str(database))
            with mock.patch.object(dashboard, "network_history", reader):
                client = dashboard.app.test_client()
                response = client.get("/api/network-history?start=1699999999&end=1700000040&device=fixture-router")
                self.assertEqual(response.status_code, 200, response.json)
                report = response.json
                self.assertEqual(report["counts"], {"events": 2, "incidents": 1})
                self.assertEqual(report["schema_version"], 1)
                self.assertEqual(report["incidents"][0]["status"], "recovered")
                self.assertEqual(report["incidents"][0]["end"] - report["incidents"][0]["onset"], 30)
                self.assertEqual(report["incidents"][0]["domains"], ["upstream-tests"])
                self.assertNotIn("fixture-secret", response.get_data(as_text=True))
                incident_id = report["incidents"][0]["id"]
                detail = client.get(f"/api/network-history/incidents/{incident_id}")
                self.assertEqual(detail.status_code, 200, detail.json)
                self.assertEqual(detail.json["counts"]["events"], 2)
                self.assertEqual(detail.json["incident"]["id"], incident_id)
                self.assertNotIn("fixture-secret", detail.get_data(as_text=True))
                self.assertEqual(database.stat().st_mtime_ns, before)


if __name__ == "__main__":
    unittest.main()
