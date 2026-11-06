"""Scan failures stay visible without reading logs or blocking healthy rotations."""
import os
from pathlib import Path

from test_log_rotate import LogRotateCase, payload


class UnlistableScanTests(LogRotateCase):
    def test_unlistable_scan_roots_and_children_are_listed_once_as_refused(self):
        healthy = self.write('.local/state/app/job.log', payload('healthy'))
        self.set_policy(healthy, '--max-mb', '1')
        for relative in ('Library/Logs', 'Library/Logs/Vendor'):
            for mode in (0o000, 0o300):
                with self.subTest(directory=relative, mode=oct(mode)):
                    Path(healthy).write_bytes(payload('healthy'))
                    locked = self.write(relative + '/job.log', b'locked fixture')
                    directory = Path(locked).parent
                    directory.chmod(mode)
                    try:
                        with self.assertRaises(PermissionError):
                            os.listdir(directory)
                        matches = [entry for entry in self.inventory()['logs']
                                   if entry['path'] == str(directory)]
                        self.assertEqual(len(matches), 1)
                        entry = matches[0]
                        self.assertEqual((entry['state'], entry['reason'], entry['bytes'], entry['source']),
                                         ('refused', 'unreadable_directory', 0, 'scan'))
                        self.assertEqual(entry['note'], str(directory))
                        code, value, _ = self.run_cli('rotate', '--json')
                        self.assertEqual((code, value['errors']), (0, []))
                        self.assertEqual([item['path'] for item in value['rotated']], [healthy])
                        self.assertEqual(os.path.getsize(healthy), 0)
                        self.assertIn(str(directory), [item['path'] for item in value['skipped']])
                    finally:
                        directory.chmod(0o700)
                    self.assertTrue(Path(locked).read_bytes() == b'locked fixture')


class TemporaryCleanupScopeTests(LogRotateCase):
    def _plant(self, relative, name):
        planted = Path(self.write(relative + '/' + name, b'planted'))
        old = 1_000_000_000
        os.utime(planted, (old, old))
        return planted

    def test_unreadable_folder_entry_never_cleans_beside_the_folder(self):
        healthy = self.write('.local/state/app/job.log', payload('healthy'))
        self.set_policy(healthy, '--max-mb', '1')
        abandoned = self._plant('.local/state/app', '.job.log.rotating.abcdefgh')
        locked = Path(self.write('Library/Logs/job.log', b'locked fixture')).parent
        outside = self._plant('Library', '.Logs.rotating.abcdefgh')
        locked.chmod(0o000)
        self.addCleanup(locked.chmod, 0o700)
        code, value, _ = self.run_cli('rotate', '--json')
        self.assertEqual((code, value['errors']), (0, []))
        self.assertTrue(outside.exists())
        self.assertFalse(abandoned.exists())
