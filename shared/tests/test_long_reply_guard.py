"""Exercise the long-reply Stop hook the way Claude Code and Codex call it."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / 'sh' / 'long_reply_guard.py'
LONG = ' '.join(['word'] * 500)
SHORT = 'A short answer that fits on one screen.'
CLAUDE_ATTENDED = {'CLAUDECODE': '1', 'CLAUDE_CODE_ENTRYPOINT': 'cli', 'CLAUDE_CODE_SESSION_ATTENDED': '1'}
CLAUDE_HEADLESS = {'CLAUDECODE': '1', 'CLAUDE_CODE_ENTRYPOINT': 'sdk-cli', 'CLAUDE_CODE_SESSION_ATTENDED': '0'}


class LongReplyGuardTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def run_hook(self, stdin, env):
        base = {key: value for key, value in os.environ.items()
                if not key.startswith(('CLAUDE', 'CODEX'))}
        result = subprocess.run([sys.executable, str(SCRIPT)], input=stdin, capture_output=True,
                                text=True, env={**base, **env}, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout) if result.stdout else None

    def claude_stop(self, message, env=CLAUDE_ATTENDED, **event):
        return self.run_hook(json.dumps({'hook_event_name': 'Stop', 'session_id': 's',
                                         'stop_hook_active': False,
                                         'last_assistant_message': message, **event}), env)

    def codex_stop(self, message, source, env=None):
        rollout = self.root / 'rollout.jsonl'
        rollout.write_text(json.dumps({'type': 'session_meta', 'payload': {'source': source}}) + '\n')
        return self.run_hook(json.dumps({'hook_event_name': 'Stop', 'session_id': 's', 'turn_id': 't',
                                         'transcript_path': str(rollout), 'stop_hook_active': False,
                                         'last_assistant_message': message}), env or {})

    def test_long_attended_claude_reply_is_sent_back_with_page_instructions(self):
        reply = self.claude_stop(LONG)
        self.assertEqual(reply['decision'], 'block')
        self.assertIn('about 500 words', reply['reason'])
        self.assertIn('agent_art.py save --agent claude', reply['reason'])

    def test_short_reply_and_reply_with_a_page_link_pass(self):
        self.assertIsNone(self.claude_stop(SHORT))
        self.assertIsNone(self.claude_stop(
            LONG + '\nfile:///Users/jacobr/.agent/artifacts/20261102-0038-config-x.html'))
        self.assertIsNone(self.claude_stop(
            LONG + '\nfile:///Volumes/pihome/.agent/artifacts/20261102-0038-pi-x.html'))

    def test_continuation_after_a_block_is_never_blocked_again(self):
        self.assertIsNone(self.claude_stop(LONG, stop_hook_active=True))

    def test_headless_claude_runs_pass(self):
        self.assertIsNone(self.claude_stop(LONG, env=CLAUDE_HEADLESS))
        self.assertIsNone(self.claude_stop(LONG, env={'CLAUDECODE': '1', 'CLAUDE_CODE_ENTRYPOINT': 'sdk-cli'}))
        self.assertIsNotNone(self.claude_stop(LONG, env={'CLAUDECODE': '1', 'CLAUDE_CODE_ENTRYPOINT': 'cli'}))

    def test_codex_tui_is_checked_but_exec_and_subagents_pass_even_from_a_claude_shell(self):
        reply = self.codex_stop(LONG, 'cli')
        self.assertEqual(reply['decision'], 'block')
        self.assertNotIn('--agent claude', reply['reason'])
        self.assertIsNone(self.codex_stop(LONG, 'exec', env=CLAUDE_ATTENDED))
        self.assertIsNone(self.codex_stop(LONG, {'subagent': {'thread_spawn': {}}}))

    def test_many_short_lines_count_as_rows(self):
        reply = self.claude_stop('\n'.join(f'- item {n}' for n in range(60)))
        self.assertIn('60 rows', reply['reason'])
        self.assertIsNone(self.claude_stop('\n'.join(f'- item {n}' for n in range(40))))

    def test_long_lines_wrap_and_tables_add_borders(self):
        # 300 characters wrap to three rows at 104 columns less the gutter.
        reply = self.claude_stop('\n'.join(['x' * 300] * 20))
        self.assertIn('60 rows', reply['reason'])
        # 48 table lines plus top and bottom borders is exactly the limit.
        table = '| a | b |\n|---|---|\n' + '\n'.join('| 1 | 2 |' for _ in range(46))
        self.assertIsNone(self.claude_stop(table))
        self.assertIn('51 rows', self.claude_stop(table + '\n| 1 | 2 |')['reason'])

    def test_fenced_scripts_do_not_count(self):
        script = '```sh\n' + '\n'.join(f'echo step {n} with several words here' for n in range(80)) + '\n```'
        self.assertIsNone(self.claude_stop(f'Run this:\n\n{script}\n\nzsh /tmp/clip/x'))

    def test_bad_input_and_other_events_fail_open(self):
        for stdin in ('', 'not json', '[]', '{"hook_event_name":"Stop","last_assistant_message":42}'):
            self.assertIsNone(self.run_hook(stdin, CLAUDE_ATTENDED))
        self.assertIsNone(self.run_hook(json.dumps({'hook_event_name': 'UserPromptSubmit',
                                                    'last_assistant_message': LONG}), CLAUDE_ATTENDED))
        missing = json.dumps({'hook_event_name': 'Stop', 'turn_id': 't', 'transcript_path': '/nonexistent',
                              'last_assistant_message': LONG})
        self.assertIsNone(self.run_hook(missing, {}))


if __name__ == '__main__':
    unittest.main()
