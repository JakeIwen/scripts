import json
import subprocess
import unittest
from unittest import mock

from flask import Flask
from werkzeug.datastructures import MultiDict
from pi.apps.van_dashboard.http import reject_cross_origin_mutations
from pi.apps.van_dashboard.routes import backup_priority as routes
from pi.apps.van_dashboard.van_dashboard_backup_priority import BackupPriorityControl


class BackupPriorityRouteTests(unittest.TestCase):
    def setUp(self):
        self.command = mock.Mock(return_value=subprocess.CompletedProcess([], 0, '{"mode":"normal"}'))
        self.control = BackupPriorityControl(self.command)
        patch = mock.patch.object(routes, 'control', self.control)
        patch.start(); self.addCleanup(patch.stop)
        app = Flask(__name__)
        app.register_blueprint(routes.bp)
        app.before_request(reject_cross_origin_mutations)
        self.client = app.test_client()

    def test_priority_and_capture_have_fixed_commands(self):
        response = self.client.get('/api/backups/priority')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        for mode in ('normal', 'pi', 'time-machine'):
            self.command.reset_mock()
            self.assertEqual(self.client.post('/api/backups/priority', data={'mode':mode}).status_code, 200)
            self.assertEqual(self.command.call_args_list[0].args[0][-2:], ['--set', mode])
        for path, flag in (('capture', '--request-capture'), ('capture/cancel', '--cancel-capture')):
            self.assertEqual(self.client.post('/api/backups/priority/'+path).status_code, 200)
            self.assertEqual(self.command.call_args.args[0][-1], flag)

    def test_unsafe_input_and_cross_origin_cannot_issue_commands(self):
        for data in ({}, {'mode':'foreign'}, {'mode':'normal','unit':'sshd'}, MultiDict([('mode','pi'),('mode','normal')])):
            self.assertEqual(self.client.post('/api/backups/priority', data=data).status_code, 400)
        self.assertEqual(self.client.get('/api/backups/priority?path=/etc/passwd').status_code, 400)
        self.assertEqual(self.client.post('/api/backups/priority/capture', json={}).status_code, 400)
        self.assertEqual(self.client.post('/api/backups/priority/capture', headers={'Origin':'https://foreign.test'}).status_code, 403)
        self.command.assert_not_called()

    def test_missing_helper_and_raw_private_output_are_not_exposed(self):
        self.command.return_value = subprocess.CompletedProcess([], 1, 'PRIVATE', 'PRIVATE')
        response = self.client.get('/api/backups/priority')
        self.assertEqual(response.status_code, 503)
        self.assertNotIn('PRIVATE', response.get_data(as_text=True))
        self.command.return_value = subprocess.CompletedProcess([], 2, json.dumps({'ok':False,'message':'The frozen copy is complete; stopping Time Machine is unnecessary.'}))
        response = self.client.post('/api/backups/priority/capture')
        self.assertEqual(response.status_code, 400)
        self.assertIn('unnecessary', response.json['message'])


if __name__ == '__main__': unittest.main()
