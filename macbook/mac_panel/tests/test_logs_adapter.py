"""Logs adapter against a fake rotator CLI: wire decoding, argv identity, confirmation and 409 mapping."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import threading

from background_tasks import BackgroundTasks

from cli_runner import CliRunner, CommandResult
from logs_adapter import LogsAdapter, agent_problems, log_samples
from panel_contracts import ApiError, Level, Section
from panel_paths import LOG_ROTATE, LOG_ROTATE_ENV, LOG_ROTATE_LABEL, ROOT
from panel_routes import PanelRoutes
from panel_test_support import FakeRunner, json_command
from sampler import CADENCES, Sampler, samples

MB = 1024 * 1024
PATH = '/Users/x/.local/state/metered/launchd.stdout.log'
REVISION = 'rev-1'


def policy(**over):
    return {'max_mb': 10, 'keep': 5, 'compress': True, 'enabled': True, 'override': False, **over}


def entry(path=PATH, **over):
    return {'path': path, 'source': 'launchagent:com.jacobr.metered', 'bytes': 35 * MB, 'archived_bytes': 4 * MB,
            'archives': 2, 'mtime': 1.7e9, 'growth_mb_per_day': 1.5, 'policy': policy(), 'last_rotated': None,
            'state': 'due', **over}


def inventory(*logs, ignored=(), problems=(), revision=REVISION):
    return {'generated_at': 1.7e9, 'revision': revision, 'problems': list(problems),
            'defaults': {'max_mb': 10, 'keep': 5, 'compress': True, 'warn_total_mb': 500, 'enabled': True},
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
        # No -I: the script's package import needs its own directory on sys.path.
        self.assertEqual(LOG_ROTATE, ('/usr/bin/python3', '-B', str(ROOT / 'macbook/scripts/log_rotate.py')))
        self.assertEqual(LOG_ROTATE_ENV, {'PYTHONPATH': str(ROOT / 'shared/python')})
        self.assertEqual(LOG_ROTATE_LABEL, 'com.jacobr.log-rotate')

    def test_every_call_carries_the_rotators_python_path(self):
        logs, runner = adapter([json_command(inventory()), json_command(inventory(entry()) | {'log': entry()}),
                                json_command({'rotated': [], 'skipped': [], 'errors': []})])
        logs.read()
        logs.mutate({'action': 'add', 'revision': REVISION, 'path': PATH})
        logs.mutate({'action': 'rotate', 'confirmed': True})
        self.assertEqual(runner.envs, [LOG_ROTATE_ENV] * 3)

    def test_inventory_is_decoded_with_service_state(self):
        logs, runner = adapter([json_command(inventory(entry(), ignored=['/Users/x/skip.log']))])
        payload = logs.read()
        self.assertEqual(runner.calls[0][0], (*LOG_ROTATE, 'inventory', '--json'))
        self.assertEqual(payload['revision'], REVISION)
        self.assertEqual(payload['logs'][0]['path'], PATH)
        self.assertEqual(payload['ignored'], ['/Users/x/skip.log'])
        self.assertTrue(payload['service']['loaded'])

    def test_problems_are_the_rotators_list_after_the_agent_state(self):
        found = [{'level': 'error', 'text': 'x.log is 36.5 MB', 'path': PATH}, {'level': 'warn', 'text': 'total is high'}]
        stopped = {'label': LOG_ROTATE_LABEL, 'installed': True, 'loaded': False, 'pid': None, 'last_exit': 0}
        logs, _ = adapter([json_command(inventory(entry(), problems=found))], FakeServices(stopped))
        self.assertEqual(logs.read()['problems'], [{'level': 'warn', 'text': 'rotator agent is not running'}, *found])
        self.assertEqual(agent_problems({'installed': False})[0]['text'], 'rotator agent is not installed')
        self.assertEqual(agent_problems({'installed': True, 'loaded': None}), [])

    def test_ignored_records_may_be_objects_and_a_launchctl_failure_leaves_service_unknown(self):
        logs, _ = adapter([json_command(inventory(ignored=[{'path': '/Users/x/a.log'}]))], FakeServices(ApiError(502, 'launchctl')))
        payload = logs.read()
        self.assertEqual(payload['ignored'], ['/Users/x/a.log'])
        self.assertIsNone(payload['service']['loaded'])

    def test_malformed_inventory_is_a_bad_gateway(self):
        bad_state = inventory(entry(state='weird'))
        no_policy = inventory(entry())
        del no_policy['logs'][0]['policy']['keep']
        bad_problem = inventory(problems=[{'level': 'fatal', 'text': 'x'}])
        no_problems = inventory()
        del no_problems['problems']
        for value in (bad_state, no_policy, {'revision': 'x'}, inventory(entry(path='relative.log')), bad_problem, no_problems):
            with self.subTest(value=value), self.assertRaises(ApiError) as caught:
                adapter([json_command(value)])[0].read()
            self.assertEqual(caught.exception.status, 502)

    def test_missing_cli_output_is_an_error_not_a_crash(self):
        with self.assertRaises(ApiError):
            adapter([CommandResult(1, '', 'no such file')])[0].read()


class RunnerEnvironmentTests(unittest.TestCase):
    def test_extra_environment_is_added_to_the_minimal_child_environment(self):
        plain = CliRunner().run(('/usr/bin/env',)).stdout.splitlines()
        extended = CliRunner().run(('/usr/bin/env',), env={'PYTHONPATH': '/x/shared/python'}).stdout.splitlines()
        self.assertFalse([line for line in plain if line.startswith('PYTHONPATH=')])
        self.assertIn('PYTHONPATH=/x/shared/python', extended)
        self.assertEqual(sorted(line for line in extended if not line.startswith(('PYTHONPATH=', '_='))),
                         sorted(line for line in plain if not line.startswith('_=')))


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

    def test_partial_rotation_is_a_result_with_rotated_and_errors_side_by_side(self):
        value = {'rotated': [{'path': PATH, 'bytes_before': 1, 'archive': None}], 'skipped': [],
                 'errors': [{'path': '/Users/x/b.log', 'error': 'disk full'}]}
        reply = adapter([json_command(value, code=1)])[0].mutate({'action': 'rotate', 'confirmed': True})
        self.assertEqual(reply.body['message'], 'Rotated 1, skipped 0; 1 failed: b.log: disk full')
        self.assertEqual((len(reply.body['rotated']), reply.body['errors'][0]['error']), (1, 'disk full'))

    def test_exit_one_without_the_result_shape_is_a_refusal(self):
        for result in (CommandResult(1, json.dumps({'error': 'path is outside HOME'})), CommandResult(1, 'not json', 'boom'),
                       CommandResult(1, json.dumps({'rotated': [], 'skipped': []}))):
            with self.subTest(stdout=result.stdout), self.assertRaises(ApiError) as caught:
                adapter([result])[0].mutate({'action': 'rotate', 'confirmed': True})
            self.assertEqual(caught.exception.status, 400)

    def test_exit_zero_with_garbage_is_a_bad_gateway(self):
        with self.assertRaises(ApiError) as caught:
            adapter([CommandResult(0, 'nope')])[0].mutate({'action': 'rotate', 'confirmed': True})
        self.assertEqual(caught.exception.status, 502)

    def test_policy_argv_for_each_field_and_clear(self):
        edited = {**inventory(entry(), revision='rev-2'), 'log': entry()}
        logs, runner = adapter([json_command(edited)] * 2)
        reply = logs.mutate({'action': 'policy', 'revision': REVISION, 'path': PATH, 'max_mb': 20, 'keep': 3,
                             'compress': False, 'enabled': True})
        self.assertEqual(reply.body, {'message': 'Policy saved', 'revision': 'rev-2', 'log': entry()})
        logs.mutate({'action': 'policy', 'revision': REVISION, 'path': PATH, 'clear': True})
        self.assertEqual(runner.calls[0][0], (*LOG_ROTATE, 'policy', '--json', '--revision', REVISION, '--max-mb', '20',
                                              '--keep', '3', '--compress', 'off', '--enabled', 'on', PATH))
        self.assertEqual(runner.calls[1][0], (*LOG_ROTATE, 'policy', '--json', '--revision', REVISION, '--clear', PATH))

    def test_keep_zero_requires_explicit_confirmation(self):
        body = {'action': 'policy', 'revision': REVISION, 'path': PATH, 'keep': 0}
        logs, runner = adapter([json_command(inventory(entry()) | {'log': entry()})])
        for extra in ({}, {'confirmed': False}, {'confirmed': 'true'}):
            with self.subTest(extra=extra), self.assertRaises(ApiError) as caught:
                logs.mutate(body | extra)
            self.assertEqual(caught.exception.status, 400)
        self.assertEqual(runner.calls, [])
        logs.mutate(body | {'confirmed': True})
        self.assertIn('--keep', runner.calls[0][0])
        self.assertIn('0', runner.calls[0][0])

    def test_edit_replies_decode_the_whole_inventory_and_the_touched_entry(self):
        body = {'action': 'ignore', 'revision': REVISION, 'path': PATH}
        for edited in ({'revision': 'rev-2', 'log': None}, inventory(entry(), revision='rev-2') | {'log': {'path': PATH}},
                       inventory(entry(), revision='rev-2')):
            with self.subTest(keys=sorted(edited)), self.assertRaises(ApiError) as caught:
                adapter([json_command(edited)])[0].mutate(body)
            self.assertEqual(caught.exception.status, 502)

    def test_revisions_are_sent_back_unchanged_whether_a_digest_or_missing(self):
        for revision in ('missing', 'a' * 64):
            logs, runner = adapter([json_command(inventory(revision=revision)), json_command(inventory(revision='b') | {'log': None})])
            self.assertEqual(logs.read()['revision'], revision)
            self.assertEqual(logs.mutate({'action': 'ignore', 'revision': revision, 'path': PATH}).body['revision'], 'b')
            self.assertIn(revision, runner.calls[1][0])

    def test_add_ignore_unignore_argv(self):
        logs, runner = adapter([json_command(inventory(entry(), revision='rev-2') | {'log': None})] * 3)
        for action in ('add', 'ignore', 'unignore'):
            self.assertEqual(logs.mutate({'action': action, 'revision': REVISION, 'path': PATH}).body['revision'], 'rev-2')
        self.assertEqual([call[0] for call in runner.calls],
                         [(*LOG_ROTATE, name, '--json', '--revision', REVISION, PATH) for name in ('add', 'ignore', 'unignore')])

    def test_adopt_requires_confirmation_and_preserves_revision_and_path(self):
        logs, runner = adapter([json_command(inventory(entry()) | {'log': entry()})])
        body = {'action': 'adopt', 'revision': REVISION, 'path': PATH}
        with self.assertRaises(ApiError) as caught:
            logs.mutate(body)
        self.assertEqual(caught.exception.status, 400)
        self.assertEqual(runner.calls, [])
        logs.mutate({**body, 'confirmed': True})
        self.assertEqual(runner.calls[0][0], (*LOG_ROTATE, 'adopt', '--json', '--revision', REVISION, PATH))

    def test_invalid_requests_never_reach_the_cli(self):
        logs, runner = adapter([])
        bad = [{'action': 'delete'}, {'action': 'add', 'revision': REVISION, 'path': 'relative'},
               {'action': 'add', 'revision': REVISION, 'path': '/a\n--force'}, {'action': 'add', 'revision': '', 'path': PATH},
               {'action': 'policy', 'revision': REVISION, 'path': PATH}, {'action': 'policy', 'revision': REVISION, 'path': PATH, 'keep': -1},
               {'action': 'policy', 'revision': REVISION, 'path': PATH, 'max_mb': 0},
               {'action': 'policy', 'revision': REVISION, 'path': PATH, 'max_mb': 2.5},
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
            self.assertIs(routes.logs.tasks, routes.tasks)
        finally:
            routes.close()


class LogsPolicyPageTests(unittest.TestCase):
    def test_keep_zero_apply_arms_before_sending_confirmation(self):
        script = r"""
