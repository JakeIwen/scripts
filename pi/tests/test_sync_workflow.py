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
    def test_sync_script_uses_primary_trusted_checkout(self):
        assignment = next(
            line.strip()
            for line in SYNC_SCRIPT.read_text(encoding="utf-8").splitlines()
            if line.strip().startswith("dsc=")
        )
        parsed = shlex.split(assignment, comments=False, posix=True)
        self.assertEqual(len(parsed), 1)
        name, value = parsed[0].split("=", 1)
        self.assertEqual(name, "dsc")
        self.assertEqual(value, "/Users/jacobr/dev/scripts")

    def test_sync_preflight_requires_each_package_service_marker(self):
        sync = SYNC_SCRIPT.read_text(encoding="utf-8")
        self.assertIn(
            "test -f /home/pi/scripts/python-packages/activated.json",
            sync,
        )
        self.assertIn(
            "test -f '/home/pi/scripts/python-packages/service-state/$unit.json'",
            sync,
        )
        self.assertIn(
            'python3 "$dsc/pi/deploy_python.py" --list-units',
            sync,
        )

    def test_broad_sync_does_not_run_legacy_flatten(self):
        sync = SYNC_SCRIPT.read_text(encoding="utf-8")
        self.assertIn(
            'python3 "$dsc/pi/deploy_python.py" --target "$pi_ip" --update',
            sync,
        )
        self.assertNotIn("--legacy-flatten", sync)

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

    def test_success_runs_each_phase_once_in_order(self):
        result, recorded = self._run_harness()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(recorded, [*PHASES, "status=0"])

    def test_failure_in_each_phase_stops_later_phases_and_propagates(self):
        for index, failing_phase in enumerate(PHASES):
            with self.subTest(failing_phase=failing_phase):
                result, recorded = self._run_harness(failing_phase)

                self.assertEqual(result.returncode, FAILURE_STATUS, result.stderr)
                self.assertEqual(
                    recorded,
                    [*PHASES[: index + 1], f"status={FAILURE_STATUS}"],
                )


if __name__ == "__main__":
    unittest.main()
