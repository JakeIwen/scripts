"""Public text and shared Tools installers checkpoint only after saved verification."""
from contextlib import ExitStack, redirect_stdout
import io
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

import install_bpm_menu
import repair_text_transforms as text
import repair_script_paths as paths
import tools_menu_item as tools
from macbook.tests.btt_guard_fixtures import create_guard_database
from macbook.bettertouchtool.btt_common import MEDIA


class TextCheckpointWiringTests(unittest.TestCase):
    def run_install(self, argv, *, noop=False, verification_fails=False):
        events = []
        plan = {'edits': [{'matches': noop}]}
        receipt = dict(message='worker verified', wrote=True, touched_uuids=['script', 'disabled-copy'])
        def worker(mode, argument=None):
            events.append(mode)
            return json.dumps(plan if mode == 'plan' else receipt)
        def verify(*args):
            events.append('saved')
            if verification_fails:
                raise RuntimeError('saved verification failed')
            return []
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            root = Path(directory)
            library = root/'case.cjs'
            library.touch()
            stack.enter_context(patch.object(text, 'CASE_LIBRARY', library))
            stack.enter_context(patch.object(text, 'DIRECTORY', root))
            stack.enter_context(patch.object(text, 'current_database', return_value=root/'fixture.sqlite'))
            stack.enter_context(patch.object(text, 'records', return_value={str(i): {
                'parent': text.TEXT_MENU, 'ZGESTURETYPE': 773, 'ZORDER': i,
                'ZUNIQUEIDENTIFIER': str(i), 'config': {}} for i in range(9)}))
            stack.enter_context(patch.object(text, 'worker', side_effect=worker))
            stack.enter_context(patch.object(text, 'saved_errors', side_effect=verify))
            stack.enter_context(patch.object(text, 'backup_configuration', return_value=root/'backup.json'))
            before = stack.enter_context(patch.object(text, 'before_install',
                side_effect=lambda **kw: events.append('baseline') or {'fixture': True}))
            after = stack.enter_context(patch.object(text, 'after_install',
                side_effect=lambda *a, **kw: events.append('checkpoint')))
            stack.enter_context(redirect_stdout(io.StringIO()))
            if verification_fails:
                with self.assertRaisesRegex(RuntimeError, 'saved verification failed'):
                    text.main(argv)
            else:
                self.assertEqual(text.main(argv), 0)
        return events, before, after

    def test_apply_uses_written_uuid_receipt_after_saved_verification(self):
        events, before, after = self.run_install(['--apply'])
        self.assertEqual(events, ['plan', 'baseline', 'apply', 'saved', 'checkpoint'])
        before.assert_called_once_with(no_checkpoint=False)
        after.assert_called_once_with({'fixture': True}, ['script', 'disabled-copy'], 'repair_text_transforms', wrote=True)

    def test_noop_optout_and_failed_verification(self):
        events, before, after = self.run_install(['--apply', '--no-checkpoint'], noop=True)
        before.assert_called_once_with(no_checkpoint=True)
        self.assertNotIn('apply', events)
        self.assertFalse(after.call_args.kwargs['wrote'])
        _, _, after = self.run_install(['--apply'], verification_fails=True)
        after.assert_not_called()

    def test_preview_never_runs_guard(self):
        events, before, after = self.run_install(['--inspect'])
        self.assertEqual(events, [])
        before.assert_not_called()
        after.assert_not_called()


