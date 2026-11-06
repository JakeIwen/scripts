"""Logs adapter against a fake rotator CLI: wire decoding, argv identity, confirmation and 409 mapping."""
import json
import unittest

from cli_runner import CommandResult
from logs_adapter import LogsAdapter, log_samples, warnings
from panel_contracts import ApiError, Level, Section
from panel_paths import LOG_ROTATE, LOG_ROTATE_LABEL, ROOT
from panel_routes import PanelRoutes
from panel_test_support import FakeRunner, json_command
from sampler import CADENCES, samples

MB = 1024 * 1024
PATH = '/Users/x/.local/state/metered/launchd.stdout.log'
REVISION = 'rev-1'


def policy(**over):
    return {'max_mb': 10, 'keep': 5, 'compress': True, 'enabled': True, 'override': False, **over}


def entry(path=PATH, **over):
    return {'path': path, 'source': 'launchagent:com.jacobr.metered', 'bytes': 35 * MB, 'archived_bytes': 4 * MB,
            'archives': 2, 'mtime': 1.7e9, 'growth_mb_per_day': 1.5, 'policy': policy(), 'last_rotated': None,
            'state': 'due', **over}


def inventory(*logs, ignored=()):
    return {'generated_at': 1.7e9, 'revision': REVISION, 'defaults': {'max_mb': 10, 'keep': 5, 'compress': True, 'warn_total_mb': 500},
            'totals': {'files': len(logs), 'bytes': sum(x['bytes'] for x in logs), 'archived_bytes': sum(x['archived_bytes'] for x in logs)},
            'logs': list(logs), 'ignored': list(ignored)}


class FakeServices:
    def __init__(self, state=None):
        self.state = state or {'label': LOG_ROTATE_LABEL, 'installed': True, 'loaded': True, 'pid': None, 'last_exit': 0}

    def agent_state(self, label):
        assert label == LOG_ROTATE_LABEL
        if isinstance(self.state, Exception):
            raise self.state
        return self.state


def adapter(results, services=None):
    runner = FakeRunner(results)
    return LogsAdapter(runner, services or FakeServices()), runner


class LogsReadTests(unittest.TestCase):
    def test_script_and_launchd_label_are_the_rotators(self):
        self.assertEqual(LOG_ROTATE, ('/usr/bin/python3', '-I', '-B', str(ROOT / 'macbook/scripts/log_rotate.py')))
        self.assertEqual(LOG_ROTATE_LABEL, 'com.jacobr.log-rotate')

    def test_inventory_is_decoded_with_service_state(self):
        logs, runner = adapter([json_command(inventory(entry(), ignored=['/Users/x/skip.log']))])
        payload = logs.read()
        self.assertEqual(runner.calls[0][0], (*LOG_ROTATE, 'inventory', '--json'))
        self.assertEqual(payload['revision'], REVISION)
        self.assertEqual(payload['logs'][0]['path'], PATH)
        self.assertEqual(payload['ignored'], ['/Users/x/skip.log'])
        self.assertTrue(payload['service']['loaded'])

    def test_ignored_records_may_be_objects_and_a_launchctl_failure_leaves_service_unknown(self):
        logs, _ = adapter([json_command(inventory(ignored=[{'path': '/Users/x/a.log'}]))], FakeServices(ApiError(502, 'launchctl')))
        payload = logs.read()
        self.assertEqual(payload['ignored'], ['/Users/x/a.log'])
        self.assertIsNone(payload['service']['loaded'])

    def test_malformed_inventory_is_a_bad_gateway(self):
        bad_state = inventory(entry(state='weird'))
        no_policy = inventory(entry())
        del no_policy['logs'][0]['policy']['keep']
        for value in (bad_state, no_policy, {'revision': 'x'}, inventory(entry(path='relative.log'))):
            with self.subTest(value=value), self.assertRaises(ApiError) as caught:
                adapter([json_command(value)])[0].read()
            self.assertEqual(caught.exception.status, 502)

    def test_missing_cli_output_is_an_error_not_a_crash(self):
        with self.assertRaises(ApiError):
            adapter([CommandResult(1, '', 'no such file')])[0].read()


