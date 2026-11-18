import importlib.util
import sys
import threading
import time
import unittest

from cli_runner import CommandResult
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
                      'effort': None, 'remote_run': None, 'remote_state': None}]}


def pi_job(**changes):
    value = inventory()
    value['jobs'][0].update({'agent': True, 'host': 'vanpi', 'model': 'sol', 'effort': 'medium', 'remote_run': None,
                             'remote_state': 'unlaunched', **changes})
    return value


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

    def test_agent_fields_pass_through_and_malformed_ones_fail_closed(self):
        running = pi_job(remote_run='1117-034729-b1e4-d0123456789abcdef', remote_state='running')
        mac = inventory()
        mac['jobs'][0].update(agent=True, host='mac', model='haiku', effort='high')
        for value in (running, mac, inventory()):
            adapter = DeferredAdapter(FakeRunner([json_command(value)]))
            try:
                self.assertEqual(adapter.read()['jobs'], value['jobs'])
            finally:
                adapter.tasks.close()
        broken_cases = (
            pi_job(remote_state='lost'), pi_job(host='pi'), pi_job(remote_state=None), pi_job(model=3),
            {**inventory(), 'jobs': [{**inventory()['jobs'][0], 'host': 'mac'}]},
            {**mac, 'jobs': [{**mac['jobs'][0], 'remote_state': 'running'}]},
            {**inventory(), 'jobs': [{k: v for k, v in inventory()['jobs'][0].items() if k != 'remote_run'}]},
        )
        for broken in broken_cases:
            adapter = DeferredAdapter(FakeRunner([json_command(broken)]))
            try:
                with self.assertRaises(ApiError, msg=broken['jobs'][0]) as raised:
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

    def test_retry_of_an_unknown_launch_needs_confirmation(self):
        held = pi_job(remote_state='launch-unknown')
        runner = FakeRunner([json_command(held), json_command(inventory()), CommandResult(0, 'retrying')])
        adapter = DeferredAdapter(runner)
        try:
            with self.assertRaises(ApiError) as raised:
                adapter.mutate({'action': 'retry', 'id': 'test-job'})
            self.assertEqual(raised.exception.status, 400)
            adapter.mutate({'action': 'retry', 'id': 'test-job'})  # an ordinary failed job needs none
            self.assertEqual(runner.calls[-1][0], (*DEFERRED, 'retry', 'test-job'))
            runner.results.append(CommandResult(0, 'retrying'))
            adapter.mutate({'action': 'retry', 'id': 'test-job', 'confirmed': True})
            self.assertEqual(len(runner.calls), 4)
        finally:
            adapter.tasks.close()

    def test_remote_states_match_the_run_script_that_reports_them(self):
        # The job's run script is a standalone copy, so the panel keeps its own enum; this keeps them equal.
        spec = importlib.util.spec_from_file_location('deferred_pi_run', ROOT / 'macbook/scripts/deferred_pi_run.py')
        script = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = script  # dataclasses resolve the module's annotations through it
        self.addCleanup(sys.modules.pop, spec.name)
        spec.loader.exec_module(script)
        self.assertEqual([state.value for state in script.RemoteState], [state.value for state in RemoteState])

    def test_invalid_cli_payload_fails_closed(self):
        adapter = DeferredAdapter(FakeRunner([json_command({'jobs': []})]))
        try:
            with self.assertRaises(ApiError):
                adapter.read()
        finally:
            adapter.tasks.close()


if __name__ == '__main__':
    unittest.main()
