import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from automations_adapter import AutomationsAdapter
from cli_runner import CommandResult
from panel_contracts import ApiError, Reply, Section
from panel_routes import PanelRoutes
from panel_test_support import FakeRunner, auth, json_command, request, running_server


class AutomationRoutes:
    def __init__(self, runner):
        self.adapter = AutomationsAdapter(runner)

    def dispatch(self, method, path, body=None):
        if path != '/api/automations':
            raise ApiError(404, 'Route not found')
        value = self.adapter.read() if method == 'GET' else self.adapter.action(body)
        return Reply(value)


class AutomationsEndpointTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name)
        self.environment = patch.dict(
            os.environ, {'JACOBR_AUTOMATIONS_DIR': str(self.directory)})
        self.environment.start()

    def tearDown(self):
        self.environment.stop()
        self.temporary.cleanup()

    @staticmethod
    def declaration(tool, *, status_timeout=7, action_timeout=11, confirm=False):
        return {
            'tool': tool,
            'title': f'{tool.title()} title',
            'description': f'{tool} description',
            'service': f'com.jacobr.{tool}',
            'settings': tool,
            'status': {'argv': ['/bin/echo', 'status', tool], 'timeout': status_timeout},
            'actions': [
                {'name': 'run-now', 'label': 'Run now', 'description': 'Run immediately.',
                 'argv': ['/bin/echo', 'action', tool], 'timeout': action_timeout,
                 'confirm': confirm},
            ],
        }

    def write(self, tool='fixture', **options):
        value = self.declaration(tool, **options)
        (self.directory / f'{tool}.json').write_text(json.dumps(value), encoding='utf-8')
        return value

    @staticmethod
    def status(summary='Watching', level='ok'):
        return {
            'level': level,
            'summary': summary,
            'fields': [{'label': 'Last run', 'value': 'Completed', 'time': 1760000000.0,
                        'level': None}],
        }

    def get(self, runner):
        with running_server(AutomationRoutes(runner)) as server:
            status, _, raw = request(server, '/api/automations', headers=auth(server))
        return status, json.loads(raw)

    def post(self, runner, body):
        with running_server(AutomationRoutes(runner)) as server:
            status, _, raw = request(
                server, '/api/automations', 'POST', json.dumps(body), auth(server, True))
        return status, json.loads(raw)

    def test_get_is_sorted_and_runs_status_with_declared_argv_and_timeout(self):
        self.write('zulu', status_timeout=13)
        self.write('alpha', status_timeout=5)
        runner = FakeRunner([
            json_command(self.status('Alpha ready')),
            json_command(self.status('Zulu ready', 'idle')),
        ])

        http_status, payload = self.get(runner)

        self.assertEqual(http_status, 200)
        self.assertEqual([item['tool'] for item in payload['automations']], ['alpha', 'zulu'])
        self.assertEqual(payload['automations'][0], {
            'tool': 'alpha',
            'title': 'Alpha title',
            'description': 'alpha description',
            'service': 'com.jacobr.alpha',
            'settings': 'alpha',
            'actions': [{'name': 'run-now', 'label': 'Run now',
                         'description': 'Run immediately.', 'confirm': False}],
            'status': self.status('Alpha ready'),
            'error': None,
        })
        self.assertEqual(runner.calls, [
            (('/bin/echo', 'status', 'alpha'), 5),
            (('/bin/echo', 'status', 'zulu'), 13),
        ])
        self.assertNotIn('argv', json.dumps(payload))

    def test_broken_declaration_and_failed_status_leave_working_item_intact(self):
        (self.directory / 'broken.json').write_text('{broken', encoding='utf-8')
        self.write('failing')
        self.write('working')
        runner = FakeRunner([
            CommandResult(9, stderr='status failed'),
            json_command(self.status('Working')),
        ])

        http_status, payload = self.get(runner)

        self.assertEqual(http_status, 200)
        records = {record['tool']: record for record in payload['automations']}
        self.assertEqual(records['broken']['title'], 'broken')
        self.assertEqual(records['broken']['actions'], [])
        self.assertIsNone(records['broken']['status'])
        self.assertTrue(records['broken']['error'])
        self.assertEqual(records['failing']['title'], 'Failing title')
        self.assertIsNone(records['failing']['status'])
        self.assertEqual(records['failing']['error'], 'status failed')
        self.assertEqual(records['working']['status']['summary'], 'Working')
        self.assertIsNone(records['working']['error'])

    def test_timeout_invalid_json_and_invalid_record_are_local_to_each_status(self):
        for tool in ('a-timeout', 'b-json', 'c-record', 'd-working'):
            self.write(tool)

        def timeout():
            raise ApiError(504, 'Tool timed out; inspect its state before retrying')

        runner = FakeRunner([
            timeout,
            CommandResult(0, '{bad json'),
            json_command({'level': 'fatal', 'summary': 'Bad', 'fields': []}),
            json_command(self.status('Still working')),
        ])
        http_status, payload = self.get(runner)

        self.assertEqual(http_status, 200)
        records = {record['tool']: record for record in payload['automations']}
        self.assertIn('timed out', records['a-timeout']['error'])
        self.assertEqual(records['b-json']['error'], 'Tool returned invalid JSON')
        self.assertIn('level must be one of', records['c-record']['error'])
        self.assertEqual(records['d-working']['status']['summary'], 'Still working')

    def test_post_runs_declared_action_and_returns_last_nonempty_stdout_line(self):
        self.write('fixture', action_timeout=19)
        runner = FakeRunner([CommandResult(0, '\nStarted\n\nFinished cleanly  \n')])

        http_status, payload = self.post(
            runner, {'tool': 'fixture', 'action': 'run-now'})

        self.assertEqual(http_status, 200)
        self.assertEqual(payload, {'message': 'Finished cleanly'})
        self.assertEqual(runner.calls, [(('/bin/echo', 'action', 'fixture'), 19)])

    def test_post_returns_done_when_action_has_no_nonempty_output(self):
        self.write('fixture')
        http_status, payload = self.post(
            FakeRunner([CommandResult(0, '\n  \n')]),
            {'tool': 'fixture', 'action': 'run-now'},
        )
        self.assertEqual(http_status, 200)
        self.assertEqual(payload, {'message': 'Done'})

    def test_confirmation_is_enforced_and_must_be_boolean(self):
        self.write('fixture', confirm=True)
        runner = FakeRunner([CommandResult(0)])
        for body in (
            {'tool': 'fixture', 'action': 'run-now'},
            {'tool': 'fixture', 'action': 'run-now', 'confirmed': False},
            {'tool': 'fixture', 'action': 'run-now', 'confirmed': 'yes'},
        ):
            with self.subTest(body=body):
                self.assertEqual(self.post(runner, body)[0], 400)
        self.assertEqual(self.post(
            runner, {'tool': 'fixture', 'action': 'run-now', 'confirmed': True})[0], 200)
        self.assertEqual(len(runner.calls), 1)

    def test_unknown_tool_action_missing_and_extra_fields_are_bad_requests(self):
        self.write('fixture')
        runner = FakeRunner([])
        bodies = (
            {'tool': 'unknown', 'action': 'run-now'},
            {'tool': 'fixture', 'action': 'unknown'},
            {'tool': 'fixture'},
            {'tool': 2, 'action': 'run-now'},
            {'tool': 'fixture', 'action': 'run-now', 'extra': True},
        )
        for body in bodies:
            with self.subTest(body=body):
                self.assertEqual(self.post(runner, body)[0], 400)
        self.assertEqual(runner.calls, [])

    def test_nonzero_action_is_502_and_timeout_is_504(self):
        self.write('fixture')
        status, payload = self.post(
            FakeRunner([CommandResult(4, stderr='action failed')]),
            {'tool': 'fixture', 'action': 'run-now'},
        )
        self.assertEqual(status, 502)
        self.assertEqual(payload, {'error': 'action failed'})

        def timeout():
            raise ApiError(504, 'Tool timed out; inspect its state before retrying')

        status, payload = self.post(
            FakeRunner([timeout]), {'tool': 'fixture', 'action': 'run-now'})
        self.assertEqual(status, 504)
        self.assertIn('timed out', payload['error'])

    def test_invalid_declaration_is_502_for_action(self):
        (self.directory / 'fixture.json').write_text('{broken', encoding='utf-8')
        status, payload = self.post(
            FakeRunner([]), {'tool': 'fixture', 'action': 'run-now'})
        self.assertEqual(status, 502)
        self.assertTrue(payload['error'])

    def test_panel_routes_exposes_automation_read_and_write(self):
        routes = PanelRoutes(runner=FakeRunner([]))
        try:
            self.assertIs(routes.adapters[Section.AUTOMATIONS], routes.automations)
            self.assertIs(routes.reads['/api/automations'].__self__, routes.automations)
            self.assertIs(routes.writes['/api/automations'].__self__, routes.automations)
        finally:
            routes.close()


if __name__ == '__main__':
    unittest.main()
