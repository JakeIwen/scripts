"""Exercise slash-command discovery through +cmd with an isolated user home."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


SCRIPTS = Path(__file__).resolve().parents[1] / "sh"
INSTRUCTIONS = "## + commands\n\n- `+z`: last command.\n- `+a`: first command.\n"


class SlashCommandsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.environment = dict(os.environ, HOME=str(self.home), CLAUDE_CODE_PLUGIN_DIRS="")
        self.instructions = self.write(".claude/CLAUDE.md", INSTRUCTIONS)
        self.codex = self.write(".codex/AGENTS.md", INSTRUCTIONS)

    def write(self, relative, content):
        path = self.home / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def mod(self, name, source):
        directory = self.home / "mods" / name
        self.write(directory / "hooks/hooks.json", json.dumps({"modules": ["./register.ts"]}))
        self.write(directory / "hooks/register.ts", source)
        return directory

    def run_script(self, *args, stdin="", script=None):
        return subprocess.run(
            [sys.executable, str(script or SCRIPTS / "plus_commands.py"), *args],
            input=stdin, capture_output=True, text=True, check=False,
            env=self.environment, cwd=self.home,
        )

    def output(self, *args):
        result = self.run_script(*args)
        self.assertEqual((result.returncode, result.stderr), (0, ""))
        return result.stdout

    def test_markdown_commands_are_sorted_aligned_and_follow_symlinks(self):
        before = self.output()
        self.write(".claude/commands/zulu.md", "---\ndescription: Last: keep the colon.\n---\nIgnored body")
        target = self.write("config/alpha.md", "---\ndescription: 'It''s first.'\n---\nBody")
        (self.home / ".claude/commands/alpha.md").symlink_to(target)
        self.write(".claude/commands/nested/hidden.md", "---\ndescription: Not top-level\n---")
        self.write(".claude/skills/ignored/SKILL.md", "---\ndescription: Not a command\n---")
        output = self.output()
        self.assertEqual(output, before + "\nSlash commands\n  /alpha  It's first.\n  /zulu   Last: keep the colon.\n")

    def test_frontmatter_description_scalars_and_blocks_stay_on_one_line(self):
        descriptions = {
            "double": ('"Quoted \\"text\\" and \\u00e9"', 'Quoted "text" and é'),
            "folded": (">-\n  Two lines\n  fold together.", "Two lines fold together."),
            "literal": ("|\n  Line one.\n  Line two.", "Line one. Line two."),
            "plain": ("Keep #inside but not this # comment", "Keep"),
        }
        for name, (value, _) in descriptions.items():
            self.write(f".claude/commands/{name}.md", f"---\ndescription: {value}\n---\nNot the summary")
        output = self.output()
        for name, (_, summary) in descriptions.items():
            self.assertIn(f"  /{name.ljust(7)}  {summary}\n", output)
        self.assertNotIn("Not the summary", output)

    def test_mod_metadata_comes_from_registered_hook_modules_and_env_paths(self):
        first = self.mod("first", r'''
// $.command.register({name: 'comment', description: 'not active'})
const example = "$.command.register({name: 'string', description: 'not active'})"
await $.command.register({
  name: 'zebra',
  handler: () => ({name: 'nested', description: 'not metadata'}),
  description: "Show {braces}, commas, and \"quotes\".",
})
await $.command.register({ name: 'e', description: 'Set effort: l|m|h|x|M', argumentHint: 'l|m|h|x|M' })
''')
        second = self.mod("second", "$.command.register({ 'name': `alpha`, description: 'It\\'s \\u00e9.', argumentHint: '[N]' })")
        self.write(first / "hooks/not-loaded.ts", "$.command.register({name:'hidden',description:'Not registered'})")
        ignored = self.mod("ignored", "$.command.register({name:'ignored',description:'Settings lose to env'})")
        self.write(".claude/settings.json", json.dumps({"env": {"CLAUDE_CODE_PLUGIN_DIRS": str(ignored)}}))
        self.environment["CLAUDE_CODE_PLUGIN_DIRS"] = f"~/mods/first:{second}:{first}"
        block = self.output().split("\nSlash commands\n")[1]
        self.assertEqual(block.splitlines(), [
            "  /alpha [N]    It's é.",
            "  /e l|m|h|x|M  Set effort: l|m|h|x|M",
            '  /zebra        Show {braces}, commas, and "quotes".',
        ])

    def test_settings_supply_mod_paths_only_when_env_is_absent(self):
        directory = self.mod("settings", "$.command.register({name:'from-settings',description:'From settings'})")
        self.write(".claude/settings.json", json.dumps({"env": {"CLAUDE_CODE_PLUGIN_DIRS": str(directory)}}))
        self.assertNotIn("Slash commands", self.output())
        del self.environment["CLAUDE_CODE_PLUGIN_DIRS"]
        self.assertIn("  /from-settings  From settings\n", self.output())

    def test_unparseable_registrations_are_skipped_without_losing_valid_calls(self):
        directory = self.mod("mixed", r'''
$.command.register({name: dynamicName, description: 'Dynamic name'})
$.command.register({name: 'concat', description: 'Two ' + 'strings'})
$.command.register({name: 'interpolation', description: `Hello ${person}`})
$.command.register({name: 'hint', description: 'Dynamic hint', argumentHint: hint})
$.command.register({...other, name: 'spread', description: 'Unknown metadata'})
$.command.register({name: 'overflow', description: '\u{110000}'})
$.command.register({name: 'missing-description'})
$.command.register({name: 'good', description: 'Still listed'})
$.command.register({name: 'incomplete', description: 'Unfinished'
''')
        self.environment["CLAUDE_CODE_PLUGIN_DIRS"] = str(directory)
        self.assertEqual(self.output().split("\nSlash commands\n")[1], "  /good  Still listed\n")

    def test_unreadable_and_malformed_sources_do_not_break_the_plus_block(self):
        before = self.output()
        commands = self.home / ".claude/commands"
        commands.mkdir()
        (commands / "directory.md").mkdir()
        (commands / "broken.md").symlink_to(self.home / "missing.md")
        (commands / "bad-encoding.md").write_bytes(b"\xff")
        self.write(commands / "missing-frontmatter.md", "description: Not frontmatter")
        self.write(commands / "incomplete.md", "---\ndescription: Missing end marker")
        self.write(commands / "bad-description.md", '---\ndescription: "Unclosed\n---')
        self.write(".claude/settings.json", "not json")
        del self.environment["CLAUDE_CODE_PLUGIN_DIRS"]
        self.assertEqual(self.output(), before)
        malformed = self.mod("malformed", "not code")
        self.write(malformed / "hooks/hooks.json", "not json")
        unreadable = self.mod("unreadable", "")
        (unreadable / "hooks/register.ts").unlink()
        (unreadable / "hooks/register.ts").mkdir()
        self.environment["CLAUDE_CODE_PLUGIN_DIRS"] = f"{malformed}:{unreadable}:{self.home / 'absent'}"
        self.assertEqual(self.output(), before)
        self.write(commands / "valid.md", "---\ndescription: Valid source survives\n---")
        self.write(unreadable / "hooks/hooks.json", json.dumps({"modules": ["bad\0path"]}))
        self.environment["CLAUDE_CODE_PLUGIN_DIRS"] += ":~no-such-slash-command-user/mod"
        self.assertIn("  /valid  Valid source survives\n", self.output())

    def test_wrong_settings_and_manifest_types_are_ignored(self):
        del self.environment["CLAUDE_CODE_PLUGIN_DIRS"]
        for value in ([], {"env": []}, {"env": {"CLAUDE_CODE_PLUGIN_DIRS": []}}):
            with self.subTest(settings=value):
                self.write(".claude/settings.json", json.dumps(value))
                self.assertNotIn("Slash commands", self.output())
        directory = self.mod("invalid-manifest", "")
        self.environment["CLAUDE_CODE_PLUGIN_DIRS"] = str(directory)
        for value in ([], {"modules": "./register.ts"}, {"modules": [None, 4, {}]}):
            with self.subTest(manifest=value):
                self.write(directory / "hooks/hooks.json", json.dumps(value))
                self.assertNotIn("Slash commands", self.output())

    def test_codex_lists_only_its_prompts_and_omits_an_empty_block(self):
        self.write(".claude/commands/claude-only.md", "---\ndescription: Not Codex\n---")
        mod = self.mod("claude", "$.command.register({name:'mod-only',description:'Not Codex'})")
        self.environment["CLAUDE_CODE_PLUGIN_DIRS"] = str(mod)
        before = self.output("--file", str(self.codex))
        self.assertNotIn("Slash commands", before)
        self.write(".codex/prompts/zeta.md", "---\ndescription: Last prompt\n---")
        self.write(".codex/prompts/alpha.md", "---\ndescription: First prompt\n---")
        output = self.output("--file", str(self.codex))
        self.assertEqual(output, before + "\nSlash commands\n  /prompts:alpha  First prompt\n  /prompts:zeta   Last prompt\n")
        sibling = self.write(".codex-other/AGENTS.md", INSTRUCTIONS)
        self.assertIn("/claude-only", self.output("--file", str(sibling)))

    def test_hook_and_plain_modes_share_the_same_listing_for_both_providers(self):
        self.write(".claude/commands/local.md", "---\ndescription: Local command\n---")
        self.write(".codex/prompts/local.md", "---\ndescription: Local prompt\n---")
        for path in (self.instructions, self.codex):
            with self.subTest(provider=path.parent.name):
                arguments = ("--file", str(path))
                plain = self.output(*arguments)
                hook = self.run_script(*arguments, "--hook", stdin=json.dumps({
                    "prompt": "+cmd", "hook_event_name": "UserPromptSubmit",
                }))
                self.assertEqual(hook.returncode, 0, hook.stderr)
                self.assertEqual(json.loads(hook.stdout), {"decision": "block", "reason": plain.rstrip("\n")})
                other = self.run_script(*arguments, "--hook", stdin=json.dumps({"prompt": "+cmd and more"}))
                self.assertEqual((other.returncode, other.stdout, other.stderr), (0, "", ""))

    def test_flat_deployment_can_import_its_sibling_from_an_unrelated_directory(self):
        flat = self.home / "deployed/scripts"
        flat.mkdir(parents=True)
        for filename in ("plus_commands.py", "slash_commands.py"):
            shutil.copy2(SCRIPTS / filename, flat / filename)
        self.write(".claude/commands/local.md", "---\ndescription: Flat deployment works\n---")
        result = self.run_script(script=flat / "plus_commands.py")
        self.assertEqual((result.returncode, result.stderr), (0, ""))
        self.assertIn("  /local  Flat deployment works\n", result.stdout)


if __name__ == "__main__":
    unittest.main()
