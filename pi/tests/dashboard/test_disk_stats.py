import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

from flask import Flask

from pi.apps.van_dashboard import van_dashboard_disk_stats as disk_stats
from pi.apps.van_dashboard import van_dashboard_disks as dashboard_disks
from pi.apps.van_dashboard.routes import disks as disk_routes
from pi.apps.van_dashboard.van_dashboard_integrations import (
    SystemMonitorClient,
    SystemMonitorCommandError,
)


def io_bucket(start, **overrides):
    return {
        "start": start,
        "minutes": 1,
        "read_bytes": 60_000,
        "write_bytes": 0,
        "peak_read_bytes_per_second": 1000,
        "peak_write_bytes_per_second": 0,
        "busy_percent": 2.5,
        "peak_busy_percent": 2.5,
        **overrides,
    }


class FakeMonitor:
    def __init__(self, latest=None, history=None):
        self.latest_payload = latest or {"ok": True, "sample_seconds": 60, "disks": {}}
        self.history_payload = history
        self.history_calls = []

    def disk_io_latest(self):
        return self.latest_payload

    def disk_io_history(self, label, hours):
        self.history_calls.append((label, hours))
        return self.history_payload


class DiskStatsReaderTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.smart_dir = self.temporary.name

    def write_smart(self, label, *lines):
        with open(os.path.join(self.smart_dir, label), "w", encoding="ascii") as handle:
            handle.write("".join(line + "\n" for line in lines))

    def test_latest_joins_monitor_io_with_the_last_smart_reading(self):
        self.write_smart(
            "bigboi",
            "1000 true 32 0 0 41 1200 50",
            "1600 true 32 0 0 43 1201 50",
            "garbage line",
            "1700 maybe 1 2 3 4 5 6",
        )
        monitor = FakeMonitor(
            latest={
                "ok": True,
                "sample_seconds": 60,
                "disks": {
                    "bigboi": {
                        "at": 1650,
                        "read_bytes_per_second": 10,
                        "write_bytes_per_second": 20,
                        "busy_percent": 1.5,
                    }
                },
            }
        )
        reader = disk_stats.DiskStatsReader(monitor, self.smart_dir, wall_clock=lambda: 1700)
        result = reader.latest(["bigboi", "EXFAT512"])
        self.assertEqual(result["checked_at"], 1700)
        self.assertEqual(result["disks"]["bigboi"]["io"]["write_bytes_per_second"], 20)
        self.assertEqual(
            result["disks"]["bigboi"]["smart"],
            {
                "at": 1600,
                "passed": True,
                "reallocated_sectors": 32,
                "pending_sectors": 0,
                "uncorrectable_sectors": 0,
                "temperature_c": 43,
                "power_on_hours": 1201,
                "power_cycles": 50,
            },
        )
        # Flash has no SMART and a detached disk no I/O; both are null, not errors.
        self.assertEqual(result["disks"]["EXFAT512"], {"io": None, "smart": None})

    def test_history_buckets_temperature_with_the_io_buckets(self):
        self.write_smart(
            "bigboi",
            "500 true 0 0 0 30 1 1",
            "1300 true 0 0 0 40 1 1",
            "1500 true 0 0 0 44 1 1",
            "1900 unknown 0 0 0 unknown 1 1",
            "1950 true 0 0 0 -2 1 1",
            "1960 true -1 0 0 20 1 1",
            "2600 true 0 0 0 39 2 2",
        )
        monitor = FakeMonitor(
            history={
                "ok": True,
                "since": 1000,
                "until": 2000,
                "sample_seconds": 60,
                "bucket_seconds": 600,
                "buckets": [io_bucket(1200)],
            }
        )
        reader = disk_stats.DiskStatsReader(monitor, self.smart_dir)
        result = reader.history("bigboi", 6)
        self.assertEqual(monitor.history_calls, [("bigboi", 6)])
        self.assertEqual(result["io"], [io_bucket(1200)])
        self.assertEqual(
            result["temperature"],
            [
                {"start": 1200, "readings": 2, "temperature_c": 42.0, "max_temperature_c": 44},
                # A negative temperature is a reading; a negative counter is a torn line.
                {"start": 1800, "readings": 1, "temperature_c": -2.0, "max_temperature_c": -2},
            ],
        )
        # The latest reading is shown even when it falls after the graphed range.
        self.assertEqual(result["smart"]["at"], 2600)

    def test_malformed_monitor_output_and_ranges_are_refused(self):
        reader = disk_stats.DiskStatsReader(
            FakeMonitor(
                latest={"ok": True, "disks": {"bigboi": {"at": "soon"}}},
                history={"ok": True, "since": 1, "until": 2, "bucket_seconds": 60,
                         "buckets": [io_bucket(0, minutes=-1)]},
            ),
            self.smart_dir,
        )
        with self.assertRaises(SystemMonitorCommandError):
            reader.latest(["bigboi"])
        with self.assertRaises(SystemMonitorCommandError):
            reader.history("bigboi", 6)
        for hours in (0, 721):
            with self.assertRaises(ValueError):
                reader.history("bigboi", hours)

    def test_monitor_client_runs_fixed_disk_io_commands(self):
        calls = []

        def command(args, timeout):
            calls.append(args)
            return SimpleNamespace(returncode=0, stdout='{"ok": true, "disks": {}}', stderr="")

        client = SystemMonitorClient(tool="/tool", database="/db", command=command)
        client.disk_io_latest()
        client.disk_io_history("bigboi", 24)
        self.assertEqual(calls[0][1:], ["/tool", "--database", "/db", "disk-io", "--latest"])
        self.assertEqual(
            calls[1][1:],
            ["/tool", "--database", "/db", "disk-io", "--label", "bigboi", "--hours", "24"],
        )


