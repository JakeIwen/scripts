import json
import io
import os
import subprocess
import unittest
from unittest import mock

from flask import Flask
from pi.apps.van_dashboard.http import reject_cross_origin_mutations
from pi.apps.van_dashboard.routes import backup_settings as routes
from pi.apps.van_dashboard.backup_settings_control import BackupSettingsControl


class SettingsRoutesTests(unittest.TestCase):
    def test_chunked_body_is_bounded_without_content_length(self):
        stream=io.BytesIO(json.dumps({'revision':'r','changes':{'x':'a'*20000}}).encode())
        response=self.client.open('/api/backups/settings',method='POST',input_stream=stream,
            content_type='application/json',environ_overrides={'CONTENT_LENGTH':'','wsgi.input_terminated':True})
        self.assertEqual(response.status_code,400)
        self.command.assert_not_called()
        self.assertLessEqual(stream.tell(),16385)

    def setUp(self):
        self.command = mock.Mock(return_value=subprocess.CompletedProcess([],0,
            json.dumps({'ok':True,'settings':{'revision':'r','values':{}}}),''))
        patch = mock.patch.object(routes,'control',BackupSettingsControl(self.command)); patch.start(); self.addCleanup(patch.stop)
        app = Flask(__name__); app.before_request(reject_cross_origin_mutations); app.register_blueprint(routes.bp)
        self.client = app.test_client()

    def test_settings_read_and_save_use_fixed_commands_and_stdin(self):
        self.assertEqual(self.client.get('/api/backups/settings').status_code,200)
        self.assertEqual(self.command.call_args.args[0][-1],'--status')
        payload={'revision':'r','changes':{'pi.interval':3}}
        result=self.client.post('/api/backups/settings',json=payload)
        self.assertEqual(result.status_code,200)
        self.assertEqual(self.command.call_args.args[0][-1],'--save')
        self.assertEqual(json.loads(self.command.call_args.kwargs['input_text']),payload)

    def test_invalid_and_oversized_requests_never_reach_writer(self):
        for options in ({'json':{}},{'json':{'revision':'r','changes':{},'path':'/etc/passwd'}},
                        {'data':'x'*17000,'content_type':'application/json'}):
            self.assertEqual(self.client.post('/api/backups/settings',**options).status_code,400)
        self.command.assert_not_called()

    def test_conflicts_are_actionable_and_private_failures_are_not_exposed(self):
        self.command.return_value=subprocess.CompletedProcess([],os.EX_TEMPFAIL,json.dumps({'ok':False,
            'message':'A newer settings revision is available.'}),'')
        self.assertEqual(self.client.post('/api/backups/settings',json={'revision':'r','changes':{}}).status_code,409)
        self.command.return_value=subprocess.CompletedProcess([],1,'PRIVATE','PRIVATE')
        response=self.client.get('/api/backups/settings')
        self.assertEqual(response.status_code,503)
        self.assertNotIn('PRIVATE',response.get_data(as_text=True))
