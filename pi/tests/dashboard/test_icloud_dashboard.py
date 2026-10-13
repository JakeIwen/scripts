import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from pi.apps.van_dashboard.van_dashboard_backups import BackupManager, BackupStatusError


def idle_status(**overrides):
    status = {'borg': {'running': False}, 'exfat_snapshot': {'running': False},
              'operation': {'status': 'idle', 'kind': None}, 'stop': {'status': 'idle', 'kind': None}}
    status.update(overrides)
    return status


class ICloudDashboardTests(unittest.TestCase):
    def test_time_machine_cloud_is_a_separate_fixed_helper(self):
        with tempfile.TemporaryDirectory() as tmp:
            helper = Path(tmp) / 'time_machine_icloud_status.py'; helper.touch()
            command = mock.Mock(return_value=subprocess.CompletedProcess([], 0, '{"available":true,"running":true}'))
            manager = BackupManager(icloud_tool='', time_machine_icloud_tool=str(helper), command=command)
            self.assertTrue(manager._icloud_status(time_machine=True)['running'])
            self.assertEqual(command.call_args.args[0][1:], ['-n','/usr/bin/python3',str(helper)])
            # A running Mac upload owns only its own locks, so Borg may start.
            with mock.patch.object(manager, 'status', return_value=idle_status(time_machine_icloud={'running': True})), \
                    mock.patch.object(manager, '_stamp', return_value=None):
                manager.start_borg_backup()
            manager.thread.join(2)
            self.assertEqual(manager.operation['kind'], 'borg')
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
        for kind, start in (('borg', 'start_borg_backup'), ('exfat', 'start_exfat_backup')):
            command = mock.Mock(return_value=subprocess.CompletedProcess([], 0, '', ''))
            manager = BackupManager(icloud_tool='', command=command)
            with mock.patch.object(manager, 'status', return_value=idle_status(icloud={'running': True})), \
                    mock.patch.object(manager, '_stamp', return_value=None):
                getattr(manager, start)()
            manager.thread.join(2)
            self.assertEqual(manager.operation['kind'], kind)
            self.assertEqual(command.call_args.args[0][2:], [getattr(manager, f'{kind}_tool'), '--force'])
        # Only the same kind conflicts with itself.
        manager = BackupManager(icloud_tool='')
        with mock.patch.object(manager, 'status', return_value=idle_status(borg={'running': True})):
            with self.assertRaisesRegex(BackupStatusError, 'Borg backup is already running'):
                manager.start_borg_backup()


if __name__ == '__main__':
    unittest.main()
