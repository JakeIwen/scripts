"""Public adapter actions with offline command boundaries and private files."""
import json
import os
from pathlib import Path
import plistlib
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from automations_adapter import AutomationsAdapter
from background_tasks import BackgroundTasks
from cli_runner import CliRunner, CommandResult
from crontab_adapter import CrontabAdapter, SYNC
from test_crontab_adapter import status_payload, JOB_ID, REVISION
from test_deferred_adapter import inventory
from deferred_adapter import DeferredAdapter, validate_due
from panel_contracts import ApiError, Section, SudoRule, TaskState
from panel_routes import PanelRoutes
from panel_test_support import FakeRunner, auth, json_command, request, running_server
from services_adapter import ServicesAdapter


class ServiceControlsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.agents, self.daemons = self.root / 'agents', self.root / 'daemons'
        self.agents.mkdir(); self.daemons.mkdir()
        for folder, label in ((self.agents, 'agent'), (self.daemons, 'daemon')):
            (folder / f'com.jacobr.{label}.plist').write_bytes(
                plistlib.dumps({'Label': f'com.jacobr.{label}'}))

    def adapter(self, results, **kwargs):
        return ServicesAdapter(FakeRunner(results), self.agents, self.daemons, uid=501, **kwargs)

    def test_agent_start_loaded_unloaded_and_persistent_stop(self):
        target = 'gui/501/com.jacobr.agent'
        for action, listed, commands in (
            ('start', '', [('enable', target), ('bootstrap', 'gui/501', str(self.agents / 'com.jacobr.agent.plist'))]),
            ('start', '- 0 com.jacobr.agent', [('enable', target), ('kickstart', target)]),
            ('stop', '5 0 com.jacobr.agent', [('disable', target), ('bootout', target)]),
            ('stop', '', [('disable', target)]),
        ):
            with self.subTest(action=action, listed=listed):
                adapter = self.adapter([CommandResult(0, listed)] + [CommandResult(0)] * len(commands))
                adapter.mutate({'action': action, 'label': 'com.jacobr.agent', 'confirmed': True})
                self.assertEqual([call[0] for call in adapter.runner.calls],
                                 [('/bin/launchctl', 'list')] + [('/bin/launchctl', *c) for c in commands])

    def test_failed_disable_never_boots_out_and_self_stop_start_are_refused(self):
        adapter = self.adapter([CommandResult(0, '5 0 com.jacobr.agent'), CommandResult(1)])
        with self.assertRaises(ApiError):
            adapter.mutate({'action': 'stop', 'label': 'com.jacobr.agent', 'confirmed': True})
        self.assertEqual(len(adapter.runner.calls), 2)
        for action in ('start', 'stop'):
            with self.assertRaises(ApiError):
                self.adapter([]).mutate({'action': action, 'label': 'com.jacobr.mac-panel', 'confirmed': True})

    def test_daemon_permission_cache_expires_and_restart_uses_exact_target(self):
        now = [10.0]
        adapter = self.adapter([CommandResult(0), CommandResult(0), CommandResult(1)], clock=lambda: now[0])
        body = {'action': 'restart', 'label': 'com.jacobr.daemon', 'confirmed': True}
        self.assertEqual(adapter.daemon_permission(body['label']), SudoRule.PRESENT)
        now[0] += 299
        adapter.mutate(body)
        self.assertEqual(adapter.runner.calls, [
            (('/usr/bin/sudo', '-n', '-l', '/bin/launchctl', 'kickstart', '-k', 'system/com.jacobr.daemon'), 30),
            (('/usr/bin/sudo', '-n', '/bin/launchctl', 'kickstart', '-k', 'system/com.jacobr.daemon'), 30)])
        now[0] += 1
        with self.assertRaises(ApiError) as error:
            adapter.mutate(body)
        self.assertEqual(error.exception.status, 403)
        self.assertEqual(len(adapter.runner.calls), 3)
        self.assertEqual(adapter.daemon_permission(body['label']), SudoRule.MISSING)
        with self.assertRaises(ApiError):
            adapter.mutate({**body, 'action': 'stop'})
        for label in ('com.jacobr.UPPER', 'com.jacobr.a.b', 'com.jacobr.good extra', 'com.apple.x'):
            with self.assertRaises(ApiError):
                adapter.mutate({**body, 'label': label})

    def test_inventory_advertises_permissions_and_install_hint(self):
        adapter = self.adapter([CommandResult(0, '5 0 com.jacobr.agent\n6 0 com.jacobr.mac-panel'),
                                CommandResult(0, 'state = running'), CommandResult(0)])
        payload = adapter.read()
        rows = {row['label']: row for row in payload['services']}
        self.assertEqual(rows['com.jacobr.agent']['actions'], ['restart', 'start', 'stop'])
        self.assertEqual(rows['com.jacobr.mac-panel']['actions'], ['restart'])
        self.assertEqual(rows['com.jacobr.daemon']['actions'], ['restart'])
        self.assertEqual(rows['com.jacobr.agent']['sudo_rule'], 'n/a')
        self.assertEqual(rows['com.jacobr.daemon']['sudo_rule'], 'present')
        self.assertTrue(payload['sudo_install_command'].endswith('panel_service.py sudoers --script'))


class DeferredAddTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.body = {'action': 'add', 'name': 'fixture', 'due': '2027-01-01 12:15',
                     'script': '#!/bin/zsh\nprint fixture\n', 'description': 'Example',
                     'attempts': 2, 'timeout': 60, 'confirmed': True}

    def test_fake_deferred_receives_private_script_and_cleanup_on_success_and_failure(self):
        fake = self.root / 'fake-deferred.zsh'
        fake.write_text('''#!/bin/zsh
set -eu
[[ $1 == add && $2 == --attempts && $3 == 2 && $4 == --timeout && $5 == 60 ]]
[[ $6 == '2027-01-01 12:15' && $7 == fixture && $9 == Example ]]
[[ $(/usr/bin/stat -f %Lp $8) == 700 ]]
[[ $(/usr/bin/head -c 2 $8) == '#!' ]]
print added
''')
        adapter = DeferredAdapter(CliRunner(), data=self.root / 'panel')
        self.addCleanup(adapter.tasks.close)
        with patch('deferred_adapter.DEFERRED', ('/bin/zsh', str(fake))):
            self.assertEqual(adapter.mutate(self.body).body, {'message': 'added'})
        self.assertEqual(list(adapter.data.iterdir()), [])
        adapter.runner = FakeRunner([CommandResult(1, stderr='fixture failed')])
        with self.assertRaises(ApiError):
            adapter.mutate(self.body)
        self.assertEqual(list(adapter.data.iterdir()), [])

    def test_invalid_add_never_writes_or_runs(self):
        runner = FakeRunner()
        adapter = DeferredAdapter(runner, data=self.root / 'panel')
        self.addCleanup(adapter.tasks.close)
        for changes in ({'name': '../bad'}, {'name': 'a' * 61}, {'due': '2027-02-30 00:00'},
                        {'script': 'echo no'}, {'attempts': True}, {'timeout': 0},
                        {'confirmed': False}, {'description': 'bad\0text'},
                        {'script': '#!/bin/zsh\n\ud800'}, {'description': '\udcff'}):
            with self.subTest(changes=changes), self.assertRaises(ApiError):
                adapter.mutate({**self.body, **changes})
        self.assertEqual(runner.calls, [])
        self.assertFalse(adapter.data.exists())