class LogsActionTests(unittest.TestCase):
    def test_rotate_requires_confirmation_and_runs_nothing_without_it(self):
        logs, runner = adapter([])
        with self.assertRaises(ApiError) as caught:
            logs.mutate({'action': 'rotate', 'path': PATH})
        self.assertEqual(caught.exception.status, 400)
        self.assertEqual(runner.calls, [])

    def test_rotate_one_forced_and_rotate_all_due(self):
        result = {'rotated': [{'path': PATH, 'bytes_before': 35 * MB, 'archive': PATH + '.1.gz'}], 'skipped': [], 'errors': []}
        logs, runner = adapter([json_command(result), json_command({'rotated': [], 'skipped': [{'path': PATH, 'reason': 'x'}], 'errors': []})])
        reply = logs.mutate({'action': 'rotate', 'path': PATH, 'force': True, 'confirmed': True})
        self.assertEqual(reply.body['message'], 'Rotated 1, skipped 0')
        logs.mutate({'action': 'rotate', 'confirmed': True})
        self.assertEqual([call[0] for call in runner.calls],
                         [(*LOG_ROTATE, 'rotate', '--json', '--only', PATH, '--force'), (*LOG_ROTATE, 'rotate', '--json')])

    def test_rotate_errors_are_named_in_the_message(self):
        value = {'rotated': [], 'skipped': [], 'errors': ['disk full']}
        reply = adapter([json_command(value)])[0].mutate({'action': 'rotate', 'confirmed': True})
        self.assertIn('1 failed: disk full', reply.body['message'])

    def test_policy_argv_for_each_field_and_clear(self):
        logs, runner = adapter([json_command({'revision': 'rev-2'})] * 2)
        reply = logs.mutate({'action': 'policy', 'revision': REVISION, 'path': PATH, 'max_mb': 20, 'keep': 3,
                             'compress': False, 'enabled': True})
        self.assertEqual(reply.body, {'message': 'Policy saved', 'revision': 'rev-2'})
        logs.mutate({'action': 'policy', 'revision': REVISION, 'path': PATH, 'clear': True})
        self.assertEqual(runner.calls[0][0], (*LOG_ROTATE, 'policy', '--json', '--revision', REVISION, '--max-mb', '20',
                                              '--keep', '3', '--compress', 'off', '--enabled', 'on', PATH))
        self.assertEqual(runner.calls[1][0], (*LOG_ROTATE, 'policy', '--json', '--revision', REVISION, '--clear', PATH))

    def test_add_ignore_unignore_argv(self):
        logs, runner = adapter([json_command({'revision': 'rev-2'})] * 3)
        for action in ('add', 'ignore', 'unignore'):
            self.assertEqual(logs.mutate({'action': action, 'revision': REVISION, 'path': PATH}).body['revision'], 'rev-2')
        self.assertEqual([call[0] for call in runner.calls],
                         [(*LOG_ROTATE, name, '--json', '--revision', REVISION, PATH) for name in ('add', 'ignore', 'unignore')])

    def test_invalid_requests_never_reach_the_cli(self):
        logs, runner = adapter([])
        bad = [{'action': 'delete'}, {'action': 'add', 'revision': REVISION, 'path': 'relative'},
               {'action': 'add', 'revision': REVISION, 'path': '/a\n--force'}, {'action': 'add', 'revision': '', 'path': PATH},
               {'action': 'policy', 'revision': REVISION, 'path': PATH}, {'action': 'policy', 'revision': REVISION, 'path': PATH, 'keep': -1},
               {'action': 'policy', 'revision': REVISION, 'path': PATH, 'max_mb': 0},
               {'action': 'policy', 'revision': REVISION, 'path': PATH, 'clear': True, 'keep': 2},
               {'action': 'policy', 'revision': REVISION, 'path': PATH, 'compress': 'yes'},
               {'action': 'ignore', 'revision': REVISION, 'path': PATH, 'keep': 1},
               {'action': 'rotate', 'path': 'x', 'confirmed': True}, {'action': 'rotate', 'confirmed': True, 'extra': 1}]
        for body in bad:
            with self.subTest(body=body), self.assertRaises(ApiError) as caught:
                logs.mutate(body)
            self.assertEqual(caught.exception.status, 400)
        self.assertEqual(runner.calls, [])

    def test_exit_codes_map_to_conflict_busy_and_refusal(self):
        body = {'action': 'ignore', 'revision': REVISION, 'path': PATH}
        stale = CommandResult(3, json.dumps({'error': 'revision changed'}))
        busy = CommandResult(75, '', 'busy')
        refused = CommandResult(1, json.dumps({'error': 'path is outside HOME'}))
        for result, status, code, text in ((stale, 409, 'conflict', 'revision changed'), (busy, 409, 'busy', 'busy'),
                                           (refused, 400, None, 'outside HOME')):
            with self.subTest(code=result.returncode), self.assertRaises(ApiError) as caught:
                adapter([result])[0].mutate(body)
            self.assertEqual((caught.exception.status, caught.exception.code), (status, code))
            self.assertIn(text, str(caught.exception))
        with self.assertRaises(ApiError) as caught:
            adapter([busy])[0].mutate({'action': 'rotate', 'confirmed': True})
        self.assertEqual((caught.exception.status, caught.exception.code), (409, 'busy'))

    def test_routes_expose_get_and_post(self):
        routes = PanelRoutes(runner=FakeRunner([]))
        try:
            self.assertIn('/api/logs', routes.reads)
            self.assertIn('/api/logs', routes.writes)
        finally:
            routes.close()


class LogsSamplingTests(unittest.TestCase):
    def payload(self, *logs, **over):
        return {**inventory(*logs), 'service': {'installed': True, 'loaded': True}, **over}

    def test_cadence_value_and_text(self):
        self.assertEqual(CADENCES[Section.LOGS], 900)
        row, = samples(Section.LOGS, self.payload(entry(bytes=15 * MB, archived_bytes=5 * MB, state='ok')), 10)
        self.assertEqual((row['key'], row['value'], row['text'], row['level']), ('total', 15 * MB, '15.0 MB live, 5.0 MB archived', Level.OK))

    def test_warnings_for_overshoot_refusal_stopped_agent_and_total(self):
        self.assertEqual(warnings(self.payload(entry(bytes=19 * MB))), [])
        self.assertEqual(len(warnings(self.payload(entry(bytes=21 * MB)))), 1)
        self.assertEqual(len(warnings(self.payload(entry(state='refused', bytes=1)))), 1)
        self.assertEqual(len(warnings(self.payload(entry(bytes=1), service={'installed': True, 'loaded': False}))), 1)
        self.assertEqual(len(warnings(self.payload(entry(bytes=1, archived_bytes=600 * MB)))), 1)
        off = entry(bytes=99 * MB, policy=policy(enabled=False))
        self.assertEqual(warnings(self.payload(off)), [])
        self.assertEqual(log_samples(self.payload(entry(bytes=21 * MB)))[0][1], Level.WARN)


if __name__ == '__main__':
    unittest.main()
