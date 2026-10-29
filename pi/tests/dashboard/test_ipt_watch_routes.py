"""Exercise the watchlist boundary against an isolated real JSON CLI process."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

from flask import Flask

from pi.apps.van_dashboard.http import reject_cross_origin_mutations
from pi.apps.van_dashboard.routes import ipt_watch as routes
from pi.apps.van_dashboard.van_dashboard_ipt_watch import IptWatchController

FAKE_CLI = '''
import json, pathlib, sys, time
root = pathlib.Path(sys.argv[1])
command, *args = sys.argv[2:]
with (root / 'calls.jsonl').open('a') as stream:
    stream.write(json.dumps([command, *args]) + '\\n')
if command == 'check':
    while (root / 'hold').exists():
        time.sleep(.01)
if (root / 'fail').exists() and command != 'status' or (root / 'statusfail').exists():
    print(json.dumps({'error': 'fixture command failed'}))
    raise SystemExit(1)
if command in ('status', 'check'):
    result = json.loads((root / 'status.json').read_text())
elif command == 'wantlist':
    result = {'wanted': [{'name': args[1], 'action': args[0]}]}
else:
    result = {'torrent_id': args[0], 'status': 'downloaded' if command == 'download' else 'dismissed'}
print(json.dumps(result))
'''


class IptWatchRoutesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        script = self.root / 'fake.py'
        script.write_text(FAKE_CLI)
        self.payload = dict(login='ok', last_check=None, last_error=None, checking=False,
                            wanted_count=0, new_candidate_count=0, tfiles_writable=True,
                            library_available=True, wanted=[], candidates=[])
        self.write_status()
        self.control = IptWatchController(cli=[sys.executable, '-I', str(script), str(self.root)])
        patch = mock.patch.object(routes, 'control', self.control)
        patch.start()
        self.addCleanup(patch.stop)
        app = Flask(__name__)
        app.before_request(reject_cross_origin_mutations)
        app.register_blueprint(routes.bp)
        self.client = app.test_client()
        self.addCleanup(self.release_job)

    def write_status(self):
        (self.root / 'status.json').write_text(json.dumps(self.payload))

    def wait_finished(self):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            job = self.control.status()['job']
            if not job or job['finished_at']:
                return job
            time.sleep(.01)
        self.fail('background check did not finish')

    def release_job(self):
        (self.root / 'hold').unlink(missing_ok=True)
        self.wait_finished()

    def post(self, path, payload=None):
        return self.client.post('/api/ipt-watch/' + path, json=payload or {})

    def calls(self):
        return [json.loads(line) for line in (self.root / 'calls.jsonl').read_text().splitlines()]

    def test_status_merge_and_check_job_lifecycle_and_conflicts(self):
        response = self.client.get('/api/ipt-watch/status')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json, dict(self.payload, job=None))
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        (self.root / 'hold').touch()
        response = self.post('check', {'show': 'Silo'})
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json['stage'], 'check')
        self.assertIsNone(response.json['finished_at'])
        self.assertEqual(self.post('check').status_code, 409)
        self.assertEqual(self.post('wantlist', {'action': 'add', 'name': 'Silo'}).status_code, 409)
        self.assertEqual(self.post('download', {'torrent_id': '123'}).status_code, 409)
        self.assertEqual(self.client.get('/api/ipt-watch/status').json['job']['stage'], 'check')
        self.release_job()
        job = self.client.get('/api/ipt-watch/status').json['job']
        self.assertTrue(job['ok'])
        self.assertIsNotNone(job['finished_at'])
        self.assertIsNone(job['result_id'])
        self.assertIn(['check', '--show', 'Silo'], self.calls())

    def test_external_check_is_preserved_and_blocks_mutation(self):
        self.payload['checking'] = True
        self.write_status()
        self.assertEqual(self.post('check').status_code, 409)
        self.assertTrue(self.client.get('/api/ipt-watch/status').json['checking'])

    def test_wantlist_actions_and_download_dismiss_passthrough(self):
        for action in ('add', 'remove', 'pause', 'resume'):
            response = self.post('wantlist', dict(action=action, name='Silo'))
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json, {'wanted': [{'name': 'Silo', 'action': action}]})
        response = self.post('wantlist', dict(action='add', name='The Show', season=3, episode=7))
        self.assertEqual(response.status_code, 200)
        self.assertIn(['wantlist', 'add', 'The Show', '--season', '3', '--episode', '7'], self.calls())
        for action, state in (('download', 'downloaded'), ('dismiss', 'dismissed')):
            response = self.post(action, {'torrent_id': '123'})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json, dict(torrent_id='123', status=state))

    def test_error_mapping_and_failed_background_check(self):
        (self.root / 'statusfail').touch()
        response = self.client.get('/api/ipt-watch/status')
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json['message'], 'fixture command failed')
        (self.root / 'statusfail').unlink()
        (self.root / 'hold').touch()
        self.assertEqual(self.post('check').status_code, 202)
        (self.root / 'fail').touch()
        self.release_job()
        (self.root / 'fail').unlink()
        job = self.client.get('/api/ipt-watch/status').json['job']
        self.assertFalse(job['ok'])
        self.assertEqual(job['error'], 'fixture command failed')

    def test_invalid_requests_and_cross_origin_are_rejected(self):
        for path, payload in [
            ('check', {'show': '--help'}), ('check', {'unknown': True}),
            ('check', {'show': []}), ('wantlist', {'action': 'other', 'name': 'Silo'}),
            ('wantlist', {'action': 'add', 'name': ''}),
            ('wantlist', {'action': 'add', 'name': 'Silo', 'season': True}),
            ('wantlist', {'action': 'pause', 'name': 'Silo', 'episode': 2}),
            ('wantlist', {'action': 'add', 'name': 'Silo', 'episode': -1}),
            ('download', {'torrent_id': '--help'}), ('dismiss', {'torrent_id': 123}),
        ]:
            self.assertEqual(self.post(path, payload).status_code, 400, (path, payload))
        self.assertFalse((self.root / 'calls.jsonl').exists())
        self.assertEqual(self.client.post('/api/ipt-watch/check', data='{}').status_code, 400)
        self.assertEqual(self.post('check?show=Silo').status_code, 400)
        self.assertEqual(self.client.post('/api/ipt-watch/check', data='{', content_type='application/json').status_code, 400)
        self.assertEqual(self.client.post('/api/ipt-watch/check', json={}, headers={'Origin': 'https://other.example'}).status_code, 403)

    def test_cli_transport_errors_and_default_package_command(self):
        self.assertEqual(IptWatchController().cli, (sys.executable, '-P', '-m', 'pi.apps.iptorrents_watch'))
        for stdout in ('bad json', '[]'):
            command = mock.Mock(return_value=subprocess.CompletedProcess([], 0, stdout))
            with self.assertRaises(RuntimeError):
                IptWatchController(command=command).status()
        for failure in (OSError('no executable'), subprocess.TimeoutExpired('cli', 1)):
            command = mock.Mock(side_effect=failure)
            with mock.patch.object(routes, 'control', IptWatchController(command=command)):
                self.assertEqual(self.client.get('/api/ipt-watch/status').status_code, 502)


if __name__ == '__main__':
    unittest.main()
