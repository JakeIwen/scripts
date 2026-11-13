"""Exercise the long-reply Stop hook the way Claude Code and Codex call it."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / 'sh' / 'long_reply_guard.py'
LONG = ' '.join(['word'] * 500)
SHORT = 'A short answer that fits on one screen.'
CLAUDE_ATTENDED = {'CLAUDECODE': '1', 'CLAUDE_CODE_ENTRYPOINT': 'cli', 'CLAUDE_CODE_SESSION_ATTENDED': '1'}
def stamp(seconds_ago):
    """A transcript timestamp, in the agents' UTC `...Z` form."""
    moment = datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)
    return moment.strftime('%Y-%m-%dT%H:%M:%S.') + f'{moment.microsecond // 1000:03d}Z'


CLAUDE_HEADLESS = {'CLAUDECODE': '1', 'CLAUDE_CODE_ENTRYPOINT': 'sdk-cli', 'CLAUDE_CODE_SESSION_ATTENDED': '0'}


class LongReplyGuardTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.home = self.root / 'home'
        self.artifacts = self.home / '.agent' / 'artifacts'
        self.artifacts.mkdir(parents=True)

    def run_hook(self, stdin, env):
        base = {key: value for key, value in os.environ.items()
                if not key.startswith(('CLAUDE', 'CODEX'))}
        result = subprocess.run([sys.executable, str(SCRIPT)], input=stdin, capture_output=True,
                                text=True, env={**base, 'HOME': str(self.home), **env},
                                check=False, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout) if result.stdout else None

    def page(self, name, age=0):
        path = self.artifacts / name
        path.write_text('<html></html>')
        stamp = time.time() - age
        os.utime(path, (stamp, stamp))
        return path

    def claude_stop(self, message, env=CLAUDE_ATTENDED, **event):
        return self.run_hook(json.dumps({'hook_event_name': 'Stop', 'session_id': 's',
                                         'stop_hook_active': False,
                                         'last_assistant_message': message, **event}), env)

    def codex_stop(self, message, source, env=None, started_ago=None):
        rollout = self.root / 'rollout.jsonl'
        rows = [{'type': 'session_meta', 'payload': {'source': source}}]
        if started_ago is not None:
            rows += [{'timestamp': stamp(started_ago + 60), 'type': 'event_msg',
                      'payload': {'type': 'task_started', 'turn_id': 'earlier'}},
                     {'timestamp': stamp(started_ago), 'type': 'event_msg',
                      'payload': {'type': 'task_started', 'turn_id': 't'}}]
        rollout.write_text(''.join(json.dumps(row) + '\n' for row in rows))
        return self.run_hook(json.dumps({'hook_event_name': 'Stop', 'session_id': 's', 'turn_id': 't',
                                         'transcript_path': str(rollout), 'stop_hook_active': False,
                                         'last_assistant_message': message}), env or {})

    def transcript(self, prompt_id, started_ago, filler_mb=0):
        """A Claude transcript whose last turn for prompt_id began started_ago
        seconds ago, followed by filler_mb megabytes of that turn's tool rows."""
        path = self.root / f'{prompt_id}.jsonl'
        rows = [{'type': 'user', 'promptId': 'older', 'turnPosition': {'turnIndex': 1},
                 'timestamp': stamp(started_ago + 600), 'message': {'content': 'earlier'}},
                {'type': 'user', 'promptId': prompt_id, 'turnPosition': {'turnIndex': 2},
                 'timestamp': stamp(started_ago), 'message': {'content': 'do the thing'}},
                {'type': 'user', 'promptId': prompt_id, 'timestamp': stamp(started_ago - 5),
                 'message': {'content': [{'type': 'tool_result'}]}}]
        filler = json.dumps({'type': 'user', 'promptId': prompt_id, 'timestamp': stamp(10),
                             'message': {'content': [{'type': 'tool_result', 'content': 'x' * 2000}]}})
        with path.open('w') as handle:
            handle.write(''.join(json.dumps(row) + '\n' for row in rows))
            handle.write((filler + '\n') * (filler_mb * 1024 * 1024 // len(filler)))
        return str(path)

    def test_long_attended_claude_reply_is_sent_back_with_page_instructions(self):
        reply = self.claude_stop(LONG)
        self.assertEqual(reply['decision'], 'block')
        self.assertIn('about 500 words', reply['reason'])
        self.assertIn('agent_art.py save --agent claude', reply['reason'])

    def test_short_reply_and_reply_linking_the_page_it_just_saved_pass(self):
        self.assertIsNone(self.claude_stop(SHORT))
        turn = {'prompt_id': 'p-now', 'transcript_path': self.transcript('p-now', started_ago=60)}
        mac = self.page('20261102-0038-config-x.html')
        self.assertIsNone(self.claude_stop(LONG + f'\n{mac.as_uri()}', **turn))
        self.assertIsNone(self.claude_stop(LONG + f'\nSee [the page](<{mac.as_uri()}>).', **turn))
        # A Pi page's link names the Mac's mount; the Pi finds it in its own home.
        self.page('20261102-0038-pi-x.html')
        self.assertIsNone(self.claude_stop(
            LONG + '\nfile:///Volumes/pihome/.agent/artifacts/20261102-0038-pi-x.html', **turn))

    def test_only_a_page_saved_during_this_turn_exempts(self):
        # Review findings: an unrelated page saved a minute ago exempted the
        # reply, and this turn's own page saved over an hour ago did not.
        unrelated = self.page('20261113-unrelated.html', age=60)
        reply = self.claude_stop(f'{LONG}\n{unrelated.as_uri()}', prompt_id='p-unrelated',
                                 transcript_path=self.transcript('p-unrelated', started_ago=30))
        self.assertEqual(reply['decision'], 'block')
        own = self.page('20261113-own.html', age=3700)
        self.assertIsNone(self.claude_stop(f'{LONG}\n{own.as_uri()}', prompt_id='p-long',
                                           transcript_path=self.transcript('p-long', started_ago=7200)))
        future = self.page('20261113-future.html', age=-3600)
        reply = self.claude_stop(f'{LONG}\n{future.as_uri()}', prompt_id='p-future',
                                 transcript_path=self.transcript('p-future', started_ago=7200))
        self.assertEqual(reply['decision'], 'block')

    def test_without_a_found_turn_start_no_page_exempts(self):
        # Re-check finding: a time-based fallback let an unrelated page exempt.
        page = self.page('fresh.html', age=10)
        older_format = self.root / 'older-format.jsonl'
        older_format.write_text(json.dumps({'type': 'user', 'timestamp': stamp(30),
                                            'message': {'content': 'do it'}}) + '\n')
        for index, path in enumerate((None, str(self.root / 'missing.jsonl'), str(older_format))):
            with self.subTest(path=path):
                event = {'prompt_id': f'p-none-{index}'}
                if path:
                    event['transcript_path'] = path
                reply = self.claude_stop(f'{LONG}\n{page.as_uri()}', **event)
                self.assertEqual(reply['decision'], 'block')

    def test_turn_start_far_back_in_a_long_transcript_is_found(self):
        # Re-check finding: the start row lay outside the old 4 MB tail.
        transcript = self.transcript('p-far', started_ago=30, filler_mb=10)
        unrelated = self.page('unrelated.html', age=5 * 60)
        reply = self.claude_stop(f'{LONG}\n{unrelated.as_uri()}', prompt_id='p-far',
                                 transcript_path=transcript)
        self.assertEqual(reply['decision'], 'block')
        transcript = self.transcript('p-far-own', started_ago=30, filler_mb=10)
        own = self.page('own.html', age=10)
        self.assertIsNone(self.claude_stop(f'{LONG}\n{own.as_uri()}', prompt_id='p-far-own',
                                           transcript_path=transcript))

    def test_codex_turn_start_comes_from_its_task_started_event(self):
        own = self.page('codex-own.html', age=3700)
        self.assertIsNone(self.codex_stop(f'{LONG}\n{own.as_uri()}', 'cli', started_ago=7200))
        before = self.page('codex-before.html', age=8000)
        self.assertIsNotNone(self.codex_stop(f'{LONG}\n{before.as_uri()}', 'cli', started_ago=7200))

    def test_links_outside_the_local_store_never_exempt(self):
        # Another host's path is not looked up by its file name here.
        self.page('shared-name.html')
        for index, link in enumerate(('file:///Users/nobody/.agent/artifacts/shared-name.html',
                                      f'file://{self.artifacts}/../../outside.html')):
            (self.home / 'outside.html').write_text('x')
            prompt = f'p-outside-{index}'
            reply = self.claude_stop(f'{LONG}\n{link}', prompt_id=prompt,
                                     transcript_path=self.transcript(prompt, started_ago=60))
            self.assertEqual(reply['decision'], 'block')

    def test_link_to_an_older_or_missing_page_does_not_exempt(self):
        # The audit's repro: citing any earlier page let a 600-word reply through.
        old = self.page('20261101-old.html', age=2 * 3600)
        for link in (old.as_uri(), 'file:///Users/nobody/.agent/artifacts/20261101-gone.html'):
            with self.subTest(link=link):
                reply = self.claude_stop(f'See the page: {link}\n\n' + 'word ' * 600)
                self.assertEqual(reply['decision'], 'block')

    def test_unclosed_fence_counts_its_lines(self):
        # The audit's repro: everything after an unclosed fence counted as nothing.
        reply = self.claude_stop('Intro line.\n```\n' + '\n'.join('word ' * 20 for _ in range(60)))
        self.assertIn('about 1202 words', reply['reason'])

    def test_long_punctuation_runs_are_measured_within_the_hook_timeout(self):
        # The audit's repro: 40k box-drawing characters took 10 s to count.
        # Review finding: unterminated link destinations ("[a](" 80k times)
        # were rescanned on every attempt.
        for run, words in (('─' * 40000, 501), ('[' * 40000, 501), ('*' * 40000, 501),
                           ('[a](' * 40000, 502), ('[a](' * 80000, 502)):
            with self.subTest(run=run[:4], length=len(run)):
                started = time.monotonic()
                reply = self.claude_stop(f'Table:\n{run}\n' + LONG)
                self.assertLess(time.monotonic() - started, 3)
                self.assertIn(f'about {words} words', reply['reason'])

    def test_continuation_without_a_prompt_id_is_never_blocked(self):
        self.assertIsNone(self.claude_stop(LONG, stop_hook_active=True))

    def test_reply_after_another_hooks_block_is_checked_and_blocked_once(self):
        # The audit's repro: a long reply written after another hook's block passed unchecked.
        reply = self.claude_stop(LONG, stop_hook_active=True, prompt_id='p1')
        self.assertEqual(reply['decision'], 'block')
        self.assertIsNone(self.claude_stop(LONG, stop_hook_active=True, prompt_id='p1'))
        # Another session's ledger is its own.
        self.assertIsNotNone(self.claude_stop(LONG, stop_hook_active=True, prompt_id='p1',
                                              session_id='other'))

    def test_own_block_is_not_repeated_in_its_continuations(self):
        self.assertEqual(self.claude_stop(LONG, prompt_id='p2')['decision'], 'block')
        for _ in range(2):
            self.assertIsNone(self.claude_stop(LONG, stop_hook_active=True, prompt_id='p2'))
        # The next prompt starts a new chain.
        self.assertIsNotNone(self.claude_stop(LONG, stop_hook_active=True, prompt_id='p3'))

    def test_two_ordinary_stops_of_one_prompt_block_once(self):
        # Review finding: the ledger was consulted only on continuations.
        self.assertEqual(self.claude_stop(LONG, prompt_id='p4')['decision'], 'block')
        self.assertIsNone(self.claude_stop(LONG, prompt_id='p4'))

    def test_simultaneous_stops_of_one_prompt_block_once(self):
        # Review finding: four simultaneous runs all blocked.
        with ThreadPoolExecutor(max_workers=6) as pool:
            replies = list(pool.map(lambda _: self.claude_stop(LONG, prompt_id='p5'), range(6)))
        self.assertEqual(sum(reply is not None for reply in replies), 1)
        ledgers = list((self.home / '.agent' / 'long-reply-guard').glob('*.json'))
        self.assertEqual([json.loads(path.read_text()) for path in ledgers], [['p5']])

    def test_prune_leaves_an_aged_held_lock_and_claims_still_serialise(self):
        # Re-check finding: pruning unlinked aged lock files, even held ones.
        self.assertIsNotNone(self.claude_stop(LONG, prompt_id='p6', session_id='first'))
        folder = self.home / '.agent' / 'long-reply-guard'
        (lock_file,) = folder.glob('*.lock')
        aged = time.time() - 40 * 86400
        for path in folder.iterdir():
            os.utime(path, (aged, aged))
        inode = lock_file.stat().st_ino
        with lock_file.open('a') as held:
            fcntl.flock(held, fcntl.LOCK_EX)
            # Another session's block prunes the folder; one on a different
            # stripe is found by trying a few session names.
            for index in range(20):
                if self.claude_stop(LONG, prompt_id='p7', session_id=f'other-{index}'):
                    break
            else:
                self.fail('no other session could claim a block')
            # The prune ran (the aged ledger is gone) and left the held lock.
            ledgers = [json.loads(path.read_text()) for path in folder.glob('*.json')]
            self.assertEqual(ledgers, [['p7']])
            self.assertEqual(lock_file.stat().st_ino, inode)
        with ThreadPoolExecutor(max_workers=6) as pool:
            replies = list(pool.map(
                lambda _: self.claude_stop(LONG, prompt_id='p8', session_id='first'), range(6)))
        self.assertEqual(sum(reply is not None for reply in replies), 1)

    def test_unwritable_ledger_never_blocks_a_claude_reply_with_a_prompt_id(self):
        (self.home / '.agent' / 'long-reply-guard').write_text('not a directory')
        self.assertIsNone(self.claude_stop(LONG, prompt_id='p1'))
        self.assertIsNone(self.claude_stop(LONG, stop_hook_active=True, prompt_id='p1'))
        self.assertIsNotNone(self.claude_stop(LONG))

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
