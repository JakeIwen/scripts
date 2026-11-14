"""Offsite restore targets through the remote fixture (real receipt key derivations)."""
import json
import unittest

from pi.tests.backup.offsite_lock_fixtures import LockFixtureCase
from pi.tests.backup.offsite_remote_fixtures import RemoteFixture

class RestoreTargetTests(LockFixtureCase):
    fixture = RemoteFixture

    def receipt(self):
        # Only the receipt matters here (restore-test is the command under test), so an identical earlier run is replayed.
        result = self.f.run_replayed('backup', '--remote', '--label', self.f.label, '--only', 'borg')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout)['sources']['borg']['receipt']

    def restore(self, target):
        receipt = self.receipt()  # before the target exists, so the replayed run sees a pristine tree
        target.mkdir(parents=True)
        return self.f.run('restore-test', '--label', self.f.label, '--receipt', receipt,
                          '--path', str(self.f.cfg.borg / 'sample'), '--target', str(target))

    def restic_restore(self):
        return [row for row in self.f.executions() if row['name'] == 'restic' and 'restore' in row['args']][-1]

    def test_target_on_a_managed_disk_holds_that_disk_through_the_restore(self):
        result = self.restore(self.f.cfg.mount_root / 'bigboi' / 'restore-test')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('bigboi=', self.restic_restore()['env']['VANPI_BACKUP_DISK_LOCK_FDS'])

    def test_target_on_the_root_filesystem_takes_no_disk_lock(self):
        result = self.restore(self.f.root / 'restore-test')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.restic_restore()['env']['VANPI_BACKUP_DISK_LOCK_FDS'], '')

    def test_root_filesystem_target_inside_the_mount_root_is_refused(self):
        # movingparts mounting later would cover the target, unlocked.
        self.unmount('movingparts')
        result = self.restore(self.f.cfg.mount_root / 'movingparts' / 'restore-probe')
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn('on the root filesystem (its disk is not mounted)', result.stderr)
        self.assertFalse(any(row['name'] == 'restic' and 'restore' in row['args'] for row in self.f.executions()))

    def test_target_on_another_filesystem_is_refused(self):
        other = self.f.root / 'other-mount'
        self.f.reload()
        self.f.state['mounts'][str(other)] = str(self.f.root / 'other-device')
        self.f.save()
        result = self.restore(other / 'restore-test')
        self.assertEqual(result.returncode, 1)
        self.assertIn('root filesystem or a managed backup disk', result.stderr)

if __name__ == '__main__':
    unittest.main()
