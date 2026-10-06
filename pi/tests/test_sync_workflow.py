from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest
from unittest import mock

from pi import check_compute_provider


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

    def test_compute_guard_stops_sync_before_any_mutation(self):
        definitions = SYNC_SCRIPT.read_text().split('source "$dsc/pi/sync_workflow.sh"')[0]
        for status in (0, 1):
            with self.subTest(status=status):
                harness = definitions + f'''
# Every external operation is a fake; only the actual preflight/workflow run.
dsc={shlex.quote(str(REPOSITORY_ROOT.parent))}
ssh() {{
  if [[ "$*" == *'/usr/bin/python3 -B -'* ]]; then
    printf 'provider-check\\n'
    return {status}
  fi
  return 0
}}
python3() {{ printf 'van-dashboard.service\\n'; }}
sync_non_python() {{ printf 'non-python-write\\n'; }}
sync_python_packages() {{ printf 'package-write\\n'; }}
sync_compute() {{ printf 'compute-write\\n'; }}
source {shlex.quote(str(WORKFLOW))}
run_sync_workflow
'''
                result = subprocess.run(['/bin/bash', '-c', harness],
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode, status, result.stderr)
                self.assertEqual(result.stdout.splitlines(), ['provider-check'] + (
                    ['non-python-write', 'package-write', 'compute-write'] if status == 0 else []))
                if status:
                    self.assertIn("owner's Terminal", result.stderr)
                    self.assertIn('install_van_compute_worker.zsh', result.stderr)

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


class ComputeProviderGuardTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name).resolve()
        self.root = self.home / 'compute'
        self.queue = self.home / 'queue'
        self.old = self.home / 'old'
        self.release = self.root / 'releases' / ('a' * 24)
        package = self.release / 'van_compute'
        package.mkdir(parents=True)
        (package / '__init__.py').write_text('')
        (package / 'metrics.py').write_text(
            '# older, compatible provider\n'
            'class ComputeMetricsError(Exception): pass\n'
            'class ComputeMetricsReader: pass\n')
        (self.release / 'source.sha256').write_text('a' * 64)
        (self.root / 'deployment.sha256').write_text('a' * 64)
        (self.root / 'current').symlink_to('releases/' + self.release.name)

    def check(self):
        check_compute_provider.check_provider(self.root, self.queue, self.old)

    def test_older_valid_provider_import_is_read_only(self):
        before = sorted(str(path) for path in self.home.rglob('*'))
        self.check()
        self.assertEqual(sorted(str(path) for path in self.home.rglob('*')), before)

    def test_missing_dangling_and_foreign_provider_refuse(self):
        current = self.root / 'current'
        current.unlink()
        with self.assertRaises(ValueError):
            self.check()
        current.symlink_to('releases/' + 'b' * 24)
        with self.assertRaises(OSError):
            self.check()
        current.unlink()
        current.symlink_to(self.release)
        with self.assertRaises(ValueError):
            self.check()

    def test_missing_unreadable_or_unimportable_metrics_refuse(self):
        metrics = self.release / 'van_compute/metrics.py'
        metrics.unlink()
        with self.assertRaises(OSError):
            self.check()
        metrics.write_text('this is not Python!')
        with self.assertRaises(subprocess.CalledProcessError):
            self.check()
        with mock.patch.object(Path, 'read_bytes', side_effect=PermissionError):
            with self.assertRaises(PermissionError):
                self.check()

    def test_marker_mismatch_and_interrupted_cutovers_refuse(self):
        (self.root / 'deployment.sha256').write_text('b' * 64)
        with self.assertRaises(ValueError):
            self.check()
        (self.root / 'deployment.sha256').write_text('a' * 64)
        for marker in (self.queue / '.maintenance.json',
                       self.root / 'scripts/.van-compute-upgrade-owner',
                       self.old / '.van-compute-upgrade-owner'):
            with self.subTest(marker=marker):
                marker.parent.mkdir(parents=True, exist_ok=True)
                marker.symlink_to(self.home / 'absent')
                with self.assertRaisesRegex(ValueError, 'fenced or in maintenance'):
                    self.check()
                marker.unlink()


if __name__ == "__main__":
    unittest.main()
