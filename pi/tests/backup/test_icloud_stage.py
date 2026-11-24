"""Rebuild only a proven-bad unpublished Borg stage, with durable cleanup and priority."""
import contextlib
from dataclasses import replace
import errno
import io
import json
import os
import signal
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import icloud_backup as backup
import icloud_stage
from icloud_status import BackupFailure, FAILURE_MESSAGES, attempt_row, reason
from pi.tests.backup.test_icloud_backup import WorkerFixture, test_config

NAME = 'vanpi-20261121T104047Z-3d30dcf0'
REPOSITORY_ERROR = 'Finished full repository check, errors found.\n'
ARCHIVE_ERROR = 'Archive consistency check complete, problems found.\n'
INTEGRITY_ERROR = 'Data integrity error: Segment entry checksum mismatch [segment 8, offset 9]\n'


def eio(path):
    return f"OSError: [Errno 5] Input/output error: '{path}'\n"


class SnapshotRejectionTests(unittest.TestCase):
    def snapshot(self, root, report=None, state=None):
        def run(args, cfg, **kwargs):
            if args[1] == 'check' and report is not None:
                raise backup.command_failure('/usr/bin/borg', 'check', *report)
        with mock.patch.object(backup, 'run', side_effect=run), mock.patch.object(backup.backup_locks, 'held'):
            backup.make_snapshot(test_config(), root, root / 'source', NAME, state)

    def test_borg_eio_names_a_staged_repository_path(self):
        with tempfile_root() as root:
            dest = root / NAME / 'payload' / 'repository'
            with self.assertRaises(icloud_stage.StageRejected) as caught:
                self.snapshot(root, (2, eio(dest / 'data/0/8')))
            self.assertEqual(caught.exception.failure_code, BackupFailure.STAGE_UNREADABLE)
            self.assertFalse((dest.parent / 'manifest.json').exists())

    def test_exact_borg_integrity_diagnostics_reject_even_when_borg_returns_warning(self):
        for code, detail in ((1, REPOSITORY_ERROR), (1, ARCHIVE_ERROR), (2, INTEGRITY_ERROR)):
            with self.subTest(detail), tempfile_root() as root:
                with self.assertRaises(icloud_stage.StageRejected) as caught:
                    self.snapshot(root, (code, detail))
                self.assertEqual(caught.exception.failure_code, BackupFailure.STAGE_CHECK_FAILED)

    def test_non_integrity_failures_keep_the_stage(self):
        with tempfile_root() as root:
            for code, detail in ((-signal.SIGKILL, ARCHIVE_ERROR), (143, REPOSITORY_ERROR),
                                 (1, 'Finished full repository check, no problems found.\n'),
                                 (2, 'Failed to create/acquire the lock (timeout).\n'),
                                 (2, eio(root / 'state.json')),
                                 (2, eio(root / NAME / 'payload/repository/../state.json')),
                                 (2, eio(root / NAME / 'payload/repository-other/8'))):
                with self.subTest(code=code, detail=detail), self.assertRaises(RuntimeError) as caught:
                    self.snapshot(root, (code, detail))
                self.assertNotIsInstance(caught.exception, icloud_stage.StageRejected)
                self.assertEqual(caught.exception.returncode, code)
                self.assertTrue((root / NAME).is_dir())

    def test_open_and_read_eio_name_the_stage_but_guard_eio_propagates(self):
        with tempfile_root() as root:
            (root / 'config').write_text('c')
            error = OSError(errno.EIO, 'Input/output error')
            stream = mock.MagicMock()
            stream.__enter__.return_value = stream
            stream.read.side_effect = error
            for operation in ({'side_effect': error}, {'return_value': stream}):
                with self.subTest(operation), mock.patch.object(Path, 'open', **operation), \
                        self.assertRaises(icloud_stage.StageReadError) as caught:
                    backup.file_manifest(root)
                self.assertEqual(caught.exception.repository, root)
            with self.assertRaises(OSError) as caught:
                backup.file_manifest(root, mock.Mock(side_effect=error))
            self.assertIs(caught.exception, error)

    def test_walk_enumeration_error_cannot_return_an_incomplete_manifest(self):
        with tempfile_root() as root:
            (root / 'data').mkdir()
            (root / 'config').write_text('c')
            scandir = os.scandir
            error = OSError(errno.EIO, 'Input/output error')

            def listing(path):
                if Path(path) == root / 'data':
                    raise error
                return scandir(path)

            with mock.patch.object(os, 'scandir', side_effect=listing), \
                    self.assertRaises(icloud_stage.StageReadError) as caught:
                backup.file_manifest(root)
            self.assertEqual(caught.exception.repository, root)

    def test_stamp_is_persisted_before_rsync_and_not_refreshed_after_borg_check(self):
        with tempfile_root() as root:
            stamp = root / 'borg_ok'
            stamp.touch()
            os.utime(stamp, (1000, 1000))
            state = {'pending': NAME}

            def run(args, cfg, **kwargs):
                if args[1] == 'with-lock':
                    self.assertEqual(backup.read_json(root / 'state.json')['pending_borg_stamp_mtime'], 1000)
                elif args[1] == 'check':
                    os.utime(stamp, (2000, 2000))
                    raise backup.command_failure('/usr/bin/borg', 'check', 1, REPOSITORY_ERROR)

            with mock.patch.dict(os.environ, {'VANPI_ICLOUD_BORG_STAMP': str(stamp)}), \
                    mock.patch.object(backup, 'STATE_DIR', root), mock.patch.object(backup, 'run', side_effect=run), \
                    mock.patch.object(backup.backup_locks, 'held'), self.assertRaises(icloud_stage.StageRejected):
                backup.make_snapshot(test_config(), root, root / 'source', NAME, state)
            icloud_stage.record(state, NAME, BackupFailure.STAGE_CHECK_FAILED)
            self.assertEqual(state['stage_rejected']['borg_stamp_mtime'], 1000)


