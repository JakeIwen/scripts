"""Cross-uid access on disposable repositories; never mount or contact a real disk."""
import json
import os
from pathlib import Path
import stat
import unittest
from unittest.mock import Mock, patch

from offsite_config import MARKER, REPO_PATH
from offsite_repository import Commands, Interrupted, LocalRepository, SHARE_GUARD_ENTRIES, block_device_number
from offsite_remote import RemoteRepository
from pi.tests.backup.offsite_fixtures import BOOTSTRAP, OffsiteFixture
from pi.tests.backup.offsite_remote_fixtures import RemoteFixture


class ShareModesTests(unittest.TestCase):
    def setUp(self):
        self.f = OffsiteFixture()
        self.addCleanup(self.f.close)
        self.commands = Commands(self.f.cfg, Mock())
        self.repo = LocalRepository(self.f.cfg, self.commands, self.f.label)
        self.repo.mount.mkdir(mode=0o700)
        self.repo.path.mkdir(mode=0o700)
        # Only the device evidence is synthetic; traversal and chmod are real.
        self.repo.exact_mount = Mock(return_value=str(self.f.device))
        self.device_number = self.enterContext(patch('offsite_repository.block_device_number',
                                                     return_value=self.repo.mount.stat().st_dev))

    def file(self, name, mode=0o400):
        path = self.repo.path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('encrypted object')
        path.chmod(mode)
        return path

    def mode(self, path):
        return stat.S_IMODE(path.stat().st_mode)

    def test_adds_modes_only_inside_repository_and_is_idempotent(self):
        pack = self.file('data/ab/pack')
        for directory in (pack.parent, pack.parent.parent):
            directory.chmod(0o700)
        wider = self.file('executable', 0o777)
        sticky = self.repo.path / 'sticky'
        sticky.mkdir(mode=0o1777)
        sticky.chmod(0o1777)
        marker = self.repo.mount / MARKER
        marker.write_text('{}')
        marker.chmod(0o644)
        outside = self.repo.mount / '@eaDir'
        outside.mkdir(mode=0o700)
        other = outside / 'private'
        other.write_text('not a repository object')
        other.chmod(0o600)
        self.repo.share_for_smb()
        self.repo.exact_mount.assert_called_once_with()
        self.assertEqual(self.mode(pack), 0o666)
        self.assertEqual(self.mode(self.repo.path), 0o777)
        self.assertEqual(self.mode(pack.parent), 0o777)
        self.assertEqual(self.mode(wider), 0o777)
        self.assertEqual(self.mode(sticky), 0o1777)
        self.assertEqual(self.mode(marker), 0o644)
        self.assertEqual(self.mode(self.repo.mount), 0o700)
        self.assertEqual(self.mode(outside), 0o700)
        self.assertEqual(self.mode(other), 0o600)
        with patch('offsite_repository.os.fchmod', wraps=os.fchmod) as chmod:
            with patch('offsite_repository.os.open', wraps=os.open) as opened:
                self.repo.share_for_smb()
        chmod.assert_not_called()
        names = [call.args[0] for call in opened.call_args_list]
        self.assertNotIn('pack', names)
        self.assertNotIn('executable', names)
        self.assertIn('sticky', names)  # Directories still need listing.

    def test_symlinks_are_counted_and_never_followed_or_chmodded(self):
        outside = self.f.root / 'outside'
        outside.mkdir(mode=0o700)
        target = outside / 'secret'
        target.write_text('private')
        target.chmod(0o400)
        (self.repo.path / 'file-link').symlink_to(target)
        (self.repo.path / 'directory-link').symlink_to(outside, target_is_directory=True)
        before = (self.repo.path / 'file-link').lstat().st_mode
        ordinary = self.file('ordinary')
        with self.assertRaisesRegex(RuntimeError, '2 symlinks'):
            self.repo.share_for_smb()
        self.assertEqual(self.mode(target), 0o400)
        self.assertEqual(self.mode(outside), 0o700)
        self.assertEqual((self.repo.path / 'file-link').lstat().st_mode, before)
        self.assertEqual(self.mode(ordinary), 0o666)

    def test_other_filesystem_is_not_opened_or_traversed(self):
        target = self.file('nested/private')
        target.parent.chmod(0o700)
        real_stat = os.stat

        def different_device(path, *args, **kwargs):
            info = real_stat(path, *args, **kwargs)
            if path == 'nested' and kwargs.get('dir_fd') is not None:
                fields = list(info)
                fields[2] += 1  # st_dev: simulate a nested mount without mount privileges.
                return os.stat_result(fields)
            return info

        with patch('offsite_repository.os.stat', side_effect=different_device):
            with self.assertRaisesRegex(RuntimeError, '1 filesystem boundaries'):
                self.repo.share_for_smb()
        self.assertEqual(self.mode(target), 0o400)
        self.assertEqual(self.mode(target.parent), 0o700)

    def test_hard_links_cannot_change_an_outside_inode(self):
        target = self.f.root / 'outside'
        target.write_text('private')
        target.chmod(0o400)
        os.link(target, self.repo.path / 'linked')
        with self.assertRaisesRegex(RuntimeError, '1 hard links'):
            self.repo.share_for_smb()
        self.assertEqual(self.mode(target), 0o400)

    def test_replaced_entry_cannot_redirect_chmod(self):
        target = self.f.root / 'outside'
        target.write_text('private')
        target.chmod(0o400)
        entry = self.file('pack')
        real_open = os.open

        def replace_before_open(path, flags, *args, **kwargs):
            if path == 'pack':
                entry.unlink()
                entry.symlink_to(target)
            return real_open(path, flags, *args, **kwargs)

        with patch('offsite_repository.os.open', side_effect=replace_before_open):
            with self.assertRaises(OSError):
                self.repo.share_for_smb()
        self.assertEqual(self.mode(target), 0o400)

    def test_cancelled_walk_stops_and_next_run_catches_up(self):
        files = [self.file(str(index)) for index in range(SHARE_GUARD_ENTRIES * 3)]
        real_chmod = os.fchmod
        changed = 0

        def ignite(fd, mode):
            nonlocal changed
            real_chmod(fd, mode)
            changed += 1
            if changed == 3:
                self.f.cfg.ignition.touch()

        with patch('offsite_repository.os.fchmod', side_effect=ignite):
            with self.assertRaises(Interrupted):
                self.repo.share_for_smb()
        self.assertLessEqual(changed, SHARE_GUARD_ENTRIES)
        self.assertGreater(changed, 0)
        self.assertTrue(any(self.mode(path) == 0o400 for path in files))
        self.f.cfg.ignition.unlink()
        self.repo.commands = Commands(self.f.cfg, Mock())
        self.repo.share_for_smb()
        self.assertTrue(all(self.mode(path) == 0o666 for path in files))

    def test_signal_mid_walk_is_seen_on_the_next_entry(self):
        import signal
        files = [self.file(str(index)) for index in range(SHARE_GUARD_ENTRIES * 2)]
        real_chmod = os.fchmod
        changed = 0

        def stop(fd, mode):
            nonlocal changed
            real_chmod(fd, mode)
            changed += 1
            if changed == 3:
                os.kill(os.getpid(), signal.SIGTERM)

        with self.commands.signals(), patch('offsite_repository.os.fchmod', side_effect=stop):
            with self.assertRaises(Interrupted):
                self.repo.share_for_smb()
        self.assertEqual(changed, 3)
        self.assertEqual(self.commands.cancelled, 'SIGTERM')
        self.assertTrue(any(self.mode(path) == 0o400 for path in files))

    def test_pinned_mount_must_match_the_verified_block_device(self):
        pack = self.file('pack')
        self.device_number.return_value += 1
        with self.assertRaisesRegex(RuntimeError, 'mount changed'):
            self.repo.share_for_smb()
        self.device_number.assert_called_once_with(str(self.f.device))
        self.assertEqual(self.mode(pack), 0o400)

    def test_device_probe_uses_rdev_and_refuses_regular_files(self):
        # A /dev inode lives on devtmpfs; its st_rdev, not its st_dev, identifies the disk.
        with patch('offsite_repository.os.stat', return_value=Mock(st_mode=stat.S_IFBLK,
                                                                  st_dev=123, st_rdev=456)):
            self.assertEqual(block_device_number(self.f.device), 456)
        with self.assertRaisesRegex(RuntimeError, 'not a block device'):
            block_device_number(self.f.device)

    def test_sharing_itself_refuses_remote_repositories(self):
        remote = RemoteRepository(self.f.cfg, self.commands, self.f.label)
        remote.exact_mount = Mock(side_effect=AssertionError('must not contact the remote'))
        with self.assertRaisesRegex(RuntimeError, 'Only local repositories'):
            remote.share_for_smb()
        remote.exact_mount.assert_not_called()

    def test_mount_must_be_reverified_and_root_must_not_be_a_symlink(self):
        pack = self.file('pack')
        self.repo.exact_mount.side_effect = RuntimeError('wrong mount')
        with self.assertRaisesRegex(RuntimeError, 'wrong mount'):
            self.repo.share_for_smb()
        self.assertEqual(self.mode(pack), 0o400)
        self.repo.exact_mount.side_effect = None
        pack.unlink()
        self.repo.path.rmdir()
        self.repo.path.symlink_to(self.f.root, target_is_directory=True)
        with self.assertRaisesRegex(RuntimeError, 'Unsafe repository'):
            self.repo.share_for_smb()


