"""Full streamed hashes, partial reports and interrupted batches retain proofs."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

import icloud_backup as cloud
from icloud_progress import TransferActivity
from time_machine_downloads import download_batch, HashReport, VerificationMeter
from time_machine_verification import ObjectVerifier


class DownloadTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.data = b'complete downloaded data'
        self.digest = hashlib.sha256(self.data).hexdigest()
        self.expected = {self.digest: {'sha256': self.digest, 'bytes': len(self.data)}}
        self.rows = {self.digest: {'Size': len(self.data), 'IsDir': False, 'ModTime': 'stable', 'ID': 'one'}}
        self.verifier = ObjectVerifier(self.expected, self.root / 'proofs.json')

    def test_partial_hash_line_is_not_a_checkpoint(self):
        output = self.root / 'hashes'
        line = (self.digest + '  ' + self.digest + '\n').encode()
        output.write_bytes(line[:-1])
        with output.open('rb') as stream:
            report = HashReport(stream, [self.digest], self.verifier, self.rows)
            report.consume()
            self.assertFalse(self.verifier.checkpoint.exists())
            with output.open('ab') as writer: writer.write(b'\n')
            report.consume()
        self.assertEqual(set(self.verifier.verified), {self.digest})

    def test_unknown_corrupt_or_mismatched_report_never_checkpoints(self):
        for line in ('x'*64+'  '+self.digest+'\n', 'a'*64+'  '+self.digest+'\n',
                     self.digest+'  '+'b'*64+'\n', 'x'*132):
            output = self.root / 'hashes'; output.write_text(line)
            with output.open('rb') as stream, self.assertRaises(RuntimeError):
                HashReport(stream, [self.digest], self.verifier, self.rows).consume()
            self.assertFalse(self.verifier.checkpoint.exists())

    def test_completed_proof_survives_batch_interruption(self):
        def interrupted(args, cfg, **kwargs):
            report = Path(args[args.index('--output-file') + 1])
            report.write_text(self.digest+'  '+self.digest+'\n')
            kwargs['progress']({'bytes': len(self.data)})
            raise cloud.Deferred('pause')
        with mock.patch.object(cloud, 'run', side_effect=interrupted), self.assertRaises(cloud.Deferred):
            download_batch({}, 'unused', [self.digest], self.verifier, self.rows, lambda _: None, lambda _: None)
        self.assertEqual(set(json.loads(self.verifier.checkpoint.read_text())), {self.digest})

    @unittest.skipUnless(shutil.which('rclone'), 'rclone integration runs on Pi')
    def test_real_rclone_streams_multiple_hashes_with_one_process(self):
        remote = self.root / 'remote'; remote.mkdir()
        for data in (self.data, b'second chunk'):
            digest = hashlib.sha256(data).hexdigest()
            (remote / digest).write_bytes(data)
            self.expected[digest] = {'sha256': digest, 'bytes': len(data)}
            self.rows[digest] = {**self.rows[self.digest], 'Size': len(data), 'ID': digest}
        def local_run(args, cfg, **kwargs):
            subprocess.run([shutil.which('rclone'), *args[1:]], check=True, capture_output=True)
        with mock.patch.object(cloud, 'run', side_effect=local_run) as run:
            download_batch({}, str(remote), sorted(self.expected), self.verifier, self.rows,
                           lambda _: None, lambda _: None)
        self.assertEqual(run.call_count, 1)
        self.assertEqual(set(self.verifier.verified), set(self.expected))

    def test_rate_includes_waits_and_survives_chunk_boundaries(self):
        now = [0]
        meter = VerificationMeter(lambda: now[0])
        now[0] = 10
        self.assertEqual(meter.sample(1000)['verification_bytes_per_second'], 100)
        now[0] = 20
        meter.activity = TransferActivity.NETWORK_CHECK
        paused = meter.sample()
        self.assertEqual(paused['verification_bytes_per_second'], 50)
        self.assertEqual(paused['verification_activity'], TransferActivity.NETWORK_CHECK)
        now[0] = 30
        self.assertEqual(meter.sample(2000)['verification_bytes_per_second'], 2000/30)
        now[0] = 100
        meter.sample()
        now[0] = 161
        self.assertEqual(meter.sample()['verification_bytes_per_second'], 0)


if __name__ == '__main__':
    unittest.main()
