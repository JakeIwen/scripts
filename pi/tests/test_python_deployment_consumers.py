"""Contracts with units other repos own that import this package release."""
import io
import json
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
SCRIPTS_ERA_UNIT = (b"[Service]\n"
                    b"ExecStart=/usr/bin/python3 -P -m pi.apps.van_dashboard\n"
                    b"ExecStopPost=/usr/bin/python3 -P -m pi.apps.van_dashboard.van_dashboard_cop --relay-off\n")
CUT_OVER_UNIT = (b"[Service]\n"
                 b"ExecStart=/usr/bin/python3 -P -m van_dashboard\n"
                 b"ExecStopPost=/usr/bin/python3 -P -m van_dashboard.van_dashboard_cop --relay-off\n")


def tree_state(root):
    """Links, journals and units a refused install must leave exactly as they were."""
    return {str(path.relative_to(root)): os.readlink(path) if path.is_symlink() else path.read_bytes()
            for path in sorted(root.rglob("*"))
            if path.is_symlink() or (path.is_file() and "releases" not in path.parts
                                     and path.name != ".install.lock")}


OLD_DROPIN = b"[Service]\nExecStart=\nExecStart=/usr/bin/python3 -P -m pi.apps.van_dashboard\n"
QUOTED_UNIT = (b"[Service]\nExecStart=/usr/bin/python3 -P -m \"pi.apps.van_dashboard\"\n")
BASE = "/etc/systemd/system/" + DASHBOARD
# Verbatim shape of `systemctl show van-dashboard.service` on the Pi (Exec lines trimmed).
SHOW = """ExecStartPre={{ path=/usr/bin/test ; argv[]=/usr/bin/test -r /home/pi/x ; ignore_errors=no ; start_time=[n/a] ; stop_time=[n/a] ; pid=0 ; code=(null) ; status=0/0 }}
ExecStart={{ path=/usr/bin/python3 ; argv[]=/usr/bin/python3 -P -m {module} ; ignore_errors=no ; start_time=[n/a] ; stop_time=[n/a] ; pid=0 ; code=(null) ; status=0/0 }}
ExecStartEx={{ path=/usr/bin/python3 ; argv[]=/usr/bin/python3 -P -m {module} ; flags= ; start_time=[n/a] ; stop_time=[n/a] ; pid=0 ; code=(null) ; status=0/0 }}
ExecReload=
LoadState={load}
FragmentPath={fragment}
DropInPaths={dropins}
NeedDaemonReload={reload}
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

    def install(self, repo, root, mode, services):
        fixtures = transactions.PythonDeploymentTests
        return fixtures._install(deployment.build_plan(repo), repo, root, mode, services=services,
                                 selected=list(deployment.SERVICES))

    def test_scripts_era_dashboard_unit_blocks_activation_and_update(self):
        fixtures = transactions.PythonDeploymentTests
        for mode in ("activate", "update"):
            with self.subTest(mode=mode), fixtures.fixture() as repo, \
                    tempfile.TemporaryDirectory() as name:
                root = Path(name) / "packages"
                services = transactions.FakeSystemServices(active=set(deployment.SERVICES))
                if mode == "update":
                    self.install(repo, root, "activate", services)
                    (repo / transactions.SAMPLE_MODULE).write_text("CHANGED = True\n")
                services.units[DASHBOARD] = SCRIPTS_ERA_UNIT
                services.install_calls.clear()
                services.restart_calls.clear()
                before = tree_state(root) if root.exists() else {}
                with self.assertRaisesRegex(ValueError, "pi.apps.van_dashboard.*van-dashboard repo"):
                    self.install(repo, root, mode, services)
                self.assertEqual(tree_state(root), before)
                self.assertEqual((services.install_calls, services.restart_calls), ([], []))
                self.assertEqual(services.units[DASHBOARD], SCRIPTS_ERA_UNIT)

    def refused_untouched(self, services, pattern):
        fixtures = transactions.PythonDeploymentTests
        with fixtures.fixture() as repo, tempfile.TemporaryDirectory() as name:
            root = Path(name) / "packages"
            services.install_calls.clear()
            with self.assertRaisesRegex(ValueError, pattern):
                self.install(repo, root, "activate", services)
            self.assertEqual((services.install_calls, services.restart_calls), ([], []))
            self.assertFalse((root / "current").exists())

    def test_new_unit_file_with_pending_reload_is_refused(self):
        # Cutover interrupted before daemon-reload: systemd still runs the old command.
        services = transactions.FakeSystemServices(active=set())
        services.loaded_files[DASHBOARD] = {BASE: SCRIPTS_ERA_UNIT}
        services.units[DASHBOARD] = CUT_OVER_UNIT
        self.refused_untouched(services, "pending a daemon-reload")

    def test_old_command_dropin_is_refused_even_after_reload(self):
        services = transactions.FakeSystemServices(active=set())
        services.units[DASHBOARD] = CUT_OVER_UNIT
        services.dropins_by_unit[DASHBOARD] = {"old.conf": OLD_DROPIN}
        self.refused_untouched(services, "still runs -m pi.apps.van_dashboard")

    def test_missing_base_file_with_loaded_old_unit_is_refused(self):
        services = transactions.FakeSystemServices(active=set())
        services.loaded_files[DASHBOARD] = {BASE: SCRIPTS_ERA_UNIT}
        self.refused_untouched(services, "pending a daemon-reload")
        # Base file gone from disk, old launch still loaded from a drop-in.
        services.loaded_files[DASHBOARD] = {BASE + ".d/x.conf": OLD_DROPIN}
        self.refused_untouched(services, "pending a daemon-reload")

    def test_quoted_module_is_refused(self):
        services = transactions.FakeSystemServices(active=set())
        services.units[DASHBOARD] = QUOTED_UNIT
        self.refused_untouched(services, "still runs -m pi.apps.van_dashboard")

    def test_unreadable_unit_state_is_refused(self):
        services = transactions.FakeSystemServices(active=set())

        def broken(unit):
            raise PermissionError("cannot read")
        services.consumer_state = broken
        self.refused_untouched(services, "cannot verify.*PermissionError")

    def systemctl_state(self, show, files=None, fail=False):
        """The real SystemServices reading a stub systemctl and a unit directory."""
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            for relative, raw in (files or {}).items():
                (root / relative).parent.mkdir(parents=True, exist_ok=True)
                (root / relative).write_bytes(raw)
            stub = root / "systemctl"
            show = show.replace("@BASE@", str(root / DASHBOARD))
            stub.write_text("#!/bin/sh\n" + ("exit 1\n" if fail else f"cat <<'EOF'\n{show}EOF\n"))
            stub.chmod(0o755)
            services = deployment.SystemServices(units=root, systemctl=str(stub))
            deployment.check_external_consumers(services)

    def test_real_systemctl_show_output_decides(self):
        old = SHOW.format(module="pi.apps.van_dashboard", load="loaded", fragment="@BASE@", dropins="", reload="no")
        new = SHOW.format(module="van_dashboard", load="loaded", fragment="@BASE@", dropins="", reload="no")
        absent = SHOW.format(module="van_dashboard", load="not-found", fragment="", dropins="", reload="no")
        self.systemctl_state(new, {DASHBOARD: CUT_OVER_UNIT})
        self.systemctl_state(absent.replace("ExecStart", "Unused"))
        with self.assertRaisesRegex(ValueError, "pi.apps.van_dashboard"):
            self.systemctl_state(old, {DASHBOARD: CUT_OVER_UNIT})
        with self.assertRaisesRegex(ValueError, "pending a daemon-reload"):
            self.systemctl_state(new.replace("NeedDaemonReload=no",
                                 "NeedDaemonReload=yes"), {DASHBOARD: CUT_OVER_UNIT})
        with self.assertRaisesRegex(ValueError, "cannot verify"):
            self.systemctl_state("", fail=True)
        with self.assertRaisesRegex(ValueError, "cannot verify"):
            self.systemctl_state("garbage without equals\n")
        # Loaded, but the fragment systemd names is gone from disk: not "absent".
        with self.assertRaisesRegex(ValueError, "cannot verify"):
            self.systemctl_state(new)

    def test_cut_over_or_absent_dashboard_unit_allows_deploys(self):
        fixtures = transactions.PythonDeploymentTests
        # A unit running a module this release still ships is fine too.
        still_shipped = b"[Service]\nExecStart=/usr/bin/python3 -P -m pi.apps.media_organizer\n"
        for unit in (CUT_OVER_UNIT, still_shipped, None):
            with self.subTest(unit=unit), fixtures.fixture() as repo, \
                    tempfile.TemporaryDirectory() as name:
                root = Path(name) / "packages"
                services = transactions.FakeSystemServices(active=set())
                if unit is not None:
                    services.units[DASHBOARD] = unit
                first = self.install(repo, root, "activate", services)
                (repo / transactions.SAMPLE_MODULE).write_text("CHANGED = True\n")
                second = self.install(repo, root, "update", services)
                self.assertEqual(os.readlink(root / "current"),
                                 "releases/" + Path(second["release"]).name)
                self.assertEqual(json.loads((root / "activated.json").read_bytes()),
                                 {"release": Path(second["release"]).name})
                self.assertNotEqual(first["release"], second["release"])

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
