import importlib.util
import re
import sys
import threading
import time
import unittest

from cli_runner import CommandResult
import deferred_adapter
from deferred_adapter import DEFERRED, DeferredAdapter, validate_due, validate_job_id
from panel_contracts import ApiError, RemoteState, TaskState
from panel_paths import ROOT
from panel_test_support import FakeRunner, json_command


def inventory():
    return {'checker': {'agent_loaded': True, 'last_run': None, 'stale': False}, 'jobs_dir': '/fixture/jobs',
            'jobs': [{'id': 'test-job', 'valid': True, 'name': 'Test', 'due': '2026-10-20T15:00',
                      'state': 'pending', 'attempts': 0, 'max_attempts': 3, 'timeout': 900,
                      'description': 'Example', 'created': 'today', 'origin': 'test', 'last_log': None,
                      'give_up': '2026-10-27T15:00', 'agent': False, 'ready': None,
                      'last_not_ready': None, 'not_ready_checks': 0, 'host': None, 'model': None,
                      'effort': None, 'remote_run': None, 'remote_state': None, 'remote_token': None,
                      'relaunch_id': None}]}

TOKEN = 'd0123456789abcdef'
RUN_ID = '1117-034729-b1e4-' + TOKEN


def pi_job(**changes):
    value = inventory()
    value['jobs'][0].update({'agent': True, 'host': 'vanpi', 'model': 'sol', 'effort': 'medium', 'remote_run': None,
                             'remote_state': 'unlaunched', **changes})
    return value


