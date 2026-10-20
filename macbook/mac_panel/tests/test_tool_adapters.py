import unittest

from cli_runner import CommandResult
from guard_adapter import GuardAdapter
from panel_contracts import ApiError
from panel_test_support import FakeRunner, json_command
from restore_adapter import RestoreAdapter


class RestoreTests(unittest.TestCase):
    def test_json_read_toggle_and_boundary_validation(self):
        state = {'enabled': True, 'installed': True, 'up_to_date': True,
                 'last_snapshot': 1780000000.5, 'sessions_open': 3}
        runner = FakeRunner([json_command(state), json_command({**state, 'enabled': False})])
        adapter = RestoreAdapter(runner)
        self.assertEqual(adapter.read(), state)
        self.assertFalse(adapter.toggle({'enabled': False})['enabled'])
        self.assertEqual(runner.calls[0][0][-2:], ('--cron', '--json'))
        self.assertEqual(runner.calls[1][0][-3:], ('--cron', 'off', '--json'))
        with self.assertRaises(ApiError):
            adapter.toggle({'enabled': 'false'})
        runner.results.append(json_command({**state, 'sessions_open': 'bad'}))
        with self.assertRaises(ApiError):
            adapter.read()


class GuardTests(unittest.TestCase):
    def test_checkpoint_replacement_is_fresh_and_explicit(self):
        for active in (None, 'cp-existing'):
            with self.subTest(active=active):
                runner = FakeRunner([json_command([] if active is None else [{'checkpoint_id': active, 'active': True}]),
                                     json_command({'checkpoint_id': 'cp-new'})])
                adapter = GuardAdapter(runner)
                adapter.action({'action': 'checkpoint', 'confirmed': True, 'replaces': active})
                self.assertEqual('--replace' in runner.calls[-1][0], active is not None)
                self.assertIn('--accept-current', runner.calls[-1][0])
                self.assertEqual(runner.calls[-1][1], 120)
        runner = FakeRunner([json_command([{'checkpoint_id': 'cp-newer', 'active': True}])])
        with self.assertRaises(ApiError) as raised:
            GuardAdapter(runner).action({'action': 'checkpoint', 'confirmed': True, 'replaces': 'cp-old'})
        self.assertEqual(raised.exception.status, 409)
        self.assertEqual(len(runner.calls), 1)

    def test_refusal_message_and_no_repair_route(self):
        runner = FakeRunner([json_command([]), CommandResult(1, stderr='Missing roots: fixture-root')])
        adapter = GuardAdapter(runner)
        with self.assertRaises(ApiError) as raised:
            adapter.action({'action': 'checkpoint', 'confirmed': True, 'replaces': None})
        self.assertEqual(str(raised.exception), 'Missing roots: fixture-root')
        for action in ('repair', 'repair --apply', '--apply', 'monitor'):
            with self.assertRaises(ApiError):
                adapter.action({'action': action, 'confirmed': True})
        self.assertFalse(any('repair' in argv for argv, _ in runner.calls))
        with self.assertRaises(ApiError):
            adapter.action({'action': 'notify-test'})

    def test_findings_exit_two_is_a_result_and_busy_guard(self):
        audit = {'status': 'drift', 'checkpoint_id': 'cp-one', 'checked_at': 1780000000.0,
                 'btt_version': '5', 'findings': [{'kind': 'missing', 'uuid': 'x', 'detail': 'Missing'}]}
        service = {'installed': False, 'supervised': False, 'job': {}, 'heartbeat': {}, 'audit_status': None}
        acceptance = {'state': 'ok', 'settle_seconds': 600, 'pending_since': 1780000000.0,
                      'accepts_at': 1780000600.0, 'last_accepted': None,
                      'last_refused': {'at': 1779990000, 'checkpoint_id': None, 'reason': 'capture_refused',
                                       'findings': 2}}
        runner = FakeRunner([json_command([]), json_command(audit, 2), json_command(service, 2),
                             json_command(acceptance),
                             json_command(audit, 2), json_command({'btt_version': '5', 'records': 2,
                                                                'findings': audit['findings']}, 2)])
        adapter = GuardAdapter(runner, read_wait=0.01)
        payload = adapter.read()
        self.assertFalse(payload['service']['supervised'])
        self.assertEqual(payload['audit']['status'], 'drift')
        self.assertEqual(payload['acceptance']['accepts_at'], 1780000600.0)
        self.assertIn('audit', runner.calls[1][0])
        self.assertEqual(runner.calls[3][0][-1], 'acceptance')
        self.assertEqual(adapter.action({'action': 'audit'})['status'], 'drift')
        self.assertEqual(len(adapter.action({'action': 'inspect'})['findings']), 1)
        with adapter.busy():
            with self.assertRaises(ApiError) as raised:
                adapter.read()
            self.assertEqual(raised.exception.status, 409)

    def test_malformed_acceptance_is_rejected(self):
        service = {'installed': True, 'supervised': True, 'job': {}, 'heartbeat': {}, 'audit_status': 'healthy'}
        audit = {'status': 'healthy', 'checkpoint_id': 'cp-one', 'checked_at': 1.0, 'btt_version': '5', 'findings': []}
        for broken in ({'state': 'ok'}, {'state': 'odd', 'settle_seconds': 600, 'pending_since': None,
                                         'accepts_at': None, 'last_accepted': None, 'last_refused': None}):
            runner = FakeRunner([json_command([]), json_command(audit), json_command(service), json_command(broken)])
            with self.assertRaises(ApiError):
                GuardAdapter(runner, read_wait=0.01).read()


if __name__ == '__main__':
    unittest.main()
