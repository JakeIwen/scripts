import json
import subprocess
import unittest
from unittest import mock

from flask import Flask

from pi.apps.van_dashboard import cloud_upload_speeds as speeds
from pi.apps.van_dashboard.routes import backups


def helper_output(stdout, returncode=0):
    return mock.Mock(return_value=subprocess.CompletedProcess([], returncode, stdout))


class CloudUploadSpeedTests(unittest.TestCase):
    def test_each_job_uses_its_own_fixed_helper_flag(self):
        command = helper_output(json.dumps({'samples': [[1000, 2048]]}))
        self.assertEqual(speeds.upload_speeds('time-machine', command), [[1000, 2048]])
        self.assertEqual(command.call_args.args[0][1:], [
            '-n', '/usr/bin/python3', '/home/pi/scripts/backup/time_machine_icloud_status.py',
            '--upload-speeds'])
        speeds.upload_speeds('pi', command)
        self.assertIn('/home/pi/scripts/backup/icloud_status.py', command.call_args.args[0])

    def test_unknown_job_and_malformed_helper_output_are_refused(self):
        with self.assertRaises(ValueError):
            speeds.upload_speeds('../etc', helper_output('{}'))
        for stdout in ('nope', '{}', '{"samples": [[1, -2]]}', '{"samples": [[1.5, 2]]}',
                       '{"samples": [[1, 2, 3]]}'):
            with self.assertRaises(RuntimeError):
                speeds.upload_speeds('pi', helper_output(stdout))
        with self.assertRaises(RuntimeError):
            speeds.upload_speeds('pi', helper_output('', returncode=1))

    def test_route_maps_errors_and_rejects_query_input(self):
        app = Flask(__name__)
        app.register_blueprint(backups.bp)
        client = app.test_client()
        with mock.patch.object(backups, 'upload_speeds', return_value=[[1, 2]]):
            response = client.get('/api/backups/cloud/pi/upload-speeds')
            self.assertEqual(response.get_json(), {'ok': True, 'samples': [[1, 2]]})
            self.assertEqual(response.headers['Cache-Control'], 'no-store')
            self.assertEqual(client.get('/api/backups/cloud/pi/upload-speeds?x=1').status_code, 400)
        with mock.patch.object(backups, 'upload_speeds', side_effect=ValueError('invalid cloud backup')):
            self.assertEqual(client.get('/api/backups/cloud/x/upload-speeds').status_code, 400)
        with mock.patch.object(backups, 'upload_speeds', side_effect=RuntimeError('private')):
            response = client.get('/api/backups/cloud/pi/upload-speeds')
            self.assertEqual(response.status_code, 503)
            self.assertNotIn('private', response.get_data(as_text=True))


if __name__ == '__main__':
    unittest.main()
