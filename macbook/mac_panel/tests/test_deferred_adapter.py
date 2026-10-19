import threading
import time
import unittest

from cli_runner import CommandResult
from deferred_adapter import DeferredAdapter, validate_due, validate_job_id
from panel_contracts import ApiError, TaskState
from panel_test_support import FakeRunner, json_command


def inventory():
    return {'checker': {'agent_loaded': True, 'last_run': None, 'stale': False}, 'jobs_dir': '/fixture/jobs',
            'jobs': [{'id': 'test-job', 'valid': True, 'name': 'Test', 'due': '2026-10-20T15:00',
                      'state': 'pending', 'attempts': 0, 'max_attempts': 3, 'timeout': 900,
                      'description': 'Example', 'created': 'today', 'origin': 'test', 'last_log': None}]}


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
                adapter.mutate({'action': 'retry', 'id': 'test-job'})
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
            response = adapter.mutate({'action': 'run-now', 'id': 'test-job'})
            self.assertEqual(response.status, 202)
            self.assertTrue(entered.wait(1))
            running = adapter.tasks.get(response.body['id'])
            self.assertEqual(running['state'], TaskState.RUNNING)
            self.assertIsInstance(running['started_at'], float)
            self.assertIsNone(running['finished_at'])
            with self.assertRaises(ApiError) as raised:
                adapter.mutate({'action': 'run-now', 'id': 'test-job'})
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
            response = adapter.mutate({'action': 'run-now', 'id': 'test-job'})
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

    def test_invalid_cli_payload_fails_closed(self):
        adapter = DeferredAdapter(FakeRunner([json_command({'jobs': []})]))
        try:
            with self.assertRaises(ApiError):
                adapter.read()
        finally:
            adapter.tasks.close()


if __name__ == '__main__':
    unittest.main()
