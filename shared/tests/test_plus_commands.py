"""Exercise the + command listing and its UserPromptSubmit hook mode."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / 'sh' / 'plus_commands.py'

INSTRUCTIONS = """# Globals

## Working conventions

- `+old`: not in the section, so never listed.

## + commands (kept in sync)

Intro paragraph mentioning `+cp` in passing.

 - `+cp`: commit and push, the same as `c/p`. Follow the rules.
 - `+id`: find which sessions own the repo's
   uncommitted work. Run the script:
   - Open sessions: never listed as commands.
 - `+art`: summarize this conversation, narrowed to any focus
   (`+art just the wiring`). Write the page.

## Terminal handoffs

- `+later`: after the section, so never listed.
"""


class PlusCommandsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.file = Path(temporary.name) / 'AGENTS.md'
        self.file.write_text(INSTRUCTIONS)

    def run_script(self, *args, stdin=''):
        return subprocess.run([sys.executable, str(SCRIPT), '--file', str(self.file), *args],
                              input=stdin, capture_output=True, text=True, check=False)

    def test_lists_each_command_with_its_first_sentence(self):
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = result.stdout.splitlines()
        self.assertEqual(lines[0], '+ commands')
        self.assertEqual(lines[1:4], [
            '  +cp   Commit and push, the same as c/p.',
            "  +id   Find which sessions own the repo's uncommitted work.",
            '  +art  Summarize this conversation, narrowed to any focus (+art just the wiring).',
        ])
        self.assertTrue(lines[4].startswith('Full definitions: "+ commands" in '))
        self.assertNotIn('+old', result.stdout)
        self.assertNotIn('+later', result.stdout)

    def test_missing_section_fails_on_the_command_line(self):
        self.file.write_text('# No plus commands here\n')
        result = self.run_script()
        self.assertEqual(result.returncode, 1)
        self.assertIn("no '## + commands' section", result.stderr)

    def test_hook_blocks_a_bare_cmd_with_the_listing(self):
        result = self.run_script('--hook', stdin=json.dumps({'prompt': '  +cmd\n'}))
        self.assertEqual(result.returncode, 0, result.stderr)
        answer = json.loads(result.stdout)
        self.assertEqual(answer['decision'], 'block')
        self.assertIn('+id   Find which sessions own', answer['reason'])

    def test_hook_passes_other_prompts_through(self):
        for prompt in ('+cmd and then +cp', 'fix the build', ''):
            result = self.run_script('--hook', stdin=json.dumps({'prompt': prompt}))
            self.assertEqual((result.returncode, result.stdout), (0, ''), prompt)

    def test_hook_passes_through_when_it_cannot_answer(self):
        self.file.write_text('# No plus commands here\n')
        bare = self.run_script('--hook', stdin=json.dumps({'prompt': '+cmd'}))
        garbled = self.run_script('--hook', stdin='not json')
        self.assertEqual((bare.returncode, bare.stdout), (0, ''))
        self.assertEqual((garbled.returncode, garbled.stdout), (0, ''))


if __name__ == '__main__':
    unittest.main()
