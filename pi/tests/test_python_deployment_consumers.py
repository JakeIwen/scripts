"""Contracts with units other repos own that import this package release."""
import io
import json
import os
from pathlib import Path
import shlex
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
# Captured on vanpi, 2026-11-03, with the guard's explicit -p list. Empty
# command lists are omitted; empty scalar properties are still printed.
SHOW = """ExecStartPre={ path=/usr/bin/test ; argv[]=/usr/bin/test -r /home/pi/van_compute/current/van_compute/metrics.py ; ignore_errors=no ; start_time=[n/a] ; stop_time=[n/a] ; pid=0 ; code=(null) ; status=0/0 }
ExecStartPre={ path=/usr/bin/test ; argv[]=/usr/bin/test -r /home/pi/scripts/van-dashboard-frontend/current/index.html ; ignore_errors=no ; start_time=[n/a] ; stop_time=[n/a] ; pid=0 ; code=(null) ; status=0/0 }
ExecStartPre={ path=/usr/bin/test ; argv[]=/usr/bin/test -r /home/pi/scripts/python-packages/current/manifest.json ; ignore_errors=no ; start_time=[n/a] ; stop_time=[n/a] ; pid=0 ; code=(null) ; status=0/0 }
ExecStartPre={ path=/usr/bin/test ; argv[]=/usr/bin/test -x /home/pi/scripts/safe_reboot.sh ; ignore_errors=no ; start_time=[n/a] ; stop_time=[n/a] ; pid=0 ; code=(null) ; status=0/0 }
ExecStartPre={ path=/usr/bin/test ; argv[]=/usr/bin/test -x /home/pi/scripts/safe_power_down.sh ; ignore_errors=no ; start_time=[n/a] ; stop_time=[n/a] ; pid=0 ; code=(null) ; status=0/0 }
ExecStart={ path=/usr/bin/python3 ; argv[]=/usr/bin/python3 -P -m pi.apps.van_dashboard ; ignore_errors=no ; start_time=[n/a] ; stop_time=[n/a] ; pid=0 ; code=(null) ; status=0/0 }
ExecStopPost={ path=/usr/bin/python3 ; argv[]=/usr/bin/python3 -P -m pi.apps.van_dashboard.van_dashboard_cop --relay-off ; ignore_errors=no ; start_time=[n/a] ; stop_time=[n/a] ; pid=0 ; code=(null) ; status=0/0 }
LoadState=loaded
FragmentPath=/etc/systemd/system/van-dashboard.service
DropInPaths=/etc/systemd/system/van-dashboard.service.d/network-storage.conf
NeedDaemonReload=no
"""
ABSENT_SHOW = """LoadState=not-found
FragmentPath=
DropInPaths=
NeedDaemonReload=no
"""
CUT_OVER_SHOW = (SHOW.replace("pi.apps.van_dashboard", "van_dashboard")
                .replace("/home/pi/scripts/van-dashboard-frontend/", "/home/pi/van_dashboard/frontend/"))
CUT_OVER_SHOW = SHOW.splitlines()[0].replace(
    "/home/pi/van_compute/current/van_compute/metrics.py",
    "/home/pi/van_dashboard/current/van_dashboard/__main__.py") + "\n" + CUT_OVER_SHOW
NETWORK_DROPIN = b"[Service]\nEnvironment=VANPI_NETWORK_STORAGE_CONFIG=/etc/vanpi-network-storage.json\n"


