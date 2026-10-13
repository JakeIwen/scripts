"""Dashboard package deployment contracts for the standalone compute provider."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from pi import deploy_python as deployment
from pi.tests.unit_contract import command_arguments, parse_directives, parse_environment


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DASHBOARD_SERVICE = REPOSITORY_ROOT / "pi" / "services" / "van-dashboard.service"


class DashboardProviderPreflightTests(unittest.TestCase):
    def invoke(self, arguments, run):
        with mock.patch.object(deployment, "build_plan", return_value={}), \
                mock.patch.object(deployment, "make_archive") as archive, \
                mock.patch.object(deployment.subprocess, "run", side_effect=run), \
                contextlib.redirect_stdout(io.StringIO()):
            deployment.main(arguments)
        return archive

    def test_provider_failure_prevents_package_transfer(self):
        for failure in (
            subprocess.CalledProcessError(1, ["fake-ssh"]),
            subprocess.TimeoutExpired(["fake-ssh"], 30),
            OSError("fake connection failure"),
        ):
            calls = []

            def fail_guard(command, **kwargs):
                calls.append((command, kwargs))
                raise failure

            with self.subTest(failure=failure), \
                    mock.patch.object(deployment.tempfile, "TemporaryFile") as temporary:
                with self.assertRaisesRegex(
                    RuntimeError, "no package deployment was started"
                ):
                    self.invoke(
                        ["--update", "--service", deployment.DASHBOARD], fail_guard
                    )
                temporary.assert_not_called()
                self.assertEqual(len(calls), 1)
                self.assertEqual(calls[0][0][-1], "/usr/bin/python3 -B -")
                self.assertEqual(
                    calls[0][1]["input"],
                    (deployment.ROOT / "pi/check_compute_provider.py").read_bytes(),
                )

    def test_healthy_provider_precedes_receiver_for_default_and_scoped_update(self):
        for selection in ([], ["--service", deployment.DASHBOARD]):
            calls = []

            def success(command, **kwargs):
                calls.append((command, kwargs))
                return subprocess.CompletedProcess(command, 0)

            with self.subTest(selection=selection):
                archive = self.invoke(
                    ["--update", "--target", "pi@example.test", *selection], success
                )
                archive.assert_called_once()
                self.assertEqual(len(calls), 2)
                self.assertEqual(
                    calls[0][0][-2:],
                    ["pi@example.test", "/usr/bin/python3 -B -"],
                )
                self.assertEqual(calls[0][1]["timeout"], 30)
                self.assertIn("--receive update", calls[1][0][-1])

    def test_dry_run_never_probes_provider_or_transfers(self):
        run = mock.Mock(side_effect=AssertionError("dry-run attempted transport"))
        archive = self.invoke(
            ["--dry-run", "--update", "--service", deployment.DASHBOARD], run
        )
        run.assert_not_called()
        archive.assert_not_called()

    def test_other_modes_and_scoped_services_keep_existing_transfer(self):
        for arguments in (
            [],
            ["--activate", "--service", deployment.DASHBOARD],
            ["--update", "--service", "video-library.service"],
        ):
            run = mock.Mock(return_value=subprocess.CompletedProcess([], 0))
            with self.subTest(arguments=arguments):
                self.invoke(arguments, run)
                run.assert_called_once()
                self.assertIn("--receive ", run.call_args.args[0][-1])

    def test_invalid_target_is_rejected_before_provider_probe(self):
        run = mock.Mock(side_effect=AssertionError("invalid target attempted transport"))
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            self.invoke(["--update", "--target=-bad"], run)
        run.assert_not_called()


class DashboardComputePackageContractTests(unittest.TestCase):
    def test_dashboard_unit_consumes_the_package_current_link(self):
        dashboard = parse_directives(DASHBOARD_SERVICE.read_text(encoding="utf-8"))
        dashboard_environment = parse_environment(dashboard)
        self.assertIn(
            "/home/pi/van_compute/current",
            dashboard_environment["PYTHONPATH"].split(":"),
        )
        self.assertIn(
            [
                "/usr/bin/test",
                "-r",
                "/home/pi/van_compute/current/van_compute/metrics.py",
            ],
            [command_arguments(value) for value in dashboard["ExecStartPre"]],
        )

    @unittest.skipUnless(
        sys.version_info >= (3, 11) and importlib.util.find_spec("flask") is not None,
        "Pi -P/dashboard smoke needs Python 3.11 and Flask",
    )
    def test_staged_dashboard_imports_only_the_compute_metrics_contract(self):
        plan = deployment.build_plan(REPOSITORY_ROOT, mode="stage")
        with tempfile.TemporaryDirectory() as directory:
            staged = Path(directory)
            for relative in plan["manifest"]["files"]:
                if not relative.endswith(".py"):
                    continue
                destination = staged / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(REPOSITORY_ROOT / relative, destination)
            metrics_package = staged / "van_compute"
            metrics_package.mkdir(parents=True, exist_ok=True)
            (metrics_package / "__init__.py").write_text("", encoding="utf-8")
            (metrics_package / "metrics.py").write_text(
                "class ComputeMetricsError(RuntimeError):\n"
                "    pass\n\n"
                "class ComputeMetricsReader:\n"
                "    def __init__(self, root):\n"
                "        self.root = root\n",
                encoding="utf-8",
            )
            environment = os.environ.copy()
            environment["PYTHONPATH"] = str(staged)
            environment["VAN_DASHBOARD_COMPUTE_ROOT"] = str(staged / "compute")
            environment["VAN_DASHBOARD_STATE_PATH"] = str(staged / "state.json")
            environment["VAN_DASHBOARD_RUNTIME_DIR"] = str(staged / "runtime")
            result = subprocess.run(
                [
                    sys.executable,
                    "-P",
                    "-c",
                    (
                        "import os; "
                        "from van_compute import metrics; "
                        "assert {name for name in vars(metrics) if not name.startswith('_')} "
                        "== {'ComputeMetricsError', 'ComputeMetricsReader'}; "
                        "import pi.apps.van_dashboard as dashboard; "
                        "from pi.apps.van_dashboard import runtime; "
                        "app = dashboard.create_app(); "
                        "assert app is not None; "
                        "assert isinstance(runtime.compute_monitor, metrics.ComputeMetricsReader); "
                        "assert runtime.compute_monitor.root == "
                        "os.environ['VAN_DASHBOARD_COMPUTE_ROOT']"
                    ),
                ],
                cwd=staged,
                env=environment,
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