# The hook must run before descriptor release. Use the CLI's real admission and
# locks, and assert the inherited lock remains held without inspecting run internals.
ASSERT_SHARE_HOLD = '''
from backup_locks import LockClass, LockBusy, lock_path, open_lock, try_flock
from offsite_repository import LocalRepository
original_share = LocalRepository.share_for_smb
def sharing_with_hold(repo):
    path = lock_path(LockClass.DISK, repo.label)
    fd = open_lock(path)
    try:
        try:
            try_flock(fd, True)
        except LockBusy:
            pass
        else:
            raise RuntimeError('sharing ran without the target disk hold')
    finally:
        os.close(fd)
    return original_share(repo)
LocalRepository.share_for_smb = sharing_with_hold
'''


class ShareFlowTests(unittest.TestCase):
    def setUp(self):
        self.f = OffsiteFixture()
        self.addCleanup(self.f.close)

    def ready(self):
        result = self.f.prepare()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.path = self.f.cfg.mount_root / self.f.label / REPO_PATH
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o777)
        self.assertEqual(stat.S_IMODE((self.path.parent / MARKER).stat().st_mode), 0o644)
        self.f.sources()
        self.pack = self.path / 'private-pack'
        self.pack.write_text('encrypted data')
        self.pack.chmod(0o400)

    def run_flow(self, command):
        args = ['--only', 'borg'] if command == 'backup' else []
        code = BOOTSTRAP.replace('with context:', ASSERT_SHARE_HOLD + '\nwith context:')
        return self.f.run(command, '--label', self.f.label, *args, code=code)

    def test_prepare_backup_and_check_share_under_their_holds(self):
        self.ready()  # A successful prepare must have changed the new 0700 root.
        for command in ('backup', 'check'):
            with self.subTest(command=command):
                self.pack.chmod(0o400)
                result = self.run_flow(command)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(stat.S_IMODE(self.pack.stat().st_mode), 0o666)

    def test_ordinary_failed_backup_still_shares(self):
        self.ready()
        self.f.reload()
        self.f.state['backup_failure'] = True
        self.f.save()
        result = self.run_flow('backup')
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual(stat.S_IMODE(self.pack.stat().st_mode), 0o666)

    def test_failed_check_preserves_failure_even_with_latched_stop(self):
        self.ready()
        for stopped in (False, True):
            with self.subTest(stopped=stopped):
                injection = '''
from offsite_repository import LocalRepository, CommandFailed
original_restic = LocalRepository.restic
def failed_check(repo, *args, **kwargs):
    if args[0] == 'check':
        if STOPPED:
            repo.commands.cancelled = 'SIGTERM'
            repo.commands.stop_cause()
        raise CommandFailed('restic exited 2: damaged repository evidence', 'damaged repository evidence')
    return original_restic(repo, *args, **kwargs)
def never_share(repo):
    raise AssertionError('failed check must not share')
LocalRepository.restic = failed_check
LocalRepository.share_for_smb = never_share
'''.replace('STOPPED', repr(stopped))
                code = BOOTSTRAP.replace('with context:', injection + '\nwith context:')
                result = self.f.run('check', '--label', self.f.label, code=code)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn('restic exited 2: damaged repository evidence', result.stderr)
                self.assertEqual(stat.S_IMODE(self.pack.stat().st_mode), 0o400)

    def test_failed_init_or_config_keeps_its_error_and_does_not_share(self):
        for failure in ('init', 'cat'):
            with self.subTest(failure=failure):
                fixture = OffsiteFixture()
                self.addCleanup(fixture.close)
                injection = '''
from offsite_repository import LocalRepository, CommandFailed
original_restic = LocalRepository.restic
def failed_init(repo, *args, **kwargs):
    if args[0] == FAILURE:
        raise CommandFailed('restic preparation failure evidence', 'preparation failure evidence')
    return original_restic(repo, *args, **kwargs)
def never_share(repo):
    raise AssertionError('failed init/config must not share')
LocalRepository.restic = failed_init
LocalRepository.share_for_smb = never_share
'''.replace('FAILURE', repr(failure))
                code = BOOTSTRAP.replace('with context:', injection + '\nwith context:')
                result = fixture.run('prepare-disk', '--device', str(fixture.link), '--label', fixture.label,
                                     '--priority', '10', '--erase-confirm', 'SERIAL123', code=code)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn('restic preparation failure evidence', result.stderr)
                self.assertFalse((fixture.cfg.state / 'repos.json').exists())
                self.assertFalse((fixture.cfg.mount_root / fixture.label / MARKER).exists())

    def test_share_failure_is_cleanup_error_and_fails_good_backup(self):
        self.ready()
        outside = self.f.root / 'outside'
        outside.write_text('untouched')
        (self.path / 'unexpected-link').symlink_to(outside)
        result = self.run_flow('backup')
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        state = json.loads(result.stdout)
        self.assertEqual(state['sources']['borg']['result'], 'ok')
        self.assertEqual(state['result'], 'failed')
        self.assertIn('1 symlinks', ' '.join(state['cleanup_errors']))

    def test_stop_after_last_source_skips_sharing(self):
        self.ready()
        code = BOOTSTRAP.replace('with context:', '''
from offsite_run import Backup
original_sources = Backup.run_sources
def stop_after_sources(backup):
    original_sources(backup)
    backup.commands.cancelled = 'SIGTERM'
Backup.run_sources = stop_after_sources
with context:''')
        result = self.f.run('backup', '--label', self.f.label, '--only', 'borg', code=code)
        self.assertEqual(result.returncode, 143, result.stdout + result.stderr)
        self.assertEqual(stat.S_IMODE(self.pack.stat().st_mode), 0o400)
        self.assertEqual(json.loads(result.stdout)['result'], 'interrupted')

    def test_unverified_target_is_not_shared(self):
        self.ready()
        self.f.reload()
        self.f.state['repo_id'] = 'f' * 64
        self.f.save()
        for command in ('backup', 'check'):
            with self.subTest(command=command):
                result = self.run_flow(command)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertEqual(stat.S_IMODE(self.pack.stat().st_mode), 0o400)

    def test_interrupted_check_skips_sharing(self):
        self.ready()
        code = BOOTSTRAP.replace('with context:', '''
from offsite_repository import LocalRepository
original_restic = LocalRepository.restic
def stop_after_check(repo, *args, **kwargs):
    result = original_restic(repo, *args, **kwargs)
    if args[0] == 'check':
        repo.commands.cancelled = 'SIGTERM'
    return result
LocalRepository.restic = stop_after_check
with context:''')
        result = self.f.run('check', '--label', self.f.label, code=code)
        self.assertEqual(result.returncode, 143, result.stdout + result.stderr)
        self.assertIn('no errors were found', result.stdout)
        self.assertEqual(stat.S_IMODE(self.pack.stat().st_mode), 0o400)

    def test_refused_sharing_after_passing_check_keeps_check_output(self):
        self.ready()
        (self.path / 'stray-link').symlink_to(self.pack)
        result = self.f.run('check', '--label', self.f.label)
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('no errors were found', result.stdout)
        self.assertIn('1 symlinks', result.stdout + result.stderr)

    def test_stop_during_failed_backup_cleanup_keeps_failure_and_cause(self):
        self.ready()
        code = BOOTSTRAP.replace('with context:', '''
from offsite_run import Backup
def failed_sources(backup):
    backup.commands.cancelled = 'SIGTERM'
    raise RuntimeError('original backup failure')
Backup.run_sources = failed_sources
with context:''')
        result = self.f.run('backup', '--label', self.f.label, '--only', 'borg', code=code)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        state = json.loads(result.stdout)
        self.assertEqual(state['result'], 'failed')
        self.assertEqual(state['cause'], 'original backup failure')
        self.assertIn('SIGTERM', ' '.join(state['cleanup_notes']))
        self.assertEqual(stat.S_IMODE(self.pack.stat().st_mode), 0o400)
        self.assertIn(['mounts', 'release', self.f.label], self.f.calls())

    def test_unexpected_share_exception_still_notifies_and_saves_failure(self):
        self.ready()
        code = BOOTSTRAP.replace('with context:', '''
from offsite_repository import LocalRepository
def unexpected_failure(repo):
    raise TypeError('unexpected share failure')
LocalRepository.share_for_smb = unexpected_failure
with context:''')
        result = self.f.run('backup', '--label', self.f.label, '--only', 'borg', code=code)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        state = json.loads(result.stdout)
        self.assertEqual(state['result'], 'failed')
        self.assertIn('unexpected share failure', ' '.join(state['cleanup_errors']))
        self.assertEqual(json.loads((self.f.cfg.state / 'status.json').read_text()), state)
        notification = next(call for call in self.f.executions() if call['name'] == 'notify')
        self.assertEqual(notification['env']['VANPI_BACKUP_DISK_LOCK_FDS'], '')
        self.assertIsNone(notification['env']['VANPI_BACKUP_CLOUD_SLOT_FD'])

    def test_stop_during_sharing_releases_target_and_records_interruption(self):
        self.ready()
        code = BOOTSTRAP.replace('with context:', '''
from offsite_repository import LocalRepository
original_share = LocalRepository.share_for_smb
def stop_during_share(repo):
    repo.commands.cancelled = 'SIGTERM'
    return original_share(repo)
LocalRepository.share_for_smb = stop_during_share
with context:''')
        result = self.f.run('backup', '--label', self.f.label, '--only', 'borg', code=code)
        self.assertEqual(result.returncode, 143, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout)['result'], 'interrupted')
        self.assertEqual(stat.S_IMODE(self.pack.stat().st_mode), 0o400)
        self.assertIn(['mounts', 'release', self.f.label], self.f.calls())

    def test_remote_backup_and_check_do_not_share_local_files(self):
        remote = RemoteFixture()
        self.addCleanup(remote.close)
        result = remote.prepare()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        remote.sources()
        path = remote.cfg.mount_root / remote.label / REPO_PATH / 'private-pack'
        path.write_text('local data')
        path.chmod(0o400)
        # If either remote operation reached the local hook it would fail, even
        # if the path were not a real local Path after configure().
        with (remote.live / 'backup' / 'offsite_repository.py').open('a') as stream:
            stream.write('\ndef no_remote_sharing(self):\n    raise RuntimeError("remote called local sharing")\n'
                         'LocalRepository.share_for_smb = no_remote_sharing\n')
        for command in ('backup', 'check'):
            with self.subTest(command=command):
                args = ['--only', 'borg'] if command == 'backup' else []
                result = remote.run(command, '--remote', '--label', remote.label, *args)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o400)


if __name__ == '__main__':
    unittest.main()
