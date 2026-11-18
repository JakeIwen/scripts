"""Public CLI/hook checks with synthetic instructions and private scratch homes."""

import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / 'sh' / 'plus_commands.py'
COMMON = ('+id', '+ho', '+ss', '+lex', '+sa', '+rcp', '+art', '+prompt')
VARIANTS = {
    'mac-claude': (*COMMON, '+clp', '+x'),
    'mac-codex': (*COMMON, '+clp'),
    'pi-claude': COMMON,
    'pi-codex': COMMON,
}


def procedure_fixture():
    return {'version': 1, 'variants': {
        variant: {name: f'- `{name}`: Fixture {variant} {name}.\n  Complete body — keep `literal`.\n'
                  for name in names}
        for variant, names in VARIANTS.items()
    }}


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
        self.home = Path(temporary.name)
        self.environment = dict(os.environ, HOME=temporary.name, CLAUDE_CODE_PLUGIN_DIRS='',
                                PYTHONDONTWRITEBYTECODE='1')
        self.file = self.home / 'AGENTS.md'
        self.file.write_text(INSTRUCTIONS)
        self.source = self.home / 'config/shared/plus-commands/procedures.json'
        self.source.parent.mkdir(parents=True)
        self.data = procedure_fixture()
        self.write_source()

    def write_source(self):
        self.source.write_text(json.dumps(self.data, ensure_ascii=False), encoding='utf-8')

    def run_script(self, *args, stdin='', platform=None, instruction_file=None, timeout=5):
        launch = [sys.executable, str(SCRIPT)]
        if platform is not None:
            # Exercise the public entry point under both host selections. The
            # module gets its own sys view so argparse's lazy stdlib imports
            # still see the actual host. No production override or real config.
            launch = [sys.executable, '-c',
                      'import sys, types, plus_commands; '
                      'plus_commands.sys = types.SimpleNamespace(**vars(sys)); '
                      'plus_commands.sys.platform = sys.argv.pop(1); '
                      'raise SystemExit(plus_commands.main(sys.argv[1:]))', platform]
        return subprocess.run([*launch, '--file', str(instruction_file or self.file), *args],
                              input=stdin, capture_output=True, text=True, check=False,
                              env=self.environment, cwd=SCRIPT.parent, timeout=timeout)

    def run_hook(self, prompt, **kwargs):
        return self.run_script('--hook', stdin=json.dumps({
            'hook_event_name': 'UserPromptSubmit', 'prompt': prompt,
        }), **kwargs)

    def assert_context(self, prompt, names, variant='mac-claude'):
        platform = 'darwin' if variant.startswith('mac-') else 'linux'
        instruction_file = (self.home / '.codex/AGENTS.md'
                            if variant.endswith('-codex') else self.file)
        result = self.run_hook(prompt, platform=platform, instruction_file=instruction_file)
        self.assertEqual((result.returncode, result.stderr), (0, ''), result.stderr)
        expected = '\n\n'.join(f'{name} procedure ({variant}):\n{self.data["variants"][variant][name]}'
                               for name in names)
        self.assertEqual(json.loads(result.stdout), {'hookSpecificOutput': {
            'hookEventName': 'UserPromptSubmit', 'additionalContext': expected,
        }})
        return expected

    def assert_quiet(self, prompt, **kwargs):
        result = self.run_hook(prompt, **kwargs)
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, '', ''), prompt)

    def assert_bad_source(self):
        self.assert_quiet('+id')
        result = self.run_script('--procedure', '+id')
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(result.stdout, '')
        self.assertIn('cannot load procedure from', result.stderr)
        self.assertNotIn('Traceback', result.stderr)

    def test_lists_each_command_alphabetically_with_its_first_sentence(self):
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = result.stdout.splitlines()
        self.assertEqual(lines[0], '+ commands')
        self.assertEqual(lines[1:4], [
            '  +art  Summarize this conversation, narrowed to any focus (+art just the wiring).',
            '  +cp   Commit and push, the same as c/p.',
            "  +id   Find which sessions own the repo's uncommitted work.",
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


    def test_listing_and_bare_cmd_are_byte_identical_even_with_procedure_data(self):
        expected = (
            '+ commands\n'
            '  +art  Summarize this conversation, narrowed to any focus (+art just the wiring).\n'
            '  +cp   Commit and push, the same as c/p.\n'
            "  +id   Find which sessions own the repo's uncommitted work.\n"
            'Full definitions: "+ commands" in ~/AGENTS.md'
        )
        for raw in (self.source.read_text(), 'malformed source'):
            with self.subTest(source=raw[:20]):
                self.source.write_text(raw)
                self.assertEqual(self.run_script().stdout, expected + '\n')
                result = self.run_hook(' \n+cmd\n ')
                self.assertEqual(json.loads(result.stdout), {'decision': 'block', 'reason': expected})

    def test_every_applicable_command_in_all_four_variants(self):
        for variant, names in VARIANTS.items():
            with self.subTest(variant=variant):
                expected = self.assert_context(' '.join(names), names, variant)
                file = self.home / '.codex/AGENTS.md' if variant.endswith('codex') else self.file
                result = self.run_script(*[arg for name in names for arg in ('--procedure', name)],
                                         platform='darwin' if variant.startswith('mac') else 'linux',
                                         instruction_file=file)
                self.assertEqual((result.returncode, result.stdout, result.stderr),
                                 (0, expected + '\n', ''))

    def test_cmd_combination_is_context_only_and_preserves_first_appearance(self):
        self.assert_context('+cmd +rcp +id focus +rcp +sa +cp +art',
                            ['+rcp', '+id', '+sa', '+art'])
        self.assert_context('Review the build +sa +id +sa', ['+sa', '+id'])
        self.assert_context('+id\n+prompt follow-up\n+id', ['+id', '+prompt'])
        self.assert_context('+rcp', ['+rcp'])  # no speculative +id chain expansion
        self.assert_context('+ho', ['+ho'])    # internal +prompt uses explicit fallback

    def test_cli_dedupes_context_without_executing_or_rewriting_source(self):
        sentinel = self.home / 'must-not-exist'
        body = f'- `+id`: Synthetic procedure.\n  touch {sentinel}\n'
        self.data['variants']['mac-claude']['+id'] = body
        self.write_source()
        before = self.source.read_bytes()
        expected = self.assert_context('+id +id', ['+id'])
        result = self.run_script('--procedure', '+id', '--procedure', '+id', platform='darwin')
        self.assertEqual(result.stdout, expected + '\n')
        self.assertEqual(self.source.read_bytes(), before)
        self.assertFalse(sentinel.exists())

    def test_whitespace_boundaries_not_punctuation_or_substrings(self):
        for prompt in ('+identifier', 'prefix+id', '+id-suffix', '+id.', '+id,',
                       '(+id)', '[+id]', '+id/+sa', '\\+id', '+ID', '+unknown',
                       'x+id +else', '+cp +cpd +n', 'read /tmp/+id'):
            with self.subTest(prompt=prompt):
                self.assert_quiet(prompt)
        self.assert_context('   +id\t+sa\r\n+art', ['+id', '+sa', '+art'])

    def test_prose_mentions_do_not_become_commands(self):
        for prompt in ('What does +id do?', 'Document the +ho command here.',
                       'The +sa flag needs explanation.', 'Use +prompt as an example.',
                       'Please explain +id and +ho without executing them.'):
            with self.subTest(prompt=prompt):
                self.assert_quiet(prompt)
        self.assert_context('Discuss +id in the documentation +art', ['+art'])

    def test_command_led_focus_mentions_do_not_become_commands(self):
        for variant in VARIANTS:
            options = {'platform': 'darwin' if variant.startswith('mac') else 'linux',
                       'instruction_file': (self.home / '.codex/AGENTS.md'
                                            if variant.endswith('codex') else self.file)}
            for prompt in ('+ir why does +ho hang', '+ir the +x hook never fires',
                           '+ir investigate why +id is slow'):
                with self.subTest(variant=variant, prompt=prompt):
                    self.assert_quiet(prompt, **options)
            with self.subTest(variant=variant):
                self.assert_context('+id investigate why +ho hangs', ['+id'], variant)

    def test_leading_commands_and_trailing_suffix_exclude_focus_mentions(self):
        for prompt in ('+id +sa investigate why +ho hangs +rcp +art',
                       '+id\t+sa investigate why +ho hangs\t+rcp\t+art'):
            with self.subTest(prompt=prompt):
                self.assert_context(prompt, ['+id', '+sa', '+rcp', '+art'])
        self.assert_context('+sa inspect +id while +ho hangs +rcp +id +sa',
                            ['+sa', '+rcp', '+id'])

    def test_quotes_inline_code_and_markdown_code_are_not_triggers(self):
        for prompt in ('`+id`', '`` +id ``', '" +id "', "' +id '", '“ +id ”', '‘ +id ’',
                       '> +id', '  > +id', '    +id', '\t+id',
                       '```text\n+id\n```', '~~~\n+id\n~~~', '```\n+id',
                       '````\n```\n+id\n````', '"quote\n+id\nend"',
                       '"unterminated quote\n+id', '`unterminated\n+id',
                       '"quoted"+id', '`quoted`+id', '"escaped \\" +id "'):
            with self.subTest(prompt=prompt):
                self.assert_quiet(prompt)
        self.assert_context('+id " +ho " ` +art ` +sa', ['+id', '+sa'])
        self.assert_context('+id\n```text\n+ho\n```\n+sa', ['+id', '+sa'])
        self.assert_context("+id don't confuse apostrophes +sa", ['+id', '+sa'])
        self.assert_context('" +id "\n+sa', ['+sa'])

    def test_other_blocking_hooks_keep_their_prompts(self):
        for prompt in ('+links', '+known', '+known " +id "', '+terms', '+terms +id',
                       '+terms search\n+id', '/rm', '/rm-cancel', '/restart'):
            with self.subTest(prompt=prompt):
                self.assert_quiet(prompt)
        self.assert_context('+links +id', ['+id'])  # links only blocks bare +links
        self.assert_context('+known +id', ['+id'])  # mixed commands reach the model
        self.assert_context('/restart +id', ['+id'])  # restart only blocks exact /restart

    def test_host_and_engine_are_not_selected_from_prompt_content(self):
        self.assert_context('+id mac-codex pi-claude ~/.codex/AGENTS.md', ['+id'])
        self.assert_context('+id use mac-claude instead', ['+id'], 'pi-codex')
        # A similarly named directory is not the established Codex --file boundary.
        result = self.run_hook('+id', platform='darwin',
                               instruction_file=self.home / '.codex-other/AGENTS.md')
        self.assertIn('+id procedure (mac-claude):', result.stdout)

    def test_absent_commands_fail_clearly_only_on_explicit_cli(self):
        for variant, name in (('mac-codex', '+x'), ('pi-claude', '+clp'),
                              ('pi-claude', '+x'), ('pi-codex', '+clp'),
                              ('mac-claude', '+unknown')):
            with self.subTest(variant=variant, name=name):
                options = {'platform': 'darwin' if variant.startswith('mac') else 'linux',
                           'instruction_file': (self.home / '.codex/AGENTS.md'
                                                if variant.endswith('codex') else self.file)}
                self.assert_quiet(name, **options)
                result = self.run_script('--procedure', name, **options)
                self.assertEqual((result.returncode, result.stdout), (1, ''))
                self.assertIn(f'no procedure for {name!r} in {variant}', result.stderr)

    def test_missing_source_is_inert_for_rollout_and_explicit_fallback_fails(self):
        self.source.unlink()
        self.assert_bad_source()
        self.assertIn('No such file', self.run_script('--procedure', '+id').stderr)

    def test_nonregular_source_is_rejected_without_hanging(self):
        self.source.unlink()
        self.source.mkdir()
        self.assert_bad_source()
        self.source.rmdir()
        os.mkfifo(self.source)
        self.assert_bad_source()

    def test_malformed_duplicate_deep_and_oversized_sources(self):
        cases = (b'not json', b'\xff', b'{"version":1,"version":1}',
                 b'[' * 2000 + b']' * 2000, b'x' * (256 * 1024 + 1),
                 json.dumps(self.data).replace('"version": 1', '"version": NaN').encode(),
                 json.dumps(self.data).replace('"version": 1', '"version": Infinity').encode(),
                 json.dumps(self.data).replace('"version": 1', '"version": -Infinity').encode())
        for raw in cases:
            with self.subTest(prefix=raw[:40]):
                self.source.write_bytes(raw)
                self.assert_bad_source()

    def test_schema_requires_exact_version_variants_keys_and_text(self):
        cases = [None, [], {}, {'version': 1}]
        for version in (True, False, '1', 1.0, 0, 2, None):
            cases.append(dict(self.data, version=version))
        cases.extend((dict(self.data, extra='not allowed'), dict(self.data, variants=[])))
        for change in ('missing-variant', 'extra-variant', 'bad-map', 'missing-command',
                       'extra-command', 'pi-clipboard', 'codex-exit'):
            data = copy.deepcopy(self.data)
            variants = data['variants']
            if change == 'missing-variant':
                del variants['pi-claude']
            elif change == 'extra-variant':
                variants['other'] = {}
            elif change == 'bad-map':
                variants['mac-claude'] = []
            elif change == 'missing-command':
                del variants['mac-claude']['+id']
            elif change == 'extra-command':
                variants['mac-claude']['+unknown'] = 'text'
            elif change == 'pi-clipboard':
                variants['pi-claude']['+clp'] = 'text'
            else:
                variants['mac-codex']['+x'] = 'text'
            cases.append(data)
        for text in ('', ' \n', None, [], {}, 1, True, 'é' * 8193, '\ud800', '\x00', '\x1b[2J'):
            data = copy.deepcopy(self.data)
            data['variants']['pi-codex']['+id'] = text  # validate unselected variants too
            cases.append(data)
        for index, data in enumerate(cases):
            with self.subTest(case=index):
                self.source.write_text(json.dumps(data))
                self.assert_bad_source()

    def test_duplicate_nested_command_keys_are_rejected(self):
        raw = json.dumps(self.data)
        self.source.write_text(raw.replace('"+id":', '"+id": "duplicate", "+id":', 1))
        self.assert_bad_source()

    def test_combined_output_is_bounded_and_never_partially_injected(self):
        names = VARIANTS['mac-claude']
        self.data['variants']['mac-claude'] = dict.fromkeys(names, 'x' * (7 * 1024))
        self.write_source()
        self.assert_quiet(' '.join(names), platform='darwin')
        result = self.run_script(*[arg for name in names for arg in ('--procedure', name)],
                                 platform='darwin')
        self.assertEqual((result.returncode, result.stdout), (1, ''))
        self.assertIn('context exceeds 64 KiB', result.stderr)

    def test_malformed_or_unrelated_hook_payloads_are_quiet(self):
        for data in (None, [], 3, 'string', {}, {'prompt': ['+id']},
                     {'prompt': None}, {'prompt': '+id', 'hook_event_name': 'Stop'},
                     {'prompt': '+id', 'hook_event_name': []}):
            with self.subTest(data=data):
                result = self.run_script('--hook', stdin=json.dumps(data))
                self.assertEqual((result.returncode, result.stdout, result.stderr), (0, '', ''))
        for raw in ('invalid', '[' * 2000, json.dumps({'prompt': '+id ' + 'x' * (1024 * 1024)})):
            result = self.run_script('--hook', stdin=raw)
            self.assertEqual((result.returncode, result.stdout, result.stderr), (0, '', ''))
        # Prefix-only events remain supported for existing callers.
        result = self.run_script('--hook', stdin=json.dumps({'prompt': '+id'}))
        self.assertIn('additionalContext', result.stdout)

    def test_long_noncommand_suffix_has_no_unbounded_backtracking(self):
        self.assert_quiet('Explain ' + '+id ' * 40000 + 'in this document.', timeout=3)

    def test_unsupported_platform_and_cli_mode_conflicts(self):
        self.assert_quiet('+id', platform='win32')
        result = self.run_script('--procedure', '+id', platform='win32')
        self.assertEqual(result.returncode, 1)
        self.assertIn('only Darwin and Linux', result.stderr)
        result = self.run_script('--hook', '--procedure', '+id')
        self.assertEqual(result.returncode, 2)
        result = self.run_script('--help')
        self.assertEqual(result.returncode, 0)
        self.assertIn('--procedure', result.stdout)


if __name__ == '__main__':
    unittest.main()