class LocalTimeTests(unittest.TestCase):
    def test_a_time_skipped_by_daylight_saving_is_refused_before_staging(self):
        previous = os.environ.get('TZ')
        os.environ['TZ'] = 'America/Chicago'
        time.tzset()

        def restore():
            if previous is None:
                os.environ.pop('TZ', None)
            else:
                os.environ['TZ'] = previous
            time.tzset()
        self.addCleanup(restore)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        runner = FakeRunner()
        adapter = DeferredAdapter(runner, data=Path(temporary.name) / 'panel')
        self.addCleanup(adapter.tasks.close)
        body = {'action': 'add-agent', 'name': 'gap', 'due': '2027-03-14 02:30', 'description': '', 'host': 'vanpi',
                'prompt': 'x', 'confirmed': True}
        with self.assertRaises(ApiError) as raised:
            adapter.mutate(body)
        self.assertEqual(raised.exception.status, 400)
        self.assertIn('daylight', str(raised.exception))
        self.assertEqual(runner.calls, [])
        self.assertFalse(adapter.data.exists())
        self.assertEqual(validate_due('2027-03-14 03:30'), '2027-03-14 03:30')
        self.assertEqual(validate_due('2027-11-07 01:30'), '2027-11-07 01:30')  # the repeated hour exists


class DeferredAgentAddTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.adapter = DeferredAdapter(FakeRunner(), data=self.root / 'panel')
        self.addCleanup(self.adapter.tasks.close)
        self.body = {'action': 'add-agent', 'name': 'pi-check', 'due': '2027-01-01 12:15', 'description': 'Example',
                     'host': 'vanpi', 'prompt': 'Check the Pi.\n', 'model': 'astra', 'effort': 'high',
                     'cwd': '/home/pi/dev', 'ready': "test -e '/tmp/x y'", 'timeout': 900,
                     'run_timeout': 30, 'confirmed': True}

    def test_fake_deferred_receives_a_private_prompt_and_cleanup_on_success_and_failure(self):
        fake = self.root / 'fake-deferred.zsh'
        fake.write_text("""#!/bin/zsh
set -eu
[[ "${(j:|:)@[1,15]}" == "add-agent|--host|vanpi|--model|astra|--effort|high|--cwd|/home/pi/dev|--ready|test -e '/tmp/x y'|--timeout|900|--run-timeout|30" ]]
[[ $16 == '2027-01-01 12:15' && $17 == pi-check && $19 == Example ]]
[[ $(/usr/bin/stat -f %Lp $18) == 600 && $(<$18) == 'Check the Pi.' ]]
print added
""")
        self.adapter.runner = CliRunner()
        with patch('deferred_adapter.DEFERRED', ('/bin/zsh', str(fake))):
            self.assertEqual(self.adapter.mutate(self.body).body, {'message': 'added'})
        self.assertEqual(list(self.adapter.data.iterdir()), [])
        self.adapter.runner = FakeRunner([CommandResult(1, stderr='fixture failed')])
        with self.assertRaises(ApiError):
            self.adapter.mutate(self.body)
        self.assertEqual(list(self.adapter.data.iterdir()), [])

    def test_pi_prompt_path_is_passed_as_vanpi_path_without_staging(self):
        runner = FakeRunner([CommandResult(0, 'added')])
        self.adapter.runner = runner
        body = {key: value for key, value in self.body.items() if key != 'prompt'}
        self.adapter.mutate({**body, 'pi_prompt_path': "/home/pi/it's $(x).md"})
        self.assertEqual(runner.calls[0][0][-4:], ('2027-01-01 12:15', 'pi-check', "vanpi:/home/pi/it's $(x).md", 'Example'))
        self.assertFalse(self.adapter.data.exists())

    def test_a_vanpi_job_without_a_ready_condition_needs_only_the_floor(self):
        runner = FakeRunner([CommandResult(0, 'added')])
        self.adapter.runner = runner
        body = {key: value for key, value in self.body.items() if key != 'ready'}
        self.adapter.mutate({**body, 'timeout': 390})
        self.assertEqual(len(runner.calls), 1)
        for refused in ({'timeout': 389}, {'timeout': 419, 'ready': 'true'}):
            with self.subTest(refused=refused), self.assertRaises(ApiError) as raised:
                self.adapter.mutate({**body, **refused})
            self.assertEqual(raised.exception.status, 400)
        self.assertEqual(len(runner.calls), 1)

    def test_mac_jobs_default_to_the_home_folder_and_take_a_free_model_name(self):
        runner = FakeRunner([CommandResult(0, 'added')])
        self.adapter.runner = runner
        body = {key: value for key, value in self.body.items() if key not in ('cwd', 'run_timeout')}
        self.adapter.mutate({**body, 'host': 'mac', 'model': 'claude-sonnet-5[1m]'})
        argv = runner.calls[0][0]
        self.assertEqual(argv[argv.index('--cwd') + 1], os.path.realpath(Path.home()))
        self.assertEqual(argv[argv.index('--model') + 1], 'claude-sonnet-5[1m]')

    def test_invalid_add_agent_never_writes_or_runs(self):
        home = os.path.realpath(Path.home())
        prompt_less = {key: value for key, value in self.body.items() if key != 'prompt'}
        cases = (
            {'host': 'pi'}, {'host': None}, {'name': '../bad'}, {'due': '2027-02-30 00:00'},
            {'prompt': '   \n'}, {'prompt': 'a\0b'}, {'prompt': 5}, {'description': 'bad\0text'},
            {'pi_prompt_path': '/home/pi/x.md'},                           # both prompt sources
            {'model': 'haiku'}, {'model': 'Sol'},
            {'effort': 'ultra'}, {'cwd': 'relative'}, {'cwd': '/a\nb'}, {'cwd': '/' + 'a' * 1100},
            {'ready': ''}, {'ready': 'a\nb'}, {'ready': 'x' * 501}, {'ready': 'a\0b'},
            {'attempts': 0}, {'attempts': True}, {'timeout': '60'}, {'run_timeout': 0},
            {'confirmed': False}, {'unexpected': 1},
            {'name': 'n' * 61}, {'prompt': 'lone \ud800 surrogate'}, {'description': '\ud800'},
            {'cwd': home + '/dev'}, {'cwd': str(Path.home())}, {'timeout': 389},
            {'attempts': 2}, {'attempts': 3, 'ready': None},               # a vanpi job starts one run by itself
            {'timeout': 419},                                              # below the floor plus the ready allowance
        )
        for changes in cases:
            body = {key: value for key, value in {**self.body, **changes}.items() if value is not None}
            with self.subTest(changes=changes), self.assertRaises(ApiError) as raised:
                self.adapter.mutate(body)
            self.assertEqual(raised.exception.status, 400)
        mac = {key: value for key, value in self.body.items() if key != 'run_timeout'}
        for changes in ({'cwd': '/tmp'}, {'cwd': home + '/../' + Path(home).name}, {'cwd': home + '/no-such-folder'},
                        {'model': 'Bad Model'}, {'run_timeout': 5}):
            with self.subTest(mac=changes), self.assertRaises(ApiError):
                self.adapter.mutate({**mac, 'host': 'mac', 'model': 'haiku', **changes})
        for changes in ({'pi_prompt_path': 'relative.md'}, {'pi_prompt_path': '/a\nb.md'},
                        {'pi_prompt_path': '/' + 'a' * 1100}):
            with self.subTest(path=changes), self.assertRaises(ApiError):
                self.adapter.mutate({**prompt_less, **changes})
        with self.assertRaises(ApiError):  # a Pi path on the Mac
            self.adapter.mutate({**prompt_less, 'host': 'mac', 'model': 'haiku', 'cwd': home, 'run_timeout': 1,
                                 'pi_prompt_path': '/x.md'})
        self.assertEqual(self.adapter.runner.calls, [])
        self.assertFalse(self.adapter.data.exists())


