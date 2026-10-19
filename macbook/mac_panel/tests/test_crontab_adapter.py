import json
from pathlib import Path
import tempfile
import unittest

from cron_schedule import describe_schedule
from crontab_adapter import CrontabAdapter, SYNC, program_summary
from cli_runner import CommandResult
from panel_contracts import ApiError
from panel_routes import PanelRoutes
from panel_test_support import FakeRunner, auth, json_command, request, running_server

REVISION = 'a' * 64
JOB_ID = 'abcdef123456'


def status_payload(log_path=None):
    return {
        'revision': REVISION,
        'uncommitted': True,
        'jobs': [{
            'id': JOB_ID,
            'schedule_fields': ['0', '3', '*', '*', '*'],
            'command': 'cleanup',
            'paused': False,
            'managed_block': 'codex-release-cleanup',
            'managed_switch': None,
            'description': 'Tracked cleanup',
            'log_path': log_path,
        }],
        'live_only': [],
        'repo_only': ['0 3 * * * cleanup'],
        'installable': True,
    }


class CrontabTests(unittest.TestCase):
    def test_plain_words(self):
        cases = {'* * * * *': 'Every minute', '*/5 * * * *': 'Every 5 minutes',
                 '7 * * * *': 'Hourly at :07', '30 3 * * *': 'Daily at 03:30',
                 '0 8 * * 1-5': 'Weekly on Monday–Friday at 08:00',
                 '0 8 * * 0,6': 'Weekly on Sunday, Saturday at 08:00',
                 '0 8 * * mon,fri': 'Weekly on Monday, Friday at 08:00',
                 '0 8 1,3-5 * *': 'Monthly on day 1, 3–5 at 08:00',
                 '@reboot': 'At cron startup (usually boot)', '@hourly': 'Hourly at :00',
                 '*/99 * * * *': '*/99 * * * *', '0 3 1 2 *': '0 3 1 2 *'}
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(describe_schedule(raw), expected)

    def test_program_summary_names_the_script_without_paths_logs_or_flags(self):
        cases = {
            '$scripts/macbook/bettertouchtool/btt_backup.zsh daily >> $scripts/log/btt_backup.log 2>&1': 'btt_backup.zsh daily',
            '/bin/mkdir -p -m 700 /Users/x/.local/state/llm-restore && /usr/bin/python3 -I -B '
            '/Users/x/dev/scripts/shared/sh/llm_restore.py --snapshot --scheduled >> /Users/x/cron.log 2>&1':
                'llm_restore.py --snapshot --scheduled',
            '/usr/bin/python3 -I -B /Users/x/cleanup_codex_releases.py --apply > /Users/x/cleanup.log 2>&1':
                'cleanup_codex_releases.py --apply',
            'FOO=bar /usr/bin/env zsh -c "echo hi" | tee /tmp/out': 'zsh -c echo hi',
            'cleanup': 'cleanup',
            '"unterminated': '"unterminated',
            '> /dev/null': '> /dev/null',
        }
        for command, expected in cases.items():
            with self.subTest(command=command):
                self.assertEqual(program_summary(command), expected)

    def test_adapter_reads_validated_cli_status(self):
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / 'job.log'
            log.write_text('fixture')
            runner = FakeRunner([json_command(status_payload(str(log)))])
            value = CrontabAdapter(runner).read()
            self.assertEqual(runner.calls, [((*SYNC, 'status', '--json'), 30)])
            self.assertEqual(value['revision'], REVISION)
            self.assertTrue(value['uncommitted'])
            self.assertTrue(value['installable'])
            self.assertEqual(value['jobs'][0]['id'], JOB_ID)
            self.assertEqual(value['jobs'][0]['owner'], 'codex-release-cleanup — macbook/crontab')
            self.assertEqual(value['jobs'][0]['program'], 'cleanup')
            self.assertEqual(value['jobs'][0]['log_path'], str(log))
            self.assertIsInstance(value['jobs'][0]['log_modified'], float)
            secret = status_payload('/x/pi/secrets/private')
            hidden = CrontabAdapter(FakeRunner([json_command(secret)])).read()
            self.assertIsNone(hidden['jobs'][0]['log_path'])

    def test_adapter_maps_conflicts_and_validates_mutations(self):
        runner = FakeRunner([
            CommandResult(75, json.dumps({'error': 'revision changed'})),
            CommandResult(3, json.dumps({'error': 'Install refused', 'live_only': ['private job']})),
            json_command({'message': 'Job resumed', 'revision': 'b' * 64, 'uncommitted': True}),
        ])
        adapter = CrontabAdapter(runner)
        with self.assertRaises(ApiError) as raised:
            adapter.mutate({'action': 'pause', 'job': JOB_ID, 'revision': REVISION})
        self.assertEqual(raised.exception.status, 409)
        self.assertIn('revision changed', str(raised.exception))
        with self.assertRaises(ApiError) as raised:
            adapter.mutate({'action': 'install', 'revision': REVISION, 'confirmed': True})
        self.assertEqual(raised.exception.status, 409)
        self.assertIn('private job', str(raised.exception))
        reply = adapter.mutate({'action': 'resume', 'job': JOB_ID, 'revision': REVISION})
        self.assertEqual(reply.body['message'], 'Job resumed')
        self.assertEqual(runner.calls[-1][0], (*SYNC, 'resume', JOB_ID, '--revision', REVISION, '--json'))
        for body in ({'action': 'install', 'revision': REVISION},
                     {'action': 'pause', 'job': '../bad', 'revision': REVISION},
                     {'action': 'delete', 'revision': REVISION}):
            with self.assertRaises(ApiError):
                adapter.mutate(body)

    def test_crontab_route_dispatches_to_adapter(self):
        runner = FakeRunner([json_command({'message': 'Installed macbook/crontab',
                                           'revision': REVISION, 'uncommitted': False})])
        routes = PanelRoutes(runner=runner)
        try:
            reply = routes.dispatch('POST', '/api/crontab', {
                'action': 'install', 'revision': REVISION, 'confirmed': True})
            self.assertEqual(reply.body['message'], 'Installed macbook/crontab')
            self.assertEqual(runner.calls[0][0], (*SYNC, 'install', '--revision', REVISION, '--json'))
        finally:
            routes.close()

    def test_crontab_http_conflict_is_409(self):
        runner = FakeRunner([CommandResult(3, json.dumps({'error': 'Unsafe install'}))])
        routes = PanelRoutes(runner=runner)
        try:
            with running_server(routes) as server:
                body = json.dumps({'action': 'install', 'revision': REVISION, 'confirmed': True})
                status, _, raw = request(server, '/api/crontab', 'POST', body, auth(server, True))
                self.assertEqual(status, 409)
                self.assertEqual(json.loads(raw)['error'], 'Unsafe install')
        finally:
            routes.close()

    def test_invalid_cli_payload_fails_closed(self):
        value = status_payload()
        value['jobs'][0]['schedule_fields'] = ['invalid', 'count']
        with self.assertRaises(ApiError):
            CrontabAdapter(FakeRunner([json_command(value)])).read()


if __name__ == '__main__':
    unittest.main()