class MountSpaceTests(unittest.TestCase):
    def test_mounted_filesystem_reports_its_space(self):
        with tempfile.TemporaryDirectory() as mount:
            health = dashboard_disks.DiskManager._mount_health("x", True, mount)
        space = health["space"]
        self.assertGreater(space["total_bytes"], 0)
        self.assertLessEqual(space["available_bytes"], space["total_bytes"] - space["used_bytes"])
        self.assertIsNone(dashboard_disks.DiskManager._mount_health("x", False, mount)["space"])


class DiskStatsRouteTests(unittest.TestCase):
    def setUp(self):
        app = Flask(__name__)
        app.register_blueprint(disk_routes.bp)
        self.client = app.test_client()
        self.manager = SimpleNamespace(labels=lambda: ["bigboi", "EXFAT512"])
        self.stats = mock.Mock()
        for name, value in (("disk_manager", self.manager), ("disk_stats", self.stats)):
            patcher = mock.patch.object(disk_routes, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_stats_cover_every_configured_label(self):
        self.stats.latest.return_value = {"disks": {}}
        response = self.client.get("/api/disks/stats")
        self.assertEqual(response.get_json(), {"ok": True, "disk_stats": {"disks": {}}})
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.stats.latest.assert_called_once_with(["bigboi", "EXFAT512"])
        self.assertEqual(self.client.get("/api/disks/stats?x=1").status_code, 400)

    def test_history_accepts_only_a_configured_label_and_whole_hours(self):
        self.stats.history.return_value = {"io": []}
        response = self.client.get("/api/disks/history?label=bigboi&hours=24")
        self.assertEqual(response.status_code, 200)
        self.stats.history.assert_called_once_with("bigboi", 24)
        for query in (
            "label=bigboi",
            "label=other&hours=24",
            "label=bigboi&hours=1.5",
            "label=bigboi&hours=24&hours=6",
            "label=bigboi&hours=24&extra=1",
        ):
            self.assertEqual(self.client.get(f"/api/disks/history?{query}").status_code, 400, query)
        self.stats.history.side_effect = ValueError("hours must be from 1 to 720")
        self.assertEqual(self.client.get("/api/disks/history?label=bigboi&hours=999").status_code, 400)

    def test_monitor_failures_are_unavailable_not_server_errors(self):
        self.stats.latest.side_effect = SystemMonitorCommandError("timed out")
        self.stats.history.side_effect = SystemMonitorCommandError("timed out")
        self.assertEqual(self.client.get("/api/disks/stats").status_code, 503)
        self.assertEqual(self.client.get("/api/disks/history?label=bigboi&hours=6").status_code, 503)


if __name__ == "__main__":
    unittest.main()
