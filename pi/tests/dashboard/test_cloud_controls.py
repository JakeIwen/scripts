import subprocess
import unittest
from unittest import mock

from flask import Flask
from werkzeug.datastructures import MultiDict

from pi.apps.van_dashboard.http import reject_cross_origin_mutations
from pi.apps.van_dashboard.routes import backups
from pi.apps.van_dashboard.van_dashboard_cloud_controls import CloudBackupControl


class CloudControlTests(unittest.TestCase):
    def setUp(self):
        self.command = mock.Mock(return_value=subprocess.CompletedProcess([], 0))
        self.control = CloudBackupControl(command=self.command)
        patch = mock.patch.object(backups, 'time_machine_cloud_control', self.control)
        patch.start()
        self.addCleanup(patch.stop)
        patch = mock.patch.object(backups, 'pi_cloud_control', CloudBackupControl('pi', command=self.command))
        patch.start()
        self.addCleanup(patch.stop)
        app = Flask(__name__)
        app.register_blueprint(backups.bp)
        app.before_request(reject_cross_origin_mutations)
        self.client = app.test_client()

    def test_pause_and_resume_use_fixed_commands(self):
        response = self.client.post('/api/backups/time-machine-icloud/pause', data={'minutes': '240'})
        self.assertEqual(response.status_code, 202)
        self.assertEqual(self.command.call_args.args[0][2:], [
            '/usr/bin/python3', '/home/pi/scripts/backup/time_machine_icloud_control.py', '--pause', '240'])
        response = self.client.post('/api/backups/time-machine-icloud/resume')
        self.assertEqual(response.status_code, 202)
        self.assertEqual(self.command.call_args.args[0][-1], '--resume')

    def test_pi_pause_and_resume_use_pi_helper_only(self):
        for action, data in (('pause', {'minutes': '15'}), ('resume', {})):
            response = self.client.post('/api/backups/icloud/' + action, data=data)
            self.assertEqual(response.status_code, 202)
            self.assertIn('Pi iCloud', response.json['message'])
            self.assertEqual(self.command.call_args.args[0][3],
                             '/home/pi/scripts/backup/icloud_backup_control.py')
            self.assertEqual(self.command.call_args.args[0][4], '--' + action)

    def test_invalid_and_cross_origin_requests_cannot_control_backups(self):
        for data in ({}, {'minutes': '0'}, {'minutes': '1.5'}, {'minutes': '10081'},
                     {'minutes': '60', 'unit': 'other'}, MultiDict([('minutes', '1'), ('minutes', '2')])):
            self.assertEqual(self.client.post('/api/backups/time-machine-icloud/pause', data=data).status_code, 400)
        self.assertEqual(self.client.post('/api/backups/time-machine-icloud/resume?force=1').status_code, 400)
        self.assertEqual(self.client.post('/api/backups/time-machine-icloud/resume', json={'force': True}).status_code, 400)
        self.assertEqual(self.client.post('/api/backups/time-machine-icloud/resume', headers={'Origin': 'https://other.test'}).status_code, 403)
        self.command.assert_not_called()

    def test_failed_command_does_not_expose_private_output(self):
        self.command.return_value = subprocess.CompletedProcess([], 1, 'PRIVATE', 'PRIVATE')
        response = self.client.post('/api/backups/time-machine-icloud/resume')
        self.assertEqual(response.status_code, 503)
        self.assertNotIn('PRIVATE', response.get_data(as_text=True))
