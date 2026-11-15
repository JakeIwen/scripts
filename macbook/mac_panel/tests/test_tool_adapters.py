import unittest

from cli_runner import CommandResult
from guard_adapter import GuardAdapter
from panel_contracts import ApiError
from panel_paths import RESTORE
from panel_test_support import FakeRunner, json_command
from restore_adapter import RestoreAdapter


SAVED = [{'name': 'crash-0523', 'path': '/s/crash-0523.sh', 'saved_at': 1780000000.5, 'sessions': 10},
         {'name': 'focus', 'path': '/s/focus.sh', 'saved_at': 1779000000.0, 'sessions': 76}]


class RestoreTests(unittest.TestCase):
    state = {'enabled': True, 'installed': True, 'up_to_date': True,
             'last_snapshot': 1780000000.5, 'sessions_open': 3}

    def test_json_read_toggle_and_boundary_validation(self):
        state = self.state
        runner = FakeRunner([json_command(state), json_command(SAVED), json_command({**state, 'enabled': False})])
        adapter = RestoreAdapter(runner)
        self.assertEqual(adapter.read(), {**state, 'saved': SAVED})
        self.assertFalse(adapter.toggle({'enabled': False})['enabled'])
        self.assertEqual(runner.calls[0][0][-2:], ('--cron', '--json'))
        self.assertEqual(runner.calls[1][0][-2:], ('--saved', '--json'))
        self.assertEqual(runner.calls[2][0][-3:], ('--cron', 'off', '--json'))
        with self.assertRaises(ApiError):
            adapter.toggle({'enabled': 'false'})
        runner.results.append(json_command({**state, 'sessions_open': 'bad'}))
        with self.assertRaises(ApiError):
            adapter.read()

    def test_malformed_saved_list_is_a_bad_gateway(self):
        for bad in ({'name': 'x'}, [{'name': 'x', 'path': 'p', 'saved_at': 1.0, 'sessions': 'many'}], [{'name': 'x'}]):
            with self.subTest(bad=bad):
                runner = FakeRunner([json_command(self.state), json_command(bad)])
                with self.assertRaises(ApiError) as raised:
                    RestoreAdapter(runner).read()
                self.assertEqual(raised.exception.status, 502)

    def test_preview_and_restore_build_exact_argv(self):
        base = RESTORE
        saved = {'kind': 'saved', 'name': 'focus'}
        cases = [
            ({'action': 'preview', 'target': saved}, (*base, '--dry-run', '--', '/s/focus.sh'), True, 30),
            ({'action': 'preview', 'target': {'kind': 'auto'}}, (*base, '--dry-run'), False, 30),
            ({'action': 'restore', 'target': saved, 'confirmed': True}, (*base, '--', '/s/focus.sh'), True, 120),
            ({'action': 'restore', 'target': {'kind': 'auto'}, 'confirmed': True}, tuple(base), False, 120),
        ]
        for body, argv, lists_saved, timeout in cases:
            with self.subTest(body=body):
                results = ([json_command(SAVED)] if lists_saved else []) + [CommandResult(0, 'would open  claude x\n')]
                runner = FakeRunner(results)
                reply = RestoreAdapter(runner).snapshots(body)
                self.assertEqual(runner.calls[-1], (argv, timeout))
                self.assertEqual(reply.body['report'], 'would open  claude x')

    def test_a_set_whose_name_looks_like_a_file_is_passed_by_its_listed_path(self):
        odd = [{'name': 'focus.sh', 'path': '/s/focus.sh.sh', 'saved_at': 1.0, 'sessions': 1},
               {'name': 'focus', 'path': '/s/focus.sh', 'saved_at': 2.0, 'sessions': 2}]
        runner = FakeRunner([json_command(odd), CommandResult(0, 'ok')])
        RestoreAdapter(runner).snapshots({'action': 'preview', 'target': {'kind': 'saved', 'name': 'focus.sh'}})
        self.assertEqual(runner.calls[-1][0], (*RESTORE, '--dry-run', '--', '/s/focus.sh.sh'))

    def test_restore_needs_confirmation_and_a_listed_set(self):
        runner = FakeRunner([json_command(SAVED)])
        adapter = RestoreAdapter(runner)
        for body in ({'action': 'restore', 'target': {'kind': 'auto'}},
                     {'action': 'restore', 'target': {'kind': 'auto'}, 'confirmed': 'yes'}):
            with self.assertRaises(ApiError) as raised:
                adapter.snapshots(body)
            self.assertEqual(raised.exception.status, 400)
        for target in ({'kind': 'saved', 'name': 'nope'}, {'kind': 'saved', 'name': '--force'}, {'kind': 'saved'},
                       {'kind': 'other'}, {'kind': 'auto', 'name': 'focus'}, None):
            runner.results[:] = [json_command(SAVED)]
            with self.subTest(target=target), self.assertRaises(ApiError) as raised:
                adapter.snapshots({'action': 'preview', 'target': target})
            self.assertEqual(raised.exception.status, 400)
        self.assertTrue(all('--dry-run' not in call[0] for call in runner.calls))

    def test_save_uses_equals_form_and_validates_the_name(self):
        for body, expected in (({'action': 'save', 'name': 'before-reboot'}, '--save=before-reboot'),
                               ({'action': 'save'}, '--save='), ({'action': 'save', 'name': ''}, '--save=')):
            runner = FakeRunner([CommandResult(0, 'Saved 3 open session(s) to /s/x.sh\n')])
            reply = RestoreAdapter(runner).snapshots(body)
            self.assertEqual(runner.calls, [((*RESTORE, expected), 30)])
            self.assertIn('Saved 3', reply.body['report'])
        for name in ('-f', '--force', '../x', 'focus.sh', 'focus.sh.sh', 'a b', '.hidden', 'x' * 65, 7):
            with self.subTest(name=name), self.assertRaises(ApiError) as raised:
                RestoreAdapter(FakeRunner()).snapshots({'action': 'save', 'name': name})
            self.assertEqual(raised.exception.status, 400)

    def test_save_conflict_and_cli_failure_statuses(self):
        exists = CommandResult(1, '', "saved set 'x' already exists (/s/x.sh); --force overwrites it")
        with self.assertRaises(ApiError) as raised:
            RestoreAdapter(FakeRunner([exists])).snapshots({'action': 'save', 'name': 'x'})
        self.assertEqual(raised.exception.status, 409)
        with self.assertRaises(ApiError) as raised:
            RestoreAdapter(FakeRunner([CommandResult(2, '', 'boom')])).snapshots({'action': 'save', 'name': 'x'})
        self.assertEqual(raised.exception.status, 502)
        with self.assertRaises(ApiError) as raised:
            RestoreAdapter(FakeRunner()).snapshots({'action': 'delete'})
        self.assertEqual(raised.exception.status, 400)


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
                                       'findings': 2}, 'refusal_active': True}
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
                                         'accepts_at': None, 'last_accepted': None, 'last_refused': None,
                                         'refusal_active': False}):
            runner = FakeRunner([json_command([]), json_command(audit), json_command(service), json_command(broken)])
            with self.assertRaises(ApiError):
                GuardAdapter(runner, read_wait=0.01).read()


if __name__ == '__main__':
    unittest.main()
