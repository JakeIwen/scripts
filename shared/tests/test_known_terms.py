"""Exercise the known-terms list, its +known hook and the merge with the peer copy."""

import fcntl
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import time
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / 'sh' / 'known_terms.py'

# Stands in for ssh: runs the remote command locally from the fake peer's home,
# or fails like an unreachable host when the peer home is missing.
# FAKE_SSH_READ_DELAY slows the peer read, widening a concurrent change's window.
FAKE_SSH = """#!/bin/sh
[ -d "$FAKE_PEER_HOME" ] || exit 255
for last; do :; done
case "$last" in cat*) sleep "${FAKE_SSH_READ_DELAY:-0}" ;; esac
cd "$FAKE_PEER_HOME" && exec sh -c "$last"
"""

# Term names the fixture glossary holds; +known recognises these without quotes.
GLOSSARY = ('idempotent', 'race condition', 'Trie', 'fixture', 'coupling', 'cohesion', 'memoization',
            'seam', 'adapter', 'observer', 'additive increase, decrease on success')


class KnownTermsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.glossary_file = root / 'mac' / '.agent' / 'glossary' / 'terms.jsonl'
        self.glossary(*GLOSSARY)
        self.file = root / 'mac' / '.claude' / 'KNOWN-TERMS.md'
        self.peer_home = root / 'pi'
        self.peer_file = self.peer_home / '.claude' / 'KNOWN-TERMS.md'
        self.seen_dir = root / 'seen'
        bin_dir = root / 'bin'
        bin_dir.mkdir()
        (bin_dir / 'ssh').write_text(FAKE_SSH)
        (bin_dir / 'ssh').chmod(0o755)
        # HOME too, so a default path the script falls back to never names real state.
        self.env = {**os.environ, 'PATH': f"{bin_dir}:{os.environ['PATH']}", 'HOME': str(root / 'mac'),
                    'FAKE_PEER_HOME': str(self.peer_home)}

    def glossary(self, *names):
        """Write the fixture glossary store, one record per term name, plus a junk line."""
        self.glossary_file.parent.mkdir(parents=True, exist_ok=True)
        rows = [json.dumps({'id': str(n), 'term': name, 'definition': 'd'}) for n, name in enumerate(names)]
        self.glossary_file.write_text('\n'.join(rows + ['not json', '[1]']) + '\n')

    def run_script(self, *args, stdin='', peer='pi'):
        return subprocess.run([sys.executable, str(SCRIPT), '--file', str(self.file), '--peer', peer,
                               '--seen-dir', str(self.seen_dir), *args],
                              input=stdin, capture_output=True, text=True, check=False, env=self.env)

    def hook(self, prompt, peer='pi', **event):
        event = {'prompt': prompt, 'hook_event_name': 'UserPromptSubmit', **event}
        result = self.run_script('--hook', stdin=json.dumps(event), peer=peer)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout) if result.stdout else None

    def terms(self, path):
        return [line[2:] for line in path.read_text().splitlines() if line.startswith('- ')]

    def test_hook_records_terms_and_blocks_the_prompt(self):
        self.peer_home.mkdir()
        reply = self.hook('+known idempotent, `race condition`; Trie')
        self.assertEqual(reply['decision'], 'block')
        self.assertIn('Marked known: idempotent, race condition, Trie', reply['reason'])
        self.assertIn('3 known terms; synced with pi', reply['reason'])
        undo = shlex.join([sys.executable, str(SCRIPT), 'remove', '--', 'idempotent', 'race condition', 'Trie'])
        self.assertTrue(reply['reason'].endswith(f'Undo with: {undo}'), reply['reason'])
        self.assertEqual(self.terms(self.file), ['idempotent', 'race condition', 'Trie'])
        self.assertEqual(self.terms(self.peer_file), self.terms(self.file))
        self.assertFalse(self.file.read_text().startswith('---'))

    def test_repeated_terms_are_not_duplicated_whatever_the_case(self):
        self.hook('+known idempotent', peer='')
        reply = self.hook('+known Idempotent, fixture', peer='')
        self.assertIn('(1 already listed)', reply['reason'])
        self.assertEqual(self.terms(self.file), ['fixture', 'idempotent'])

    def test_bare_trigger_lists_terms(self):
        self.assertIn('No known terms yet', self.hook('+known', peer='')['reason'])
        self.hook('+known seam', peer='')
        self.assertEqual(self.hook('  +known  ', peer='')['reason'], 'Known terms (1): seam')

    def test_other_prompts_pass_through(self):
        for prompt in ('fix the +known bug', '+knownish', '+cmd', 'I know idempotent'):
            self.assertIsNone(self.hook(prompt, peer=''), prompt)
        self.assertFalse(self.file.exists())

    def test_change_merges_terms_added_on_the_peer(self):
        self.peer_file.parent.mkdir(parents=True)
        self.peer_file.write_text('# Known terms\n\n- coupling\n')
        self.hook('+known cohesion')
        self.assertEqual(self.terms(self.file), ['cohesion', 'coupling'])
        self.assertEqual(self.terms(self.peer_file), ['cohesion', 'coupling'])

    def test_unreachable_peer_keeps_the_change_local_until_the_next_one(self):
        reply = self.hook('+known memoization')
        self.assertIn('pi unreachable; kept local', reply['reason'])
        self.peer_home.mkdir()
        result = self.run_script('sync')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.terms(self.peer_file), ['memoization'])

    def test_remove_drops_the_term_on_both_copies(self):
        self.peer_home.mkdir()
        self.run_script('add', 'adapter', 'observer')
        result = self.run_script('remove', 'Observer')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.terms(self.file), ['adapter'])
        self.assertEqual(self.terms(self.peer_file), ['adapter'])
        self.assertEqual(self.run_script('list', peer='').stdout.strip(), 'Known terms (1): adapter')

    def context(self, reply):
        return reply['hookSpecificOutput']['additionalContext'] if reply else None

    def test_session_sees_the_list_once_then_only_later_additions(self):
        self.hook('+known coupling, fixture', peer='')
        start = self.hook('', peer='', hook_event_name='SessionStart', session_id='s1')
        self.assertEqual(start['hookSpecificOutput']['hookEventName'], 'SessionStart')
        self.assertIn('Terms Jacob already knows', self.context(start))
        self.assertIn('coupling, fixture.', self.context(start))
        self.assertIsNone(self.hook('next question', peer='', session_id='s1'))
        self.hook('+known seam', peer='', session_id='s1')
        later = self.context(self.hook('another question', peer='', session_id='s1'))
        self.assertIn('also marked these terms as known', later)
        self.assertTrue(later.endswith(': seam.'), later)
        self.assertIsNone(self.hook('and another', peer='', session_id='s1'))

    def test_compaction_or_a_new_session_gets_the_whole_list_again(self):
        self.hook('+known coupling', peer='')
        self.hook('', peer='', hook_event_name='SessionStart', session_id='s1')
        again = self.hook('', peer='', hook_event_name='SessionStart', session_id='s1')
        self.assertIn('coupling', self.context(again))
        other = self.hook('first prompt', peer='', session_id='s2')
        self.assertIn('Terms Jacob already knows', self.context(other))

    def test_empty_list_adds_no_context(self):
        self.assertIsNone(self.hook('', peer='', hook_event_name='SessionStart', session_id='s1'))
        self.assertIsNone(self.hook('hello', peer='', session_id='s1'))

    # Finding 4: the hook answers only lists of recognised or quoted terms.

    def test_prompt_that_only_starts_with_known_reaches_the_model_and_stores_nothing(self):
        prompts = (
            '+known idempotent\nCan you also explain what a mutex is and fix the retry loop in foo.py?',
            '+known how do I remove a term again?',
            '+known idempotent +lex race condition',
            '+known idempotent,+lex race condition',
            '+known idempotent;+lex race condition',
            '+known idempotent, now fix the retry loop',
            '+known please explain this command',
            '+known idempotent, update the tests',
            '+known idempotent, delete the cache',
            '+known idempotent and test it',
            '+known Why Is This Slow',
            '+known idempotent. Also fix the retry loop',
            '+known wow!',
            '+known ' + 'x' * 61,
            '+known one two three four five six seven',
            '+known ' + ', '.join(f'"t{n}"' for n in range(21)),
            '+known red\x1b[31m',
            '+known a b',
            '+known a‮b',
            '+known a\rb',
            '+known "unterminated',
            '+known "a" b',
            '+known ""',
        )
        for prompt in prompts:
            reply = self.hook(prompt, peer='')
            self.assertTrue(reply is None or 'decision' not in reply, repr(prompt))
        self.assertFalse(self.file.exists())

    def run_shell_command(self, command):
        """Run a command line the hook printed, split as a shell would, with this test's file and no peer."""
        argv = shlex.split(command)
        self.assertEqual(argv[:2], [sys.executable, str(SCRIPT)])
        argv[2:2] = ['--file', str(self.file), '--peer', '', '--seen-dir', str(self.seen_dir)]
        return subprocess.run(argv, capture_output=True, text=True, check=False, env=self.env)

    def test_unrecognised_list_items_reach_the_model_with_context_saying_so(self):
        for prompt, requested, unknown in (
                ('+known idempotent, update the tests', ['idempotent', 'update the tests'], ['update the tests']),
                ('+known idempotent, delete the cache', ['idempotent', 'delete the cache'], ['delete the cache']),
                ('+known idempotent and test it', ['idempotent and test it'], ['idempotent and test it']),
                ('+known idempotent, event sourcing', ['idempotent', 'event sourcing'], ['event sourcing'])):
            reply = self.hook(prompt, peer='')
            self.assertNotIn('decision', reply)
            note = self.context(reply)
            listed = ', '.join(json.dumps(term) for term in requested)
            self.assertIn(f"Jacob's +known message asked to mark these terms as known: {listed}. ", note)
            self.assertIn('The known-terms hook recorded none of them, because it did not recognise these '
                          '(not glossary terms, not already known, not in double quotes): '
                          + ', '.join(json.dumps(term) for term in unknown) + '. ', note)
            self.assertIn('record all of the requested terms, the recognised ones included, with '
                          f"`{shlex.join([sys.executable, str(SCRIPT), 'add', '--', *requested])}`", note)
            self.assertIn('resend the list with those items in double quotes', note)
            self.assertTrue(note.endswith('Otherwise treat the message as an ordinary request.'), note)
        self.assertFalse(self.file.exists())

    def test_the_printed_add_command_records_every_requested_term(self):
        note = self.context(self.hook('+known idempotent, event sourcing', peer=''))
        command = note.split('`')[1]
        result = self.run_shell_command(command)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.terms(self.file), ['event sourcing', 'idempotent'])

    def test_glossary_terms_record_in_any_case(self):
        reply = self.hook('+known IDEMPOTENT,  Race Condition ; trie', peer='')
        self.assertEqual(reply['decision'], 'block')
        self.assertEqual(self.terms(self.file), ['IDEMPOTENT', 'Race Condition', 'trie'])

    def test_copied_glossary_term_with_a_comma_records_whole(self):
        reply = self.hook('+known additive increase, decrease on success', peer='')
        self.assertEqual(reply['decision'], 'block')
        self.assertEqual(self.terms(self.file), ['additive increase, decrease on success'])

    def test_already_known_terms_are_recognised(self):
        self.hook('+known "event sourcing"', peer='')
        reply = self.hook('+known Event Sourcing, seam', peer='')
        self.assertIn('(1 already listed)', reply['reason'])

    def test_quoted_terms_record_as_given_within_the_bounds(self):
        reply = self.hook('+known "event sourcing", "saga; outbox", seam', peer='')
        self.assertEqual(reply['decision'], 'block')
        self.assertIn('Marked known: event sourcing, saga; outbox, seam', reply['reason'])
        self.assertEqual(self.terms(self.file), ['event sourcing', 'saga; outbox', 'seam'])
        for prompt in ('+known "' + 'x' * 61 + '"', '+known "a\x1bb"', '+known "x +lex y"',
                       '+known "one two three four five six seven"'):
            reply = self.hook(prompt, peer='')
            self.assertTrue(reply is None or 'decision' not in reply, repr(prompt))
        self.assertEqual(self.terms(self.file), ['event sourcing', 'saga; outbox', 'seam'])

    # Item 4: the CLI takes each argument as one literal term, and the printed
    # commands survive a shell whatever the terms hold.

    def test_cli_takes_each_argument_as_one_term(self):
        # argv as a shell delivers `add "saga; outbox" 'a, b' -- -x` and `remove "saga; outbox"`.
        result = self.run_script('add', 'saga; outbox', 'a, b', '--', '-x', peer='')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.terms(self.file), ['-x', 'a, b', 'saga; outbox'])
        result = self.run_script('remove', 'saga; outbox', peer='')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.terms(self.file), ['-x', 'a, b'])

    def test_the_undo_hint_runs_in_a_shell_for_awkward_terms(self):
        awkward = ['saga; outbox', "O'Brien $HOME `id`", 'a, b & c | d', '-dash']
        reply = self.hook('+known ' + ', '.join(f'"{term}"' for term in awkward) + ', seam', peer='')
        self.assertEqual(reply['decision'], 'block')
        self.assertEqual(sorted(self.terms(self.file)), sorted(awkward + ['seam']))
        undo = reply['reason'].split('Undo with: ', 1)[1]
        result = self.run_shell_command(undo)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.terms(self.file))

    def test_missing_or_unreadable_glossary_recognises_nothing(self):
        self.glossary_file.unlink()
        reply = self.hook('+known idempotent', peer='')
        self.assertIn('"idempotent"', self.context(reply))
        self.glossary_file.mkdir()
        reply = self.hook('+known idempotent', peer='')
        self.assertIn('"idempotent"', self.context(reply))
        self.assertFalse(self.file.exists())

    # Item 1: no prompt can make the parse slow.

    def test_malformed_long_lists_are_answered_quickly(self):
        for prompt in ('+known a' + ' ' * 30000 + '"', '+known a' + ' ' * 1900 + '"',
                       '+known ' + ' ,' * 990 + '"x'):
            start = time.monotonic()
            reply = self.hook(prompt, peer='')
            elapsed = time.monotonic() - start
            self.assertTrue(reply is None or 'decision' not in reply)
            self.assertLess(elapsed, 3.0, f'{len(prompt)} characters took {elapsed:.1f} s')
        self.assertFalse(self.file.exists())

    # Item 2: a corrupt or oversized glossary never crashes the hook.

    def test_corrupt_and_oversized_records_are_skipped(self):
        deep = '[' * 2000 + ']' * 2000
        huge = json.dumps({'term': 'seam', 'definition': 'x' * 20000})
        good = json.dumps({'term': 'idempotent'})
        lines = [deep, '{"term": ' + deep + '}', huge, good]
        self.glossary_file.write_bytes('\n'.join(lines).encode() + b'\n{"term": "seam\xff\xfe"}\n')
        reply = self.hook('+known idempotent', peer='')
        self.assertEqual(reply['decision'], 'block')
        reply = self.hook('+known seam', peer='')
        self.assertIn('"seam"', self.context(reply))
        self.assertEqual(self.terms(self.file), ['idempotent'])

    def test_oversized_store_recognises_nothing(self):
        record = json.dumps({'term': 'idempotent', 'definition': 'x' * 800}) + '\n'
        self.glossary_file.write_text(record * (8 * 1024 * 1024 // len(record) + 1))
        reply = self.hook('+known idempotent, "saga"', peer='')
        self.assertNotIn('decision', reply)
        self.assertIn('did not recognise these (not glossary terms, not already known, not in double '
                      'quotes): "idempotent".', self.context(reply))
        self.assertFalse(self.file.exists())

    def test_symbols_record_only_when_the_glossary_has_them(self):
        terms = ['C++', 'O(n²)', 'N+1 queries', "Amdahl's law"]
        prompt = '+known ' + ', '.join(terms)
        self.assertNotIn('decision', self.hook(prompt, peer=''))
        self.assertFalse(self.file.exists())
        self.glossary(*GLOSSARY, *terms)
        self.assertEqual(self.hook(prompt, peer='')['decision'], 'block')
        self.assertEqual(sorted(self.terms(self.file)), sorted(terms))

    def test_passed_through_known_prompt_still_gets_the_list_as_context(self):
        self.hook('+known coupling', peer='')
        reply = self.hook('+known seam\nand what is a fixture?', peer='', session_id='s1')
        self.assertNotIn('decision', reply)
        self.assertIn('coupling', self.context(reply))
        self.assertEqual(self.terms(self.file), ['coupling'])

    def test_terms_at_the_bounds_are_recorded(self):
        terms = ['Node.js', 'Post/Redirect/Get', 'TOCTOU (time-of-check to time-of-use)',
                 'a b c d e f', 'y' * 60, 'CAN bus', 'dry run', 'last write wins', 'optimistic update']
        reply = self.hook('+known ' + ', '.join(f'"{term}"' for term in terms), peer='')
        self.assertEqual(reply['decision'], 'block')
        self.assertEqual(sorted(self.terms(self.file), key=str.casefold), sorted(terms, key=str.casefold))
        twenty = ', '.join(f'"u{n}"' for n in range(20))
        self.assertEqual(self.hook('+known ' + twenty, peer='')['decision'], 'block')

    def test_cli_add_refuses_terms_over_the_bounds(self):
        result = self.run_script('add', 'fine, ' + 'z' * 61, peer='')
        self.assertEqual(result.returncode, 2)
        self.assertIn('refused', result.stderr)
        self.assertFalse(self.file.exists())

    def test_bad_payloads_pass_quietly_and_use_up_no_terms(self):
        self.hook('+known coupling', peer='')
        for stdin in ('[1, 2]', 'not json',
                      '{"hook_event_name": "UserPromptSubmit", "prompt": 5, "session_id": "s"}',
                      '{"hook_event_name": "UserPromptSubmit", "session_id": "s"}',
                      '{"hook_event_name": "UserPromptSubmit", "prompt": "hi", "session_id": 7}',
                      '{"prompt": "hi", "session_id": "s"}'):
            result = self.run_script('--hook', stdin=stdin, peer='')
            self.assertEqual((result.returncode, result.stdout), (0, ''), stdin)
        self.assertIn('Terms Jacob already knows', self.context(self.hook('hello', peer='', session_id='s')))

    def test_lock_contention_answers_busy_and_stores_nothing(self):
        lock = self.file.with_name(self.file.name + '.lock')
        lock.parent.mkdir(parents=True)
        with lock.open('a') as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            result = self.run_script('--hook', '--hook-budget', '0.5', peer='',
                                     stdin=json.dumps({'hook_event_name': 'UserPromptSubmit', 'prompt': '+known seam'}))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {'decision': 'block', 'reason': (
            'Another known-terms change is still running; nothing stored. Send it again.')})
        self.assertFalse(self.file.exists())

    def test_change_with_too_little_time_for_the_peer_stays_local(self):
        self.peer_home.mkdir()
        result = self.run_script('--hook', '--hook-budget', '1', peer='pi',
                                 stdin=json.dumps({'hook_event_name': 'UserPromptSubmit', 'prompt': '+known seam'}))
        self.assertIn('no time left to reach pi; kept local', json.loads(result.stdout)['reason'])
        self.assertEqual(self.terms(self.file), ['seam'])
        self.assertFalse(self.peer_file.exists())

    def test_unusable_lock_path_answers_clearly_and_exits_zero(self):
        self.file.with_name(self.file.name + '.lock').mkdir(parents=True)
        reply = self.hook('+known seam', peer='')
        self.assertEqual(reply['decision'], 'block')
        self.assertIn('Nothing stored', reply['reason'])
        self.assertFalse(self.file.exists())

    # Finding 5a: a prompt another hook blocks never shows its context to the model.

    def test_prompts_another_hook_blocks_do_not_use_up_new_terms(self):
        self.hook('+known coupling', peer='')
        self.hook('', peer='', hook_event_name='SessionStart', session_id='s1')
        self.hook('+known seam', peer='', session_id='s2')
        for prompt in ('+cmd', '+links', '+terms', '+terms sort', '/rm', '/rm-cancel', '/restart'):
            self.assertIsNone(self.hook(prompt, peer='', session_id='s1'), prompt)
        later = self.context(self.hook('/tmp contains the report; read it', peer='', session_id='s1'))
        self.assertTrue(later.endswith(': seam.'), later)

    def test_prompts_with_a_model_turn_get_new_terms(self):
        for number, prompt in enumerate(('/tmp contains the report; read it', '/review the diff', '/rm now',
                                         '/restartx', '+cmd please', '+links extra')):
            session = f'm{number}'
            self.hook('', peer='', hook_event_name='SessionStart', session_id=session)
            self.hook(f'+known "term{number}"', peer='')
            reply = self.hook(prompt, peer='', session_id=session)
            self.assertTrue(self.context(reply).endswith(f': term{number}.'), prompt)

    # Finding 5b: concurrent changes each keep their terms.

    def test_concurrent_changes_keep_every_term(self):
        self.peer_home.mkdir()
        env = {**self.env, 'FAKE_SSH_READ_DELAY': '0.4'}
        runs = [subprocess.Popen([sys.executable, str(SCRIPT), '--file', str(self.file), '--peer', 'pi',
                                  '--seen-dir', str(self.seen_dir), 'add', term],
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
                for term in ('alpha', 'beta', 'gamma')]
        for run in runs:
            _, err = run.communicate(timeout=30)
            self.assertEqual(run.returncode, 0, err)
        self.assertEqual(self.terms(self.file), ['alpha', 'beta', 'gamma'])
        self.assertEqual(self.terms(self.peer_file), ['alpha', 'beta', 'gamma'])


if __name__ == '__main__':
    unittest.main()