class GuardedDiscardTests(unittest.TestCase):
    def setUp(self):
        self.mount = self.enterContext(tempfile_root()) / 'mount'
        stage = self.mount / 'icloud-weekly'
        self.generation = stage / NAME
        stage.mkdir(parents=True, mode=0o700)
        self.payload = self.generation / 'payload'
        self.payload.mkdir(parents=True)

    def discard(self, verify=lambda mount_id: None):
        icloud_stage.discard(self.mount, self.generation, verify)

    def test_published_markers_including_dangling_symlink_are_never_discarded(self):
        marker = self.payload / 'manifest.json'
        marker.symlink_to('missing')
        for path in (marker, self.generation / '_COMPLETE.json'):
            with self.subTest(path=path):
                if path != marker:
                    path.write_text('{}')
                with self.assertRaisesRegex(RuntimeError, 'published for upload'):
                    self.discard()
                path.unlink()

    def test_nested_same_device_mount_refuses_before_removing_any_files(self):
        retained = self.payload / 'first-file'
        retained.touch()
        nested = self.payload / 'nested-mount'
        nested.mkdir()
        child = nested / 'other-disk-file'
        child.touch()
        inode = nested.stat().st_ino
        with mock.patch.object(icloud_stage, 'mount_id', side_effect=lambda fd: 2 if os.fstat(fd).st_ino == inode else 1):
            with self.assertRaisesRegex(RuntimeError, 'mount boundary'):
                self.discard()
        self.assertTrue(retained.exists())
        self.assertTrue(child.exists())

    @unittest.skipUnless(hasattr(os, 'O_PATH'), 'file bind mount identity requires Linux O_PATH')
    def test_same_device_file_bind_mount_refuses_before_deleting(self):
        target = self.payload / 'file-mount'
        target.touch()
        inode = target.stat().st_ino
        with mock.patch.object(icloud_stage, 'mount_id', side_effect=lambda fd: 2 if os.fstat(fd).st_ino == inode else 1):
            with self.assertRaisesRegex(RuntimeError, 'mount boundary'):
                self.discard()
        self.assertTrue(target.exists())

    def test_mount_swap_during_path_verification_refuses_both_trees(self):
        original = self.mount.with_name('original')

        def swap(mount_id):
            self.mount.rename(original)
            self.payload.mkdir(parents=True)

        with self.assertRaisesRegex(RuntimeError, 'mount changed during verification'):
            self.discard(swap)
        self.assertTrue((original / self.payload.relative_to(self.mount)).is_dir())
        self.assertTrue(self.payload.is_dir())

    def test_symlink_to_external_directory_is_unlinked_not_traversed(self):
        outside = self.mount.parent / 'outside'
        outside.mkdir()
        (outside / 'must-stay').touch()
        (self.payload / 'link').symlink_to(outside)
        self.discard()
        self.assertFalse(self.generation.exists())
        self.assertTrue((outside / 'must-stay').exists())