def show_state(raw=CUT_OVER_SHOW, **changes):
    """Vary captured state without fabricating systemd's command serialization."""
    return "\n".join(key + "=" + changes.get(key, value)
                     for key, value in (line.split("=", 1) for line in raw.splitlines())) + "\n"


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
        self.refused_untouched(services, "unsupported launch.*pi.apps.van_dashboard.*allowlist")

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
        self.refused_untouched(services, "unsupported launch.*pi.apps.van_dashboard.*allowlist")

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
            show = show.replace(BASE, str(root / DASHBOARD))
            expected = (f"show {DASHBOARD} -p LoadState -p NeedDaemonReload -p FragmentPath -p DropInPaths "
                        "-p ExecCondition -p ExecStartPre -p ExecStart -p ExecStartPost "
                        "-p ExecReload -p ExecStop -p ExecStopPost")
            stub.write_text("#!/bin/sh\n" + f'test "$*" = {shlex.quote(expected)} || exit 2\n' +
                            ("exit 1\n" if fail else f"cat <<'EOF'\n{show}EOF\n"))
            stub.chmod(0o755)
            services = deployment.SystemServices(units=root, systemctl=str(stub))
            deployment.check_external_consumers(services)

    def test_real_systemctl_show_output_decides(self):
        files = {DASHBOARD: CUT_OVER_UNIT, DASHBOARD + ".d/network-storage.conf": NETWORK_DROPIN}
        self.systemctl_state(CUT_OVER_SHOW, files)
        self.systemctl_state(ABSENT_SHOW)
        self.systemctl_state(show_state(DropInPaths=""), {DASHBOARD: CUT_OVER_UNIT})
        with self.assertRaisesRegex(ValueError, "pi.apps.van_dashboard"):
            self.systemctl_state(SHOW, files)
        with self.assertRaisesRegex(ValueError, "pending a daemon-reload"):
            self.systemctl_state(show_state(NeedDaemonReload="yes"), files)
        with self.assertRaisesRegex(ValueError, "cannot verify"):
            self.systemctl_state("", fail=True)
        with self.assertRaisesRegex(ValueError, "cannot verify"):
            self.systemctl_state("garbage without equals\n")
        # Loaded, but the fragment systemd names is gone from disk: not "absent".
        with self.assertRaisesRegex(ValueError, "cannot verify"):
            self.systemctl_state(CUT_OVER_SHOW)

    def test_unrecognized_launches_refuse_on_disk_and_in_effective_commands(self):
        commands = (
            "/usr/bin/python3 -Pmpi.apps.van_dashboard",
            "/usr/bin/python3 -P -mpi.apps.van_dashboard",
            "/bin/sh /home/pi/start-dashboard.sh",
            "/bin/sh -c '/usr/bin/python3 -P -m pi.apps.van_dashboard'",
            "/usr/bin/env /usr/bin/python3 -P -m pi.apps.van_dashboard",
            "/usr/bin/python3 -P -m pi.apps.media_organizer",
            "/home/pi/pyvenv/bin/python -P -m van_dashboard",
            "/usr/bin/python3 -P -m van_dashboard_other",
        )
        original = deployment.parse_show(CUT_OVER_SHOW)["ExecStart"][0]
        for command in commands:
            with self.subTest(command=command):
                path = shlex.split(command)[0]
                record = original.replace("path=/usr/bin/python3", f"path={path}").replace(
                    "argv[]=/usr/bin/python3 -P -m van_dashboard ;", f"argv[]={command} ;")
                with self.assertRaisesRegex(ValueError, "unsupported launch.*allowlist"):
                    self.systemctl_state(show_state(ExecStart=record, DropInPaths=""), {DASHBOARD: CUT_OVER_UNIT})
                with self.assertRaisesRegex(ValueError, "unsupported launch.*allowlist"):
                    self.systemctl_state(show_state(DropInPaths=""),
                                         {DASHBOARD: f"[Service]\nExecStart={command}\n".encode()})

    def test_malformed_records_and_missing_scalars_refuse(self):
        original = deployment.parse_show(CUT_OVER_SHOW)["ExecStart"][0]
        for record in (original[:-2], original.split(" ; ignore_errors")[0] + " ;",
                       "junk " + original, original + " trailing", original + original,
                       original.replace("path=/usr/bin/python3", "path=/bin/sh"),
                       original.replace("pid=0", "pid=unknown"), "{ argv[]= ; }"):
            with self.subTest(record=record), self.assertRaisesRegex(ValueError, "cannot verify"):
                self.systemctl_state(show_state(ExecStart=record, DropInPaths=""), {DASHBOARD: CUT_OVER_UNIT})
        for key in ("LoadState", "NeedDaemonReload", "FragmentPath", "DropInPaths"):
            for raw in (CUT_OVER_SHOW, ABSENT_SHOW):
                missing = "\n".join(line for line in raw.splitlines() if not line.startswith(key + "=")) + "\n"
                with self.subTest(key=key, raw=raw), self.assertRaisesRegex(ValueError, "cannot verify"):
                    self.systemctl_state(missing, {DASHBOARD: CUT_OVER_UNIT})

    def test_each_effective_exec_property_is_checked(self):
        old = deployment.parse_show(SHOW)["ExecStart"][0]
        for key in deployment.CONSUMER_EXEC_PROPERTIES:
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, "unsupported launch"):
                self.systemctl_state(show_state(DropInPaths="") + f"{key}={old}\n", {DASHBOARD: CUT_OVER_UNIT})

    def test_absent_unit_with_residual_commands_or_files_refuses(self):
        for raw, files in ((ABSENT_SHOW, {DASHBOARD: CUT_OVER_UNIT}),
                           (ABSENT_SHOW + CUT_OVER_SHOW.splitlines()[0] + "\n", {})):
            with self.subTest(raw=raw), self.assertRaisesRegex(ValueError, "not found but still"):
                self.systemctl_state(raw, files)

    def test_exec_prefixes_and_empty_resets_are_supported_on_disk(self):
        for prefix in ("", "-", "@", ":", "+", "!", "!!", "-:"):
            with self.subTest(prefix=prefix):
                dropin = ("[Service]\nExecStart=\nExecStart=" + prefix +
                          "/usr/bin/python3 -P -m van_dashboard\n").encode()
                self.systemctl_state(show_state(DropInPaths=BASE + ".d/override.conf"),
                                     {DASHBOARD: CUT_OVER_UNIT, DASHBOARD + ".d/override.conf": dropin})

    def test_cut_over_or_absent_dashboard_unit_allows_deploys(self):
        fixtures = transactions.PythonDeploymentTests
        for unit in (CUT_OVER_UNIT, None):
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
