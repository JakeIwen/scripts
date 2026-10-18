"""Public ingestion, upgrade, and offline compaction regression coverage."""

import contextlib
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest import mock

from pi.scripts.system_monitor import cli, crash, daemon, journal, maintenance, report
from pi.scripts.system_monitor.coalesce import INDIVIDUAL_LIMIT
from pi.scripts.system_monitor.common import EVENT_STATE_RETENTION_DAYS, json_dumps
from pi.scripts.system_monitor.store import EventStore

NOW = 1_800_000_000
BOOT = "0123456789abcdef0123456789abcdef"
MESSAGE = "usb 1-1: device descriptor read/64, error -71"


def journal_record(index, boot=BOOT, second=120, message=MESSAGE):
    return {
        "_BOOT_ID": boot, "MESSAGE": message,
        "__MONOTONIC_TIMESTAMP": str(second * 1_000_000 + index),
        "__REALTIME_TIMESTAMP": str((NOW - 60) * 1_000_000 + index),
    }


def create_v4(path, records, old_state=True):
    with contextlib.closing(sqlite3.connect(path)) as connection, connection:
        connection.executescript("""
            CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE events(
                id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp REAL NOT NULL,
                boot_id TEXT, category TEXT NOT NULL, kind TEXT NOT NULL,
                severity TEXT NOT NULL, source TEXT NOT NULL, summary TEXT NOT NULL,
                message TEXT NOT NULL, fingerprint TEXT NOT NULL UNIQUE, state_json TEXT
            );
            CREATE INDEX events_timestamp_idx ON events(timestamp DESC);
            CREATE INDEX events_kind_timestamp_idx ON events(kind, timestamp DESC);
            CREATE INDEX events_report_idx ON events(timestamp, kind, severity, category);
            CREATE TABLE resource_rollups(period_end REAL, metrics_json TEXT);
            PRAGMA journal_mode=WAL;
            PRAGMA user_version=4;
        """)
        connection.execute("INSERT INTO meta VALUES ('last_boot_id', ?)", (json_dumps(BOOT),))
        for raw in records:
            event = journal.parse_journal_record(raw)
            keys = ("timestamp", "boot_id", "category", "kind", "severity", "source",
                    "summary", "message", "fingerprint")
            connection.execute(
                "INSERT INTO events(timestamp, boot_id, category, kind, severity, source, "
                "summary, message, fingerprint, state_json) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (*[event[k] for k in keys], json_dumps({"payload": "x" * 7600})),
            )
        if old_state:
            connection.execute(
                "INSERT INTO events(timestamp, boot_id, category, kind, severity, source, "
                "summary, message, fingerprint, state_json) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (NOW - 40 * 86400, BOOT, "system", "old_event", "info", "monitor",
                 "old", "old", "old-fingerprint", json_dumps({"old": "x" * 7600})),
            )


