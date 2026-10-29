#!/usr/bin/env python3
"""Exercise the installed standalone Mac helper without touching live disks."""
from contextlib import redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
import plistlib
import subprocess
import time
import unittest
from unittest import mock

REPO = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location(
    'coordinator', REPO / 'macbook/scripts/time_machine_offsite_coordinator.py')
coordinator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(coordinator)
GEN = 'tm-20260930T010203Z-12345678'
SNAPSHOT = '/Volumes/.timemachine/destination/2026-09-21-035400.backup/2026-09-21-035400.backup'
PRIMARY = '/Volumes/Backups of Test Mac'


class CoordinatorTests(unittest.TestCase):
    def setUp(self):
        self.request = {
            'requested': True, 'generation': GEN, 'capture_id': 'a' * 32,
            'expires_at': time.time() + 60, 'bundle': 'm4mac0.sparsebundle',
        }
        self.image = {
            'image-path': '/Volumes/.timemachine/vanpi.lan/id/mbp2tbkup/m4mac0.sparsebundle',
            'image-encrypted': True,
            'system-entities': [
                {'dev-entry': '/dev/disk44'},
                {'dev-entry': '/dev/disk44s1', 'mount-point': PRIMARY},
            ],
        }
        self.detached = False
        self.snapshots = [SNAPSHOT]
        self.detach_failures = []
        self.unmount_error = None
        self.remote_error = None
        self.remote_failure_step = '--capture-request'
        self.mount_calls = 0
        self.mounted_on_retry = None

    def local_command(self, args, timeout=30):
        key = tuple(args[:2])
        if key == ('/usr/bin/tmutil', 'status'):
            return b'Running = 0;'
        if key == ('/usr/bin/hdiutil', 'info'):
            return plistlib.dumps({'images': [] if self.detached else [self.image]})
        if args == ['/sbin/mount']:
            self.mount_calls += 1
            if self.mounted_on_retry and self.mount_calls == 2:
                self.snapshots.append(self.mounted_on_retry)
            lines = [f'/dev/disk44s1 on {PRIMARY} (apfs, local, journaled)',
                     '/dev/disk9s1 on /Volumes/.timemachine/other/snapshot (apfs, read-only)',
                     '//private@server/share on /Volumes/private (smbfs, local)']
            # macOS lists APFS snapshot mounts as <snapshot>@<device>, which is how
            # Time Machine's leftover backup-snapshot mounts appear.
            lines += [f'com.apple.TimeMachine.{Path(path).name}@/dev/disk44s1 on {path} (apfs, local, read-only)'
                      for path in self.snapshots]
            return ('\n'.join(lines) + '\n').encode()
        if key == ('/usr/sbin/diskutil', 'unmount'):
            if self.unmount_error:
                raise self.unmount_error
            self.snapshots.remove(args[2])
            return b'Volume unmounted\n'
        if key == ('/usr/bin/hdiutil', 'detach'):
            self.assertEqual(args, ['/usr/bin/hdiutil', 'detach', '/dev/disk44'])
            self.assertEqual(timeout, 90)
            if self.detach_failures:
                raise self.detach_failures.pop(0)
            self.assertEqual(self.snapshots, [])
            self.detached = True
            return b'disk44 ejected\n'
        self.fail('Unexpected command: ' + repr(args))

    def remote_command(self, user, *args):
        self.assertEqual(user, 'test')
        if self.remote_error and args[0] == self.remote_failure_step:
            raise self.remote_error
        if args == ('--mac-present-v2',):
            return {'ok': True}
        if args == ('--capture-request',):
            return self.request
        if args == ('--capture-ready', GEN, 'a' * 32):
            return {'accepted': True}
        self.fail('Unexpected remote command: ' + repr(args))

    def poll(self):
        output = io.StringIO()
        config = mock.Mock()
        config.read_text.return_value = json.dumps({'user': 'test'})
        with mock.patch.object(coordinator, 'CONFIG', config), \
                mock.patch.object(coordinator, 'command', side_effect=self.local_command) as local, \
                mock.patch.object(coordinator, 'remote', side_effect=self.remote_command) as remote, \
                redirect_stdout(output):
            code = coordinator.main([])
        line = output.getvalue()
        self.assertEqual(len(line.splitlines()), 1, line)
        return code, line, local, remote

    @staticmethod
    def busy(stderr=b'hdiutil: detach failed - Resource busy\n'):
        return subprocess.CalledProcessError(
            16, ['/usr/bin/hdiutil', 'detach', '/dev/disk44'], stderr=stderr)

    def assert_no_acknowledgement(self, remote):
        self.assertFalse(any('--capture-ready' in call.args for call in remote.call_args_list))

    def test_snapshot_is_unmounted_before_nonforced_detach_and_acknowledgement(self):
        code, line, local, remote = self.poll()
        self.assertEqual(code, 0, line)
        self.assertIn('diskutil-unmount: ' + SNAPSHOT + ': unmounted', line)
        self.assertIn('Image cleanly detached', line)
        commands = [call.args[0] for call in local.call_args_list]
        unmount = ['/usr/sbin/diskutil', 'unmount', SNAPSHOT]
        detach = ['/usr/bin/hdiutil', 'detach', '/dev/disk44']
        self.assertEqual([args for args in commands if args[0] == '/usr/sbin/diskutil'], [unmount])
        self.assertLess(commands.index(unmount), commands.index(detach))
        self.assertNotIn('force', repr(commands))
        self.assertEqual(remote.call_args.args, ('test', '--capture-ready', GEN, 'a' * 32))

    def test_secondary_mount_with_spaces_and_parentheses_is_also_unmounted(self):
        secondary = '/Volumes/Backup Snapshot (old)'
        self.snapshots.append(secondary)
        code, line, local, _ = self.poll()
        self.assertEqual(code, 0, line)
        unmounts = [call.args[0][2] for call in local.call_args_list
                    if call.args[0][:2] == ['/usr/sbin/diskutil', 'unmount']]
        self.assertEqual(unmounts, [SNAPSHOT, secondary])
        self.assertNotIn(PRIMARY, unmounts)

    def test_busy_detach_rechecks_new_snapshot_once_then_succeeds(self):
        self.snapshots = []
        self.detach_failures = [self.busy()]
        self.mounted_on_retry = SNAPSHOT
        code, line, local, remote = self.poll()
        self.assertEqual(code, 0, line)
        self.assertIn('rechecking snapshot mounts once', line)
        commands = [call.args[0] for call in local.call_args_list]
        self.assertEqual(commands.count(['/usr/bin/hdiutil', 'detach', '/dev/disk44']), 2)
        self.assertEqual(commands.count(['/usr/sbin/diskutil', 'unmount', SNAPSHOT]), 1)
        self.assertEqual(self.mount_calls, 2)
        self.assertEqual(remote.call_args.args[1], '--capture-ready')

    def test_still_busy_defers_with_step_sanitized_reason_mounts_and_dissenter(self):
        self.detach_failures = [self.busy(), self.busy(
            b'\n\x1b[31mResource busy\x1b[0m\x00\nDO NOT LOG SECOND LINE\n')]
        self.unmount_error = subprocess.CalledProcessError(
            1, 'diskutil', output=b'Unmount failed: dissented by PID 123 (mds)\nPRIVATE SECOND LINE')
        code, line, local, remote = self.poll()
        self.assertEqual(code, 1)
        self.assertTrue(line.startswith(
            'Offsite coordinator deferred: hdiutil-detach: CalledProcessError exit 16: Resource busy;'))
        self.assertNotIn('SECOND LINE', line)
        self.assertNotIn('\x1b', line)
        self.assertNotIn('\x00', line)
        self.assertIn('dissented by PID 123 (mds)', line)
        self.assertIn('remaining mounts:', line)
        self.assertIn(SNAPSHOT, line)
        self.assertEqual(sum(call.args[0][1:2] == ['detach'] for call in local.call_args_list), 2)
        self.assertEqual(sum(call.args[0][1:2] == ['unmount'] for call in local.call_args_list), 2)
        self.assert_no_acknowledgement(remote)

    def test_nonbusy_detach_failure_is_not_retried(self):
        self.detach_failures = [subprocess.CalledProcessError(5, 'hdiutil', stderr=b'Input/output error')]
        code, line, local, remote = self.poll()
        self.assertEqual(code, 1)
        self.assertIn('hdiutil-detach: CalledProcessError exit 5: Input/output error', line)
        self.assertEqual(sum(call.args[0][1:2] == ['detach'] for call in local.call_args_list), 1)
        self.assert_no_acknowledgement(remote)

    def test_acknowledged_capture_keeps_heartbeat_without_image_or_ready_operations(self):
        self.request['acknowledged'] = True
        code, line, local, remote = self.poll()
        self.assertEqual(code, 0, line)
        self.assertIn('Capture in progress; image stays detached.', line)
        local.assert_called_once_with(['/usr/bin/tmutil', 'status'])
        self.assertEqual([call.args[1] for call in remote.call_args_list],
                         ['--mac-present-v2', '--capture-request'])

    def test_acknowledged_request_still_validates_nonce(self):
        self.request.update(acknowledged=True, capture_id='bad nonce')
        code, line, local, remote = self.poll()
        self.assertEqual(code, 1)
        self.assertEqual(line, 'Offsite coordinator deferred: Invalid capture nonce\n')
        local.assert_called_once_with(['/usr/bin/tmutil', 'status'])
        self.assert_no_acknowledgement(remote)

    def test_ssh_steps_never_log_stderr_stdout_or_command_accounts(self):
        for flag, label in (('--mac-present-v2', 'heartbeat'),
                            ('--capture-request', 'capture-request'),
                            ('--capture-ready', 'capture-ready')):
            with self.subTest(step=label):
                self.remote_failure_step = flag
                self.remote_error = subprocess.CalledProcessError(
                    255, ['ssh', 'private-account@private-host'],
                    output=b'private-output', stderr=b'private-ssh-diagnostic\nsecond line')
                code, line, _, _ = self.poll()
                self.assertEqual(code, 1)
                self.assertTrue(line.startswith(
                    f'Offsite coordinator deferred: {label}: CalledProcessError exit 255'))
                self.assertNotIn('private-', line)

    def test_local_stderr_is_first_nonempty_line_bounded_and_url_redacted(self):
        self.detach_failures = [subprocess.CalledProcessError(
            5, 'hdiutil', stderr=b'\nCannot open smb://user:password@host/share ' + b'x' * 300 + b'\nsecret')]
        code, line, _, _ = self.poll()
        self.assertEqual(code, 1)
        reason = line.split('exit 5: ', 1)[1].split(';', 1)[0]
        self.assertEqual(len(reason), 160)
        self.assertIn('<url>', reason)
        self.assertNotIn('password', line)
        self.assertNotIn('secret', line)

    def test_mount_discovery_failure_never_detaches_or_acknowledges(self):
        original = self.local_command
        def command(args, timeout=30):
            if args == ['/sbin/mount']:
                raise subprocess.TimeoutExpired(args, timeout, stderr=b'private mount output')
            return original(args, timeout)
        self.local_command = command
        code, line, local, remote = self.poll()
        self.assertEqual(code, 1)
        self.assertEqual(line, 'Offsite coordinator deferred: mounts: TimeoutExpired\n')
        self.assertFalse(any(call.args[0][1:2] == ['detach'] for call in local.call_args_list))
        self.assert_no_acknowledgement(remote)

    def test_unencrypted_image_never_runs_mount_operations(self):
        self.image['image-encrypted'] = False
        code, line, local, remote = self.poll()
        self.assertEqual(code, 1)
        self.assertEqual(line, 'Offsite coordinator deferred: Time Machine image is not encrypted\n')
        self.assertFalse(any(call.args[0][0] in ('/sbin/mount', '/usr/sbin/diskutil')
                             for call in local.call_args_list))
        self.assert_no_acknowledgement(remote)

    def test_ambiguous_image_never_runs_mount_operations(self):
        original = self.local_command
        def command(args, timeout=30):
            if args[:2] == ['/usr/bin/hdiutil', 'info']:
                return plistlib.dumps({'images': [self.image, self.image]})
            return original(args, timeout)
        self.local_command = command
        code, line, local, remote = self.poll()
        self.assertEqual(code, 1)
        self.assertEqual(line, 'Offsite coordinator deferred: Ambiguous Time Machine image\n')
        self.assertFalse(any(call.args[0][0] in ('/sbin/mount', '/usr/sbin/diskutil')
                             for call in local.call_args_list))
        self.assert_no_acknowledgement(remote)

    def test_status_failure_is_step_labelled_without_remote_calls(self):
        def command(args, timeout=30):
            raise subprocess.CalledProcessError(
                1, args, stderr=b'Cannot query private-account@private-host\nsecond line')
        self.local_command = command
        code, line, _, remote = self.poll()
        self.assertEqual(code, 1)
        self.assertEqual(line, 'Offsite coordinator deferred: tmutil-status: '
                              'CalledProcessError exit 1: Cannot query <account>\n')
        remote.assert_not_called()

    def test_ready_failure_retains_the_same_poll_unmount_diagnostics(self):
        self.remote_failure_step = '--capture-ready'
        self.remote_error = subprocess.CalledProcessError(1, 'ssh', stderr=b'private remote failure')
        code, line, _, _ = self.poll()
        self.assertEqual(code, 1)
        self.assertTrue(line.startswith('Offsite coordinator deferred: capture-ready: CalledProcessError exit 1;'))
        self.assertIn('diskutil-unmount: ' + SNAPSHOT + ': unmounted', line)
        self.assertNotIn('private remote failure', line)

    def test_detached_image_must_be_absent_on_recheck_before_acknowledgement(self):
        original = self.local_command
        def command(args, timeout=30):
            result = original(args, timeout)
            if args[:2] == ['/usr/bin/hdiutil', 'detach']:
                self.detached = False
            return result
        self.local_command = command
        code, line, _, remote = self.poll()
        self.assertEqual(code, 1)
        self.assertIn('Time Machine image remains attached', line)
        self.assert_no_acknowledgement(remote)

    def test_command_bounds_the_actual_child_process(self):
        result = mock.Mock(stdout=b'ok')
        with mock.patch.object(coordinator.subprocess, 'run', return_value=result) as run:
            self.assertEqual(coordinator.command(['/sbin/mount']), b'ok')
        run.assert_called_once_with(['/sbin/mount'], capture_output=True, timeout=30, check=True)


if __name__ == '__main__':
    unittest.main()
