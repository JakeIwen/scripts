"""A staged copy that fails its Borg check or a read is discarded and copied again, not resumed forever."""
import contextlib
import errno
import io
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

PI = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PI / 'scripts' / 'backup'))
import icloud_backup as backup
import icloud_stage
from icloud_status import BackupFailure, FAILURE_MESSAGES, reason
from pi.tests.backup.test_icloud_backup import WorkerFixture, test_config

EIO_STDERR = ('Data integrity error: Segment entry checksum mismatch\n'
              "OSError: [Errno 5] Input/output error: '/mnt/bigboi/icloud-weekly/x/payload/repository/data/0/8'\n")


class SnapshotRejectionTests(unittest.TestCase):
    NAME = 'vanpi-20261121T104047Z-3d30dcf0'

    def snapshot(self, root, check=None, manifest=None):
        def run(args, cfg, **kwargs):
            if args[1] == 'check' and check is not None:
                raise check
        with mock.patch.object(backup, 'run', side_effect=run), mock.patch.object(backup.backup_locks, 'held'), \
                mock.patch.object(backup, 'file_manifest', side_effect=manifest, return_value={}):
            backup.make_snapshot(test_config(), root, root / 'source', self.NAME)

    def test_borg_check_reporting_a_read_error_rejects_the_stage_as_unreadable(self):
        with tempfile_root() as root:
            with self.assertRaises(icloud_stage.StageRejected) as caught:
                self.snapshot(root, check=backup.command_failure('/usr/bin/borg', 'check', 2, EIO_STDERR))
            self.assertEqual(caught.exception.failure_code, BackupFailure.STAGE_UNREADABLE)
            self.assertFalse((root / self.NAME / 'payload' / 'manifest.json').exists())

    def test_borg_check_failure_without_a_read_error_is_a_failed_check(self):
        with tempfile_root() as root:
            with self.assertRaises(icloud_stage.StageRejected) as caught:
                self.snapshot(root, check=backup.command_failure('/usr/bin/borg', 'check', 1, 'Index mismatch\n'))
            self.assertEqual(caught.exception.failure_code, BackupFailure.STAGE_CHECK_FAILED)

    def test_read_error_while_hashing_rejects_the_stage(self):
        with tempfile_root() as root:
            with self.assertRaises(icloud_stage.StageRejected) as caught:
                self.snapshot(root, manifest=OSError(errno.EIO, 'Input/output error'))
            self.assertEqual(caught.exception.failure_code, BackupFailure.STAGE_UNREADABLE)

    def test_stops_watchdogs_and_a_missing_disk_never_condemn_the_copy(self):
        for label, kwargs, expected in (
                ('deferred', {'check': backup.Deferred('ignition is on')}, backup.Deferred),
                ('watchdog', {'check': RuntimeError('borg check made no progress')}, RuntimeError),
                ('disk gone', {'manifest': OSError(errno.ENODEV, 'No such device')}, OSError)):
            with self.subTest(label), tempfile_root() as root:
                with self.assertRaises(expected) as caught:
                    self.snapshot(root, **kwargs)
                self.assertNotIsInstance(caught.exception, icloud_stage.StageRejected)

    def test_a_source_read_error_is_a_disk_read_error_but_keeps_the_stage(self):
        error = backup.command_failure('/usr/bin/borg', 'with-lock', 23, EIO_STDERR)
        self.assertIsNone(icloud_stage.rejection(error))
        self.assertEqual(backup.failure_code(error), BackupFailure.DISK_READ)
        self.assertIsNone(backup.failure_code(RuntimeError('rclone copy failed (exit 5)')))

    def test_published_stage_is_never_discarded(self):
        for published in (('payload', 'manifest.json'), ('_COMPLETE.json',)):
            with self.subTest(published), tempfile_root() as root:
                generation = root / self.NAME
                path = generation.joinpath(*published); path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('{}')
                with self.assertRaisesRegex(RuntimeError, 'published for upload'):
                    icloud_stage.discard(generation)
                self.assertTrue(path.exists())


