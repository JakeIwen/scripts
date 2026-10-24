import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPOSITORY_ROOT / "scripts/nalias.py"
HEADER = "### NALIAS ### (added by nalias; defined last, so no function above expands them)"
BASE = "### CORE ###\nalias gs='git status'\nls_all() { ls -a; }\n"

# Stands in for `gh api`: the "remote" file is pi/.bashrc on GitHub master, and
# a PUT succeeds only when its sha still matches it, as the contents API does.
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
    print(json.dumps({"content": {"sha": sha(data)},
                      "commit": {"sha": "c0ffee", "html_url": "https://example.test/c0ffee"}}))
else:
    print(json.dumps({"sha": sha(remote.read_bytes())}))
    race = state / "race"
    if race.exists():
        remote.write_bytes(race.read_bytes())
'''


class NaliasTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="nalias-test-")
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.home, self.state, self.bin = root / "home", root / "state", root / "bin"
        for directory in (self.home, self.state, self.bin):
            directory.mkdir()
        gh = self.bin / "gh"
        gh.write_text(f"#!{sys.executable}\n{FAKE_GH}")
        gh.chmod(0o755)
        self.rc, self.remote = self.home / ".bashrc", self.state / "remote"
        self.rc.write_text(BASE)
        self.remote.write_text(BASE)

    def nalias(self, *arguments):
        environment = dict(os.environ, HOME=str(self.home), FAKE_GH_STATE=str(self.state),
                           PATH=f"{self.bin}{os.pathsep}{os.environ['PATH']}")
        return subprocess.run([sys.executable, str(SCRIPT), *arguments], env=environment,
                              capture_output=True, text=True)

    def calls(self):
        log = self.state / "calls"
        return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []

    def puts(self):
        return [call for call in self.calls() if "PUT" in call]

    def run_alias(self, name):
        """Run the alias the way an interactive shell would expand it."""
        script = f'shopt -s expand_aliases\n. "$1"\n{name}\n'
        return subprocess.run(["/bin/bash", "-c", script, "_", str(self.rc)],
                              capture_output=True, text=True).stdout

    def test_new_alias_is_committed_then_installed_under_one_section(self):
        first = self.nalias("gpo", "git push origin")
        second = self.nalias("gpl", "git pull")

        self.assertEqual((first.returncode, second.returncode), (0, 0), first.stderr + second.stderr)
        self.assertIn("appending to rc: alias gpo='git push origin'", first.stdout)
        self.assertEqual(self.rc.read_text(), self.remote.read_text())
        self.assertEqual(self.rc.read_text(), BASE + f"\n{HEADER}\n"
                         "alias gpo='git push origin'\nalias gpl='git pull'\n")
        body = json.loads((self.state / "put.json").read_text())
        self.assertEqual(body["branch"], "master")
        self.assertTrue(body["message"].startswith("Pi bashrc: nalias gpl\n"))
        self.assertEqual(len(self.puts()), 2)

    def test_existing_alias_is_replaced_in_place_with_quotes_intact(self):
        result = self.nalias("gs", "printf '%s|' one 'two three'")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("found alias, replaced: alias gs='git status'", result.stdout)
        lines = self.rc.read_text().splitlines()
        self.assertEqual(len(lines), 3)
        self.assertTrue(lines[1].startswith("alias gs="))
        self.assertEqual(self.run_alias("gs"), "one|two three|")
        self.assertEqual(self.rc.read_text(), self.remote.read_text())

    def test_differing_copies_abort_before_any_commit(self):
        self.remote.write_text(BASE + "alias newer='on github'\n")

        result = self.nalias("gpo", "git push origin")

        self.assertEqual(result.returncode, 1)
        self.assertIn("nothing changed", result.stderr)
        self.assertIn("differs from pi/.bashrc on GitHub master", result.stderr)
        self.assertEqual(self.rc.read_text(), BASE)
        self.assertEqual(self.puts(), [])

    def test_commit_rejected_by_a_concurrent_change_leaves_bashrc_alone(self):
        concurrent = BASE + "alias other='committed meanwhile'\n"
        (self.state / "race").write_text(concurrent)

        result = self.nalias("gpo", "git push origin")

        self.assertEqual(result.returncode, 1)
        self.assertIn("HTTP 409", result.stderr)
        self.assertEqual(self.rc.read_text(), BASE)
        self.assertEqual(self.remote.read_text(), concurrent)

    def test_unreachable_github_changes_nothing(self):
        (self.state / "offline").touch()

        result = self.nalias("gpo", "git push origin")

        self.assertEqual(result.returncode, 1)
        self.assertIn("error connecting", result.stderr)
        self.assertEqual(self.rc.read_text(), BASE)

    def test_unchanged_alias_and_dry_run_change_nothing(self):
        unchanged = self.nalias("gs", "git status")
        self.assertEqual(unchanged.returncode, 0, unchanged.stderr)
        self.assertIn("nothing changed", unchanged.stdout)
        self.assertEqual(self.calls(), [])

        dry = self.nalias("--dry-run", "gpo", "git push origin")
        self.assertEqual(dry.returncode, 0, dry.stderr)
        self.assertIn("appending to rc: alias gpo='git push origin'", dry.stdout)
        self.assertIn("dry run", dry.stdout)
        self.assertEqual((self.rc.read_text(), self.remote.read_text()), (BASE, BASE))
        self.assertEqual(self.puts(), [])

    def test_invalid_names_and_commands_are_refused_before_github(self):
        for arguments in (("a/b", "ls"), ("a=b", "ls"), ("", "ls"), ("ok", ""), ("ok", "ls\nrm x")):
            with self.subTest(arguments=arguments):
                result = self.nalias(*arguments)
                self.assertEqual(result.returncode, 2)
        self.assertEqual(self.calls(), [])
        self.assertEqual(self.rc.read_text(), BASE)


if __name__ == "__main__":
    unittest.main()
