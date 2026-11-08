"""Exercise the public CLI against fake system commands, never live disks."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import unittest

from offsite_config import INCOMPLETE_TAG, MARKER
from pi.tests.backup.offsite_fixtures import OffsiteFixture


class OffsiteTests(unittest.TestCase):
    def setUp(self):
        self.f = OffsiteFixture()
        self.addCleanup(self.f.close)

    def ready(self):
        result = self.f.prepare()
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.f.sources()

    def assert_no_erase(self, result, message):
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(message, result.stderr)
        self.assertFalse(any(row[0] in ('wipefs', 'sfdisk', 'mkfs') for row in self.f.calls()))

    def test_prepare_records_marker_password_and_ext4_compatibility(self):
        result = self.f.prepare()
        self.assertEqual(result.returncode, 0, result.stderr)
        marker = self.f.marker()
        self.assertEqual(marker['schema'], 1)
        self.assertEqual(marker['repo_id'], 'a' * 64)
        self.assertEqual(marker['repo_path'], 'vanpi-restic')
        self.assertEqual(marker['priority'], 10)
        known = json.loads((self.f.cfg.state / 'repos.json').read_text())
        self.assertEqual(known['repos'][self.f.label], marker)
        self.assertEqual(self.f.cfg.password.stat().st_mode & 0o777, 0o600)
        self.assertEqual((self.f.cfg.state / 'repos.json').stat().st_mode & 0o777, 0o600)
        self.assertNotIn(self.f.cfg.password.read_text().strip(), result.stdout + result.stderr)
        call = next(row for row in self.f.calls() if row[0] == 'mkfs')
        self.assertIn('^orphan_file,^metadata_csum_seed', call)
        self.assertEqual(call[call.index('-m') + 1], '0')

    def test_mounted_partition_refused(self):
        self.f.state['disks'][1]['children'] = [{'name': str(self.f.device) + '1', 'type': 'part',
                                               'label': None, 'mountpoints': ['/media/borrowed']}]
        self.f.save()
        self.assert_no_erase(self.f.prepare(), 'mounted partition')

    def test_policy_label_refused(self):
        self.f.state['disks'][1]['children'] = [{'name': str(self.f.device) + '1', 'type': 'part',
                                               'label': 'hotspare-b', 'mountpoints': [None]}]
        self.f.save()
        self.assert_no_erase(self.f.prepare(), 'protected disk-policy')

    def test_serial_mismatch_prints_inventory_without_writing(self):
        result = self.f.run('prepare-disk', '--device', str(self.f.link), '--label', self.f.label,
                            '--priority', '10', '--erase-confirm', 'WRONG')
        self.assert_no_erase(result, 'exactly match')
        self.assertIn('Borrowed USB drive', result.stdout)
        self.assertIn('SERIAL123', result.stdout)
        self.assertFalse(self.f.cfg.password.exists())

    def test_duplicate_serial_refused(self):
        self.f.state['disks'][0]['serial'] = 'SERIAL123'
        self.f.save()
        self.assert_no_erase(self.f.prepare(), 'shared by multiple')

    def test_label_outside_offsite_policy_refused(self):
        self.f.state['offsite'] = ['offsite-14tb']
        self.f.save()
        self.assert_no_erase(self.f.prepare(), 'OFFSITE_LABELS')

    def test_existing_label_refused(self):
        self.f.state['disks'][0]['label'] = self.f.label
        self.f.save()
        self.assert_no_erase(self.f.prepare(), 'already in use')

    def test_root_device_refused_even_if_lsblk_mount_field_is_empty(self):
        self.f.state['mounts']['/'] = str(self.f.device)
        self.f.save()
        self.assert_no_erase(self.f.prepare(), 'root or boot')

    def test_discovery_failure_is_not_absence(self):
        self.f.state['fail_command'] = 'lsblk'
        self.f.save()
        self.assert_no_erase(self.f.prepare(), 'lsblk exited 7')

    def test_empty_discovery_refused(self):
        self.f.state['disks'] = []
        self.f.save()
        self.assert_no_erase(self.f.prepare(), 'Empty block discovery')

    def test_ignition_refuses_before_any_commands(self):
        self.f.cfg.ignition.touch()
        self.assert_no_erase(self.f.prepare(), 'Ignition is on')
        self.assertEqual(self.f.calls(), [])

    def test_unknown_repository_id_never_initializes_or_backs_up(self):
        self.ready()
        self.f.state['repo_id'] = 'f' * 64
        self.f.save()
        before = len(self.f.calls())
        result = self.f.run('backup', '--label', self.f.label)
        self.assertEqual(result.returncode, 1)
        state = json.loads(result.stdout)
        self.assertIn('Unknown repository ID', state['cause'])
        calls = self.f.calls()[before:]
        self.assertFalse(any(row[0] == 'restic' and ('init' in row or 'backup' in row) for row in calls))
        self.assertEqual(sum(row[0] == 'notify' for row in calls), 1)

    def test_unrecorded_marker_refused(self):
        self.ready()
        (self.f.cfg.state / 'repos.json').write_text('{"schema":1,"repos":{}}')
        result = self.f.run('backup', '--label', self.f.label, '--only', 'borg')
        self.assertEqual(result.returncode, 1)
        self.assertIn('not recorded', result.stdout)

    def test_samba_handle_open_refuses_and_clears_only_our_gate(self):
        self.ready()
        self.f.state['handles'] = True
        self.f.save()
        result = self.f.run('backup', '--label', self.f.label, '--only', 'time-machine')
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn('offsite_tm_refresh.zsh', result.stdout)
        self.assertFalse(self.f.cfg.drain.exists())
        self.assertFalse(any(row[0] == 'samba' for row in self.f.calls()))
        self.assertFalse(any(row[0] == 'restic' and 'backup' in row for row in self.f.calls()))

    def test_existing_samba_gate_is_never_replaced_or_removed(self):
        self.ready()
        self.f.cfg.drain.write_text('another owner')
        result = self.f.run('backup', '--label', self.f.label, '--only', 'time-machine')
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.f.cfg.drain.read_text(), 'another owner')

    def test_all_sources_excludes_and_final_status(self):
        self.ready()
        result = self.f.run('backup', '--label', self.f.label)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        calls = [row for row in self.f.calls() if row[0] == 'restic' and 'backup' in row]
        self.assertEqual(len(calls), 4)
        by_name = {row[row.index('--tag') + 1]: row for row in calls}
        for name, source in self.f.cfg.sources().items():
            row = by_name[name]
            self.assertEqual(row[row.index('--host') + 1], 'vanpi')
            self.assertIn('--one-file-system', row)
            self.assertIn('--no-cache', row)
            self.assertEqual(row[row.index('--') + 1:], [str(path) for path in source.paths])
            self.assertEqual(row[row.index('--compression') + 1], source.compression)
            for exclude in source.excludes:
                self.assertIn(exclude, row)
        self.assertTrue(any('vanpi-network' in arg for arg in by_name['exfat512']))
        self.assertFalse(self.f.cfg.drain.exists())
        status = self.f.run('status')
        state = json.loads(status.stdout)
        self.assertEqual(state['result'], 'ok')
        self.assertEqual(state['percent'], 100)
        self.assertEqual(state['last_result']['snapshot'], 'c' * 64)
        self.assertEqual({row['result'] for row in state['sources'].values()}, {'ok'})

    def test_only_subset_and_borg_mount_released(self):
        self.ready()
        self.f.state['mounts'].pop(str(self.f.cfg.mount_root / 'bigboi'))
        self.f.save()
        result = self.f.run('backup', '--label', self.f.label, '--only', 'borg')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(['mounts', 'mount', 'bigboi'], self.f.calls())
        self.assertIn(['mounts', 'release', 'bigboi'], self.f.calls())
        self.assertEqual(set(json.loads(result.stdout)['sources']), {'borg'})

    def test_changed_image_stays_incomplete_even_when_restic_succeeded(self):
        self.ready()
        self.f.state['change_image'] = True
        self.f.save()
        result = self.f.run('backup', '--label', self.f.label, '--only', 'time-machine')
        self.assertEqual(result.returncode, 1)
        self.assertIn('image changed during backup', result.stdout)
        self.assertFalse(any(row[0] == 'restic' and 'tag' in row for row in self.f.calls()))
        self.assertFalse(self.f.cfg.drain.exists())

    def test_failure_cause_retained_and_not_tagged_complete(self):
        self.ready()
        self.f.state['backup_failure'] = True
        self.f.save()
        result = self.f.run('backup', '--label', self.f.label, '--only', 'borg')
        self.assertEqual(result.returncode, 1)
        self.assertIn('Input/output error', result.stdout)
        self.assertFalse(any(row[0] == 'restic' and 'tag' in row for row in self.f.calls()))

    def test_check_subset_and_status_before_first_run(self):
        result = self.f.run('status')
        self.assertEqual(json.loads(result.stdout), {'result': 'not-run'})
        self.ready()
        result = self.f.run('check', '--label', self.f.label, '--read-data-subset', '5%')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('no errors were found', result.stdout)
        self.assertTrue(any(row[-3:] == ['check', '--read-data-subset', '5%'] for row in self.f.calls()))

    def test_sigterm_stops_child_clears_drain_and_publishes_interrupted(self):
        self.ready()
        self.f.state['hang'] = True
        self.f.save()
        child = self.f.start('backup', '--label', self.f.label, '--only', 'time-machine')
        try:
            deadline = time.monotonic() + 60
            while not (self.f.root / 'backup-started').exists() and child.poll() is None:
                if time.monotonic() > deadline:
                    self.fail('backup did not start')
                time.sleep(0.02)
            self.assertTrue(self.f.cfg.drain.exists())
            state = json.loads(self.f.run('status').stdout)
            while state['bytes_done'] != 25 and time.monotonic() < deadline:
                time.sleep(0.02)
                state = json.loads(self.f.run('status').stdout)
            self.assertEqual(state['bytes_done'], 25)
            self.assertEqual(state['rate'], 5)
            self.assertEqual(state['eta'], 15)
            child.send_signal(signal.SIGTERM)
            out, err = child.communicate(timeout=25)
            self.assertEqual(child.returncode, 143, out + err)
            self.assertFalse(self.f.cfg.drain.exists())
            state = json.loads(self.f.run('status').stdout)
            self.assertEqual(state['result'], 'interrupted')
            self.assertIn('SIGTERM', state['sources']['time-machine']['cause'])
            self.assertEqual(sum(row[0] == 'notify' for row in self.f.calls()), 1)
        finally:
            if child.poll() is None:
                child.terminate()
                try:
                    child.communicate(timeout=25)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.communicate()

    def test_existing_password_is_preserved(self):
        self.f.cfg.password.write_text('an-existing-secret-' * 4 + '\n')
        self.f.cfg.password.chmod(0o600)
        before = self.f.cfg.password.read_bytes()
        result = self.f.prepare()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.f.cfg.password.read_bytes(), before)

    def test_failed_initialization_never_registers_repository(self):
        self.f.state['fail_command'] = 'mkfs'
        self.f.save()
        result = self.f.prepare()
        self.assertEqual(result.returncode, 1)
        self.assertFalse((self.f.cfg.state / 'repos.json').exists())
        self.assertFalse((self.f.cfg.mount_root / self.f.label / MARKER).exists())

    def test_source_mount_mismatch_refuses_copy(self):
        self.ready()
        self.f.state['mounts'][str(self.f.cfg.mount_root / 'bigboi')] = str(self.f.device)
        self.f.save()
        result = self.f.run('backup', '--label', self.f.label, '--only', 'borg')
        self.assertEqual(result.returncode, 1)
        self.assertIn('not mounted from exact label', result.stdout)
        self.assertFalse(any(row[0] == 'restic' and 'backup' in row for row in self.f.calls()))

    def test_missing_source_does_not_become_an_empty_successful_snapshot(self):
        self.ready()
        path = self.f.cfg.borg
        (path / 'sample').unlink()
        path.rmdir()
        result = self.f.run('backup', '--label', self.f.label, '--only', 'borg')
        self.assertEqual(result.returncode, 1)
        self.assertIn('Missing or unsafe source', result.stdout)

    @unittest.skipUnless(sys.platform == 'linux', 'kernel inherited-flock ownership requires Linux /proc fdinfo')
    def test_unlocked_descriptor_refuses_public_entry(self):
        import fcntl
        fcntl.flock(self.f.fd, fcntl.LOCK_UN)
        self.assert_no_erase(self.f.prepare(), 'does not hold')


if __name__ == '__main__':
    unittest.main()