class ToolsCheckpointWiringTests(unittest.TestCase):
    def receipt(self, wrote=True):
        return dict(message='verified tools', wrote=wrote, touched_uuids=['button', 'action'] if wrote else [],
            saved_plan={'entities': [
                {'uuid': 'button', 'parent': MEDIA, 'node': {'BTTTriggerType': 773, 'BTTOrder': 0}},
                {'uuid': 'action', 'parent': 'button', 'node': {'BTTTriggerType': -1, 'BTTOrder': 119,
                    'BTTPredefinedActionType': 248, 'BTTNamedTriggerToTrigger': 'Sample Task'}}],
                'configChanges': [], 'media': {'BTTMenuItems': []}})

    def run_install(self, argv, *, wrote=True, worker_fails=False, saved_fails=False):
        events = []
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            root = Path(directory)
            database, _ = create_guard_database(root)
            stack.enter_context(patch.object(tools, 'BUILD', root/'build'))
            stack.enter_context(patch.object(tools, 'build_definition', return_value={'fixture': True}))
            stack.enter_context(patch.object(tools, 'current_database', return_value=database))
            stack.enter_context(patch('sys.argv', ['install_bpm_menu.py', *argv]))
            def run(*args, **kwargs):
                events.append('worker')
                return SimpleNamespace(returncode=1 if worker_fails else 0, stderr='failed',
                                       stdout=json.dumps(self.receipt(wrote)))
            worker = stack.enter_context(patch.object(tools.subprocess, 'run', side_effect=run))
            before = stack.enter_context(patch.object(tools, 'before_install',
                side_effect=lambda **kw: events.append('baseline') or {'fixture': True}))
            after = stack.enter_context(patch.object(tools, 'after_install',
                side_effect=lambda *a, **kw: events.append('checkpoint')))
            verify = tools.verify_saved_item
            def saved(plan):
                events.append('saved')
                if saved_fails:
                    raise RuntimeError('saved verification failed')
                verify(plan)
            stack.enter_context(patch.object(tools, 'verify_saved_item', side_effect=saved))
            stack.enter_context(redirect_stdout(io.StringIO()))
            if saved_fails:
                with self.assertRaisesRegex(RuntimeError, 'saved verification failed'):
                    install_bpm_menu.main()
            else:
                with self.assertRaises(SystemExit) as exit:
                    install_bpm_menu.main()
                self.assertEqual(exit.exception.code, int(worker_fails))
        return events, worker, before, after

    def test_public_wrapper_accepts_only_after_real_saved_database_verification(self):
        events, worker, before, after = self.run_install(['--apply'])
        self.assertEqual(events, ['baseline', 'worker', 'saved', 'checkpoint'])
        before.assert_called_once_with(no_checkpoint=False)
        self.assertEqual(worker.call_args.args[0][-1], 'receipt')
        after.assert_called_once_with({'fixture': True}, ['button', 'action'], 'bpm-over-time', wrote=True)

    def test_noop_and_optout_do_not_claim_writes(self):
        events, _, before, after = self.run_install(['--apply', '--no-checkpoint'], wrote=False)
        self.assertEqual(events, ['baseline', 'worker', 'checkpoint'])
        before.assert_called_once_with(no_checkpoint=True)
        self.assertFalse(after.call_args.kwargs['wrote'])

    def test_failed_worker_or_saved_verification_never_checkpoints(self):
        for kwargs in ({'worker_fails': True}, {'saved_fails': True}):
            _, _, _, after = self.run_install(['--apply'], **kwargs)
            after.assert_not_called()

    def test_saved_verifier_refuses_disabled_actions_and_wrong_commands(self):
        for column, value in [('ZENABLEDNEW', 0), ('ZLAUNCHPATH', 'wrong command')]:
            with self.subTest(column=column), tempfile.TemporaryDirectory() as directory:
                database, _ = create_guard_database(directory)
                connection = sqlite3.connect(database)
                try:
                    connection.execute('UPDATE ZBTTBASEENTITY SET '+column+'=? WHERE ZUNIQUEIDENTIFIER=?',
                                       (value, 'action'))
                    connection.commit()
                finally:
                    connection.close()
                with patch.object(tools, 'current_database', return_value=database), \
                        patch.object(tools.time, 'monotonic', side_effect=[0, 46]):
                    with self.assertRaisesRegex(RuntimeError, 'disk save is pending'):
                        tools.verify_saved_item(self.receipt()['saved_plan'])

    def test_inspection_never_runs_guard(self):
        events, _, before, after = self.run_install(['--inspect'])
        self.assertEqual(events, ['worker'])
        before.assert_not_called()
        after.assert_not_called()


class PathCheckpointWiringTests(unittest.TestCase):
    def run_install(self, argv, *, noop=False, pending=False, runtime_current=False):
        plan = {'targets': [] if noop else [{'uuid': 'owned', 'replacements': [],
                                           'field': 'command', 'after': 'new'}]}
        snapshot = [{'uuid': 'owned', 'trigger': {'command': 'new' if runtime_current else 'old'}}]
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            stack.enter_context(patch.object(paths, 'current_database', return_value='fixture.sqlite'))
            stack.enter_context(patch.object(paths, 'build_plan', return_value=plan))
            stack.enter_context(patch.object(paths, 'records', return_value={}))
            stack.enter_context(patch.object(paths, 'worker', return_value=json.dumps(snapshot)))
            stack.enter_context(patch.object(paths, 'backup_configuration', return_value=Path(directory)/'plan.json'))
            stack.enter_context(patch.object(paths, 'saved_report', return_value=(['owned'] if pending else [], [])))
            stack.enter_context(patch.object(paths.time, 'monotonic', side_effect=[0, 6]))
            before = stack.enter_context(patch.object(paths, 'before_install', return_value={'fixture': True}))
            after = stack.enter_context(patch.object(paths, 'after_install'))
            skipped = stack.enter_context(patch.object(paths, 'skip_checkpoint'))
            stack.enter_context(redirect_stdout(io.StringIO()))
            paths.main(argv)
        return before, after, skipped

    def test_verified_path_changes_pass_exact_target_ids(self):
        before, after, skipped = self.run_install(['--apply', '--no-checkpoint'])
        before.assert_called_once_with(no_checkpoint=True)
        after.assert_called_once_with({'fixture': True}, ['owned'], 'repair_script_paths', wrote=True)
        skipped.assert_not_called()

    def test_existing_runtime_updates_are_not_counted_as_this_runs_writes(self):
        _, after, _ = self.run_install(['--apply'], runtime_current=True)
        after.assert_called_once_with({'fixture': True}, [], 'repair_script_paths', wrote=False)

    def test_pending_save_reports_skip_and_never_requests_capture(self):
        before, after, skipped = self.run_install(['--apply'], pending=True)
        before.assert_called_once_with(no_checkpoint=False)
        after.assert_not_called()
        skipped.assert_called_once_with('repair_script_paths', paths.InstallerCheckpointReason.SAVED_UNVERIFIED)

    def test_noop_and_preview_do_not_request_capture(self):
        _, after, _ = self.run_install(['--apply'], noop=True)
        after.assert_called_once_with({'fixture': True}, [], 'repair_script_paths', wrote=False)
        before, after, skipped = self.run_install(['--inspect'])
        before.assert_not_called()
        after.assert_not_called()
        skipped.assert_not_called()


if __name__ == '__main__':
    unittest.main()
