"""A run executes only from its frozen snapshot, local and remote, after the live tree moves."""
from pathlib import Path
import unittest

from pi.tests.backup.offsite_lock_fixtures import LockFixtureCase
from pi.tests.backup.offsite_remote_fixtures import RemoteFixture


class IsolationTests(LockFixtureCase):
    fixture = RemoteFixture

    def test_run_uses_only_its_snapshot_after_the_live_tree_moves(self):
        self.f.install_stub_helpers()
        self.unmount('bigboi')
        for remote in (False, True):
            with self.subTest(remote=remote):
                (self.f.root / 'executions.jsonl').unlink(missing_ok=True)
                self.update(move_live=str(self.f.live), manifest_ok=None,
                            snapshot_commands=['restic', 'mounts', 'samba', 'notify', 'uplink'])
                child = self.start('borg', 'time-machine', 'movingparts') if not remote else None
                if remote:
                    child = self.f.start('backup', '--remote', '--label', self.f.label,
                                         '--only', 'borg', '--only', 'movingparts')
                    self.addCleanup(self.stop, child)
                out, err = child.communicate(timeout=self.f.command_timeout)
                self.assertEqual(child.returncode, 0, out + err)
                moved = Path(str(self.f.live) + '.moved')
                self.assertTrue(moved.exists())
                self.f.reload()
                self.assertTrue(self.f.state['manifest_ok'])
                frozen = [row for row in self.f.executions() if row['name'] in
                          ('restic', 'mounts', 'samba', 'notify') or row['path'].endswith('icloud_uplink.py')]
                code = self.f.cfg.state / 'code'
                self.assertTrue(frozen)
                for row in frozen:
                    self.assertTrue(Path(row['path']).is_relative_to(code), row)
                self.assertIn('notify', {row['name'] for row in frozen})
                if remote:
                    self.assertTrue(any(row['path'].endswith('icloud_uplink.py') for row in frozen))
                moved.rename(self.f.live)


if __name__ == '__main__':
    unittest.main()
