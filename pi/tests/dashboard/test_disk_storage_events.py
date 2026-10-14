"""Storage-event ordering through the dashboard disk reader (no Flask needed)."""
import json
import os
import sqlite3
import tempfile
import unittest
from types import SimpleNamespace

from pi.tests.dashboard._disk_manager_support import DiskManagerFixture


class AggregatedStorageEventTests(DiskManagerFixture, unittest.TestCase):
    def test_aggregated_storage_errors_order_by_their_last_occurrence(self):
        # Aggregates are selected by first occurrence but reported by last, so an
        # older aggregate that kept repeating is the latest error.
        with tempfile.TemporaryDirectory() as tempdir:
            database = os.path.join(tempdir, "events.sqlite3")
            health_dir = os.path.join(tempdir, "health")
            os.mkdir(health_dir)
            state = json.dumps(
                {
                    "boot_id": self.PREVIOUS_BOOT_ID,
                    "disk_io": {"devices": [{"name": "sda", "labels": ["movingparts"]}]},
                }
            )
            connection = sqlite3.connect(database)
            connection.execute(
                """
                CREATE TABLE events (
                    id INTEGER PRIMARY KEY,
                    timestamp REAL,
                    category TEXT,
                    severity TEXT,
                    message TEXT,
                    state_json TEXT,
                    repeat_count INTEGER,
                    last_seen REAL,
                    state_event_id INTEGER
                )
                """
            )
            connection.executemany(
                "INSERT INTO events (timestamp, category, severity, message, state_json, "
                "repeat_count, last_seen, state_event_id) "
                "VALUES (?, 'storage', 'critical', ?, ?, ?, ?, NULL)",
                [
                    (1000, "EXT4-fs (sda1): repeating I/O error", state, 3, 1059),
                    (1030, "EXT4-fs (sda1): single I/O error", state, 1, 1030),
                ],
            )
            connection.commit()
            connection.close()

            def command(args, timeout):
                return SimpleNamespace(
                    returncode=0,
                    stdout=json.dumps(self.lsblk(movingparts_mounted=False)),
                    stderr="",
                )

            manager = self.manager(
                tempdir,
                command,
                health_dir=health_dir,
                event_database=database,
            )
            health = manager.status()["disks"][0]["health"]
            self.assertEqual(health["latest_error_at"], 1059)
            self.assertEqual(health["latest_error"], "EXT4-fs (sda1): repeating I/O error")
            self.assertEqual(health["previous_boot_error_count"], 4)


if __name__ == "__main__":
    unittest.main()