class AsyncControlsTests(unittest.TestCase):
    def test_cron_run_is_a_receipt_visible_through_routes_and_inventory(self):
        release, entered = threading.Event(), threading.Event()
        def work():
            entered.set()
            if not release.wait(3):
                raise AssertionError('release timed out')
            return json_command({'id': JOB_ID, 'exit': 0, 'output': 'Completed',
                                 'started': '2026-01-01T00:00:00+00:00',
                                 'finished': '2026-01-01T00:00:01+00:00', 'timed_out': False})
        runner = FakeRunner([json_command(status_payload()), work, json_command(status_payload())])
        routes = PanelRoutes(runner=runner)
        self.addCleanup(routes.close)
        try:
            receipt = routes.dispatch('POST', '/api/crontab', {
                'action': 'run', 'job': JOB_ID, 'revision': REVISION, 'confirmed': True})
            self.assertEqual(receipt.status, 202)
            self.assertTrue(entered.wait(1))
            self.assertEqual(routes.dispatch('GET', '/api/tasks/' + receipt.body['id']).body['state'], 'running')
            with self.assertRaises(ApiError) as error:
                routes.adapters[next(key for key in routes.adapters if key.value == 'crontab')].mutate({
                    'action': 'run', 'job': JOB_ID, 'revision': REVISION, 'confirmed': True})
            self.assertEqual(error.exception.status, 409)
            release.set()
            routes.tasks.executor.shutdown(wait=True)
            runner.results.append(json_command(status_payload()))
            payload = routes.dispatch('GET', '/api/crontab').body
            self.assertEqual(payload['tasks'][0]['id'], receipt.body['id'])
            self.assertEqual(payload['tasks'][0]['state'], 'done')
            self.assertEqual(runner.calls[0], ((*SYNC, 'status', '--json'), 30))
            self.assertEqual(runner.calls[1], ((*SYNC, 'run', JOB_ID, '--revision', REVISION, '--json'), 930))
        finally:
            release.set()

    def test_cron_receipt_reports_job_failure_not_cli_success(self):
        tasks = BackgroundTasks()
        self.addCleanup(tasks.close)
        value = {'id': 'a' * 12, 'exit': 7, 'output': 'job failed',
                 'started': '2026-01-01T00:00:00+00:00',
                 'finished': '2026-01-01T00:00:01+00:00', 'timed_out': False}
        runner = FakeRunner([json_command({**status_payload(), 'revision': 'b' * 64}), json_command(value)])
        adapter = CrontabAdapter(runner, tasks)
        receipt = adapter.mutate({'action': 'run', 'job': value['id'],
                                  'revision': 'b' * 64, 'confirmed': True})
        tasks.executor.shutdown(wait=True)
        result = tasks.get(receipt.body['id'])
        self.assertEqual(result['state'], 'failed')
        self.assertEqual(result['exit_status'], 7)
        self.assertEqual(result['message'], 'job failed')

    def test_cron_revision_failure_keeps_the_cli_reason(self):
        tasks = BackgroundTasks()
        self.addCleanup(tasks.close)
        adapter = CrontabAdapter(FakeRunner([json_command({**status_payload(), 'revision': 'b' * 64}),
            json_command({'error': 'revision changed'}, 75)]), tasks)
        receipt = adapter.mutate({'action': 'run', 'job': 'a' * 12,
                                  'revision': 'b' * 64, 'confirmed': True})
        tasks.executor.shutdown(wait=True)
        result = tasks.get(receipt.body['id'])
        self.assertEqual(result['state'], 'failed')
        self.assertEqual(result['exit_status'], 75)
        self.assertIn('revision changed', result['message'])
        self.assertNotIn('checker', result['message'])

    def test_task_inventories_belong_to_their_adapter_even_while_running(self):
        tasks = BackgroundTasks()
        self.addCleanup(tasks.close)
        release = threading.Event()
        def operation():
            if not release.wait(3):
                raise AssertionError('release timed out')
            return CommandResult(0, 'Completed')
        try:
            receipts = {section: tasks.start(section, 'same-job', operation)
                        for section in (Section.CRONTAB, Section.DEFERRED, Section.AUTOMATIONS)}
            adapters = {
                Section.CRONTAB: CrontabAdapter(FakeRunner([json_command(status_payload())]), tasks),
                Section.DEFERRED: DeferredAdapter(FakeRunner([json_command(inventory())]), tasks),
                Section.AUTOMATIONS: AutomationsAdapter(FakeRunner(), SimpleNamespace(
                    declaration_tools=lambda: []), tasks),
            }
            for section, adapter in adapters.items():
                with self.subTest(section=section):
                    self.assertEqual(adapter.read()['tasks'], [receipts[section]])
                    self.assertEqual(tasks.get(receipts[section]['id']), receipts[section])
            release.set()
            tasks.executor.shutdown(wait=True)
            for section in adapters:
                self.assertEqual(tasks.list(section)[0]['state'], 'done')
        finally:
            release.set()

    def test_cron_stale_revision_returns_http_409_before_enqueuing(self):
        runner = FakeRunner([json_command(status_payload())])
        routes = PanelRoutes(runner=runner)
        self.addCleanup(routes.close)
        with running_server(routes) as server:
            body = json.dumps({'action': 'run', 'job': JOB_ID,
                               'revision': 'b' * 64, 'confirmed': True})
            status, _, raw = request(server, '/api/crontab', 'POST', body, auth(server, True))
        self.assertEqual(status, 409)
        self.assertIn('revision changed', json.loads(raw)['error'])
        self.assertEqual(routes.tasks.list(Section.CRONTAB), [])
        self.assertEqual(runner.calls, [((*SYNC, 'status', '--json'), 30)])

    def test_confirmed_async_automation_uses_declared_timeout_and_receipt(self):
        import tool_automation
        from test_automations_adapter import AutomationsEndpointTests
        with tempfile.TemporaryDirectory() as directory:
            value = AutomationsEndpointTests.declaration('fixture', confirm=True)
            value['actions'][0].update({'async': True, 'timeout': 1000})
            Path(directory, 'fixture.json').write_text(json.dumps(value))
            with patch.dict(os.environ, JACOBR_AUTOMATIONS_DIR=directory):
                runner = FakeRunner([CommandResult(0, 'Deferred: metered or unknown')])
                adapter = AutomationsAdapter(runner, tool_automation)
                self.addCleanup(adapter.tasks.close)
                reply = adapter.action({'tool': 'fixture', 'action': 'run-now', 'confirmed': True})
                self.assertEqual(reply.status, 202)
                adapter.tasks.executor.shutdown(wait=True)
                task = adapter.tasks.get(reply.body['id'])
                self.assertEqual(task['state'], TaskState.DONE)
                self.assertIn('metered or unknown', task['message'])
                self.assertEqual(runner.calls, [(('/bin/echo', 'action', 'fixture'), 1000)])