class RestageGateTests(unittest.TestCase):
    def test_first_known_rejection_restages_at_once_later_wait_for_newer_borg(self):
        state = {'pending_borg_stamp_mtime': 100.0}
        icloud_stage.record(state, NAME, BackupFailure.STAGE_UNREADABLE, now=1)
        self.assertFalse(icloud_stage.must_wait(state, lambda: self.fail('unnecessary read')))
        icloud_stage.record(state, NAME, BackupFailure.STAGE_UNREADABLE, now=2)
        self.assertEqual(state['stage_rejected']['count'], 2)
        self.assertTrue(icloud_stage.must_wait(state, lambda: 100.0))
        self.assertTrue(icloud_stage.must_wait(state, lambda: None))
        self.assertFalse(icloud_stage.must_wait(state, lambda: 101.0))
        icloud_stage.clear(state)
        self.assertFalse(icloud_stage.must_wait(state, lambda: self.fail('no rejection: stamp never read')))

    def test_unknown_baseline_waits_even_on_first_rejection(self):
        state = {}
        icloud_stage.record(state, NAME, BackupFailure.STAGE_UNREADABLE, now=100)
        for stamp in (None, 99.0, 100.0):
            self.assertTrue(icloud_stage.must_wait(state, lambda: stamp))
        self.assertFalse(icloud_stage.must_wait(state, lambda: 100.1))

    def test_dashboard_reasons(self):
        for code in (BackupFailure.STAGE_UNREADABLE, BackupFailure.STAGE_CHECK_FAILED, BackupFailure.DISK_ERROR):
            self.assertEqual(reason({'phase': 'error', 'failure_code': code.value}), FAILURE_MESSAGES[code])
        self.assertIn('next local Borg backup', reason({'phase': 'deferred', 'last_error': icloud_stage.WAIT_MESSAGE}))


