"""Contracts with units other repos own that import this package release."""
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from pi import deploy_python as deployment
from pi.tests import test_python_deployment as transactions


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DASHBOARD = "van-dashboard.service"
# What ~/dev/van-dashboard imports in-process from the scripts release...
DASHBOARD_IMPORTS = (
    "pi.package_runtime",
    "pi.apps.media_organizer.config",
    "pi.apps.media_organizer.records",
    "pi.apps.metered.controller",
    "pi.apps.metered.policy_requests",
    "shared.metered.records",
    "shared.metered.store",
    "shared.python.tool_settings",
)
# ...and runs as `python3 -P -m <package>` against the same release.
DASHBOARD_CLIS = ("pi.apps.media_organizer", "pi.apps.iptorrents_watch")
IMPORT_CHECK = """
import importlib, importlib.util, sys
release = sys.argv[1]
for name in sys.argv[2:]:
    if name.endswith('.__main__'):
        spec = importlib.util.find_spec(name)
        origin = spec and spec.origin
    else:
        origin = importlib.import_module(name).__file__
    if not origin or not origin.startswith(release + '/'):
        raise SystemExit(f'{name} resolved outside the release: {origin}')
"""


class ExternalConsumerTests(unittest.TestCase):
    def test_dashboard_is_an_external_consumer_and_never_a_package_unit(self):
        self.assertIn(DASHBOARD, deployment.EXTERNAL_CONSUMERS)
        self.assertNotIn(DASHBOARD, deployment.SERVICES)
        with self.assertRaises(ValueError):
            deployment.selected_units([DASHBOARD])

    def test_gc_keeps_the_release_an_external_consumer_still_runs(self):
        fixtures = transactions.PythonDeploymentTests
        with fixtures._gc_root(count=6) as (root, releases, services):
            oldest = releases[0]
            for unit in deployment.EXTERNAL_CONSUMERS:
                services.active.add(unit)
                services.set_running_release(unit, oldest)
            result = fixtures._collect_gc(root, services, releases[-1])
            remaining = {path.name for path in (root / "releases").iterdir()}
        self.assertEqual(result["status"], "ok")
        self.assertIn(oldest.name, remaining)
        # Without the consumer's record the oldest two would go (newest three
        # plus the installed one are retained), so collection really ran.
        self.assertEqual(result["removed"], [releases[1].name, releases[2].name])

    def test_staged_release_keeps_every_module_the_dashboard_imports(self):
        plan = deployment.build_plan(REPOSITORY_ROOT)
        archive = io.BytesIO()
        deployment.make_archive(plan, REPOSITORY_ROOT, archive)
        archive.seek(0)
        with tempfile.TemporaryDirectory() as directory:
            release = Path(directory).resolve() / "release"
            release.mkdir()
            deployment.unpack_archive(archive, release)
            environment = dict(os.environ, PYTHONPATH=str(release), PYTHONDONTWRITEBYTECODE="1",
                               VANPI_METERED_HOME=str(Path(directory) / "home"))
            names = [*DASHBOARD_IMPORTS, *(f"{name}.__main__" for name in DASHBOARD_CLIS)]
            result = subprocess.run(
                [sys.executable, "-P", "-B", "-c", IMPORT_CHECK, str(release), *names],
                cwd=directory, env=environment, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(list(release.rglob("__pycache__")), [])


if __name__ == "__main__":
    unittest.main()