def load_script(name):
    """A module from macbook/scripts, loaded the way the scripts load each other."""
    folder = str(ROOT / 'macbook/scripts')
    if folder not in sys.path:
        sys.path.insert(0, folder)
    spec = importlib.util.spec_from_file_location(name, ROOT / f'macbook/scripts/{name}.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve the module's annotations through it
    spec.loader.exec_module(module)
    return module


class DeferredTests(unittest.TestCase):
    def test_identifiers_due_dates_and_mutation_busy(self):
        self.assertEqual(validate_job_id('2026-10-19-test-job'), '2026-10-19-test-job')
        for invalid in ('../bad', 'UPPER', 'a;echo', '', 'a' * 61, 'a\n'):
            with self.assertRaises(ApiError):
                validate_job_id(invalid)
        for invalid in ('2026-02-30 00:00', '2026-01-01T01:00', 'tomorrow', '2026-10-19 25:00'):
            with self.assertRaises(ApiError):
                validate_due(invalid)
        runner = FakeRunner([CommandResult(75)])
        adapter = DeferredAdapter(runner)
        try:
            with self.assertRaises(ApiError) as raised:
                adapter.mutate({'action': 'retry', 'id': 'test-job', 'confirmed': True})
            self.assertEqual(raised.exception.status, 409)
            self.assertIn('checker is running jobs', str(raised.exception))
            runner.results.append(CommandResult(0, 'new-id'))
            adapter.mutate({'action': 'reschedule', 'id': 'test-job', 'due': '2026-10-22 12:15'})
            self.assertEqual(runner.calls[-1][0][-3:], ('reschedule', 'test-job', '2026-10-22 12:15'))
            with self.assertRaises(ApiError):
                adapter.mutate({'action': 'cancel', 'id': 'test-job'})
        finally:
            adapter.tasks.close()

    def test_background_task_lifecycle_and_duplicate_refusal(self):
        release, entered = threading.Event(), threading.Event()
        def work():
            entered.set()
            if not release.wait(3):
                raise AssertionError('test release timed out')
            return CommandResult(0, 'Completed')
        runner = FakeRunner([json_command(inventory()), work, json_command(inventory())])
        adapter = DeferredAdapter(runner)
        try:
            response = adapter.mutate({'action': 'run-now', 'id': 'test-job', 'confirmed': True})
            self.assertEqual(response.status, 202)
            self.assertTrue(entered.wait(1))
            running = adapter.tasks.get(response.body['id'])
            self.assertEqual(running['state'], TaskState.RUNNING)
            self.assertIsInstance(running['started_at'], float)
            self.assertIsNone(running['finished_at'])
            with self.assertRaises(ApiError) as raised:
                adapter.mutate({'action': 'run-now', 'id': 'test-job', 'confirmed': True})
            self.assertEqual(raised.exception.status, 409)
            release.set()
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                result = adapter.tasks.get(response.body['id'])
                if result['state'] != TaskState.RUNNING:
                    break
                time.sleep(.01)
            self.assertEqual(result['state'], TaskState.DONE)
            self.assertEqual(result['exit_status'], 0)
            self.assertGreaterEqual(result['finished_at'], result['started_at'])
        finally:
            release.set()
            adapter.tasks.close()

    def test_failed_task_preserves_exit_status(self):
        runner = FakeRunner([json_command(inventory()), CommandResult(3, 'token=hidden-value')])
        adapter = DeferredAdapter(runner)
        try:
            response = adapter.mutate({'action': 'run-now', 'id': 'test-job', 'confirmed': True})
            for _ in range(200):
                result = adapter.tasks.get(response.body['id'])
                if result['state'] != TaskState.RUNNING:
                    break
                time.sleep(.01)
            self.assertEqual(result['state'], TaskState.FAILED)
            self.assertEqual(result['exit_status'], 3)
            self.assertNotIn('hidden-value', result['message'])
        finally:
            adapter.tasks.close()

    def test_ready_fields_pass_through_and_malformed_ones_fail_closed(self):
        waiting = inventory()
        waiting['jobs'][0].update(agent=True, ready="ssh h 'true'", last_not_ready='2026-10-20T16:00', not_ready_checks=4,
                                  host='mac', model='haiku', effort='medium')
        adapter = DeferredAdapter(FakeRunner([json_command(waiting)]))
        try:
            job = adapter.read()['jobs'][0]
            self.assertEqual((job['ready'], job['not_ready_checks'], job['give_up']),
                             ("ssh h 'true'", 4, '2026-10-27T15:00'))
        finally:
            adapter.tasks.close()
        for key, value in (('ready', 5), ('agent', 'yes'), ('not_ready_checks', -1), ('give_up', None)):
            broken = inventory()
            broken['jobs'][0][key] = value
            adapter = DeferredAdapter(FakeRunner([json_command(broken)]))
            try:
                with self.assertRaises(ApiError, msg=key):
                    adapter.read()
            finally:
                adapter.tasks.close()

    def test_agent_fields_pass_through_and_contradictory_rows_become_invalid(self):
        running = pi_job(remote_run=RUN_ID, remote_state='running', remote_token=TOKEN)
        mac = inventory()
        mac['jobs'][0].update(agent=True, host='mac', model='haiku', effort='high')
        for value in (running, mac, inventory()):
            adapter = DeferredAdapter(FakeRunner([json_command(value)]))
            try:
                self.assertEqual(adapter.read()['jobs'], value['jobs'])
            finally:
                adapter.tasks.close()
        script = inventory()['jobs'][0]
        contradictory = (
            pi_job(remote_state='lost'), pi_job(host='pi'), pi_job(remote_state=None), pi_job(model=3),
            pi_job(model=None), pi_job(effort=None),
            pi_job(remote_state='running'),                                   # running without a run id
            pi_job(remote_state='running', remote_run=RUN_ID),                 # ... or without its token
            pi_job(remote_state='launch-unknown'),                             # a hold without its token
            pi_job(remote_state='unlaunched', remote_run=RUN_ID, remote_token=TOKEN),
            pi_job(remote_state='failed'), pi_job(remote_state='finished'),
            pi_job(remote_state='running', remote_run=RUN_ID, remote_token=TOKEN, relaunch_id=RUN_ID),  # a run still owed
            pi_job(remote_state='unlaunched', relaunch_id=TOKEN),
            pi_job(remote_state='launch-unknown', remote_token=TOKEN, relaunch_id='d' + 'f' * 16),    # not its own id
            {**inventory(), 'jobs': [{**script, 'host': 'mac'}]},
            {**inventory(), 'jobs': [{**script, 'model': 'haiku', 'effort': 'high', 'remote_run': RUN_ID}]},
            {**mac, 'jobs': [{**mac['jobs'][0], 'remote_state': 'running'}]},
        )
        healthy = {**inventory()['jobs'][0], 'id': 'healthy'}
        for broken in contradictory:
            value = {**broken, 'jobs': [*broken['jobs'], healthy]}
            adapter = DeferredAdapter(FakeRunner([json_command(value)]))
            try:
                bad, good = adapter.read()['jobs']
                self.assertEqual((bad['id'], bad['valid']), ('test-job', False), msg=broken['jobs'][0])
                self.assertIsInstance(bad['problem'], str)
                self.assertEqual(good, healthy)
            finally:
                adapter.tasks.close()
        missing = {**inventory(), 'jobs': [{k: v for k, v in script.items() if k != 'remote_token'}]}
        adapter = DeferredAdapter(FakeRunner([json_command(missing)]))
        try:
            with self.assertRaises(ApiError) as raised:  # a stream missing a field fails closed
                adapter.read()
            self.assertEqual(raised.exception.status, 502)
        finally:
            adapter.tasks.close()

    def test_cancel_relays_a_refusal_and_abandon_is_the_explicit_override(self):
        refusal = 'Could not read run 1117-x on vanpi. The job is kept so its run is not left on vanpi unseen.'
        runner = FakeRunner([CommandResult(6, stderr=refusal), CommandResult(0, 'cancelled test-job')])
        adapter = DeferredAdapter(runner)
        try:
            with self.assertRaises(ApiError) as raised:
                adapter.mutate({'action': 'cancel', 'id': 'test-job', 'confirmed': True})
            self.assertEqual((raised.exception.status, raised.exception.code), (409, 'remote-active'))
            self.assertIn('1117-x', str(raised.exception))
            self.assertEqual(runner.calls[0], ((*DEFERRED, 'cancel', 'test-job'), 200))
            reply = adapter.mutate({'action': 'cancel', 'id': 'test-job', 'confirmed': True, 'abandon': True})
            self.assertEqual(reply.body, {'message': 'cancelled test-job'})
            self.assertEqual(runner.calls[1][0], (*DEFERRED, 'cancel', '--abandon', 'test-job'))
            for body in ({'action': 'retry', 'id': 'test-job', 'abandon': True},
                         {'action': 'cancel', 'id': 'test-job', 'confirmed': True, 'abandon': False},
                         {'action': 'cancel', 'id': 'test-job', 'abandon': True}):
                with self.subTest(body=body), self.assertRaises(ApiError):
                    adapter.mutate(body)
            self.assertEqual(len(runner.calls), 2)
        finally:
            adapter.tasks.close()

    def test_retry_relays_the_relaunch_consent_check_as_its_own_conflict(self):
        refusal = f'its Pi launch with token {TOKEN} is unknown. To start another run anyway: ...'
        runner = FakeRunner([CommandResult(7, stderr=refusal), CommandResult(0, 'retrying')])
        adapter = DeferredAdapter(runner)
        try:
            with self.assertRaises(ApiError) as raised:
                adapter.mutate({'action': 'retry', 'id': 'test-job'})
            self.assertEqual((raised.exception.status, raised.exception.code), (409, 'relaunch-unconfirmed'))
            self.assertIn(TOKEN, str(raised.exception))
            with self.assertRaises(ApiError):  # consent to a relaunch is an armed action
                adapter.mutate({'action': 'retry', 'id': 'test-job', 'relaunch': TOKEN})
            adapter.mutate({'action': 'retry', 'id': 'test-job', 'relaunch': TOKEN, 'confirmed': True})
            self.assertEqual(runner.calls[-1][0], (*DEFERRED, 'retry', 'test-job', '--relaunch', TOKEN))
            for body in ({'action': 'retry', 'id': 'test-job', 'relaunch': 'd1', 'confirmed': True},
                         {'action': 'cancel', 'id': 'test-job', 'relaunch': TOKEN, 'confirmed': True}):
                with self.subTest(body=body), self.assertRaises(ApiError):
                    adapter.mutate(body)
            self.assertEqual(len(runner.calls), 2)
        finally:
            adapter.tasks.close()

    def test_run_now_of_a_held_launch_needs_its_token(self):
        held = pi_job(remote_state='launch-unknown', remote_token=TOKEN, relaunch_id=TOKEN, state='failed')
        stale = 'd' + 'f' * 16
        runner = FakeRunner([json_command(held), json_command(held), json_command(held), CommandResult(0, 'done')])
        adapter = DeferredAdapter(runner)
        try:
            for extra in ({}, {'relaunch': stale}):
                with self.subTest(extra=extra), self.assertRaises(ApiError) as raised:
                    adapter.mutate({'action': 'run-now', 'id': 'test-job', 'confirmed': True, **extra})
                self.assertEqual((raised.exception.status, raised.exception.code), (409, 'relaunch-unconfirmed'))
            receipt = adapter.mutate({'action': 'run-now', 'id': 'test-job', 'confirmed': True, 'relaunch': TOKEN})
            self.assertEqual(receipt.status, 202)
            deadline = time.monotonic() + 2
            while adapter.tasks.get(receipt.body['id'])['state'] == TaskState.RUNNING and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertEqual(runner.calls[-1][0], (*DEFERRED, 'run-now', 'test-job', '--relaunch', TOKEN))
        finally:
            adapter.tasks.close()

    def test_shared_vocabulary_matches_the_scripts(self):
        # The job's scripts are standalone, so the panel keeps its own copies; this keeps them equal.
        state = load_script('deferred_pi_state')
        run = load_script('deferred_pi_run')
        self.assertEqual([s.value for s in state.RemoteState], [s.value for s in RemoteState])
        self.assertEqual((deferred_adapter.CANCEL_REFUSED, deferred_adapter.RELAUNCH_REFUSED,
                          deferred_adapter.RESCHEDULE_REFUSED),
                         (state.REFUSED, state.RELAUNCH_REFUSED, state.RESCHEDULE_REFUSED))
        self.assertEqual(deferred_adapter.PI_MIN_TIMEOUT, run.MIN_PASS_TIMEOUT)
        self.assertEqual(deferred_adapter.PI_READY_ALLOWANCE, run.READY_ALLOWANCE)
        self.assertEqual(deferred_adapter.TOKEN.pattern, state.TOKEN.pattern)
        self.assertEqual(deferred_adapter.RUN_ID.pattern, state.RUN_ID.pattern)
        # deferred.zsh keeps its own copies of the numbers and the name rule.
        script = (ROOT / 'macbook/scripts/deferred.zsh').read_text()
        number = lambda variable: int(re.search(rf'\b{variable}=(\d+)', script).group(1))
        self.assertEqual((number('refused_status'), number('relaunch_refused_status'), number('reschedule_refused_status')),
                         (state.REFUSED, state.RELAUNCH_REFUSED, state.RESCHEDULE_REFUSED))
        self.assertEqual((number('pi_min_timeout'), number('pi_ready_allowance')),
                         (run.MIN_PASS_TIMEOUT, run.READY_ALLOWANCE))
        self.assertEqual(int(re.search(r'valid_name\(\) \{.*-le (\d+)', script).group(1)), deferred_adapter.NAME_LIMIT)
        self.assertEqual(deferred_adapter.NAME_LIMIT, 60)

    def test_a_run_id_names_the_attempt_to_start_another_over(self):
        runner = FakeRunner([CommandResult(0, 'retrying')])
        adapter = DeferredAdapter(runner)
        try:
            adapter.mutate({'action': 'retry', 'id': 'test-job', 'relaunch': RUN_ID, 'confirmed': True})
            self.assertEqual(runner.calls[-1][0], (*DEFERRED, 'retry', 'test-job', '--relaunch', RUN_ID))
            for bad in (RUN_ID + ';', '1117-34729-b1e4', 'x' * 90, ''):
                with self.subTest(bad=bad), self.assertRaises(ApiError) as raised:
                    adapter.mutate({'action': 'retry', 'id': 'test-job', 'relaunch': bad, 'confirmed': True})
                self.assertEqual(raised.exception.status, 400)
            self.assertEqual(len(runner.calls), 1)
        finally:
            adapter.tasks.close()

    def test_run_now_with_a_relaunch_id_when_none_is_listed_is_a_conflict(self):
        settled = pi_job(remote_state='running', remote_run=RUN_ID, remote_token=TOKEN, relaunch_id=None)
        runner = FakeRunner([json_command(settled)])
        adapter = DeferredAdapter(runner)
        try:
            with self.assertRaises(ApiError) as raised:
                adapter.mutate({'action': 'run-now', 'id': 'test-job', 'confirmed': True, 'relaunch': RUN_ID})
            self.assertEqual((raised.exception.status, raised.exception.code), (409, 'relaunch-unconfirmed'))
            self.assertIn('no longer applies', str(raised.exception))
            self.assertEqual(len(runner.calls), 1)  # only the listing ran, never run-now
        finally:
            adapter.tasks.close()

    def test_reschedule_of_a_launched_vanpi_job_is_its_own_conflict(self):
        refusal = 'job x has launched on vanpi (Pi run: running); reschedule never starts another run'
        runner = FakeRunner([CommandResult(8, stderr=refusal), CommandResult(0, 'rescheduled')])
        adapter = DeferredAdapter(runner)
        try:
            body = {'action': 'reschedule', 'id': 'test-job', 'due': '2027-01-01 12:15'}
            with self.assertRaises(ApiError) as raised:
                adapter.mutate(body)
            self.assertEqual((raised.exception.status, raised.exception.code), (409, 'reschedule-launched'))
            self.assertIn('never starts another run', str(raised.exception))
            self.assertEqual(adapter.mutate(body).body, {'message': 'rescheduled'})
        finally:
            adapter.tasks.close()

    def test_invalid_cli_payload_fails_closed(self):
        adapter = DeferredAdapter(FakeRunner([json_command({'jobs': []})]))
        try:
            with self.assertRaises(ApiError):
                adapter.read()
        finally:
            adapter.tasks.close()


if __name__ == '__main__':
    unittest.main()
