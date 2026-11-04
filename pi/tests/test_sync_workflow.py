from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = REPOSITORY_ROOT / "sync_workflow.sh"
SYNC_SCRIPT = REPOSITORY_ROOT / "sync_scripts.sh"
PHASES = ("sync_preflight", "sync_non_python", "sync_python_packages", "sync_compute")
FAILURE_STATUS = 23


class SyncWorkflowTests(unittest.TestCase):
    def test_package_activation_guard_stops_sync_before_any_mutation(self):
        definitions = SYNC_SCRIPT.read_text().split('source "$dsc/pi/sync_workflow.sh"')[0]
        temporary = tempfile.TemporaryDirectory(prefix="sync-compute-checkout-")
        self.addCleanup(temporary.cleanup)
        checkout = Path(temporary.name)
        installer = checkout / "macbook/scripts/install_van_compute_worker.zsh"
        installer.parent.mkdir(parents=True)
        installer.write_text("#!/bin/zsh\nexit 0\n", encoding="utf-8")
        installer.chmod(0o700)
        for status in (0, 1):
            with self.subTest(status=status):
                harness = definitions + f'''
# Every external operation is a fake; only the actual preflight/workflow run.
dsc={shlex.quote(str(REPOSITORY_ROOT.parent))}
VAN_COMPUTE_CHECKOUT={shlex.quote(str(checkout))}
ssh() {{
  if [[ "$*" == *activated.json* ]]; then
    printf 'activation-check\\n'
    return {status}
  fi
  [[ "$*" == *'git hash-object'* ]] && git -C "$dsc" hash-object pi/.bashrc
  return 0
}}
python3() {{ [[ "$*" == *--list-units* ]] && printf 'video-library.service\\n'; return 0; }}
sync_non_python() {{ printf 'non-python-write\\n'; }}
sync_python_packages() {{ printf 'package-write\\n'; }}
sync_compute() {{ printf 'compute-write\\n'; }}
source {shlex.quote(str(WORKFLOW))}
run_sync_workflow
'''
                result = subprocess.run(['/bin/bash', '-c', harness],
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode, status, result.stderr)
                self.assertEqual(result.stdout.splitlines(), ['activation-check'] + (
                    ['non-python-write', 'package-write', 'compute-write'] if status == 0 else []))
                if status:
                    self.assertIn("activation preflight failed", result.stderr)

    def test_missing_compute_installer_stops_before_any_remote_probe(self):
        definitions = SYNC_SCRIPT.read_text().split('source "$dsc/pi/sync_workflow.sh"')[0]
        with tempfile.TemporaryDirectory(prefix="sync-missing-compute-") as directory:
            missing = Path(directory) / "absent"
            harness = definitions + f'''
VAN_COMPUTE_CHECKOUT={shlex.quote(str(missing))}
ssh() {{ printf 'unexpected-remote-probe\\n'; }}
sync_preflight
'''
            result = subprocess.run(
                ['/bin/bash', '-c', harness], capture_output=True, text=True
            )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")

    def _run_harness(self, failing_phase=None):
        with tempfile.TemporaryDirectory(prefix="sync-workflow-") as directory:
            root = Path(directory)
            events = root / "events"
            harness = root / "harness.sh"
            fail_value = failing_phase or ""
            harness.write_text(
                "\n".join(
                    (
                        "#!/bin/bash",
                        f"events_file={shlex.quote(str(events))}",
                        f"fail_phase={shlex.quote(fail_value)}",
                        "record_phase() {",
                        '  printf \'%s\\n\' "$1" >> "$events_file"',
                        '  if [[ "$1" == "$fail_phase" ]]; then',
                        f"    return {FAILURE_STATUS}",
                        "  fi",
                        "  return 0",
                        "}",
                        "sync_preflight() { record_phase sync_preflight; }",
                        "sync_non_python() { record_phase sync_non_python; }",
                        "sync_python_packages() { record_phase sync_python_packages; }",
                        "sync_compute() { record_phase sync_compute; }",
                        f"source {shlex.quote(str(WORKFLOW))}",
                        "run_sync_workflow",
                        "status=$?",
                        'printf \'status=%s\\n\' "$status" >> "$events_file"',
                        "exit \"$status\"",
                        "",
                    )
                ),
                encoding="utf-8",
            )
            result = subprocess.run(
                ["/bin/bash", str(harness)],
                check=False,
                capture_output=True,
                text=True,
            )
            recorded = events.read_text(encoding="utf-8").splitlines()
            return result, recorded

    def test_failure_in_each_phase_stops_later_phases_and_propagates(self):
        for index, failing_phase in enumerate(PHASES):
            with self.subTest(failing_phase=failing_phase):
                result, recorded = self._run_harness(failing_phase)

                self.assertEqual(result.returncode, FAILURE_STATUS, result.stderr)
                self.assertEqual(
                    recorded,
                    [*PHASES[: index + 1], f"status={FAILURE_STATUS}"],
                )


class PiBashrcGuardTests(unittest.TestCase):
    """Broad sync must not install pi/.bashrc over a live copy it has never seen."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="sync-bashrc-guard-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.checkout = self.root / "checkout"
        (self.checkout / "pi").mkdir(parents=True)
        self.git("init", "-q")
        for version in ("one", "two"):
            (self.checkout / "pi/.bashrc").write_text(f"alias v='{version}'\n")
            self.git("add", "pi/.bashrc")
            self.git("-c", "user.name=test", "-c", "user.email=test@example.test",
                     "commit", "-q", "-m", version)
        (self.checkout / "pi/.bashrc").write_text("alias v='uncommitted'\n")

    def git(self, *arguments):
        subprocess.run(["git", "-C", str(self.checkout), *arguments], check=True)

    def guard(self, live_text, reachable=True):
        live = self.root / "live-bashrc"
        live.write_text(live_text)
        definitions = SYNC_SCRIPT.read_text().split('source "$dsc/pi/sync_workflow.sh"')[0]
        fake_ssh = f"git hash-object {shlex.quote(str(live))}" if reachable else "return 255"
        harness = definitions + f"""
dsc={shlex.quote(str(self.checkout))}
ssh() {{ {fake_ssh}; }}
check_pi_bashrc_known
"""
        return subprocess.run(["/bin/bash", "-c", harness], capture_output=True, text=True)

    def test_any_version_this_checkout_has_seen_may_be_replaced(self):
        for version in ("one", "two", "uncommitted"):
            with self.subTest(version=version):
                result = self.guard(f"alias v='{version}'\n")
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_unseen_or_unreadable_live_copy_refuses(self):
        unseen = self.guard("alias v='from a pi nfn'\n")
        self.assertEqual(unseen.returncode, 1)
        self.assertIn("pull --ff-only", unseen.stderr)
        unreachable = self.guard("alias v='one'\n", reachable=False)
        self.assertEqual(unreachable.returncode, 1)
        self.assertIn("couldn't read", unreachable.stderr)


if __name__ == "__main__":
    unittest.main()
