"""Pending archive recovery with real crash remnants and a temporary HOME."""
import gzip
import os
from pathlib import Path
import subprocess

from log_rotate import archives, state
from test_log_rotate import LogRotateCase, PYTHON, REPOSITORY, payload
import test_log_rotate_archives as fixtures


class PendingPrefixTests(LogRotateCase):
    def history(self, name, compress):
        path = self.write('.local/state/{}/job.log'.format(name), payload('gen1'))
        self.set_policy(path, '--max-mb', '1', '--keep', '3', '--compress', 'on' if compress else 'off')
        for number in range(2, 5):
            self.assertEqual(self.run_cli('rotate', '--only', path, '--json')[0], 0)
            Path(path).write_bytes(payload('gen%d' % number))
        return path

    def archive(self, path, index, compress):
        return Path(path + '.%d%s' % (index, '.gz' if compress else ''))

    def assert_bytes(self, archive, expected, compress):
        with (gzip.open(archive, 'rb') if compress else open(archive, 'rb')) as source:
            self.assertTrue(source.read() == expected, 'archive bytes differ')

    def assert_published(self, path, compress):
        ledger = state.read_json(self.home, state.ARCHIVES_FILE)
        for index in range(1, 4):
            archive = str(self.archive(path, index, compress))
            archives.require_owned(archive, ledger)
            self.assertNotIn('pending_truncate', ledger[archive])
        self.assertEqual(len(list(Path(path).parent.glob('job.log.[0-9]*'))), 3)
        self.assertEqual(os.path.getsize(path), 0)

    def crash_late_append(self, path):
        script = """
import gzip, os, sys
from log_rotate import cli, copytruncate
path = sys.argv[1]
real_sync, real_copy = copytruncate._sync_directory, copytruncate._copy_pass
def sync(directory):
    real_sync(directory)
    with open(path, 'ab') as output:
        output.write(b'late bytes\\n' * 1000)
def copy(source, output, start, end, compress):
    if start:
        sink = gzip.GzipFile(fileobj=output, mode='wb', mtime=0) if compress else output
        sink.write(os.pread(source, 100, start))
        sink.flush()
        output.flush()
        os.fsync(output.fileno())
        os._exit(91)
    return real_copy(source, output, start, end, compress)
copytruncate._sync_directory, copytruncate._copy_pass = sync, copy
cli.main(['rotate', '--only', path, '--json'])
"""
        environment = dict(os.environ, PYTHONPATH=os.pathsep.join(
            str(REPOSITORY / part) for part in ('macbook/scripts', 'shared/python')))
        result = subprocess.run([PYTHON, '-c', script, path], env=environment,
                                capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 91)

    def test_clean_prefix_discards_only_uncommitted_late_append_and_finishes(self):
        for compress in (True, False):
            with self.subTest(compress=compress):
                path = self.history('clean-%s' % compress, compress)
                self.crash_late_append(path)
                archive = self.archive(path, 1, compress)
                inode = archive.stat().st_ino
                expected = Path(path).read_bytes()
                code, value, _ = self.run_cli('rotate', '--only', path, '--json')
                self.assertEqual((code, value['errors']), (0, []))
                self.assertEqual(archive.stat().st_ino, inode)
                self.assert_bytes(archive, expected, compress)
                self.assert_bytes(self.archive(path, 2, compress), payload('gen3'), compress)
                self.assert_bytes(self.archive(path, 3, compress), payload('gen2'), compress)
                self.assert_published(path, compress)

    def test_writer_truncation_and_regrowth_after_partial_append_keeps_pending_generation(self):
        for compress in (True, False):
            with self.subTest(compress=compress):
                path = self.history('regrown-%s' % compress, compress)
                self.crash_late_append(path)
                self.check_kept_after_regrowth(path, compress, 'prefix differs')

    def check_kept_after_regrowth(self, path, compress, reason):
        archive = self.archive(path, 1, compress)
        damaged, inode = archive.read_bytes(), archive.stat().st_ino
        current = payload('gen5', 1_400_000)
        Path(path).write_bytes(current)
        code, value, _ = self.run_cli('rotate', '--only', path, '--json')
        self.assertEqual(code, 1)
        self.assertEqual([item['path'] for item in value['rotated']], [path])
        self.assertEqual(len(value['errors']), 1)
        self.assertIn(reason, value['errors'][0]['error'])
        self.assertIn('kept', value['errors'][0]['error'])
        retained = self.archive(path, 2, compress)
        self.assertEqual(retained.stat().st_ino, inode)
        self.assertTrue(retained.read_bytes() == damaged, 'pending archive changed')
        self.assert_bytes(archive, current, compress)
        self.assert_bytes(self.archive(path, 3, compress), payload('gen3'), compress)
        self.assert_published(path, compress)
        self.assertEqual(self.run_cli('status', '--json')[1]['level'], 'warn')

    def test_unreadable_prefix_after_truncation_keeps_pending_generation(self):
        for compress in (True, False):
            for damage in ('bytes', 'permissions'):
                with self.subTest(compress=compress, damage=damage):
                    path = self.history('unreadable-%s-%s' % (compress, damage), compress)
                    fixtures.RetryRetentionTests.crash_rotation(self, path, 'truncated')
                    archive = self.archive(path, 1, compress)
                    if damage == 'bytes':
                        data = bytearray(archive.read_bytes())
                        data[0] ^= 0xff
                        archive.write_bytes(data)
                        reason = 'unreadable' if compress else 'prefix differs'
                        self.check_kept_after_regrowth(path, compress, reason)
                    else:
                        damaged, inode = archive.read_bytes(), archive.stat().st_ino
                        archive.chmod(0o000)
                        Path(path).write_bytes(payload('gen5', 1_400_000))
                        try:
                            code, value, _ = self.run_cli('rotate', '--json')
                            self.assertEqual(code, 1)
                            self.assertEqual(value['errors'], [
                                {'path': path, 'error': 'pending archive unreadable; kept'}])
                            self.assertEqual(self.archive(path, 2, compress).stat().st_ino, inode)
                            self.assertEqual(self.run_cli('status', '--json')[1]['level'], 'warn')
                        finally:
                            self.archive(path, 2, compress).chmod(0o600)
                        self.assertTrue(self.archive(path, 2, compress).read_bytes() == damaged)
                        self.assert_bytes(archive, payload('gen5', 1_400_000), compress)
                        self.assert_bytes(self.archive(path, 3, compress), payload('gen3'), compress)
                        self.assert_published(path, compress)