class RestageGateTests(unittest.TestCase):
    def test_the_first_rejection_restages_at_once_and_later_ones_wait_for_a_newer_borg_backup(self):
        state = {}
        icloud_stage.record(state, 'g1', BackupFailure.STAGE_UNREADABLE, 100.0, now=1)
        self.assertFalse(icloud_stage.must_wait(state, lambda: 100.0))
        icloud_stage.record(state, 'g2', BackupFailure.STAGE_UNREADABLE, 100.0, now=2)
        self.assertEqual(state['stage_rejected']['count'], 2)
        self.assertTrue(icloud_stage.must_wait(state, lambda: 100.0))
        self.assertTrue(icloud_stage.must_wait(state, lambda: None))  # unreadable stamp: keep waiting
        self.assertFalse(icloud_stage.must_wait(state, lambda: 101.0))
        icloud_stage.clear(state)
        self.assertFalse(icloud_stage.must_wait(state, lambda: self.fail('no rejection: stamp never read')))

    def test_malformed_records_never_block_a_copy(self):
        for record in ('x', {'count': 'two', 'borg_stamp_mtime': 1.0}, {'count': 5, 'borg_stamp_mtime': None},
                       {'count': 5, 'borg_stamp_mtime': True}):
            with self.subTest(record):
                self.assertFalse(icloud_stage.must_wait({'stage_rejected': record}, lambda: 0.0))

    def test_dashboard_reasons(self):
        for code in (BackupFailure.STAGE_UNREADABLE, BackupFailure.STAGE_CHECK_FAILED, BackupFailure.DISK_READ):
            self.assertEqual(reason({'phase': 'error', 'failure_code': code.value}), FAILURE_MESSAGES[code])
        self.assertIn('next local Borg backup', reason({'phase': 'deferred', 'last_error': icloud_stage.WAIT_MESSAGE}))


class StuckStageTests(WorkerFixture):
    """The 2026-11-21 case: a pending stage whose staged files read EIO on every check."""
    STUCK = 'vanpi-20261121T104047Z-3d30dcf0'

    def setUp(self):
        super().setUp()
        self.write_state(last_success_at=time.time() - 8 * 86400, retention_done_for=self.LAST, pending=self.STUCK)
        repo = self.root / 'borg'; repo.mkdir()
        (repo / 'config').write_text('[repository]\nkey = encrypted-test-key\n')
        self.stamp = self.root / 'borg_ok'; self.stamp.write_text('')
        os.utime(self.stamp, (1000, 1000))
        self.enterContext(mock.patch.dict(os.environ, {'BORG_REPO': str(repo), 'VANPI_ICLOUD_BORG_STAMP': str(self.stamp)}))
        self.enterContext(mock.patch.object(backup, 'require_fresh_borg'))
        self.enterContext(mock.patch.object(backup.shutil, 'disk_usage', return_value=mock.Mock(free=10**15)))
        staged = self.stage / self.STUCK / 'payload' / 'repository' / 'data' / '0'
        staged.mkdir(parents=True); (staged / '8').write_bytes(b'bad sector')
        self.copies = []
        def snapshot(cfg, root, repo, name, state=None):
            self.copies.append(name)
            raise icloud_stage.StageRejected('staged repository copy could not be read and was discarded',
                                             BackupFailure.STAGE_UNREADABLE)
        self.snapshot = self.enterContext(mock.patch.object(backup, 'make_snapshot', side_effect=snapshot))

    def run_quietly(self, code):
        with contextlib.redirect_stderr(io.StringIO()) as log:
            state = self.run_worker(code)
        return state, log.getvalue()

    def test_unreadable_stage_is_discarded_reported_and_copied_afresh(self):
        state, log = self.run_quietly(1)
        self.assertEqual(self.copies, [self.STUCK])
        self.assertNotIn(self.STUCK, self.local_names())
        self.assertIsNone(state['pending'])
        self.assertEqual((state['phase'], state['failure_code']), ('error', BackupFailure.STAGE_UNREADABLE.value))
        self.assertEqual(state['stage_rejected']['generation'], self.STUCK)
        self.assertIn('Discarding staged copy ' + self.STUCK, log)
        self.notify.assert_called_with(state, 'vanpi backup disk read error',
                                       FAILURE_MESSAGES[BackupFailure.STAGE_UNREADABLE])
        # The next run copies at once, into a new generation.
        state, _ = self.run_quietly(1)
        self.assertEqual(len(self.copies), 2)
        self.assertNotEqual(self.copies[1], self.STUCK)
        self.assertTrue(backup.GENERATION.fullmatch(self.copies[1]))
        self.assertNotIn(self.copies[1], self.local_names())
        # Two in a row: wait for a newer local Borg backup instead of rereading the repository hourly.
        state, _ = self.run_quietly(0)
        self.assertEqual(len(self.copies), 2)
        self.assertEqual((state['phase'], state['last_error']), ('deferred', icloud_stage.WAIT_MESSAGE))
        self.assertIsNone(state['pending'])
        os.utime(self.stamp, (2000, 2000))
        self.run_quietly(1)
        self.assertEqual(len(self.copies), 3)

    def test_a_changed_pending_name_is_not_discarded(self):
        state = {'pending': 'vanpi-20261122T000000Z-00000000'}
        with mock.patch.object(backup.icloud_stage, 'discard') as delete, \
                self.assertRaisesRegex(RuntimeError, 'pending generation changed'):
            backup.discard_stage(self.cfg, state, self.root, self.stage / self.STUCK, RuntimeError('x'))
        delete.assert_not_called()
        self.assertIn(self.STUCK, self.local_names())


@contextlib.contextmanager
def tempfile_root():
    with tempfile.TemporaryDirectory() as tmp:
        yield Path(tmp)


if __name__ == '__main__':
    unittest.main()
