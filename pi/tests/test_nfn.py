import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPOSITORY_ROOT / "scripts/nfn.py"
HEADER = "### NFN ### (added by nfn)"
BOUNDARY = "# Codex command shells need helpers, not secrets, DISPLAY or command shadows.\n"
CORE = '### CORE ###\nnfnexample() { git status "$@"; }\nls_all() { ls -a; }\n\n'
BASE = (CORE + BOUNDARY + '[[ -n ${CODEX_THREAD_ID-} && $- != *i* ]] && return\n'
        "shopt -s expand_aliases\nalias sudo='sudo '\n"
        'grep() { command grep "$@"; }\n'
        'codex() {\n  if false; then\n    echo "}"\n  fi\n}\n'
        'export NFN_TEST_ENV=loaded\n')

# Stands in for `gh api`: PUT compares the blob SHA before accepting bytes.
FAKE_GH = r'''
import base64, hashlib, json, os, sys
from pathlib import Path
state = Path(os.environ["FAKE_GH_STATE"])
remote = state / "remote"
def sha(data):
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()
with (state / "calls").open("a") as log:
    log.write(json.dumps(sys.argv[1:]) + "\n")
if (state / "offline").exists():
    sys.exit("error connecting to api.github.com")
if "PUT" in sys.argv:
    body = json.load(sys.stdin)
    (state / "put.json").write_text(json.dumps(body))
    if body["sha"] != sha(remote.read_bytes()):
        sys.exit("gh: pi/.bashrc does not match (HTTP 409)")
    data = base64.b64decode(body["content"])
    remote.write_bytes(data)
    if (state / "live-race").exists():
        (Path.home() / ".bashrc").write_bytes((state / "live-race").read_bytes())
    stored_sha = "wrong" if (state / "bad-sha").exists() else sha(data)
    print(json.dumps({"content": {"sha": stored_sha},
                      "commit": {"sha": "c0ffee", "html_url": "https://example.test/c0ffee"}}))
else:
    print(json.dumps({"sha": sha(remote.read_bytes())}))
    race = state / "race"
    if race.exists():
        remote.write_bytes(race.read_bytes())
'''


class NfnTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="nfn-test-")
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.home, self.state, self.bin = root / "home", root / "state", root / "bin"
        for directory in (self.home, self.state, self.bin):
            directory.mkdir()
        gh = self.bin / "gh"
        gh.write_text(f"#!{sys.executable}\n{FAKE_GH}")
        gh.chmod(0o755)
        self.rc, self.remote = self.home / ".bashrc", self.state / "remote"
        self.reset(BASE)

    def reset(self, text):
        self.rc.write_text(text)
        self.remote.write_text(text)

    def nfn(self, *arguments, environment=None):
        env = dict(os.environ, HOME=str(self.home), FAKE_GH_STATE=str(self.state),
                   PATH=f"{self.bin}{os.pathsep}{os.environ['PATH']}")
        if environment:
            env.update(environment)
        return subprocess.run([sys.executable, str(SCRIPT), *arguments], env=env,
                              capture_output=True, text=True)

    def calls(self):
        log = self.state / "calls"
        return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []

    def puts(self):
        return [call for call in self.calls() if "PUT" in call]

    def run_function(self, name, *arguments):
        result = subprocess.run(
            ["/bin/bash", "--norc", "--noprofile", "-c",
             '. "$1"\nshift\n"$@"', "_", str(self.rc), name, *arguments],
            env={"PATH": os.environ["PATH"]}, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def test_new_functions_are_committed_above_boundary_in_order(self):
        first = self.nfn("nfnfirst", "git push origin")
        second = self.nfn("nfnsecond", "git pull")
        self.assertEqual((first.returncode, second.returncode), (0, 0), first.stderr + second.stderr)
        self.assertIn('appending to rc: nfnfirst() { git push origin "$@"; }', first.stdout)
        self.assertIn("above the Codex boundary", first.stdout)
        self.assertEqual(self.rc.read_text(), self.remote.read_text())
        self.assertEqual(self.rc.read_text(), BASE.replace(
            BOUNDARY, HEADER + '\nnfnfirst() { git push origin "$@"; }\n'
            'nfnsecond() { git pull "$@"; }\n' + BOUNDARY))
        body = json.loads((self.state / "put.json").read_text())
        self.assertEqual(body["branch"], "master")
        self.assertEqual(body["message"], 'Pi bashrc: nfn nfnsecond\n\n'
                         'nfnsecond() { git pull "$@"; }\n')
        self.assertEqual(len(self.puts()), 2)

    def test_shadowing_names_land_at_end_of_block_with_self_call_prefix(self):
        for name, command, prefix in (("echo", "echo hello", "builtin"),
                                      ("cat", "cat -n", "command")):
            with self.subTest(name=name):
                self.reset(BASE)
                result = self.nfn(name, command)
                self.assertEqual(result.returncode, 0, result.stderr)
                line = f'{name}() {{ {prefix} {command} "$@"; }}'
                self.assertIn("below the Codex boundary", result.stdout)
                self.assertIn('}\n' + line + '\nexport NFN_TEST_ENV', self.rc.read_text())
                if name == "echo":
                    self.assertEqual(self.run_function("echo", "two words"), "hello two words\n")
        path = self.home / "two words"
        path.write_text("hello\n")
        self.assertTrue(self.run_function("cat", str(path)).rstrip().endswith("hello"))

    def test_existing_function_is_replaced_in_place_with_quotes_intact(self):
        result = self.nfn("nfnexample", "printf '%s|' one 'two three'")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('found function, replaced: nfnexample() { git status "$@"; }', result.stdout)
        self.assertEqual(self.rc.read_text().splitlines()[1],
                         'nfnexample() { printf \'%s|\' one \'two three\' "$@"; }')
        self.assertEqual(self.run_function("nfnexample", "two words"), "one|two three|two words|")
        self.assertEqual(self.rc.read_text(), self.remote.read_text())

    def test_definitions_on_wrong_side_are_moved_in_both_directions(self):
        for name, command, old, side in (
            ("cd", "cd", 'cd() { builtin cd "$@"; }\n', "below"),
            ("nfnmoved", "echo moved", 'nfnmoved() { echo moved "$@"; }\n', "above"),
        ):
            with self.subTest(name=name):
                before = BASE.replace(BOUNDARY, old + BOUNDARY) if side == "below" else BASE + old
                self.reset(before)
                result = self.nfn(name, command)
                self.assertEqual(result.returncode, 0, result.stderr)
                text = self.rc.read_text()
                self.assertEqual(text.count(name + "() {"), 1)
                self.assertEqual(text.index(name + "() {") > text.index(BOUNDARY), side == "below")
                self.assertIn(side + " the Codex boundary", result.stdout)
                self.assertEqual(text, self.remote.read_text())

    def test_ambiguous_definitions_abort_without_any_put(self):
        for definition in ('nfnexample() {\n echo hi\n}\n',
                           'function nfnexample { echo hi; }\n',
                           "alias nfnexample='echo hi'\n",
                           'nfnexample() { echo first; }\nnfnexample() { echo second; }\n',
                           'nfnexample() { echo first; }; nfnexample() { echo second; }\n'):
            with self.subTest(definition=definition):
                text = BASE.replace('nfnexample() { git status "$@"; }\n', definition)
                self.reset(text)
                result = self.nfn("nfnexample", "echo new")
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertIn("edit the file by hand", result.stderr)
                self.assertEqual(self.rc.read_text(), text)
                self.assertEqual(self.remote.read_text(), text)
                self.assertEqual(self.puts(), [])

    def test_quoted_braces_do_not_hide_or_split_definitions(self):
        old = 'nfnexample() { echo "} #quoted"; }\n'
        text = BASE.replace('nfnexample() { git status "$@"; }\n', old)
        self.reset(text)
        result = self.nfn("nfnexample", "echo replaced")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.run_function("nfnexample", "two words"), "replaced two words\n")
        self.reset(text.replace(old, old.rstrip() + '; nfnexample() { :; }\n'))
        result = self.nfn("nfnexample", "echo replaced")
        self.assertEqual(result.returncode, 1)
        self.assertIn("edit the file by hand", result.stderr)
        self.assertEqual(len(self.puts()), 1)

    def test_legacy_nalias_section_is_preserved(self):
        legacy = "\n### NALIAS ### (old section)\nalias oldexample='echo old'\n"
        self.reset(BASE + legacy)
        result = self.nfn("nfnfirst", "echo hi")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.rc.read_text().endswith(legacy))

    def test_clean_shell_ignores_bash_env_and_exported_functions(self):
        poison = self.home / "poison"
        poison.write_text('echo() { :; }\ntouch "' + str(self.home / "leaked") + '"\n')
        result = self.nfn("echo", "echo hi", environment={
            "BASH_ENV": str(poison), "BASH_FUNC_echo%%": "() { :; }"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('echo() { builtin echo hi "$@"; }', self.rc.read_text())
        self.assertFalse((self.home / "leaked").exists())

    def test_differing_copies_abort_before_any_commit(self):
        self.remote.write_text(BASE + "# newer on github\n")
        result = self.nfn("nfnfirst", "git push origin")
        self.assertEqual(result.returncode, 1)
        self.assertIn("nothing changed", result.stderr)
        self.assertIn("differs from pi/.bashrc on GitHub master", result.stderr)
        self.assertEqual(self.rc.read_text(), BASE)
        self.assertEqual(self.puts(), [])

    def test_commit_rejected_by_concurrent_change_leaves_bashrc_alone(self):
        concurrent = BASE + "# committed meanwhile\n"
        (self.state / "race").write_text(concurrent)
        result = self.nfn("nfnfirst", "git push origin")
        self.assertEqual(result.returncode, 1)
        self.assertIn("HTTP 409", result.stderr)
        self.assertEqual(self.rc.read_text(), BASE)
        self.assertEqual(self.remote.read_text(), concurrent)

    def test_unreachable_github_changes_nothing(self):
        (self.state / "offline").touch()
        result = self.nfn("nfnfirst", "git push origin")
        self.assertEqual(result.returncode, 1)
        self.assertIn("error connecting", result.stderr)
        self.assertEqual(self.rc.read_text(), BASE)
        self.assertEqual(self.remote.read_text(), BASE)

    def test_unexpected_committed_bytes_leave_live_bashrc_alone(self):
        (self.state / "bad-sha").touch()
        result = self.nfn("nfnfirst", "echo hi")
        self.assertEqual(result.returncode, 2)
        self.assertIn("GitHub stored different bytes", result.stderr)
        self.assertEqual(self.rc.read_text(), BASE)

    def test_local_race_after_commit_is_not_overwritten(self):
        concurrent = BASE + "# local edit meanwhile\n"
        (self.state / "live-race").write_text(concurrent)
        result = self.nfn("nfnfirst", "echo hi")
        self.assertEqual(result.returncode, 2)
        self.assertIn("couldn't install", result.stderr)
        self.assertEqual(self.rc.read_text(), concurrent)
        self.assertIn("nfnfirst()", self.remote.read_text())

    def test_unchanged_function_and_dry_run_change_nothing(self):
        unchanged = self.nfn("nfnexample", "git status")
        self.assertEqual(unchanged.returncode, 0, unchanged.stderr)
        self.assertIn("nothing changed", unchanged.stdout)
        self.assertEqual(self.calls(), [])
        dry = self.nfn("--dry-run", "nfnfirst", "git push origin")
        self.assertEqual(dry.returncode, 0, dry.stderr)
        self.assertIn('appending to rc: nfnfirst() { git push origin "$@"; }', dry.stdout)
        self.assertIn("dry run: ~/.bashrc matches JakeIwen/scripts master", dry.stdout)
        self.assertEqual((self.rc.read_text(), self.remote.read_text()), (BASE, BASE))
        self.assertEqual(self.puts(), [])

    def test_pi_shell_has_committing_nfn_and_nalias_stub(self):
        environment = dict(os.environ, HOME=str(self.home), CODEX_THREAD_ID="t")
        result = subprocess.run(
            ["/bin/bash", "-c", '. "$1" && declare -f nfn && nalias', "_",
             str(REPOSITORY_ROOT / ".bashrc")],
            env=environment, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('"$HOME/scripts/nfn.py"', result.stdout)
        self.assertIn('`nalias` replaced with `nfn`', result.stdout)

    def test_invalid_names_and_commands_are_refused_before_github(self):
        for arguments in (("a/b", "ls"), ("a=b", "ls"), ("", "ls"), ("ok", ""), ("ok", "ls\nrm x")):
            with self.subTest(arguments=arguments):
                result = self.nfn(*arguments)
                self.assertEqual(result.returncode, 2)
        self.assertEqual(self.calls(), [])
        self.assertEqual(self.rc.read_text(), BASE)

    def test_invalid_shell_syntax_aborts_without_put(self):
        result = self.nfn("nfnfirst", "echo 'unclosed")
        self.assertEqual(result.returncode, 1)
        self.assertIn("doesn't parse", result.stderr)
        self.assertEqual(self.puts(), [])
        self.assertEqual(self.rc.read_text(), BASE)


if __name__ == "__main__":
    unittest.main()
