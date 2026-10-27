"""Exercise the known-terms list, its +known hook and the merge with the peer copy."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / 'sh' / 'known_terms.py'

# Stands in for ssh: runs the remote command locally from the fake peer's home,
# or fails like an unreachable host when the peer home is missing.
FAKE_SSH = """#!/bin/sh
[ -d "$FAKE_PEER_HOME" ] || exit 255
for last; do :; done
cd "$FAKE_PEER_HOME" && exec sh -c "$last"
"""


class KnownTermsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.file = root / 'mac' / '.claude' / 'KNOWN-TERMS.md'
        self.peer_home = root / 'pi'
        self.peer_file = self.peer_home / '.claude' / 'KNOWN-TERMS.md'
        self.seen_dir = root / 'seen'
        bin_dir = root / 'bin'
        bin_dir.mkdir()
        (bin_dir / 'ssh').write_text(FAKE_SSH)
        (bin_dir / 'ssh').chmod(0o755)
        self.env = {**os.environ, 'PATH': f"{bin_dir}:{os.environ['PATH']}",
                    'FAKE_PEER_HOME': str(self.peer_home)}

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
        self.run_script('add', 'adapter, observer')
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


if __name__ == '__main__':
    unittest.main()
