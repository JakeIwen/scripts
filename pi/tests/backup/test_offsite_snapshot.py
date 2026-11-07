"""offsite_snapshot.py create/remove against a disposable live tree and state root."""
from contextlib import nullcontext
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import unittest
from unittest import mock

from offsite_config import (FROZEN_IMPLEMENTATION, MANIFEST, RCLONE_VERSION, RESTIC_INSTALLER, RUN_ID_PATTERN,
                            SNAPSHOT_RCLONE, SNAPSHOT_RESTIC)
import offsite_snapshot
from pi.tests.backup.offsite_fixtures import (CORE_PROBE, FAKE_SHEBANG, SNAPSHOT_BOOTSTRAP, OffsiteFixture,
                                              core_limits_after)
from pi.tests.backup.offsite_remote_fixtures import SMB_TRANSPORT


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def mode(path):
    return stat.S_IMODE(path.lstat().st_mode)


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.f = OffsiteFixture()
        self.addCleanup(self.f.close)
        self.code = self.f.cfg.state / 'code'

    def create(self, remote=False):
        # In-process calls hold the fixture's real job-offsite descriptor; Darwin
        # cannot read fdinfo, so only there the kernel ownership check is stubbed.
        held = mock.patch('backup_locks.require_held') if sys.platform != 'linux' else nullcontext()
        with held:
            return offsite_snapshot.create(self.f.job_fd, self.f.cfg.state, self.f.live, remote=remote)

    def assert_no_snapshot(self):
        self.assertEqual(list(self.code.iterdir()), [])

    def test_create_copies_the_closure_and_pinned_restic_privately(self):
        root = self.f.snapshot()
        self.assertEqual(root.parent, self.code)
        self.assertRegex(root.name, '^' + RUN_ID_PATTERN + '$')
        manifest = json.loads((root / MANIFEST).read_text())
        self.assertEqual(set(manifest['files']), set(FROZEN_IMPLEMENTATION))
        for relative, value in manifest['files'].items():
            copy, live = root / relative, self.f.live / relative
            self.assertEqual(digest(copy), value)
            self.assertEqual(digest(live), value)
            self.assertEqual(mode(copy), 0o700 if live.stat().st_mode & 0o111 else 0o600, relative)
        restic = root / SNAPSHOT_RESTIC
        self.assertEqual(manifest['restic']['sha256'], digest(restic))
        self.assertEqual(manifest['restic']['version'], '0.19.1')
        self.assertEqual(mode(restic), 0o700)
        self.assertEqual(mode(root / MANIFEST), 0o600)
        self.assertIsNone(manifest['deployed_revision'])
        self.assertIsNone(manifest['rclone'])
        self.assertFalse((root / SNAPSHOT_RCLONE).exists())
        self.assertEqual(manifest['run_id'], root.name)
        for directory in (self.code, root, root / 'backup', restic.parent):
            self.assertEqual(mode(directory), 0o700, directory)
        self.assertFalse(any(path.is_symlink() for path in root.rglob('*')))

    def use_rclone(self, transport=SMB_TRANSPORT, source=None):
        """remote.json of the given kind, and the rclone (default: the fixture double) the snapshot copies."""
        config = self.f.cfg.state / 'remote.json'
        config.write_text(json.dumps(transport))
        config.chmod(0o600)
        source = source or self.f.bin / 'fake-rclone'
        self.f.state['installed_rclone'] = str(source)
        self.f.save()
        return config, source

    def writable_rclone(self, text=None):
        # The shared double is read-only (it is hard-linked into every fixture); tests that change it get a copy.
        path = self.f.root / 'installed-rclone'
        path.write_text(text or (self.f.bin / 'fake-rclone').read_text())
        path.chmod(0o755)
        return path

    def test_rclone_is_frozen_only_for_a_remote_command_of_a_valid_shared_folder_remote(self):
        rest = {'schema': 1, 'address': '100.64.0.10', 'port': 8088, 'transport_password': 'x' * 48}
        config, source = self.use_rclone(rest)
        root = self.f.snapshot(remote=True)
        self.assertIsNone(json.loads((root / MANIFEST).read_text())['rclone'])
        self.assertFalse((root / SNAPSHOT_RCLONE).exists())
        self.use_rclone()
        config.chmod(0o644)  # unusable: the remote run refuses it at configure, a local run needs no rclone
        self.assertFalse((self.f.snapshot(remote=True) / SNAPSHOT_RCLONE).exists())
        config.chmod(0o600)
        # A local command (a seed above all) never freezes rclone, even for a kind smb record.
        root = self.f.snapshot()
        self.assertIsNone(json.loads((root / MANIFEST).read_text())['rclone'])
        self.assertFalse((root / SNAPSHOT_RCLONE).exists())
        root = self.f.snapshot(remote=True)
        rclone = json.loads((root / MANIFEST).read_text())['rclone']
        self.assertEqual(rclone, {'path': str(source), 'sha256': digest(source), 'version': RCLONE_VERSION})
        self.assertEqual(digest(root / SNAPSHOT_RCLONE), digest(source))
        self.assertEqual(mode(root / SNAPSHOT_RCLONE), 0o700)

    def test_a_wrong_rclone_version_refuses_and_leaves_nothing(self):
        self.use_rclone(source=self.writable_rclone(FAKE_SHEBANG + "print('rclone v1.70.0')\n"))
        self.f.snapshot()  # a local command never runs it
        with self.assertRaisesRegex(AssertionError, 'is not rclone v'):
            self.f.snapshot(remote=True)
        self.assert_no_snapshot()

    def test_an_rclone_copy_that_differs_from_its_source_refuses(self):
        source = self.writable_rclone()
        copy = offsite_snapshot.copy_file
        def changed_source(path, destination):
            result = copy(path, destination)
            if destination.name == 'rclone':
                path.write_text(path.read_text() + '# replaced by an rclone upgrade\n')
            return result
        def changed_copy(path, destination):
            result = copy(path, destination)
            if destination.name == 'rclone':
                destination.write_text(destination.read_text() + '# corrupted\n')
            return result
        self.use_rclone(source=source)
        for racing, message in ((changed_source, 'Installed rclone changed'), (changed_copy, 'differs from the bytes')):
            with self.subTest(message=message), mock.patch('offsite_snapshot.INSTALLED_RCLONE', str(source)), \
                    mock.patch('offsite_snapshot.copy_file', side_effect=racing):
                with self.assertRaisesRegex(RuntimeError, message):
                    self.create(remote=True)
            self.assert_no_snapshot()

    def test_every_stale_snapshot_is_collected_without_following_links(self):
        outside = self.f.root / 'outside'
        outside.mkdir()
        (outside / 'keep').write_text('not ours')
        self.code.mkdir(mode=0o700)
        stale = self.code / '20260101T000000Z-00000000'
        (stale / 'backup').mkdir(parents=True)
        (stale / 'backup' / 'offsite_run.py').write_text('old')
        (stale / 'link').symlink_to(outside)
        (self.code / 'stray-file').write_text('x')
        (self.code / 'stray-link').symlink_to(outside)
        root = self.f.snapshot()
        self.assertEqual(list(self.code.iterdir()), [root])
        self.assertEqual((outside / 'keep').read_text(), 'not ours')

    def test_a_mount_inside_the_snapshot_directory_is_never_deleted(self):
        self.code.mkdir(mode=0o700)
        stale = self.code / '20260101T000000Z-00000000'
        stale.mkdir()
        (stale / 'data').write_text('mounted')
        with mock.patch('offsite_snapshot.mount_points', return_value={str(stale)}):
            with self.assertRaisesRegex(RuntimeError, 'mount point'):
                self.create()
        self.assertEqual((stale / 'data').read_text(), 'mounted')

    def test_restic_hash_mismatch_refuses_and_leaves_nothing(self):
        installer = self.f.live / RESTIC_INSTALLER
        installer.write_text(re.sub('binary_sha=[0-9a-f]+', 'binary_sha=' + '0' * 64, installer.read_text()))
        with self.assertRaisesRegex(AssertionError, 'pinned SHA-256'):
            self.f.snapshot()
        self.assert_no_snapshot()

    def test_missing_or_repeated_pin_refuses(self):
        installer = self.f.live / RESTIC_INSTALLER
        text = installer.read_text()
        for broken in (text.replace('version=0.19.1\n', ''), text + 'version=0.19.2\n'):
            installer.write_text(broken)
            with self.subTest(broken=broken), self.assertRaisesRegex(AssertionError, 'pinned restic version'):
                self.f.snapshot()
            self.assert_no_snapshot()

    def test_a_source_changed_during_the_copy_refuses(self):
        copy = offsite_snapshot.copy_file
        def racing_sync(source, destination):
            result = copy(source, destination)
            if source.name == 'backup_conf.sh':
                source.write_text(source.read_text() + '# changed by a sync\n')
            return result
        with mock.patch('offsite_snapshot.copy_file', side_effect=racing_sync):
            with self.assertRaisesRegex(RuntimeError, 'backup/backup_conf.sh changed while the code snapshot'):
                self.create()
        self.assert_no_snapshot()

    def test_symlinked_or_missing_live_file_refuses(self):
        target = self.f.live / 'ntfy_send.sh'
        original = target.read_bytes()
        target.unlink()
        with self.assertRaises(AssertionError):
            self.f.snapshot()
        self.assert_no_snapshot()
        (self.f.root / 'elsewhere.sh').write_bytes(original)
        target.symlink_to(self.f.root / 'elsewhere.sh')
        with self.assertRaises(AssertionError):
            self.f.snapshot()
        self.assert_no_snapshot()

    def test_remove_accepts_only_a_run_root_under_the_state_code_directory(self):
        root = self.f.snapshot()
        state = self.f.cfg.state
        for path in (self.code / 'not-a-run', state / root.name, self.f.root / 'code' / root.name,
                     Path(str(root) + '/..'), Path(root.name)):
            with self.subTest(path=path), self.assertRaises((RuntimeError, OSError)):
                offsite_snapshot.remove(state, path)
        outside = self.f.root / 'outside'
        outside.mkdir()
        link = self.code / '20260101T000000Z-00000001'
        link.symlink_to(outside)
        with self.assertRaisesRegex(RuntimeError, 'Unsafe'):
            offsite_snapshot.remove(state, link)
        self.assertTrue(outside.is_dir())
        offsite_snapshot.remove(state, root)
        self.assertFalse(root.exists())

    def test_cli_requires_the_job_descriptor(self):
        environment = {**self.f.env, 'PYTHONPATH': str(self.f.live / 'backup'),
                       'OFFSITE_STATE_ROOT': str(self.f.cfg.state)}
        environment.pop('VANPI_BACKUP_JOB_LOCK_FD', None)
        result = subprocess.run([sys.executable, '-B', '-c', SNAPSHOT_BOOTSTRAP, 'create'], env=environment,
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 1)
        self.assertIn('VANPI_BACKUP_JOB_LOCK_FD', result.stderr)
        for usage in (['other'], ['create', '--rem'], ['create', '--remote', '--remote'], ['--remote', 'create']):
            with self.subTest(usage=usage):
                result = subprocess.run([sys.executable, '-B', '-c', SNAPSHOT_BOOTSTRAP, *usage], env=environment,
                                        capture_output=True, text=True, timeout=30)
                self.assertEqual((result.returncode, result.stderr),
                                 (2, 'usage: offsite_snapshot.py create [--remote]\n'))

    @unittest.skipUnless(sys.platform == 'linux', 'kernel inherited-flock ownership requires Linux /proc fdinfo')
    def test_cli_refuses_a_descriptor_that_does_not_hold_job_offsite(self):
        fd = os.open(self.f.job_path, os.O_RDONLY)
        self.addCleanup(os.close, fd)
        environment = {**self.f.env, 'PYTHONPATH': str(self.f.live / 'backup'),
                       'OFFSITE_STATE_ROOT': str(self.f.cfg.state), 'VANPI_BACKUP_JOB_LOCK_FD': str(fd)}
        result = subprocess.run([sys.executable, '-B', '-c', SNAPSHOT_BOOTSTRAP, 'create'], env=environment,
                                pass_fds=(fd,), capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 1)
        self.assertIn('does not hold', result.stderr)
        self.assertFalse(self.code.exists() and any(self.code.iterdir()))

    def test_choosing_whether_to_freeze_rclone_reads_the_transport_with_core_dumps_disabled(self):
        # needs_rclone runs in the launcher's snapshot process, before the protected worker starts.
        self.use_rclone()
        body = 'import offsite_snapshot\noffsite_snapshot.needs_rclone(cfg.state, True)'
        before, after = core_limits_after(self.f.config_file, body, self.f.env)
        self.assertNotEqual(before, (0, 0))
        self.assertEqual(after, (0, 0))

    def test_create_leaves_core_dumps_disabled_in_a_fresh_process(self):
        self.use_rclone()
        # The real snapshot entry point, with the fixture's bootstrap, reporting its limit just before exit.
        report = 'print(json.dumps([before, resource.getrlimit(resource.RLIMIT_CORE)]), file=sys.stderr)'
        code = CORE_PROBE + SNAPSHOT_BOOTSTRAP.replace(
            'raise SystemExit(offsite_snapshot.main(sys.argv[1:]))',
            'status = offsite_snapshot.main(sys.argv[1:])\n    ' + report + '\n    raise SystemExit(status)')
        environment = {**self.f.env, 'PYTHONPATH': str(self.f.live / 'backup'), 'OFFSITE_STATE_ROOT': str(self.f.cfg.state),
                       'VANPI_BACKUP_JOB_LOCK_FD': str(self.f.job_fd)}
        result = subprocess.run([sys.executable, '-B', '-c', code, 'create', '--remote'], cwd=self.f.root,
                                env=environment, pass_fds=(self.f.job_fd,), text=True, capture_output=True, timeout=180)
        self.assertEqual(result.returncode, 0, result.stderr)
        before, after = (tuple(value) for value in json.loads(result.stderr.splitlines()[-1]))
        self.assertNotEqual(before, (0, 0))
        self.assertEqual(after, (0, 0))


if __name__ == '__main__':
    unittest.main()