class ConfirmationTests(unittest.TestCase):
    def test_armed_routes_require_literal_true_before_any_command(self):
        routes = PanelRoutes(runner=FakeRunner())
        self.addCleanup(routes.close)
        bodies = [
            ('/api/services', {'action': 'restart', 'label': 'com.jacobr.fixture'}),
            ('/api/services/restart', {'label': 'com.jacobr.fixture'}),
            ('/api/deferred', {'action': 'run-now', 'id': 'fixture'}),
            ('/api/deferred', {'action': 'cancel', 'id': 'fixture'}),
            ('/api/deferred', {'action': 'add', 'name': 'fixture', 'due': '2027-01-01 00:00',
                               'script': '#!/bin/zsh\n', 'description': ''}),
            ('/api/metered', {'action': 'ssid-remove', 'ssid': 'fixture', 'revision': 1}),
            ('/api/metered', {'action': 'low-data-mode', 'enabled': True, 'revision': 'missing'}),
            ('/api/settings', {'action': 'reset', 'tool': 'fixture', 'values': {}, 'revision': None}),
            ('/api/guard', {'action': 'checkpoint', 'replaces': None}),
            ('/api/ntfy/test', {}),
        ]
        bodies.extend(('/api/crontab', {'action': action, 'revision': 'a' * 64, 'job': 'a' * 12})
                      for action in ('pause', 'resume', 'install', 'run'))
        with running_server(routes) as server:
            for path, body in bodies:
                for extra in ({}, {'confirmed': False}, {'confirmed': 'true'}, {'confirmed': 1}):
                    with self.subTest(path=path, body=body, extra=extra):
                        status, _, raw = request(server, path, 'POST', json.dumps({**body, **extra}), auth(server, True))
                        self.assertEqual(status, 400)
                        self.assertEqual(json.loads(raw), {'error': 'confirmation required'})
        self.assertEqual(routes.services.runner.calls, [])


if __name__ == '__main__':
    unittest.main()