import assert from 'node:assert/strict';
const base = process.argv[1];
const {Element, section} = await import(base + '/tests/dom_fixture.mjs');
globalThis.Node = Element;
globalThis.document.addEventListener = () => {};
Element.prototype.setAttribute = function (key, value) { this[key] = value; };
const timer = globalThis.setTimeout;
globalThis.setTimeout = (...args) => { const value = timer(...args); value.unref(); return value; };
const {register} = await import(base + '/static/panel_sections.mjs');
const {logRow} = await import(base + '/static/section_logs_table.mjs');
section('logs');
register([{id: 'logs', render: () => ({node: new Element(), summary: '', level: 'ok'})}]);
const sent = [];
globalThis.fetch = async (path, options) => {
  if (options.body) sent.push(JSON.parse(options.body));
  return {ok: true, status: 200, json: async () => ({message: 'Saved'})};
};
const entry = {path: '/fixture/job.log', source: 'scan', bytes: 1, archived_bytes: 0,
  archives: 0, state: 'ok', growth_mb_per_day: null,
  policy: {max_mb: 10, keep: 5, compress: true, enabled: true, override: false}};
const walk = root => [root, ...root.childNodes.filter(x => x instanceof Element).flatMap(walk)];
const cell = logRow(entry, {revision: 'rev'})[5];
const form = walk(cell).find(x => x.tagName === 'FORM');
const keep = walk(form).find(x => x.dataset.key === 'policy-keep-/fixture/job.log');
const apply = () => walk(form).find(x => x.dataset.key === 'policy-apply-/fixture/job.log');
keep.value = '0';
form.dispatchEvent(new Event('input'));
apply().click();
assert.equal(sent.length, 0);
assert.equal(apply().textContent, 'Confirm: discard on rotate');
apply().click();
await new Promise(setImmediate);
assert.equal(sent.length, 1);
assert.equal(sent[0].keep, 0);
assert.equal(sent[0].confirmed, true);
keep.value = '1';
form.dispatchEvent(new Event('input'));
apply().click();
await new Promise(setImmediate);
assert.equal(sent.length, 2);
assert.equal(sent[1].keep, 1);
assert.equal('confirmed' in sent[1], false);
const refused = {...entry, state: 'refused', note: 'unrecorded archives; adopt to continue'};
const actions = logRow(refused, {revision: 'rev'})[7];
const adopt = walk(actions).find(x => x.dataset.key === 'adopt-/fixture/job.log');
assert.equal(adopt.textContent, 'Adopt archives');
adopt.click();
assert.equal(sent.length, 2);
adopt.click();
await new Promise(setImmediate);
assert.deepEqual(sent[2], {action: 'adopt', path: entry.path, revision: 'rev', confirmed: true});
const ordinary = logRow({...entry, state: 'refused', note: 'symlink'}, {revision: 'rev'})[7];
assert.equal(walk(ordinary).some(x => x.dataset.key === 'adopt-/fixture/job.log'), false);
console.log('PASS: keep-zero Apply requires two clicks; ordinary policy requires one');
"""
        with tempfile.TemporaryDirectory() as home:
            result = subprocess.run([shutil.which('node'), '--input-type=module', '-e', script,
                                     Path(__file__).resolve().parents[1].as_uri()],
                                    env={**os.environ, 'HOME': home}, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('PASS: keep-zero Apply requires two clicks', result.stdout)


class LogsSamplingTests(unittest.TestCase):
    def payload(self, *logs, problems=(), **over):
        return {**inventory(*logs, problems=problems), 'service': {'installed': True, 'loaded': True}, **over}

    def test_cache_is_used_until_stale_with_one_refresh_in_flight(self):
        class Tasks:
            def __init__(self):
                self.operations = []
            def start(self, section, job, operation):
                self.operations.append(operation)
        tasks, now = Tasks(), [100]
        runner = FakeRunner([json_command(inventory(entry()))] * 2)
        logs = LogsAdapter(runner, FakeServices(), tasks=tasks, clock=lambda: now[0])
        payload = logs.read()
        now[0] = 1899
        self.assertEqual(logs.sample(900), payload)
        self.assertEqual(len(runner.calls), 1)
        now[0] = 1900
        self.assertIsNone(logs.sample(900))
        self.assertIsNone(logs.sample(900))
        self.assertEqual(len(tasks.operations), 1)
        tasks.operations[0]()
        self.assertEqual(runner.calls[-1][1], 30)
        self.assertEqual(logs.sample(900), payload)
        self.assertEqual(len(tasks.operations), 1)

    def test_slow_inventory_does_not_delay_another_sections_sample(self):
        entered, release, sampled = threading.Event(), threading.Event(), threading.Event()
        recorded = []
        def slow_inventory():
            entered.set()
            if not release.wait(5):
                raise AssertionError('fixture inventory never released')
            return json_command(inventory(entry()))
        class OtherAdapter:
            def read(self):
                return {'sessions_open': 2, 'last_snapshot': 42}
        class Store:
            def record(self, rows):
                recorded.extend(rows)
                sampled.set()
            def prune(self):
                pass
        tasks = BackgroundTasks()
        logs = LogsAdapter(FakeRunner([slow_inventory]), FakeServices(), tasks=tasks)
        sampler = Sampler({Section.LOGS: logs, Section.RESTORE: OtherAdapter()}, Store(),
                          {Section.LOGS: 900, Section.RESTORE: 60})
        sampler.start()
        try:
            self.assertTrue(entered.wait(1))
            self.assertTrue(sampled.wait(1), 'slow logs inventory blocked the shared sampler')
            self.assertEqual({row['section'] for row in recorded}, {Section.RESTORE})
            self.assertIsNone(logs.sample(900))
            self.assertEqual(len(tasks.list(Section.LOGS)), 1)
        finally:
            release.set()
            sampler.stop()
            tasks.executor.shutdown(wait=True)
        self.assertIsNotNone(logs.sample(900))

    def test_failed_refresh_can_be_retried_without_stale_samples(self):
        class Tasks:
            def start(self, section, job, operation):
                self.operation = operation
        tasks = Tasks()
        logs = LogsAdapter(FakeRunner([CommandResult(1, '', 'unavailable'), json_command(inventory())]),
                           FakeServices(), tasks=tasks)
        self.assertIsNone(logs.sample(900))
        with self.assertRaises(ApiError):
            tasks.operation()
        self.assertIsNone(logs.sample(900))
        tasks.operation()
        self.assertEqual(logs.sample(900)['totals']['files'], 0)

    def test_cadence_value_and_text(self):
        self.assertEqual(CADENCES[Section.LOGS], 900)
        row, = samples(Section.LOGS, self.payload(entry(bytes=15 * MB, archived_bytes=5 * MB, state='ok')), 10)
        self.assertEqual((row['key'], row['value'], row['text'], row['level']), ('total', 20 * MB, '15 MB live, 5 MB archived', Level.OK))

    def test_level_and_text_follow_the_worst_problem(self):
        warn = {'level': 'warn', 'text': 'total is high'}
        error = {'level': 'error', 'text': 'a.log is failing', 'path': PATH}
        self.assertEqual(log_samples(self.payload(entry(), problems=[warn]))[0][1:], (Level.WARN, 39 * MB, 'total is high; 35 MB live, 4 MB archived'))
        self.assertEqual(log_samples(self.payload(entry(), problems=[warn, error]))[0][1:], (Level.ERROR, 39 * MB, 'a.log is failing; 35 MB live, 4 MB archived'))


if __name__ == '__main__':
    unittest.main()
