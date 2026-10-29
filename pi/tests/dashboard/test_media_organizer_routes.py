"""Exercise the dashboard boundary against a real, isolated JSON CLI process."""
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest import mock

from flask import Flask

from pi.apps.van_dashboard.http import reject_cross_origin_mutations
from pi.apps.van_dashboard.routes import media_organizer as routes
from pi.apps.van_dashboard.van_dashboard_media_organizer import MediaOrganizerController


FAKE_CLI = '''
import json, pathlib, sys, time
root = pathlib.Path(sys.argv[1])
command, *args = sys.argv[2:]
with (root / 'calls.jsonl').open('a') as stream:
    stream.write(json.dumps([command, *args]) + '\\n')
if 'fail' in args:
    print(json.dumps({'error': 'fixture command failed'}))
    raise SystemExit(1)
if command in ('propose', 'apply', 'undo'):
    while (root / 'hold').exists():
        time.sleep(.01)
if command == 'status':
    result = json.loads((root / 'status.json').read_text())
elif command == 'decide':
    decisions = json.loads(pathlib.Path(args[args.index('--decisions-file') + 1]).read_text())
    (root / 'decisions.json').write_text(json.dumps(decisions))
    result = {'id': args[0], 'decisions': decisions}
elif command == 'settings':
    result = json.loads((root / 'status.json').read_text())['settings']
    for index, arg in enumerate(args):
        if arg == '--set':
            key, value = args[index + 1].split('=', 1)
            try: value = json.loads(value)
            except ValueError: pass
            result[key] = value
elif command == 'alias':
    result = {'status': 'scheduled'}
else:
    result = {'id': args[0] if args and not args[0].startswith('--') else 'new-plan', 'command': command}
print(json.dumps(result))
'''


class MediaOrganizerRoutesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        script = self.root / 'fake.py'
        script.write_text(FAKE_CLI)
        self.payload = {
            'media_mounted': True, 'mirror_available': True, 'qbittorrent_available': True,
            'new_count': 12, 'tracked_count': 10, 'untracked_count': 2, 'incomplete_count': 1,
            'latest_plan': None, 'recent_runs': [], 'busy': None, 'last_error': None,
            'settings': {'auto_apply': True, 'auto_apply_min_confidence': .85,
                         'auto_apply_new_shows': False, 'classifier': 'codex', 'codex_model': '',
                         'mirror_rename': True, 'run_alias_after_apply': True},
        }
        self.write_status()
        self.control = MediaOrganizerController(cli=[sys.executable, '-I', str(script), str(self.root)])
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

    def release_job(self):
        (self.root / 'hold').unlink(missing_ok=True)
        self.wait_finished()

    def wait_finished(self):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            result = self.control.status()
            if not result['job'] or result['job']['finished_at']:
                return result['job']
            time.sleep(.01)
        self.fail('background job did not finish')

    def post(self, path, payload=None):
        return self.client.post('/api/media-organizer/' + path, json=payload or {})

    def test_status_merges_cli_and_job_lifecycle_and_rejects_second_job(self):
        response = self.client.get('/api/media-organizer/status')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json, dict(self.payload, job=None))
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        (self.root / 'hold').touch()
        response = self.post('propose', {'classifier': 'offline'})
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json['stage'], 'propose')
        self.assertIsNone(response.json['finished_at'])
        self.assertEqual(self.post('apply', {'plan_id': 'p'}).status_code, 409)
        self.assertEqual(self.post('alias').status_code, 409)
        self.assertEqual(self.client.get('/api/media-organizer/status').json['job']['stage'], 'propose')
        self.release_job()
        job = self.client.get('/api/media-organizer/status').json['job']
        self.assertTrue(job['ok'])
        self.assertIsNotNone(job['finished_at'])
        self.assertEqual(job['result_id'], 'new-plan')
        calls = [json.loads(line) for line in (self.root / 'calls.jsonl').read_text().splitlines()]
        self.assertIn(['propose', '--classifier', 'offline'], calls)

    def test_external_busy_blocks_mutations_and_is_preserved(self):
        self.payload['busy'] = {'stage': 'apply', 'started_at': 'now', 'pid': 123}
        self.write_status()
        self.assertEqual(self.post('propose').status_code, 409)
        self.assertEqual(self.post('settings', {'auto_apply': False}).status_code, 409)
        self.assertEqual(self.client.get('/api/media-organizer/status').json['busy'], self.payload['busy'])

    def test_decisions_use_private_file_removed_after_command(self):
        decisions = {'Silo S01': {'category': 'TV', 'show': 'Silo', 'accepted': True}}
        response = self.post('decide', {'plan_id': 'plan-1', 'decisions': decisions})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['decisions'], decisions)
        calls = [json.loads(line) for line in (self.root / 'calls.jsonl').read_text().splitlines()]
        call = next(call for call in calls if call[0] == 'decide')
        self.assertEqual(call[:3], ['decide', 'plan-1', '--decisions-file'])
        self.assertNotIn('Silo', ' '.join(call))
        self.assertFalse(Path(call[3]).exists())

    def test_settings_partial_update_and_alias(self):
        response = self.post('settings', {'auto_apply': False, 'classifier': 'offline'})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json['auto_apply'])
        self.assertEqual(response.json['classifier'], 'offline')
        self.assertEqual(self.client.get('/api/media-organizer/settings').status_code, 200)
        self.assertEqual(self.post('alias').json, {'status': 'scheduled'})

    def test_plan_and_run_lookups_and_cli_error_mapping(self):
        for plural, command in (('plans', 'plan'), ('runs', 'run')):
            response = self.client.get('/api/media-organizer/' + plural + '/record-1')
            self.assertEqual(response.json, {'id': 'record-1', 'command': command})
        response = self.client.get('/api/media-organizer/plans/fail')
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json['message'], 'fixture command failed')

    def test_apply_undo_and_background_failure(self):
        self.assertEqual(self.post('apply', {'plan_id': 'p', 'auto': True}).status_code, 202)
        self.assertTrue(self.wait_finished()['ok'])
        self.assertEqual(self.post('undo', {'run_id': 'fail'}).status_code, 202)
        job = self.wait_finished()
        self.assertFalse(job['ok'])
        self.assertEqual(job['error'], 'fixture command failed')

    def test_invalid_requests_fail_before_cli_mutations(self):
        for path, payload in [('apply', {'plan_id': 'p', 'auto': 'false'}),
                              ('apply', {'plan_id': '--help'}),
                              ('settings', {'auto_apply': 'false'}),
                              ('settings', {'auto_apply_min_confidence': 1.1}),
                              ('settings', {'unknown': True}),
                              ('propose', {'classifier': 'unknown'}),
                              ('decide', {'plan_id': 'p', 'decisions': []})]:
            self.assertEqual(self.post(path, payload).status_code, 400, (path, payload))
        self.assertEqual(self.client.post('/api/media-organizer/propose', data='{}').status_code, 400)
        response = self.client.post('/api/media-organizer/propose', json={}, headers={'Origin': 'https://other.example'})
        self.assertEqual(response.status_code, 403)

    def test_default_cli_preserves_package_execution_contract(self):
        control = MediaOrganizerController()
        self.assertEqual(control.cli, (sys.executable, '-P', '-m', 'pi.apps.media_organizer'))


if __name__ == '__main__':
    unittest.main()
