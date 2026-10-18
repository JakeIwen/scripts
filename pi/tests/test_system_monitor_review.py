"""Cross-consumer, transient-lock and retained-history cost regressions."""

import contextlib
import sqlite3
from pathlib import Path
from unittest import mock

from pi.scripts.network_recorder.collector import Collector
from pi.scripts.network_recorder.store import Store as RecorderStore
from pi.scripts.system_monitor import daemon, journal, maintenance, report
from pi.scripts.system_monitor.store import EventStore
from pi.tests import test_system_monitor_coalescing as fixtures
from pi.tests.test_system_monitor_coalescing import (
    NOW, MonitorFixture, create_v4, journal_record,
)


@contextlib.contextmanager
def transient_read_lock(path):
    """Release a real SQLite lock after its first error, exposing fallback retries."""
    connect = sqlite3.connect
    with contextlib.closing(connect(path, timeout=0)) as blocker:
        blocker.execute("PRAGMA journal_mode=DELETE")

        class ReleasingConnection(sqlite3.Connection):
            def execute(self, *args, **kwargs):
                try:
                    return super().execute(*args, **kwargs)
                except sqlite3.OperationalError:
                    blocker.rollback()
                    raise

        def connect_reader(*args, **kwargs):
            kwargs.update(timeout=0, factory=ReleasingConnection)
            return connect(*args, **kwargs)

        with mock.patch.object(sqlite3, "connect", side_effect=connect_reader):
            yield blocker


class MonitorConsumerTests(MonitorFixture):
    def test_power_burst_is_imported_completely_across_recorder_checkpoints(self):
        store = self.store()
        monitor = daemon.SystemEventMonitor(store)
        recorder = RecorderStore(Path(self.directory.name) / "recorder.sqlite3",
                                 portable_permissions=True)
        self.addCleanup(recorder.close)
        collector = Collector(recorder, monitor_db=str(self.path), ubnt_target="")
        for first, last in ((0, 30), (30, 50)):
            for index in range(first, last):
                event = journal.parse_journal_record(journal_record(
                    index, message="Undervoltage detected!",
                ))
                self.assertTrue(monitor.insert_normalized_event(event, historical=True))
            collector.monitor()
            observed = recorder.db.execute(
                "SELECT COUNT(*), MAX(source_time) FROM events WHERE source='system-monitor'"
            ).fetchone()
            self.assertEqual(observed[0], last)
            self.assertEqual(observed[1], NOW - 60 + (last - 1) / 1_000_000)
        self.assertEqual(len(report.list_events(store, 24, 100)), 50)
        self.assertEqual(report.build_report(store)["diagnosis"]["evidence"]["undervoltage_episodes"], 50)

    def test_offline_compaction_preserves_non_storm_journal_rows(self):
        messages = ("Undervoltage detected!", "Soft temperature limit active", "Kernel panic")
        records = [journal_record(i, message=message) for message in messages for i in range(50)]
        records.extend(journal_record(i) for i in range(50))
        create_v4(self.path, records, old_state=False)
        maintenance.compact_database(self.path, apply=True, now=NOW, progress=lambda _: None)
        rows = self.store().connection.execute(
            "SELECT category, COUNT(*), SUM(repeat_count), COUNT(state_json) "
            "FROM events GROUP BY category"
        ).fetchall()
        self.assertEqual({row[0]: tuple(row[1:]) for row in rows}, {
            "power": (50, 50, 50), "thermal": (50, 50, 50),
            "kernel": (50, 50, 50), "usb": (fixtures.INDIVIDUAL_LIMIT + 1, 50, 1),
        })


class MonitorLockTests(MonitorFixture):
    def seed_storm(self, message=None):
        store = self.store()
        for index in range(75):
            raw = journal_record(index, **({"message": message} if message else {}))
            store.insert_event(**journal.parse_journal_record(raw))
        store.close()

    def test_transient_lock_is_not_retried_as_unweighted_counts(self):
        self.seed_storm()
        with transient_read_lock(self.path) as blocker:
            reader = EventStore(str(self.path), read_only=True)
            self.addCleanup(reader.close)
            reader.connection.execute("PRAGMA busy_timeout=0")
            for query in (lambda: reader.grouped_event_counts(0),
                          lambda: reader.usb_count_between(0, NOW),
                          lambda: reader.power_transition_rows(0)):
                with self.subTest(query=query):
                    blocker.execute("BEGIN EXCLUSIVE")
                    with self.assertRaises(sqlite3.OperationalError):
                        query()
            self.assertEqual(reader.grouped_event_counts(0)[0]["count"], 75)

    def test_dashboard_marks_transient_lock_as_unavailable(self):
        self.seed_storm("Buffer I/O error on dev sda1")
        with transient_read_lock(self.path) as blocker:
            blocker.execute("BEGIN EXCLUSIVE")
            disk = fixtures.MonitorStorageHistoryTests.disk_status(self)
        self.assertEqual(disk["health"]["basis"], "history_unavailable")


class MonitorExpiryTests(MonitorFixture):
    def test_expiry_work_does_not_grow_with_cleared_event_history(self):
        store = self.store()
        event = journal.parse_journal_record(journal_record(0))
        event.pop("journal_monotonic")
        event["timestamp"] = NOW - 40 * 86400
        store.insert_event(**event, state={"old": True})
        columns = "timestamp,boot_id,category,kind,severity,source,summary,message,fingerprint"
        values = [event[name] for name in columns.split(",")[:-1]]
        with store.connection:
            store.connection.executemany(
                f"INSERT INTO events({columns}) VALUES (?,?,?,?,?,?,?,?,?)",
                ((*values, f"cleared-{i}") for i in range(20000)),
            )
        event["fingerprint"] = "old-snapshot-at-end"
        store.insert_event(**event, state={"old": True})
        event.update(timestamp=NOW, fingerprint="recent-snapshot")
        store.insert_event(**event, state={"recent": True})
        steps = []
        store.connection.set_progress_handler(lambda: steps.append(1) or 0, 100)
        try:
            store.prune()
        finally:
            store.connection.set_progress_handler(None, 0)
        self.assertLess(len(steps), 50, "prune scanned cleared event history")
        rows, snapshots = store.connection.execute(
            "SELECT COUNT(*), COUNT(state_json) FROM events"
        ).fetchone()
        self.assertEqual((rows, snapshots), (20003, 1))
        self.assertIsNotNone(report.list_events(store, 24, 10, include_state=True)[0]["state"])