class StuckStageTests(WorkerFixture):
    def setUp(self):
        super().setUp()
        self.write_state(last_success_at=time.time() - 8 * 86400, retention_done_for=self.LAST, pending=NAME)
        repo = self.root / 'borg'; repo.mkdir()
        (repo / 'config').write_text('[repository]\nkey = encrypted-test-key\n')
        self.stamp = self.root / 'borg_ok'; self.stamp.touch()
        os.utime(self.stamp, (1000, 1000))
        self.enterContext(mock.patch.dict(os.environ, {'BORG_REPO': str(repo), 'VANPI_ICLOUD_BORG_STAMP': str(self.stamp)}))
        self.enterContext(mock.patch.object(backup, 'require_fresh_borg'))
        self.enterContext(mock.patch.object(backup.shutil, 'disk_usage', return_value=mock.Mock(free=10**15)))
        self.damaged = self.stage / NAME / 'payload' / 'repository' / 'data' / '0' / '8'
        self.damaged.parent.mkdir(parents=True)
        self.damaged.write_bytes(b'bad sector')
        self.copies = []
        self.snapshot = self.enterContext(mock.patch.object(backup, 'make_snapshot', side_effect=self.reject_snapshot))

    def reject_snapshot(self, cfg, root, repo, name, state):
        self.copies.append(name)
        state['pending_borg_stamp_mtime'] = self.stamp.stat().st_mtime
        raise icloud_stage.StageReadError(root / name / 'payload' / 'repository')

    def run_quietly(self, code):
        with contextlib.redirect_stderr(io.StringIO()) as log:
            state = self.run_worker(code)
        return state, log.getvalue()

    def test_unreadable_stage_is_discarded_reported_and_copied_afresh(self):
        state, log = self.run_quietly(1)
        self.assertEqual(self.copies, [NAME])
        self.assertNotIn(NAME, self.local_names())
        self.assertIsNone(state['pending'])
        self.assertEqual((state['phase'], state['failure_code']), ('error', BackupFailure.STAGE_UNREADABLE.value))
        self.assertEqual(state['stage_rejected']['generation'], NAME)
        self.assertIn('Discarding staged copy ' + NAME, log)
        self.notify.assert_called_with(state, 'vanpi backup disk read error', FAILURE_MESSAGES[BackupFailure.STAGE_UNREADABLE])
        history = backup.read_json(self.state_dir / 'history.json')
        self.assertEqual(history['attempts'][-1]['generation'], NAME)
        self.assertEqual(attempt_row(state)['generation'], NAME)
        state, _ = self.run_quietly(1)
        self.assertEqual(len(self.copies), 2)
        self.assertNotEqual(self.copies[1], NAME)
        self.assertTrue(backup.GENERATION.fullmatch(self.copies[1]))
        self.assertNotIn(self.copies[1], self.local_names())
        state, _ = self.run_quietly(0)
        self.assertEqual(len(self.copies), 2)
        self.assertEqual((state['phase'], state['last_error']), ('deferred', icloud_stage.WAIT_MESSAGE))
        self.assertIsNone(state['pending'])
        os.utime(self.stamp, (2000, 2000))
        self.run_quietly(1)
        self.assertEqual(len(self.copies), 3)

    def test_rsync_receiver_eio_alerts_without_condemning_the_stage(self):
        self.snapshot.side_effect = backup.command_failure('borg', 'with-lock', 23, 'rsync: write failed: Input/output error')
        state, _ = self.run_quietly(1)
        self.assertEqual((state['pending'], state['failure_code']), (NAME, 'disk_error'))
        self.assertTrue(self.damaged.exists())
        self.assertNotIn('stage_rejected', state)
        self.notify.assert_called_with(state, 'vanpi backup disk I/O error', FAILURE_MESSAGES[BackupFailure.DISK_ERROR])

    def test_partial_cleanup_is_journaled_and_restart_retries_only_cleanup(self):
        def partial(*args):
            persisted = backup.read_json(self.state_dir / 'state.json')
            self.assertEqual(persisted['pending'], NAME)
            self.assertEqual(persisted['stage_rejected']['state'], 'discarding')
            self.assertEqual(persisted['stage_rejected']['count'], 1)
            self.damaged.unlink(missing_ok=True)
            raise RuntimeError('deletion failed after removing a file')

        with mock.patch.object(icloud_stage, 'discard', side_effect=partial) as discard:
            _, log = self.run_quietly(1)
            self.assertIn('deletion failed after removing a file', log)
            self.run_quietly(1)
        self.assertEqual(discard.call_count, 2)
        self.assertEqual(self.copies, [NAME])
        self.snapshot.side_effect = backup.Deferred('stop after claiming replacement')
        state, _ = self.run_quietly(0)
        self.assertNotEqual(state['pending'], NAME)
        self.assertEqual(state['stage_rejected']['state'], 'discarded')
        self.assertEqual(state['stage_rejected']['count'], 1)
        self.assertNotIn(NAME, self.local_names())

    def test_a_changed_pending_name_is_not_discarded(self):
        state = {'pending': self.NEWER}
        with mock.patch.object(icloud_stage, 'discard') as delete, \
                self.assertRaisesRegex(RuntimeError, 'pending generation changed'):
            backup.discard_stage(state, self.root, self.stage / NAME, RuntimeError('x'))
        delete.assert_not_called()
        self.assertIn(NAME, self.local_names())

    def set_priority(self, pinned):
        priority = backup.priority
        jobs = {**priority.JOBS, priority.PriorityMode.PI: replace(priority.JOBS[priority.PriorityMode.PI], directory=self.state_dir)}
        self.enterContext(mock.patch.object(priority, 'JOBS', jobs))
        priority.DIRECTORY.mkdir(mode=0o700)
        self.priority_record = priority.DIRECTORY / 'priority.json'
        backup.atomic_json(self.priority_record, {'version': 1, 'mode': 'pi', 'requested_at': time.time() - 1,
                    'generation': pinned, 'previous_success_at': time.time() - 8 * 86400})

    def test_replacement_verification_releases_priority_across_two_restarts(self):
        priority = backup.priority
        self.set_priority(NAME)
        self.run_quietly(1)
        self.assertFalse(priority.allows('time-machine'))
        # A second interruption happens after claiming the replacement but before updating the policy record.
        with mock.patch.object(priority, 'replace_rejected_pi_generation', side_effect=backup.Deferred('test interruption')):
            state, _ = self.run_quietly(0)
        replacement = state['pending']
        self.assertNotEqual(replacement, NAME)
        self.assertEqual(backup.read_json(self.priority_record)['generation'], NAME)
        self.assertEqual(self.copies, [NAME])

        self.snapshot.side_effect = lambda cfg, root, repo, name, state: self.local_generation(name, complete=False)
        fake_rclone = self.rclone

        def rclone(args, cfg, **kwargs):
            if args[1] == 'mkdir':
                self.remote[replacement] = {}
            elif args[1] == 'copyto':
                self.remote[replacement]['_COMPLETE.json'] = Path(args[2]).read_text()
                return ''
            return fake_rclone(args, cfg, **kwargs)

        with mock.patch.object(backup, 'run', side_effect=rclone), \
                mock.patch.object(backup, 'verification_plan', return_value=({}, 'hash')), \
                mock.patch.object(backup, 'verify_generation', return_value={}):
            state, _ = self.run_quietly(0)
        self.assertEqual(state['last_generation'], replacement)
        self.assertNotIn('stage_rejected', state)
        policy = backup.read_json(self.priority_record)
        self.assertEqual(policy['generation'], replacement)
        self.assertIn('completed_at', policy)
        self.assertTrue(priority.allows('time-machine'))

    def test_restage_does_not_override_newer_operator_selection(self):
        self.set_priority(NAME)
        self.run_quietly(1)
        new = {**backup.read_json(self.priority_record), 'generation': self.NEWER, 'requested_at': time.time()}
        backup.atomic_json(self.priority_record, new)
        self.snapshot.side_effect = backup.Deferred('stop after claiming replacement')
        self.run_quietly(0)
        self.assertEqual(backup.read_json(self.priority_record), new)


@contextlib.contextmanager
def tempfile_root():
    with tempfile.TemporaryDirectory() as tmp:
        yield Path(tmp)


if __name__ == '__main__':
    unittest.main()
