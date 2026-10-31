#!/usr/bin/env python3
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts' / 'backup'))
import icloud_backup as backup
import icloud_status as status
import upload_speed_log as speed_log

DAY = 86400
NOW = 1_800_000_000


class UploadSpeedLogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)
        self.path = speed_log.log_path(self.directory)

    def feed(self, sampler, points):
        for at, sent in points:
            sampler.observe(self.directory, sent, now=at)

    def test_one_minute_average_per_sample_from_cumulative_bytes(self):
        sampler = speed_log.UploadSpeedSampler()
        self.feed(sampler, [(NOW + t, t * 1000) for t in range(0, 125, 5)])
        self.assertEqual(speed_log.read_samples(self.path, NOW + 200),
                         [(NOW + 60, 1000), (NOW + 120, 1000)])
        self.assertEqual(self.path.stat().st_size, 2 * speed_log.RECORD.size)

    def test_new_command_and_pauses_do_not_count_as_throughput(self):
        sampler = speed_log.UploadSpeedSampler()
        # 30 s at 2 kB/s, a fresh rclone command restarting its counter, 35 s
        # at 4 kB/s, a 10-minute pause, then 61 s at 1 kB/s.
        self.feed(sampler, [(NOW, 0), (NOW + 30, 60_000), (NOW + 35, 0), (NOW + 70, 140_000),
                            (NOW + 670, 0), (NOW + 700, 30_000), (NOW + 731, 61_000)])
        self.assertEqual(speed_log.read_samples(self.path, NOW + 800),
                         [(NOW + 70, round(200_000 / 65)), (NOW + 731, 1000)])

    def test_prune_keeps_only_thirty_days_and_drops_a_torn_tail(self):
        with self.path.open('wb') as f:
            for at in (NOW - 31 * DAY, NOW - 29 * DAY, NOW - DAY):
                f.write(speed_log.RECORD.pack(at, 5))
            f.write(b'\x01\x02\x03')
        speed_log.prune(self.path, NOW)
        self.assertEqual(speed_log.read_samples(self.path, NOW), [(NOW - 29 * DAY, 5), (NOW - DAY, 5)])
        self.assertEqual(self.path.stat().st_size, 2 * speed_log.RECORD.size)

    def test_append_realigns_after_torn_write_and_prunes_hourly(self):
        with self.path.open('wb') as f:
            f.write(speed_log.RECORD.pack(NOW - 30 * DAY - 2 * 3600, 7) + b'\x09')
        speed_log.append(self.path, NOW, 3.6)
        self.assertEqual(speed_log._records(self.path.read_bytes()), [(NOW, 4)])

    def test_unwritable_log_never_stops_an_upload(self):
        sampler = speed_log.UploadSpeedSampler()
        missing = self.directory / 'gone'
        for at in (NOW, NOW + 61):
            sampler.observe(missing, 1000 * (at - NOW), now=at)

    def test_worker_progress_records_only_upload_counters(self):
        state = {}
        with mock.patch.object(backup, 'STATE_DIR', self.directory), \
                mock.patch.object(backup, 'save_attempt'), \
                mock.patch.object(backup, 'UPLOAD_SPEED', speed_log.UploadSpeedSampler()), \
                mock.patch.object(speed_log, 'time') as clock:
            clock.time.side_effect = [NOW, NOW + 30, NOW + 61]
            backup.record_progress(state, 'uploading', command_bytes=0)
            backup.record_progress(state, 'verifying', current_file_bytes=10**9)
            backup.record_progress(state, 'uploading', command_bytes=30_000)
            backup.record_progress(state, 'uploading', command_bytes=61_000)
        self.assertEqual(speed_log.read_samples(self.path, NOW + 100), [(NOW + 61, 1000)])

    def test_status_helper_exports_fixed_pairs(self):
        speed_log.append(self.path, NOW, 1234)
        with mock.patch.object(speed_log, 'time') as clock:
            clock.time.return_value = NOW + 10
            self.assertEqual(json.loads(json.dumps(status.upload_speeds(self.directory))),
                             {'samples': [[NOW, 1234]]})

    def test_status_helper_rejects_other_arguments(self):
        script = Path(status.__file__)
        result = subprocess.run([sys.executable, '-B', str(script), '--upload-speeds', 'x'],
                                capture_output=True, text=True, cwd=script.parent)
        self.assertEqual(result.returncode, 2)


if __name__ == '__main__':
    unittest.main()
