import contextlib
import io
import json
import os
import tempfile
import time
import unittest

from pi.scripts.system_monitor import cli, disk_history


def sample(at, *devices):
    return {"timestamp": at, "disk_io": {"devices": list(devices)}}


def device(name, labels, read, write, busy):
    return {
        "name": name,
        "labels": labels,
        "read_bytes_per_second": read,
        "write_bytes_per_second": write,
        "busy_percent": busy,
    }


class DiskHistoryRecorderTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = os.path.join(self.temporary.name, "disk-io")
        # The CLI reads the real clock, and readers drop records far in the future.
        self.now = int(time.time()) // 60 * 60

    def record_minutes(self, minutes, *devices):
        recorder = disk_history.DiskHistoryRecorder(self.directory)
        at = self.now - minutes * 60
        while at < self.now:
            recorder.observe(sample(at, *devices))
            at += 5
        recorder.flush()

    def test_minute_averages_per_label(self):
        recorder = disk_history.DiskHistoryRecorder(self.directory)
        start = self.now - 60
        for offset, read in ((0, 100), (5, 300)):
            recorder.add(
                sample(
                    start + offset,
                    device("sdc", ["bigboi"], read, 50, 10),
                    # Every SD card carries bootfs, so its history would mix cards.
                    device("mmcblk0", ["bootfs", "rootfs"], 0, 8, 1),
                    device("sdf", ["bootfs", "hotspare-b"], 0, 0, 0),
                    # A disk's first sample has no previous counters to rate against.
                    device("sdb", ["hdd1tb"], None, None, None),
                )
            )
        self.assertFalse(recorder.ready(start + 5))
        self.assertTrue(recorder.ready(start + 60))
        self.assertEqual(recorder.flush(), 3)
        self.assertEqual(
            sorted(os.listdir(self.directory)), ["bigboi.bin", "hotspare-b.bin", "rootfs.bin"]
        )
        self.assertEqual(
            disk_history.read_records(self.directory, "bigboi", self.now),
            [(start + 5, 200, 50, 1000)],
        )

    def test_backwards_clock_step_closes_the_minute(self):
        recorder = disk_history.DiskHistoryRecorder(self.directory)
        recorder.add(sample(self.now, device("sdc", ["bigboi"], 1, 1, 1)))
        self.assertTrue(recorder.ready(self.now - 3600))

    def test_history_buckets_rates_peaks_and_totals(self):
        self.record_minutes(10, device("sdc", ["bigboi"], 1000, 2000, 50))
        result = disk_history.history(self.directory, "bigboi", 6, self.now)
        self.assertEqual(result["bucket_seconds"], 60)
        self.assertEqual(len(result["buckets"]), 10)
        first = result["buckets"][0]
        self.assertEqual(first["minutes"], 1)
        self.assertEqual(first["read_bytes"], 60_000)
        self.assertEqual(first["write_bytes"], 120_000)
        self.assertEqual(first["peak_write_bytes_per_second"], 2000)
        self.assertEqual(first["busy_percent"], 50)

        wide = disk_history.history(self.directory, "bigboi", 720, self.now)
        self.assertEqual(wide["bucket_seconds"], 7200)
        self.assertEqual(sum(bucket["minutes"] for bucket in wide["buckets"]), 10)
        self.assertEqual(sum(bucket["read_bytes"] for bucket in wide["buckets"]), 600_000)

    def test_latest_reads_each_disks_last_minute(self):
        self.record_minutes(3, device("sdc", ["bigboi"], 7, 9, 12.5))
        result = disk_history.latest(self.directory, self.now)
        self.assertEqual(list(result["disks"]), ["bigboi"])
        self.assertEqual(result["disks"]["bigboi"]["read_bytes_per_second"], 7)
        self.assertEqual(result["disks"]["bigboi"]["busy_percent"], 12.5)
        self.assertGreater(result["disks"]["bigboi"]["at"], self.now - 60)

    def test_torn_tail_is_dropped_and_old_records_are_pruned(self):
        path = disk_history.history_path(self.directory, "bigboi")
        old = self.now - disk_history.RETENTION_SECONDS - disk_history.PRUNE_SLACK_SECONDS - 60
        disk_history.append(self.directory, "bigboi", old, 1, 1, 1)
        with open(path, "ab") as handle:
            handle.write(b"\x01\x02\x03")
        disk_history.append(self.directory, "bigboi", self.now, 5, 6, 7)
        self.assertEqual(os.path.getsize(path), disk_history.RECORD.size)
        self.assertEqual(
            disk_history.read_records(self.directory, "bigboi", self.now), [(self.now, 5, 6, 700)]
        )

    def test_rejects_labels_that_are_not_file_names(self):
        with self.assertRaises(ValueError):
            disk_history.history(self.directory, "../events", 1, self.now)
        with self.assertRaises(ValueError):
            disk_history.history(self.directory, "bigboi", 721, self.now)

    def test_cli_prints_json_for_the_dashboard(self):
        self.record_minutes(2, device("sdc", ["bigboi"], 1, 2, 3))
        database = os.path.join(self.temporary.name, "events.sqlite3")
        for argv, check in (
            (["disk-io", "--latest"], lambda payload: self.assertIn("bigboi", payload["disks"])),
            (["disk-io", "--label", "bigboi", "--hours", "1"],
             lambda payload: self.assertTrue(payload["buckets"])),
        ):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(cli.main(["--database", database, *argv]), 0)
            payload = json.loads(output.getvalue())
            self.assertIs(payload["ok"], True)
            check(payload)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(cli.main(["--database", database, "disk-io", "--label", "a/b"]), 1)
        self.assertIs(json.loads(output.getvalue())["ok"], False)


if __name__ == "__main__":
    unittest.main()
