"""iTerm restore transport boundaries; no unit test opens a real terminal."""

import asyncio
import io
import json
import subprocess
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import iterm_restore_tabs as tabs


class Transport(unittest.TestCase):
    def run_transport(self, titles, responses=None, commands=None, raw=None):
        commands = commands or ['command one', 'command two']
        responses = responses or [subprocess.CompletedProcess([], 0, 'id-first\nid-second\n', ''),
                                  subprocess.CompletedProcess([], 0, '', '')]
        stderr = io.StringIO()
        with patch.object(tabs.subprocess, 'run', side_effect=responses) as run, \
                patch('sys.stdin', io.StringIO(json.dumps(titles) if raw is None else raw)), patch('sys.stderr', stderr):
            result = tabs.main(commands)
        return result, run.call_args_list, stderr.getvalue()

    def test_created_session_ids_follow_command_order_not_current_tab(self):
        commands = ['printf \'"quoted"\'', 'command two']
        result, calls, warning = self.run_transport([[1, 'Second'], [0, 'First']], commands=commands)
        self.assertEqual((result, warning), (0, ''))
        self.assertEqual(calls[0].args[0], ['/usr/bin/osascript', '-e', tabs.ITERM_SCRIPT, *commands])
        self.assertEqual(calls[0].kwargs['stdin'], subprocess.DEVNULL)
        self.assertEqual(json.loads(calls[1].kwargs['input']), [['id-second', 'Second'], ['id-first', 'First']])
        self.assertEqual(calls[1].args[0], [str(tabs.TITLE_PYTHON), '-I', '-B', str(tabs.Path(tabs.__file__).resolve()), '--titles'])
        self.assertEqual(calls[1].kwargs['timeout'], tabs.TITLE_TIMEOUT)

    def test_titles_are_json_data_with_literal_interpolation_not_source(self):
        hostile = '"; $(touch /not-created) \\(session.name) \\end\n\x00\x1b\x85\x9f'
        _, calls, warning = self.run_transport([[0, hostile]])
        payload = json.loads(calls[1].kwargs['input'])
        self.assertEqual(payload, [['id-first', '"; $(touch /not-created) \\\\(session.name) \\\\end']])
        self.assertNotIn(hostile, ' '.join(calls[1].args[0]))
        self.assertEqual(warning, '')

    def test_visible_limit_precedes_backslash_escaping(self):
        self.assertEqual(tabs.literal_title(' ' + '\\' * 65), '\\' * 120)
        self.assertEqual(tabs.literal_title('z' * 100), 'z' * 60)
        self.assertEqual(tabs.literal_title(' \n\t\x00\x7f\x80\x9f '), '')

    def test_empty_titles_never_launch_optional_runtime(self):
        for titles in ([], [[0, '   ']], [[1, '\n\x00\x85']]):
            with self.subTest(titles=titles):
                result, calls, warning = self.run_transport(titles)
                self.assertEqual((result, len(calls), warning), (0, 1, ''))

    def test_no_title_request_ignores_unavailable_identity_mapping(self):
        for output in ('', 'one-only\n', 'first\n\n'):
            responses = [subprocess.CompletedProcess([], 0, output, '')]
            result, calls, warning = self.run_transport([], responses, raw='')
            self.assertEqual((result, len(calls), warning), (0, 1, ''))

    def test_unavailable_broken_or_timed_out_api_warns_once_but_succeeds(self):
        failures = [FileNotFoundError('private path'), PermissionError('private path'),
                    subprocess.CalledProcessError(1, ['python'], stderr='private title'),
                    subprocess.TimeoutExpired(['python'], tabs.TITLE_TIMEOUT, stderr='private title')]
        for failure in failures:
            with self.subTest(failure=failure):
                responses = [subprocess.CompletedProcess([], 0, 'id-first\nid-second\n', ''), failure]
                result, calls, warning = self.run_transport([[0, 'First']], responses)
                self.assertEqual((result, len(calls)), (0, 2))
                self.assertEqual(warning, f'{tabs.TITLE_WARNING} ({type(failure).__name__})\n')
                self.assertNotIn('private', warning)

    def test_invalid_identity_output_cannot_target_other_tabs(self):
        for output in ('', 'one-only\n', 'first\n\n', 'first\nsecond\nextra\n'):
            responses = [subprocess.CompletedProcess([], 0, output, '')]
            result, calls, warning = self.run_transport([[0, 'First']], responses)
            self.assertEqual((result, len(calls), warning), (0, 1, tabs.TITLE_WARNING + ' (ValueError)\n'))

    def test_bad_title_payload_does_not_fail_successful_creation(self):
        for titles in ({}, [[-1, 'bad']], [[2, 'bad']], [[True, 'bad']], [[0, 7]], ['bad']):
            result, calls, warning = self.run_transport(titles)
            self.assertEqual((result, len(calls), warning), (0, 1, tabs.TITLE_WARNING + ' (ValueError)\n'))
        with patch.object(tabs.subprocess, 'run') as run, patch('sys.stderr', io.StringIO()) as warning:
            tabs.set_titles(['created'], '{not json')
        run.assert_not_called()
        self.assertEqual(warning.getvalue(), tabs.TITLE_WARNING + ' (JSONDecodeError)\n')

    def test_creation_failure_keeps_existing_failure_behavior(self):
        with patch.object(tabs.subprocess, 'run', side_effect=subprocess.CalledProcessError(1, ['osascript'])) as run:
            with self.assertRaises(subprocess.CalledProcessError):
                tabs.main(['command one'])
        self.assertEqual(run.call_count, 1)

    def test_no_commands_opens_nothing(self):
        with patch.object(tabs.subprocess, 'run') as run:
            self.assertEqual(tabs.main([]), 0)
        run.assert_not_called()

    def test_system_python_creation_does_not_import_optional_iterm_dependency(self):
        with patch.dict(sys.modules, {'iterm2': None}):
            result, calls, warning = self.run_transport([])
        self.assertEqual((result, len(calls), warning), (0, 1, ''))

    def test_api_uses_exact_session_identity_and_never_focus(self):
        session = object()
        tab = SimpleNamespace(async_set_title=AsyncMock())
        app = SimpleNamespace(get_session_by_id=Mock(return_value=session),
                              get_window_and_tab_for_session=Mock(return_value=(object(), tab)))
        api = SimpleNamespace(async_get_app=AsyncMock(return_value=app))
        with patch.dict(sys.modules, {'iterm2': api}):
            asyncio.run(tabs.apply_titles('connection', [['created-id', 'literal']]))
        app.get_session_by_id.assert_called_once_with('created-id')
        app.get_window_and_tab_for_session.assert_called_once_with(session)
        tab.async_set_title.assert_awaited_once_with('literal')

    def test_closed_session_fails_instead_of_renaming_the_current_tab(self):
        app = SimpleNamespace(get_session_by_id=Mock(return_value=None),
                              get_window_and_tab_for_session=Mock())
        api = SimpleNamespace(async_get_app=AsyncMock(return_value=app))
        with patch.dict(sys.modules, {'iterm2': api}):
            with self.assertRaises(ValueError):
                asyncio.run(tabs.apply_titles('connection', [['closed-id', 'literal']]))
        app.get_window_and_tab_for_session.assert_not_called()


if __name__ == '__main__':
    unittest.main()
