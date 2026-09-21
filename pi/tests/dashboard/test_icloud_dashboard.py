import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from pi.apps.van_dashboard.van_dashboard_backups import BackupManager, BackupStatusError


class ICloudDashboardTests(unittest.TestCase):
    def test_fixed_read_only_helper_and_failure_isolated(self):
        with tempfile.TemporaryDirectory() as tmp:
            helper = Path(tmp) / 'icloud_status.py'
            helper.touch()
            command = mock.Mock(return_value=subprocess.CompletedProcess([], 0, json.dumps({'available': True, 'running': False})))
            manager = BackupManager(icloud_tool=str(helper), command=command)
            self.assertTrue(manager._icloud_status()['available'])
            self.assertEqual(command.call_args.args[0][1:], ['-n', '/usr/bin/python3', str(helper)])
            command.return_value = subprocess.CompletedProcess([], 1, 'PRIVATE')
            self.assertEqual(manager._icloud_status(), {'available': False, 'running': False, 'attention': True})
            command.side_effect = subprocess.TimeoutExpired('helper', 8)
            self.assertFalse(manager._icloud_status()['available'])

    def test_no_helper_supports_older_installations(self):
        self.assertIsNone(BackupManager(icloud_tool='')._icloud_status())

    def test_manual_jobs_do_not_conflict_with_cloud_worker(self):
        manager = BackupManager(icloud_tool='')
        with mock.patch.object(manager, 'status', return_value={'icloud': {'running': True}}):
            with self.assertRaisesRegex(BackupStatusError, 'iCloud'):
                manager.start_borg_backup()
            with self.assertRaisesRegex(BackupStatusError, 'iCloud'):
                manager.start_exfat_backup()
            with mock.patch.object(manager, '_configuration', return_value={'targets': [{'label': 'spare'}]}):
                with self.assertRaisesRegex(BackupStatusError, 'iCloud'):
                    manager.start_clone('spare')


if __name__ == '__main__':
    unittest.main()
