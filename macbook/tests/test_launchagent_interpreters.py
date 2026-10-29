"""LaunchAgent interpreters stay put across Homebrew upgrades and can start their jobs."""
from pathlib import Path
import plistlib
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[2]
LAUNCHAGENTS = ROOT / "macbook/launchagents"
LIVE_CHECKOUT = "/Users/jacobr/dev/scripts"
# Homebrew repoints these links on each Python install or upgrade, and macOS
# then treats every job that runs one as a new background item and notifies.
FLOATING_PYTHONS = {"/opt/homebrew/bin/python3", "/opt/homebrew/bin/python",
                    "/usr/local/bin/python3", "/usr/local/bin/python"}


def load(path):
    with path.open("rb") as source:
        return plistlib.load(source)


def in_this_checkout(value):
    return value.replace(LIVE_CHECKOUT, str(ROOT))


class LaunchAgentInterpreterTests(unittest.TestCase):
    def test_no_agent_runs_a_floating_homebrew_python(self):
        for path in sorted(LAUNCHAGENTS.glob("*.plist")):
            with self.subTest(path.name):
                plist = load(path)
                program = plist.get("Program") or plist["ProgramArguments"][0]
                self.assertNotIn(program, FLOATING_PYTHONS)

    @unittest.skipUnless(sys.platform == "darwin", "the named interpreters exist only on the Mac")
    def test_python_agents_start_on_their_named_interpreter(self):
        for label in ("com.jacobr.deal-watch-refresh", "com.jacobr.hosted-project-scan"):
            with self.subTest(label):
                plist = load(LAUNCHAGENTS / f"{label}.plist")
                argv = [in_this_checkout(arg) for arg in plist["ProgramArguments"]]
                entry = argv[:argv.index("-m") + 2] if "-m" in argv else argv[:2]
                env = {"PATH": "/usr/bin:/bin"}
                env.update({key: in_this_checkout(value)
                            for key, value in plist.get("EnvironmentVariables", {}).items()})
                result = subprocess.run([*entry, "--help"], cwd=ROOT, env=env,
                                        capture_output=True, text=True, timeout=60)
                self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
