import os
import tempfile
import unittest
from pathlib import Path

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


class SavedSetChangeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.saved = Path(self.tmp.name) / 'saved'
        self.saved.mkdir()
        self.file = self.saved / 'focus.sh'
        self.file.write_text('#!/bin/sh\noriginal\n')
        self.listing = [{'name': 'focus', 'path': str(self.file), 'saved_at': 1.0, 'sessions': 2}]
        self.target = {'kind': 'saved', 'name': 'focus'}

    def adapter(self, *results, listing=None):
        self.runner = FakeRunner([json_command(self.listing if listing is None else listing), *results])
        return RestoreAdapter(self.runner, saved_dir=self.saved)

    def backups(self):
        return sorted((self.saved / '.deleted').glob('*')) if (self.saved / '.deleted').exists() else []

    def test_delete_moves_the_set_out_of_the_listing_but_keeps_it(self):
        reply = self.adapter().snapshots({'action': 'delete', 'target': self.target, 'confirmed': True})
        self.assertFalse(self.file.exists())
        [kept] = self.backups()
        self.assertRegex(kept.name, r'^focus-\d{8}-\d{6}\.sh$')
        self.assertEqual(kept.read_text(), '#!/bin/sh\noriginal\n')
        self.assertEqual(oct(kept.stat().st_mode & 0o777), '0o600')
        self.assertEqual(oct((self.saved / '.deleted').stat().st_mode & 0o777), '0o700')
        self.assertIn(str(kept), reply.body['message'])
        self.assertEqual([p.name for p in self.saved.glob('*.sh')], [], 'the CLI glob no longer sees it')

    def test_both_need_confirmation_and_a_saved_target(self):
        for action in ('delete', 'overwrite'):
            for body in ({'action': action, 'target': self.target},
                         {'action': action, 'target': self.target, 'confirmed': 'yes'}):
                with self.subTest(action=action, body=body), self.assertRaises(ApiError) as raised:
                    self.adapter().snapshots(body)
                self.assertEqual(raised.exception.status, 400)
            for target in ({'kind': 'auto'}, {'kind': 'saved', 'name': 'nope'}, None):
                with self.subTest(action=action, target=target), self.assertRaises(ApiError) as raised:
                    self.adapter().snapshots({'action': action, 'target': target, 'confirmed': True})
                self.assertEqual(raised.exception.status, 400)
        self.assertTrue(self.file.exists())
        self.assertEqual(self.backups(), [])

    def test_a_listed_path_outside_the_saved_directory_is_refused(self):
        outside = Path(self.tmp.name) / 'elsewhere.sh'
        outside.write_text('x')
        link = self.saved / 'link.sh'
        os.symlink(outside, link)
        for path in (outside, link, self.saved / 'missing.sh', self.saved / '..' / 'elsewhere.sh', self.saved / 'notes.txt'):
            listing = [{'name': path.stem, 'path': str(path), 'saved_at': 1.0, 'sessions': 1}]
            for action in ('delete', 'overwrite'):
                with self.subTest(path=path, action=action), self.assertRaises(ApiError) as raised:
                    self.adapter(listing=listing).snapshots(
                        {'action': action, 'target': {'kind': 'saved', 'name': path.stem}, 'confirmed': True})
                self.assertEqual(raised.exception.status, 400)
        self.assertTrue(outside.exists())

    def overwrite(self, runner_result):
        adapter = self.adapter(runner_result)
        return adapter.snapshots({'action': 'overwrite', 'target': self.target, 'confirmed': True})

    def cli_saves(self, text='new\n', result=None):
        """A runner result that, like the CLI, writes the new set when called."""
        def run():
            self.file.write_text(text)
            return result or CommandResult(0, 'Saved 4 open session(s) to x\n')
        return run

    def test_overwrite_moves_the_old_version_aside_then_saves_without_force(self):
        reply = self.overwrite(self.cli_saves())
        self.assertEqual(self.runner.calls[-1], ((*RESTORE, '--save=focus'), 30))
        [kept] = self.backups()
        self.assertEqual(kept.read_text(), '#!/bin/sh\noriginal\n')
        self.assertEqual(oct(kept.stat().st_mode & 0o777), '0o600')
        self.assertEqual(self.file.read_text(), 'new\n')
        self.assertIn('Saved 4', reply.body['report'])
        self.assertIn(str(kept), reply.body['report'])

    def test_a_failed_overwrite_puts_the_old_version_back(self):
        with self.assertRaises(ApiError) as raised:
            self.overwrite(CommandResult(1, '', 'boom'))
        self.assertEqual(raised.exception.status, 502)
        self.assertIn('restored', str(raised.exception))
        self.assertEqual(self.file.read_text(), '#!/bin/sh\noriginal\n')
        self.assertEqual(self.backups(), [])

    def test_restore_back_never_clobbers_a_set_that_appeared(self):
        failing = self.cli_saves('concurrent', CommandResult(1, '', 'boom'))
        with self.assertRaises(ApiError) as raised:
            self.overwrite(failing)
        self.assertEqual(self.file.read_text(), 'concurrent')
        [kept] = self.backups()
        self.assertEqual(kept.read_text(), '#!/bin/sh\noriginal\n')
        self.assertIn(str(kept), str(raised.exception))

    def test_a_save_landing_meanwhile_is_a_conflict_that_keeps_both(self):
        exists = self.cli_saves('theirs', CommandResult(1, '', "saved set 'focus' already exists"))
        with self.assertRaises(ApiError) as raised:
            self.overwrite(exists)
        self.assertEqual(raised.exception.status, 409)
        self.assertEqual(self.file.read_text(), 'theirs')
        [kept] = self.backups()
        self.assertEqual(kept.read_text(), '#!/bin/sh\noriginal\n')

    def test_overwrite_refuses_a_name_the_cli_cannot_address(self):
        odd = self.saved / 'focus.sh.sh'
        odd.write_text('other')
        other = [{'name': 'focus.sh', 'path': str(odd), 'saved_at': 1.0, 'sessions': 1}]
        with self.assertRaises(ApiError) as raised:
            self.adapter(listing=other).snapshots(
                {'action': 'overwrite', 'target': {'kind': 'saved', 'name': 'focus.sh'}, 'confirmed': True})
        self.assertEqual(raised.exception.status, 400)
        self.assertEqual(odd.read_text(), 'other')
        self.assertEqual(self.file.read_text(), '#!/bin/sh\noriginal\n')
        self.assertEqual(self.backups(), [])
        self.adapter(listing=other).snapshots(  # delete still works: it goes by path
            {'action': 'delete', 'target': {'kind': 'saved', 'name': 'focus.sh'}, 'confirmed': True})
        self.assertFalse(odd.exists())

    def test_a_backup_folder_that_is_a_symlink_or_file_is_refused(self):
        elsewhere = Path(self.tmp.name) / 'elsewhere'
        elsewhere.mkdir()
        for make in (lambda p: os.symlink(elsewhere, p), lambda p: p.write_text('x')):
            folder = self.saved / '.deleted'
            make(folder)
            for action in ('delete', 'overwrite'):
                with self.subTest(action=action), self.assertRaises(ApiError) as raised:
                    self.adapter(CommandResult(0, '')).snapshots({'action': action, 'target': self.target, 'confirmed': True})
                self.assertEqual(raised.exception.status, 400)
            self.assertTrue(self.file.exists())
            self.assertEqual(list(elsewhere.iterdir()), [])
            folder.unlink()

    def test_backups_never_replace_each_other(self):
        for _ in range(2):
            self.file.write_text('again')
            self.adapter().snapshots({'action': 'delete', 'target': self.target, 'confirmed': True})
        self.assertEqual(len(self.backups()), 2)

    def test_a_duplicate_save_name_points_at_overwrite(self):
        exists = CommandResult(1, '', "saved set 'x' already exists")
        with self.assertRaises(ApiError) as raised:
            RestoreAdapter(FakeRunner([exists])).snapshots({'action': 'save', 'name': 'x'})
        self.assertIn('Overwrite', str(raised.exception))


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
