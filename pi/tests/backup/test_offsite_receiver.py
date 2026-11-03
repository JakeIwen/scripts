"""DSM receiver discovery and owned watcher lifecycle with disposable files/processes."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from offsite_config import MARKER, REPO_PATH
from synology.offsite import binaries, configuration, control, mounts

WATCH_FIXTURE = '''
import json,os,subprocess,sys
from pathlib import Path
from unittest.mock import patch
from synology.offsite import runtime
from synology.offsite.mounts import Drive
root=Path(sys.argv[1])
class OwnedChild:
    def __init__(self,root,cfg,drive,binary,lock_fd):
        self.child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(120)'],pass_fds=(lock_fd,))
        with (root/'children').open('a') as f: f.write(str(self.child.pid)+'\\n')
    def close(self):
        self.child.terminate(); self.child.wait(timeout=5)
def discovery():
    value=json.loads((root/'drive.json').read_text())
    if value.get('error'): raise ValueError('fixture mount failure')
    if not value.get('id'): return None
    return Drive(root,('1','1:1','/dev/fixture'),{'repo_id':value['id'],'priority':10,'label':'fixture'})
with patch.object(runtime,'process_identity',side_effect=lambda pid=None:str(pid or os.getpid())), patch.object(runtime.binaries,'verify',return_value=root/'binary'), patch.object(runtime,'config',return_value={}), patch.object(runtime,'tailscale_ready'), patch.object(runtime.mounts,'discover',side_effect=discovery), patch.object(runtime,'Server',OwnedChild):
    raise SystemExit(runtime.watch(root))
'''


class ReceiverTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.root.chmod(0o700)
        self.inventory = self.root / 'mountinfo'
        self.pattern = re.compile(re.escape(str(self.root)) + r'/volumeUSB[0-9]+/usbshare\Z')
        self.lines = []

    def drive(self, number, priority=10, repo_id=None):
        target = self.root / ('volumeUSB' + str(number)) / 'usbshare'
        target.mkdir(parents=True)
        value = {'schema': 1, 'label': 'offsite-' + str(number), 'repo_id': repo_id or str(number) * 64,
                 'repo_path': REPO_PATH, 'priority': priority, 'created': 'fixture'}
        (target / MARKER).write_text(json.dumps(value))
        self.lines.append('{} 1 8:{} / {} rw - ext4 /dev/fixture{} rw'.format(number, number, target, number))
        self.inventory.write_text('\n'.join(self.lines))
        return target

    def discover(self):
        return mounts.discover(self.inventory, self.pattern)

    def test_only_exact_mounted_usb_targets_are_inspected(self):
        unmounted = self.drive(1)
        self.inventory.write_text('1 0 1:1 / / rw - ext4 /dev/root rw\n')
        self.assertIsNone(self.discover())
        self.assertTrue((unmounted / MARKER).exists())
        # A NAS internal share path is not opened, even if its name is suggestive.
        self.inventory.write_text('2 0 1:1 / /volume1/friends-share rw - ext4 /dev/root rw\n')
        self.assertIsNone(self.discover())

    def test_priority_upgrade_ambiguity_duplicate_and_marker_layout(self):
        self.drive(1)
        upgraded = self.drive(2, 20)
        self.assertEqual(self.discover().target, upgraded)
        self.drive(3, 20)
        with self.assertRaisesRegex(ValueError, 'Ambiguous'):
            self.discover()
        (upgraded / MARKER).write_text('{}')
        with self.assertRaises(RuntimeError):
            self.discover()

    def test_malformed_marker_types_are_validation_errors_not_watcher_crashes(self):
        target = self.drive(1)
        marker = target / MARKER
        original = json.loads(marker.read_text())
        for field, value in [('label', None), ('label', ['offsite-1']),
                             ('repo_id', int('1' * 64))]:
            with self.subTest(field=field, value=value):
                marker.write_text(json.dumps({**original, field: value}))
                with self.assertRaisesRegex(RuntimeError, 'Invalid offsite marker'):
                    self.discover()

    def test_unmarked_non_ext4_usb_is_ignored_not_traversed(self):
        target = self.drive(1)
        (target / MARKER).unlink()
        self.inventory.write_text(self.inventory.read_text().replace(' - ext4 ', ' - ntfs '))
        self.assertIsNone(self.discover())

    def test_duplicate_repo_id_fails_even_at_different_priorities(self):
        self.drive(1, repo_id='a' * 64)
        self.drive(2, priority=20, repo_id='a' * 64)
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            self.discover()

    def test_unsupported_readonly_bind_mount_symlink_or_broken_inventory_refuses(self):
        target = self.drive(1)
        original = self.inventory.read_text()
        for value in [original.replace(' - ext4 ', ' - ntfs '), original.replace(' rw - ', ' ro - '),
                      original.replace(' / ' + str(target), ' /bind-root ' + str(target)), 'broken row']:
            self.inventory.write_text(value)
            with self.assertRaises(ValueError):
                self.discover()
        self.inventory.write_text(original)
        marker = target / MARKER
        marker.rename(target / 'other')
        marker.symlink_to(target / 'other')
        with self.assertRaises(OSError):
            self.discover()

    def test_receiver_config_rejects_non_tailscale_or_unsafe_private_records(self):
        cfg = {'schema': 1, 'address': '100.64.0.10', 'port': 8088, 'transport_password': 'x' * 48,
               'tailscale': sys.executable}
        # Use a real regular interpreter path, not the venv's symlink.
        cfg['tailscale'] = str(Path(sys.executable).resolve())
        configuration.publish(self.root / 'config.json', cfg)
        self.assertEqual(configuration.config(self.root), cfg)
        for field, value in [('address', '0.0.0.0'), ('address', '127.0.0.1'), ('port', True),
                             ('transport_password', 'short'), ('tailscale', '/missing')]:
            configuration.publish(self.root / 'config.json', {**cfg, field: value})
            with self.assertRaises((ValueError, OSError)):
                configuration.config(self.root)
        (self.root / 'config.json').chmod(0o644)
        with self.assertRaises(ValueError):
            configuration.config(self.root)

    def test_installer_fails_unsupported_architecture_instead_of_guessing(self):
        with patch.object(binaries.platform, 'system', return_value='Linux'), patch.object(binaries.platform, 'machine', return_value='armv5tel'):
            with self.assertRaises(ValueError):
                binaries.target_architecture()
        with patch.object(binaries.platform, 'system', return_value='Linux'), patch.object(binaries.platform, 'machine', return_value='aarch64'):
            self.assertEqual(binaries.target_architecture(), 'linux_arm64')

    def wait_status(self, state):
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            record = configuration.read(self.root / 'status.json')
            if record and record.get('state') == state:
                return record
            time.sleep(0.02)
        self.fail('watcher never entered ' + state)

    def start_watch(self):
        return subprocess.Popen([sys.executable, '-B', '-c', WATCH_FIXTURE, str(self.root)],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    def test_watcher_singleton_switch_error_reaps_and_persistent_stop_survives_boot(self):
        for name in ('watch.lock', 'control.lock'):
            (self.root / name).touch(mode=0o600)
        configuration.publish(self.root / 'enabled.json', {'enabled': True})
        configuration.publish(self.root / 'drive.json', {'id': 'a' * 64})
        watcher = self.start_watch()
        self.addCleanup(lambda: watcher.poll() is None and watcher.kill())
        first = self.wait_status('serving')['server_pid']
        duplicate = self.start_watch()
        out, err = duplicate.communicate(timeout=5)
        self.assertEqual(duplicate.returncode, 0, out + err)
        self.assertEqual((self.root / 'children').read_text().splitlines(), [str(first)])
        configuration.publish(self.root / 'drive.json', {'id': 'b' * 64})
        deadline = time.monotonic() + 8
        while configuration.read(self.root / 'status.json').get('server_pid') == first:
            self.assertLess(time.monotonic(), deadline)
            time.sleep(0.02)
        with self.assertRaises(ProcessLookupError):
            os.kill(first, 0)
        second = self.wait_status('serving')['server_pid']
        configuration.publish(self.root / 'drive.json', {'error': True})
        self.wait_status('error')
        with self.assertRaises(ProcessLookupError):
            os.kill(second, 0)
        self.assertEqual(control.main(['--root', str(self.root), 'stop']), 0)
        out, err = watcher.communicate(timeout=10)
        self.assertEqual(watcher.returncode, 0, out + err)
        self.assertEqual(configuration.read(self.root / 'enabled.json'), {'enabled': False})
        self.assertEqual(control.main(['--root', str(self.root), 'boot']), 0)
        self.assertEqual(configuration.read(self.root / 'status.json')['state'], 'stopped')
        self.assertFalse(control.status(self.root)['owned_watcher'])


if __name__ == '__main__':
    unittest.main()