class MonitorFixture(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "events.sqlite3"

    def store(self, read_only=False):
        store = EventStore(str(self.path), clock=lambda: NOW, read_only=read_only)
        self.addCleanup(store.close)
        return store



class MonitorStormTests(MonitorFixture):
    def test_storm_bounds_rows_snapshots_and_preserves_report_counts(self):
        store = self.store()
        for index in range(750):
            store.insert_event(**journal.parse_journal_record(journal_record(index)),
                               state={"usb_devices": "x" * 7600})
        events = report.list_events(store, 24, 1000, include_state=True)
        self.assertEqual(len(events), INDIVIDUAL_LIMIT + 1)
        self.assertEqual(sum(e["state"] is not None for e in events), 1)
        self.assertEqual(sum(e.get("repeat_count", 1) for e in events), 750)
        aggregate = next(e for e in events if e.get("repeat_count", 1) > 1)
        self.assertEqual(aggregate["last_seen"], (NOW - 60) + 749 / 1_000_000)
        self.assertIsNotNone(aggregate["state_event_id"])
        result = report.build_report(store, limit=1)
        self.assertEqual(result["summary"]["events"], 750)
        self.assertEqual(result["diagnosis"]["evidence"]["usb_failures"], 750)
        self.assertEqual(store.usb_count_between(NOW - 61, NOW)[0], 750)
        self.assertEqual(report.build_diagnosis(events, {})["evidence"]["usb_failures"], 750)

    @unittest.skipUnless(sqlite3.sqlite_version_info >= (3, 45), "older SQLite planner choice")
    def test_report_query_uses_covering_index(self):
        store = self.store()
        statements = []
        store.connection.set_trace_callback(statements.append)
        store.grouped_event_counts(0)
        query = statements[-1]
        plan = store.connection.execute("EXPLAIN QUERY PLAN " + query).fetchall()
        self.assertTrue(any("COVERING INDEX" in r[3] for r in plan))

    def test_restart_backfill_and_live_overlap_are_idempotent_including_ties(self):
        raw = [journal_record(i) for i in range(75)]
        raw.append(journal_record(74, message=MESSAGE + " another device"))
        store = self.store()
        for record in raw:
            store.insert_event(**journal.parse_journal_record(record), state={"live": True})
        store.close()
        reopened = self.store()
        service = daemon.SystemEventMonitor(reopened)
        output = "\n".join(json.dumps(record) for record in raw)
        with mock.patch.object(daemon.subprocess, "run", return_value=mock.Mock(
            returncode=0, stdout=output, stderr="",
        )):
            self.assertEqual(service.backfill_journal(), 0)
        new = journal_record(75)
        service.journal_queue.put(("event", journal.parse_journal_record(new)))
        with mock.patch.object(daemon, "collect_usb_state", return_value=[]), mock.patch.object(
            daemon, "collect_mount_state", return_value=[],
        ):
            self.assertEqual(service.drain_journal(), 1)
        self.assertEqual(report.build_report(reopened)["summary"]["events"], 77)

    def test_boot_window_kind_and_severity_boundaries_are_independent(self):
        store = self.store()
        for boot in (BOOT, "another-boot"):
            for second in (120, 180):
                for message in (MESSAGE, "Soft temperature limit normal", "Soft temperature limit active"):
                    for i in range(25):
                        event = journal.parse_journal_record(journal_record(i, boot, second, message))
                        store.insert_event(**event, state={"state": 1})
        grouped = {tuple(row[:3]): row[3] for row in store.grouped_event_counts(0)}
        self.assertEqual(grouped, {
            ("usb_error", "warning", "usb"): 100,
            ("soft_temperature_limit", "info", "thermal"): 100,
            ("soft_temperature_limit", "warning", "thermal"): 100,
        })
        self.assertEqual(len(report.list_events(store, 24, 1000)), 4 * (INDIVIDUAL_LIMIT + 1 + 25 + 25))

    def test_v4_readers_do_not_migrate_and_writer_does(self):
        create_v4(self.path, [journal_record(0)], old_state=False)
        before = self.path.read_bytes()
        reader = self.store(read_only=True)
        self.assertEqual(report.build_report(reader)["summary"]["events"], 1)
        self.assertEqual(report.list_events(reader, 24, 10)[0].get("repeat_count", 1), 1)
        self.assertEqual(reader.usb_count_between(NOW - 120, NOW)[0], 1)
        self.assertEqual(reader.power_transition_rows(0), [])
        self.assertEqual(reader.connection.execute("PRAGMA user_version").fetchone()[0], 4)
        reader.close()
        self.assertEqual(self.path.read_bytes(), before)
        writer = self.store()
        self.assertEqual(writer.connection.execute("PRAGMA user_version").fetchone()[0], 5)
        self.assertEqual(report.build_report(writer)["summary"]["events"], 1)

    def test_prune_removes_only_old_snapshots_and_releases_free_pages(self):
        store = self.store()
        cutoff = NOW - EVENT_STATE_RETENTION_DAYS * 86400
        for i, timestamp in enumerate((cutoff - 1, cutoff, NOW)):
            event = journal.parse_journal_record(journal_record(i))
            event.pop("journal_monotonic")
            event["timestamp"] = timestamp
            store.insert_event(**event, state={"payload": "x" * 7600})
        self.assertEqual(store.connection.execute("PRAGMA auto_vacuum").fetchone()[0], 2)
        statements = []
        store.connection.set_trace_callback(statements.append)
        store.prune()
        events = report.list_events(store, 24 * 31, 10, include_state=True)
        self.assertEqual(len(events), 3)
        self.assertIsNone(events[-1]["state"])
        self.assertIsNotNone(events[-2]["state"])
        self.assertTrue(any("incremental_vacuum" in sql for sql in statements))

    def test_crash_analysis_counts_represented_events(self):
        analysis = crash.analyze_previous_boot([{
            "timestamp": NOW, "message": MESSAGE, "monotonic": "1", "boot_id": BOOT,
            "priority": 4, "source": "kernel", "repeat_count": 85,
        }])
        self.assertEqual(analysis["counts"]["usb_error"], 85)


class MonitorCompactionTests(MonitorFixture):
    def run_cli(self, *args):
        with contextlib.redirect_stdout(io.StringIO()) as stdout, contextlib.redirect_stderr(
            io.StringIO()
        ) as stderr, mock.patch.object(maintenance, "utc_timestamp", return_value=NOW):
            status = cli.main(["--database", str(self.path), "compact", *args])
        return status, stdout.getvalue(), stderr.getvalue()

    def test_dry_run_apply_shrink_replay_and_expired_state(self):
        records = [journal_record(i) for i in range(1200)]
        create_v4(self.path, records)
        before_bytes = self.path.stat().st_size
        before_hash = hashlib.sha256(self.path.read_bytes()).hexdigest()
        reader = self.store(read_only=True)
        before_counts = [tuple(row) for row in reader.grouped_event_counts(0)]
        reader.close()
        status, output, error = self.run_cli()
        self.assertEqual((status, error), (0, ""))
        self.assertEqual(hashlib.sha256(self.path.read_bytes()).hexdigest(), before_hash)
        status, output, error = self.run_cli("--apply")
        self.assertEqual((status, error), (0, ""))
        self.assertLess(self.path.stat().st_size, before_bytes // 3)
        store = self.store()
        self.assertEqual([tuple(row) for row in store.grouped_event_counts(0)], before_counts)
        self.assertEqual(store.connection.execute("PRAGMA auto_vacuum").fetchone()[0], 2)
        old = store.connection.execute("SELECT state_json FROM events WHERE kind='old_event'").fetchone()
        self.assertIsNone(old[0])
        self.assertEqual(sum(store.insert_event(**journal.parse_journal_record(r)) for r in records), 0)
        self.assertEqual(store.connection.execute("SELECT COUNT(*) FROM events").fetchone()[0], INDIVIDUAL_LIMIT + 2)
        store.set_meta("last_boot_id", "next-boot")
        store.prune()
        self.assertEqual(store.connection.execute("SELECT COUNT(*) FROM legacy_event_fingerprints").fetchone()[0], 0)
        store.close()
        status, _output, error = self.run_cli("--apply")
        self.assertEqual((status, error), (0, ""))
        reopened = self.store()
        self.assertEqual([tuple(row) for row in reopened.grouped_event_counts(0)], before_counts)

    def test_exclusive_lock_refuses_idle_collector_and_sqlite_writer(self):
        store = self.store()
        store.set_meta("last_boot_id", BOOT)
        status, _output, error = self.run_cli()
        self.assertEqual(status, 1)
        store.close()
        with contextlib.closing(sqlite3.connect(self.path)) as foreign, foreign:
            foreign.execute("BEGIN IMMEDIATE")
            status, _output, error = self.run_cli("--apply")
            self.assertEqual(status, 1)

    def test_low_space_refuses_before_changing_database(self):
        create_v4(self.path, [journal_record(i) for i in range(30)])
        before = self.path.read_bytes()
        with mock.patch.object(maintenance.shutil, "disk_usage", return_value=mock.Mock(free=0)):
            status, _output, error = self.run_cli("--apply")
        self.assertEqual(status, 1)
        self.assertEqual(self.path.read_bytes(), before)

    def test_partial_apply_is_resumable_without_count_loss(self):
        create_v4(self.path, [journal_record(i) for i in range(1100)])

        def interrupted(message):
            if message.startswith("Compacted"):
                raise InterruptedError("simulated interrupted maintenance")

        with self.assertRaises(InterruptedError):
            maintenance.compact_database(self.path, apply=True, now=NOW, progress=interrupted)
        maintenance.compact_database(self.path, apply=True, now=NOW, progress=lambda _: None)
        store = self.store()
        self.assertEqual(report.build_report(store)["summary"]["events"], 1100)
        self.assertEqual(sum(store.insert_event(**journal.parse_journal_record(journal_record(i)))
                             for i in range(1100)), 0)

    def test_post_fold_space_check_fails_closed_before_vacuum(self):
        create_v4(self.path, [journal_record(i) for i in range(30)])
        with mock.patch.object(maintenance.shutil, "disk_usage", side_effect=[
            mock.Mock(free=10**10), mock.Mock(free=0),
        ]):
            status, output, error = self.run_cli("--apply")
        self.assertEqual(status, 1)
        store = self.store()
        self.assertEqual(report.build_report(store)["summary"]["events"], 30)

    def test_compaction_retains_lock_through_batches_and_vacuum(self):
        create_v4(self.path, [journal_record(i) for i in range(600)])
        checks = []

        def progress(message):
            if message.startswith(("Compacted", "VACUUM")):
                with contextlib.closing(sqlite3.connect(self.path, timeout=0)) as other:
                    with self.assertRaises(sqlite3.OperationalError):
                        other.execute("BEGIN IMMEDIATE")
                checks.append(message)

        maintenance.compact_database(self.path, apply=True, now=NOW, progress=progress)
        self.assertGreaterEqual(len(checks), 3)


class MonitorStorageHistoryTests(MonitorFixture):
    def disk_status(self):
        from pi.apps.van_dashboard.van_dashboard_disks import DiskManager

        root = Path(self.directory.name)
        config = root / "policy.conf"
        config.write_text("\n".join(
            f"{name}=(\n testdisk\n)" for name in
            ("MOUNT_LABELS", "ALWAYS_MOUNT_LABELS", "MANUAL_MOUNT_LABELS", "HDD_LABELS")
        ))
        boot = root / "boot-id"
        boot.write_text(BOOT)
        command = mock.Mock(return_value=mock.Mock(
            returncode=0, stdout='{"blockdevices":[]}', stderr="",
        ))
        manager = DiskManager(
            config=str(config), event_database=str(self.path), boot_id_path=str(boot),
            hold_dir=str(root / "holds"), health_dir=str(root / "health"),
            command=command, wall_clock=lambda: NOW,
        )
        return manager.status()["disks"][0]

    def test_v5_storage_history_resolves_deduped_snapshot_and_counts_repeats(self):
        store = self.store()
        state = {"boot_id": BOOT, "disk_io": {"devices": [{"name": "sda", "labels": ["testdisk"]}]}}
        for i in range(150):
            raw = journal_record(i, message="Buffer I/O error on dev sda1")
            store.insert_event(**journal.parse_journal_record(raw), state=state)
        disk = self.disk_status()
        self.assertEqual(disk["health"]["historical_error_count"], 150)
        self.assertEqual(disk["health"]["current_boot_error_count"], 150)

    def test_v4_storage_history_does_not_migrate(self):
        create_v4(self.path, [journal_record(0, message="Buffer I/O error on dev sda1")])
        state = {"boot_id": BOOT, "disk_io": {"devices": [{"name": "sda", "labels": ["testdisk"]}]}}
        with contextlib.closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute("UPDATE events SET state_json=?", (json_dumps(state),))
        disk = self.disk_status()
        self.assertEqual(disk["health"]["historical_error_count"], 1)
        with contextlib.closing(sqlite3.connect(self.path)) as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 4)


if __name__ == "__main__":
    unittest.main()
