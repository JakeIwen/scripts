import io
import json
import os
from pathlib import Path, PurePosixPath
import runpy
import shutil
import subprocess
import sys
import tarfile
import tempfile
import types
from contextlib import contextmanager
import unittest
from unittest import mock

from pi import deploy_python as deployment


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DEPLOY_SCRIPT = REPOSITORY_ROOT / "pi" / "deploy_python.py"
DASHBOARD_ENTRYPOINT = REPOSITORY_ROOT / "pi" / "apps" / "van_dashboard" / "__main__.py"

FIXTURE_MODULES = {
    "pi/apps/audiobooks": ("__main__.py", "audiobook_server.py"),
    "pi/apps/bme280": ("__main__.py", "bme280_mqtt.py", "bme280_testread.py"),
    "pi/apps/van_dashboard": (
        "__main__.py",
        "van_dashboard.py",
        "van_dashboard_projects.py",
        "react_dashboard_preview.py",
    ),
    "pi/apps/van_dashboard/routes": ("__init__.py", "common.py", "projects.py"),
    "pi/apps/video_library": ("__main__.py", "video_library_server.py"),
    "pi/apps/video_library/players": ("vlc_player.py", "sonos_volume.py"),
    "pi/scripts/python": ("ip_info.py", "vlc_property.py"),
    "shared/python": ("__init__.py", "shared_tool.py", "sonos_tasks.py"),
}


def package_unit(unit, description="fixture package unit"):
    spec = deployment.SERVICES[unit]
    return (
        "[Service]\n"
        f"ExecStart={spec['interpreter']} -P -m pi.apps.{spec['app']}\n"
        f"Description={description}\n"
    ).encode()


def flat_unit(unit, description="fixture flat unit"):
    spec = deployment.SERVICES[unit]
    return (
        "[Service]\n"
        f"ExecStart={spec['interpreter']} /home/pi/scripts/python-automation/{spec['flat']}\n"
        f"Description={description}\n"
    ).encode()


class FakeSystemServices:
    """An in-memory four-service manager for receiver-side tests."""

    def __init__(self, units=None, active=None, restart_failures=None):
        self.units = {
            unit: flat_unit(unit) for unit in deployment.SERVICES
        }
        if isinstance(units, bytes):
            self.units[deployment.DASHBOARD] = units
        elif units:
            self.units.update(units)
        self.active = set(active or ())
        self.restart_failures = dict(restart_failures or {})
        self.install_calls = []
        self.restart_calls = []
        self.saved_legacy = []
        self.dropins_by_unit = {unit: {} for unit in deployment.SERVICES}
        self.release_root = None
        self.next_pid = 4100
        self.invocation_number = 0
        self.running_records = {unit: None for unit in deployment.SERVICES}
        self.state_overrides = {}
        self.state_errors = {}
        self.running_record_errors = {}

    def bind_release_root(self, root):
        self.release_root = root.resolve()

    def set_running_release(self, unit, release):
        self.next_pid += 1
        self.invocation_number += 1
        invocation_id = f"{self.invocation_number:032x}"
        self.running_records[unit] = {
            "package_path": str(release.resolve() / "pi"),
            "pid": self.next_pid,
            "invocation_id": invocation_id,
        }

    def read_unit(self, unit):
        return self.units[unit]

    def dropins(self, unit):
        return dict(self.dropins_by_unit[unit])

    def save_legacy(self, unit, destination, dropins):
        self.saved_legacy.append((unit, destination, dict(dropins)))
        if destination.exists():
            if not (destination / unit).is_file():
                raise ValueError("incomplete fake backup")
            return
        destination.mkdir(parents=True, mode=0o700)
        (destination / unit).write_bytes(self.units[unit])
        if dropins:
            directory = destination / (unit + ".d")
            directory.mkdir()
            for filename, data in dropins.items():
                (directory / filename).write_bytes(data)

    def install_unit(self, unit, source):
        self.install_calls.append((unit, source))
        self.units[unit] = source.read_bytes()

    def is_active(self, unit):
        return unit in self.active

    def service_state(self, unit):
        if unit in self.state_errors:
            raise self.state_errors[unit]
        if unit in self.state_overrides:
            return dict(self.state_overrides[unit])
        if unit not in self.active:
            return {"ActiveState": "inactive", "MainPID": "0", "InvocationID": ""}
        record = self.running_records[unit]
        if record is None:
            return {
                "ActiveState": "active",
                "MainPID": str(self.next_pid),
                "InvocationID": f"{self.invocation_number:032x}",
            }
        return {
            "ActiveState": "active",
            "MainPID": str(record["pid"]),
            "InvocationID": record["invocation_id"],
        }

    def read_running_record(self, unit):
        if unit in self.running_record_errors:
            raise self.running_record_errors[unit]
        record = self.running_records[unit]
        if record is None:
            raise FileNotFoundError(f"{unit} running record is absent")
        return dict(record)

    def restart(self, unit):
        self.restart_calls.append(unit)
        if self.restart_failures.get(unit, 0):
            self.restart_failures[unit] -= 1
            self.active.discard(unit)
            raise RuntimeError(f"simulated {unit} restart failure")
        self.active.add(unit)
        if self.release_root is not None:
            current = deployment.current_release(self.release_root)
            if current is None:
                raise RuntimeError("package restart requires a current release")
            self.set_running_release(unit, current)


class InterruptedArchive(io.BytesIO):
    def __init__(self, data, cutoff):
        super().__init__(data)
        self.cutoff = cutoff

    def read(self, size=-1):
        position = self.tell()
        if position >= self.cutoff:
            raise OSError("simulated interrupted archive stream")
        if size < 0 or position + size > self.cutoff:
            size = self.cutoff - position
        return super().read(size)

    def readinto(self, buffer):
        data = self.read(len(buffer))
        buffer[: len(data)] = data
        return len(data)


class PythonDeploymentTests(unittest.TestCase):
    @staticmethod
    def _git(root, *args):
        return subprocess.run(
            ["/usr/bin/git", "-C", str(root), *args],
            check=True,
            capture_output=True,
            text=True,
        )

    @classmethod
    def _commit_fixture(cls, root):
        cls._git(root, "init", "--quiet")
        cls._git(root, "config", "user.email", "fixture@example.invalid")
        cls._git(root, "config", "user.name", "Python deployment fixture")
        cls._git(root, "add", ".")
        cls._git(root, "commit", "--quiet", "-m", "fixture")

    @classmethod
    def _populate_fixture(cls, root, copy_script=False):
        for relative in deployment.INITIALIZERS:
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("PACKAGE_MARKER = 'initializer'\n", encoding="utf-8")

        for directory, names in FIXTURE_MODULES.items():
            directory_path = root / directory
            directory_path.mkdir(parents=True, exist_ok=True)
            for name in names:
                value = f"{directory}/{name}"
                (directory_path / name).write_text(
                    f"SOURCE_MARKER = {value!r}\n", encoding="utf-8"
                )

        for relative in deployment.ASSETS:
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"asset marker: {relative}\n", encoding="utf-8")

        unit_directory = root / "pi" / "services"
        unit_directory.mkdir(parents=True, exist_ok=True)
        for unit in deployment.SERVICES:
            (unit_directory / unit).write_bytes(package_unit(unit))

        if copy_script:
            destination = root / "pi" / "deploy_python.py"
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(DEPLOY_SCRIPT, destination)

        cls._commit_fixture(root)

    @classmethod
    @contextmanager
    def fixture(cls, copy_script=False):
        with tempfile.TemporaryDirectory(prefix="python-deployment-") as name:
            root = Path(name).resolve()
            cls._populate_fixture(root, copy_script=copy_script)
            yield root

    @staticmethod
    def _archive(plan, repo):
        output = io.BytesIO()
        deployment.make_archive(plan, repo, output)
        return output.getvalue()

    @staticmethod
    def _services(active=(deployment.DASHBOARD,)):
        return FakeSystemServices(active=set(active))

    @staticmethod
    def _install(plan, repo, root, mode, services=None, selected=(deployment.DASHBOARD,)):
        if services is not None and hasattr(services, "bind_release_root"):
            services.bind_release_root(root)
        stream = io.BytesIO(PythonDeploymentTests._archive(plan, repo))
        return deployment.install_release(
            stream,
            root,
            mode,
            services=services,
            selected=selected,
        )

    @staticmethod
    def _current_snapshot(root):
        active = deployment.current_release(root)
        files = {
            path.relative_to(active).as_posix(): path.read_bytes()
            for path in active.rglob("*")
            if path.is_file()
        }
        return os.readlink(root / "current"), files

    @staticmethod
    def _manual_archive(manifest, repo, omit=(), replacements=None, extra=None, symlink=None):
        replacements = replacements or {}
        omit = set(omit)
        with io.BytesIO() as output:
            with tarfile.open(fileobj=output, mode="w") as archive:
                raw = deployment.encoded(manifest)
                member = tarfile.TarInfo("manifest.json")
                member.size = len(raw)
                member.mode = 0o644
                archive.addfile(member, io.BytesIO(raw))
                for relative in manifest["files"]:
                    if relative in omit:
                        continue
                    member = tarfile.TarInfo(relative)
                    member.mode = 0o644
                    if relative == symlink:
                        member.type = tarfile.SYMTYPE
                        member.linkname = "outside"
                        member.size = 0
                        archive.addfile(member)
                        continue
                    data = replacements.get(relative, (repo / relative).read_bytes())
                    member.size = len(data)
                    archive.addfile(member, io.BytesIO(data))
                if extra is not None:
                    name, data = extra
                    member = tarfile.TarInfo(name)
                    member.mode = 0o644
                    member.size = len(data)
                    archive.addfile(member, io.BytesIO(data))
            return output.getvalue()

    @staticmethod
    def _make_release(root, marker, mtime_ns=None):
        payload = f"RELEASE_MARKER = {marker!r}\n".encode()
        manifest = {"schema": 1, "files": {"payload.py": deployment.digest(payload)}}
        release_id = deployment.digest(deployment.encoded(manifest))[:24]
        release = root / "releases" / release_id
        release.mkdir(parents=True)
        (release / "payload.py").write_bytes(payload)
        (release / "manifest.json").write_bytes(deployment.encoded(manifest))
        if mtime_ns is not None:
            os.utime(release, ns=(mtime_ns, mtime_ns))
        return release

    @classmethod
    @contextmanager
    def _gc_root(cls, count=6):
        with tempfile.TemporaryDirectory(prefix="python-release-gc-") as name:
            root = Path(name).resolve() / "packages"
            (root / "releases").mkdir(parents=True)
            releases = [
                cls._make_release(root, f"seed-{index}", (index + 1) * 1_000_000_000)
                for index in range(count)
            ]
            services = FakeSystemServices(active=set())
            services.bind_release_root(root)
            yield root, releases, services

    @staticmethod
    def _collect_gc(root, services, installed, mount_points=None):
        import fcntl

        mounts = [Path("/")] if mount_points is None else mount_points
        if isinstance(mounts, BaseException):
            mount_patch = mock.patch.object(deployment, "mount_points", side_effect=mounts)
        else:
            mount_patch = mock.patch.object(deployment, "mount_points", return_value=mounts)
        with (root / ".install.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            with mount_patch:
                return deployment.collect_releases(root, lock, services, installed)

    @staticmethod
    def _entrypoint_modules(runtime_dir, production_main):
        apps = types.ModuleType("pi.apps")
        apps.__path__ = []
        package = types.ModuleType("pi.apps.van_dashboard")
        package.__path__ = []
        dashboard = types.ModuleType("pi.apps.van_dashboard.van_dashboard")
        dashboard.main = production_main
        common = types.ModuleType("pi.apps.van_dashboard.van_dashboard_common")
        common.RUNTIME_DIR = runtime_dir
        return {
            "pi.apps": apps,
            "pi.apps.van_dashboard": package,
            "pi.apps.van_dashboard.van_dashboard": dashboard,
            "pi.apps.van_dashboard.van_dashboard_common": common,
        }

    @staticmethod
    def _network_poison(root):
        fake_bin = root / "poisoned-bin"
        fake_bin.mkdir()
        marker = root / "network-invocations"
        marker_literal = str(marker).replace("'", "'\\''")
        body = (
            "#!/bin/sh\n"
            f"printf '%s\\n' \"$0\" >> '{marker_literal}'\n"
            "exit 97\n"
        )
        for name in ("ssh", "scp", "rsync", "curl", "wget", "systemctl", "launchctl"):
            executable = fake_bin / name
            executable.write_text(body, encoding="utf-8")
            executable.chmod(0o755)
        return fake_bin, marker

    def test_source_files_build_plan_and_archive_are_allowlisted(self):
        with self.fixture() as repo:
            files = deployment.source_files(repo)
            expected = set(deployment.INITIALIZERS) | set(deployment.ASSETS)
            expected.update(f"pi/services/{unit}" for unit in deployment.SERVICES)
            expected.update(
                f"{directory}/{name}"
                for directory, names in FIXTURE_MODULES.items()
                for name in names
            )
            self.assertIn("pi/package_runtime.py", expected)
            self.assertEqual(set(files), expected)
            self.assertEqual(files, sorted(files))
            self.assertTrue(all(not Path(path).is_absolute() for path in files))

            plan = deployment.build_plan(repo, mode="stage")
            self.assertEqual(set(plan["manifest"]["services"]), set(deployment.SERVICES))
            self.assertEqual(plan["manifest"]["files"], {
                relative: deployment.digest((repo / relative).read_bytes())
                for relative in sorted(expected)
            })
            self.assertEqual(plan["manifest"]["provenance"]["checkout"], str(repo))
            self.assertEqual(
                plan["release"],
                deployment.digest(deployment.encoded(plan["manifest"]))[:24],
            )
            self.assertTrue(all(
                Path(item["source"]).is_relative_to(repo)
                and Path(item["source"]) == repo / item["relative"]
                for item in plan["sources"]
            ))

            archive_bytes = self._archive(plan, repo)
            with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:") as archive:
                members = archive.getmembers()
                self.assertEqual(members[0].name, "manifest.json")
                self.assertEqual({member.name for member in members}, {"manifest.json"} | expected)
                self.assertEqual(json.loads(archive.extractfile(members[0]).read()), plan["manifest"])

            changed = repo / "pi/apps/audiobooks/audiobook_server.py"
            changed.write_text("SOURCE_MARKER = 'changed after planning'\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                deployment.make_archive(plan, repo, io.BytesIO())

    def test_service_digests_cover_only_each_services_runtime_dependencies(self):
        cases = {
            "pi/apps/van_dashboard/routes/common.py": {deployment.DASHBOARD},
            "pi/apps/video_library/players/vlc_player.py": {"video-library.service"},
            "pi/apps/video_library/static/video_library.js": {"video-library.service"},
            "shared/python/sonos_tasks.py": {"video-library.service"},
            "shared/__init__.py": {"video-library.service"},
            "shared/python/__init__.py": {"video-library.service"},
            "pi/apps/audiobooks/audiobook_server.py": {"audiobooks.service"},
            "pi/apps/bme280/bme280_mqtt.py": {"bme280-mqtt.service"},
            "pi/package_runtime.py": set(deployment.SERVICES),
            "pi/__init__.py": set(deployment.SERVICES),
            "pi/apps/__init__.py": set(deployment.SERVICES),
            "pi/scripts/__init__.py": set(),
        }
        for relative, expected in cases.items():
            with self.subTest(relative=relative), self.fixture() as repo:
                before = deployment.build_plan(repo)["manifest"]["services"]
                path = repo / relative
                path.write_text(path.read_text() + "CHANGED = True\n")
                after = deployment.build_plan(repo)["manifest"]["services"]
                changed = {unit for unit in deployment.SERVICES if before[unit] != after[unit]}
                self.assertEqual(changed, expected)

    def test_build_plan_selection_is_scoped_and_defaults_to_all(self):
        with self.fixture() as repo:
            default = deployment.build_plan(repo, mode="update")
            self.assertTrue(all("not selected" not in value for value in default["units"].values()))
            scoped = deployment.build_plan(repo, mode="update", selected=[deployment.DASHBOARD])
            self.assertNotIn("not selected", scoped["units"][deployment.DASHBOARD])
            for unit in set(deployment.SERVICES) - {deployment.DASHBOARD}:
                self.assertIn("not selected", scoped["units"][unit])
            self.assertEqual(default["release"], scoped["release"])
            with self.assertRaises(ValueError):
                deployment.build_plan(repo, selected=[])
            with self.assertRaises(ValueError):
                deployment.build_plan(repo, selected=["foreign.service"])

    def test_source_files_rejects_symlinked_source(self):
        with self.fixture() as repo:
            outside = repo / "outside.py"
            outside.write_text("SOURCE_MARKER = 'outside'\n", encoding="utf-8")
            (repo / "pi/scripts/python/unsafe.py").symlink_to(outside)
            with self.assertRaises(ValueError):
                deployment.source_files(repo)

    def test_minimal_checkout_dry_run_is_local_and_legacy_is_frozen(self):
        with self.fixture(copy_script=True) as repo:
            fake_bin, marker = self._network_poison(repo)
            environment = os.environ.copy()
            environment["PATH"] = f"{fake_bin}:/usr/bin:/bin"
            script = str(repo / "pi/deploy_python.py")
            result = subprocess.run(
                [sys.executable, script, "--dry-run"], cwd=repo, env=environment,
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            plan = json.loads(result.stdout)
            self.assertEqual(plan["manifest"]["provenance"]["checkout"], str(repo))
            self.assertFalse(marker.exists())

            legacy = subprocess.run(
                [sys.executable, script, "--dry-run", "--legacy-flatten"],
                cwd=repo, env=environment, capture_output=True, text=True, check=False,
            )
            self.assertNotEqual(legacy.returncode, 0)
            self.assertIn("retired", legacy.stderr)
            self.assertFalse(marker.exists())

            for option, expected_mode in (("--activate", "activate"), ("--update", "update")):
                mode_result = subprocess.run(
                    [sys.executable, script, "--dry-run", option, "--service", deployment.DASHBOARD],
                    cwd=repo, env=environment, capture_output=True, text=True, check=False,
                )
                self.assertEqual(mode_result.returncode, 0, mode_result.stderr)
                mode_plan = json.loads(mode_result.stdout)
                self.assertEqual(mode_plan["mode"], expected_mode)
                self.assertIn("not selected", mode_plan["units"]["video-library.service"])
                self.assertFalse(marker.exists())

    def test_actual_repository_dry_run_contains_only_direct_allowlisted_sources(self):
        with tempfile.TemporaryDirectory(prefix="python-deployment-network-") as name:
            fake_bin, marker = self._network_poison(Path(name))
            environment = os.environ.copy()
            environment["PATH"] = f"{fake_bin}:/usr/bin:/bin"
            result = subprocess.run(
                [sys.executable, str(DEPLOY_SCRIPT), "--dry-run"],
                cwd=REPOSITORY_ROOT, env=environment, capture_output=True, text=True, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            plan = json.loads(result.stdout)
            expected_python = {
                f"{directory}/{path.name}"
                for directory in deployment.MODULE_DIRS
                for path in (REPOSITORY_ROOT / directory).glob("*.py")
            }
            expected = (
                expected_python | set(deployment.INITIALIZERS) | set(deployment.ASSETS)
                | {f"pi/services/{unit}" for unit in deployment.SERVICES}
            )
            self.assertEqual(set(plan["manifest"]["files"]), expected)
            self.assertEqual(set(deployment.source_files(REPOSITORY_ROOT)), expected)
            for relative in expected:
                parts = PurePosixPath(relative).parts
                for excluded in ("van_compute", "tests", "secrets", "frontend", "node_modules", "__pycache__", ".pytest_cache"):
                    self.assertNotIn(excluded, parts)
            self.assertFalse(marker.exists())

    def test_legacy_modes_refuse_before_writing(self):
        with self.fixture() as repo, tempfile.TemporaryDirectory() as name:
            root = Path(name) / "packages"
            with self.assertRaisesRegex(ValueError, "legacy flatten is retired"):
                deployment.build_plan(repo, mode="legacy")
            with self.assertRaisesRegex(ValueError, "legacy flatten is retired"):
                deployment.install_release(io.BytesIO(b"not even an archive"), root, "legacy")
            self.assertFalse(root.exists())

    def test_stage_leaves_flat_sentinel_untouched_and_has_no_current(self):
        with self.fixture() as repo, tempfile.TemporaryDirectory() as name:
            root = Path(name) / "packages"
            sentinel = Path(name) / "audiobook_server.py"
            sentinel.write_bytes(b"old flat sentinel")
            plan = deployment.build_plan(repo, mode="stage")
            result = self._install(plan, repo, root, "stage", selected=None)
            self.assertEqual(result["restarted"], [])
            self.assertFalse((root / "current").exists())
            self.assertEqual(sentinel.read_bytes(), b"old flat sentinel")
            self.assertTrue((root / "releases" / plan["release"] / "manifest.json").is_file())

    def test_first_dashboard_activation_is_explicit_and_saves_unit_and_state(self):
        with self.fixture() as repo, tempfile.TemporaryDirectory() as name:
            root = Path(name) / "packages"
            services = self._services()
            plan = deployment.build_plan(repo)
            archive = self._archive(plan, repo)
            with self.assertRaises(ValueError):
                deployment.install_release(io.BytesIO(archive), root, "update", services=services,
                                           selected=[deployment.DASHBOARD])
            self.assertFalse((root / "current").exists())
            self.assertFalse((root / "pre-package-units").exists())
            self.assertEqual(services.restart_calls, [])

            result = deployment.install_release(
                io.BytesIO(archive), root, "activate", services=services,
                selected=[deployment.DASHBOARD],
            )
            backup = root / "pre-package-units" / (deployment.DASHBOARD + ".backup")
            self.assertEqual(result["restarted"], [deployment.DASHBOARD])
            self.assertEqual((backup / deployment.DASHBOARD).read_bytes(), flat_unit(deployment.DASHBOARD))
            self.assertEqual(
                json.loads((root / "service-state" / (deployment.DASHBOARD + ".json")).read_bytes()),
                {"release": plan["release"], "digest": plan["manifest"]["services"][deployment.DASHBOARD]},
            )
            self.assertEqual(json.loads((root / "activated.json").read_bytes()), {"release": plan["release"]})
            self.assertFalse((root / "pending-restarts" / (deployment.DASHBOARD + ".json")).exists())

    def test_all_four_activation_saves_each_unit_and_restarts_only_active(self):
        with self.fixture() as repo, tempfile.TemporaryDirectory() as name:
            root = Path(name) / "packages"
            active = {deployment.DASHBOARD, "audiobooks.service"}
            services = self._services(active=active)
            plan = deployment.build_plan(repo)
            services.bind_release_root(root)
            result = deployment.install_release(
                io.BytesIO(self._archive(plan, repo)), root, "activate", services=services
            )
            self.assertEqual(result["restarted"], [
                unit for unit in deployment.SERVICES if unit in active
            ])
            self.assertEqual(set(services.units), set(deployment.SERVICES))
            for unit in deployment.SERVICES:
                backup = root / "pre-package-units" / (unit + ".backup")
                self.assertEqual((backup / unit).read_bytes(), flat_unit(unit))
                state = json.loads((root / "service-state" / (unit + ".json")).read_bytes())
                self.assertEqual(state, {
                    "release": plan["release"],
                    "digest": plan["manifest"]["services"][unit],
                })
                self.assertEqual(services.units[unit], package_unit(unit))

    def test_all_four_noop_update_repeats_without_install_or_restart(self):
        with self.fixture() as repo, tempfile.TemporaryDirectory() as name:
            root = Path(name) / "packages"
            units = list(deployment.SERVICES)
            services = self._services(active=set(units))
            plan = deployment.build_plan(repo)
            services.bind_release_root(root)
            deployment.install_release(
                io.BytesIO(self._archive(plan, repo)), root, "activate", services=services
            )
            services.install_calls.clear()
            services.restart_calls.clear()

            result = deployment.install_release(
                io.BytesIO(self._archive(plan, repo)), root, "update", services=services
            )
            self.assertEqual(result["restarted"], [])
            self.assertEqual(services.install_calls, [])
            self.assertEqual(services.restart_calls, [])
            self.assertFalse(any((root / "pending-restarts").glob("*.json")))
            for unit in units:
                self.assertEqual(
                    json.loads((root / "service-state" / (unit + ".json")).read_bytes()),
                    {
                        "release": plan["release"],
                        "digest": plan["manifest"]["services"][unit],
                    },
                )

    def test_scoped_non_dashboard_activation_can_be_routinely_updated(self):
        unit = "video-library.service"
        with self.fixture() as repo, tempfile.TemporaryDirectory() as name:
            root = Path(name) / "packages"
            services = self._services(active=(unit,))
            first = deployment.build_plan(repo, selected=[unit])
            self._install(first, repo, root, "activate", services=services, selected=[unit])
            services.restart_calls.clear()
            player = repo / "pi/apps/video_library/players/vlc_player.py"
            player.write_text(player.read_text() + "CHANGED = True\n")
            second = deployment.build_plan(repo, selected=[unit])
            result = self._install(second, repo, root, "update", services=services, selected=[unit])
            self.assertEqual(result["restarted"], [unit])
            self.assertEqual(services.restart_calls, [unit])
            self.assertFalse((root / "service-state" / (deployment.DASHBOARD + ".json")).exists())

    def test_reinstalling_same_release_does_not_restart_or_reinstall(self):
        with self.fixture() as repo, tempfile.TemporaryDirectory() as name:
            root = Path(name) / "packages"
            services = self._services()
            plan = deployment.build_plan(repo)
            self._install(plan, repo, root, "activate", services=services)
            services.restart_calls.clear()
            services.install_calls.clear()
            for mode in ("activate", "update"):
                result = self._install(plan, repo, root, mode, services=services)
                self.assertEqual(result["restarted"], [])
                self.assertEqual(services.restart_calls, [])
                self.assertEqual(services.install_calls, [])

    def test_scoped_module_and_unit_changes_restart_only_selected_service(self):
        with self.fixture() as repo, tempfile.TemporaryDirectory() as name:
            root = Path(name) / "packages"
            services = self._services()
            first = deployment.build_plan(repo)
            self._install(first, repo, root, "activate", services=services)
            services.restart_calls.clear()
            services.install_calls.clear()
            dashboard = repo / "pi/apps/van_dashboard/van_dashboard.py"
            dashboard.write_text("SOURCE_MARKER = 'dashboard v2'\n")
            second = deployment.build_plan(repo)
            result = self._install(second, repo, root, "update", services=services)
            self.assertEqual(result["restarted"], [deployment.DASHBOARD])
            self.assertEqual(services.install_calls, [])

            services.restart_calls.clear()
            unit_path = repo / "pi/services" / deployment.DASHBOARD
            unit_path.write_bytes(package_unit(deployment.DASHBOARD, "fixture package v2"))
            third = deployment.build_plan(repo)
            self.assertEqual(second["manifest"]["services"][deployment.DASHBOARD],
                             third["manifest"]["services"][deployment.DASHBOARD])
            result = self._install(third, repo, root, "update", services=services)
            self.assertEqual(result["restarted"], [deployment.DASHBOARD])
            self.assertEqual([call[0] for call in services.install_calls], [deployment.DASHBOARD])

    def test_unselected_change_moves_release_without_restarting_dashboard(self):
        with self.fixture() as repo, tempfile.TemporaryDirectory() as name:
            root = Path(name) / "packages"
            services = self._services()
            first = deployment.build_plan(repo)
            self._install(first, repo, root, "activate", services=services)
            services.restart_calls.clear()
            audiobook = repo / "pi/apps/audiobooks/audiobook_server.py"
            audiobook.write_text("SOURCE_MARKER = 'audiobook v2'\n")
            second = deployment.build_plan(repo)
            self.assertEqual(first["manifest"]["services"][deployment.DASHBOARD],
                             second["manifest"]["services"][deployment.DASHBOARD])
            result = self._install(second, repo, root, "update", services=services)
            self.assertEqual(result["restarted"], [])
            self.assertEqual(os.readlink(root / "current"), f"releases/{second['release']}")

    def test_all_selected_intents_are_journaled_before_switch_and_complete_incrementally(self):
        with self.fixture() as repo, tempfile.TemporaryDirectory() as name:
            root = Path(name) / "packages"
            units = list(deployment.SERVICES)
            services = self._services(active={deployment.DASHBOARD, "video-library.service", "audiobooks.service"})
            services.restart_failures["video-library.service"] = 1
            plan = deployment.build_plan(repo)
            real_replace = deployment.replace_link
            checked = False

            def assert_journaled(root_arg, link_name, target):
                nonlocal checked
                if link_name == "current":
                    checked = True
                    for selected in units:
                        pending = json.loads((root / "pending-restarts" / (selected + ".json")).read_bytes())
                        self.assertEqual(pending["release"], plan["release"])
                        self.assertEqual(
                            pending["digest"], plan["manifest"]["services"][selected]
                        )
                        self.assertIsInstance(pending["restart"], bool)
                return real_replace(root_arg, link_name, target)

            with mock.patch.object(deployment, "replace_link", side_effect=assert_journaled):
                with self.assertRaisesRegex(RuntimeError, "video-library"):
                    self._install(plan, repo, root, "activate", services=services, selected=units)
            self.assertTrue(checked)
            self.assertTrue((root / "service-state" / (deployment.DASHBOARD + ".json")).is_file())
            self.assertFalse((root / "pending-restarts" / (deployment.DASHBOARD + ".json")).exists())
            for unit in units[1:]:
                self.assertTrue((root / "pending-restarts" / (unit + ".json")).is_file())
                self.assertFalse((root / "service-state" / (unit + ".json")).exists())

            services.restart_calls.clear()
            self._install(plan, repo, root, "activate", services=services,
                          selected=["video-library.service"])
            self.assertEqual(services.restart_calls, ["video-library.service"])
            services.restart_calls.clear()
            self._install(plan, repo, root, "activate", services=services,
                          selected=["audiobooks.service"])
            self.assertEqual(services.restart_calls, ["audiobooks.service"])
            services.restart_calls.clear()
            self._install(plan, repo, root, "activate", services=services,
                          selected=["bme280-mqtt.service"])
            self.assertEqual(services.restart_calls, [])
            self.assertFalse(any((root / "pending-restarts").glob("*.json")))
            self.assertEqual({p.name for p in (root / "service-state").glob("*.json")},
                             {unit + ".json" for unit in units})

    def test_pending_reload_failure_reinstalls_unit_before_retry(self):
        with self.fixture() as repo, tempfile.TemporaryDirectory() as name:
            root = Path(name) / "packages"
            services = self._services()
            plan = deployment.build_plan(repo)
            original = services.install_unit

            def fail_after_copy(unit, source):
                original(unit, source)
                raise RuntimeError("simulated daemon-reload failure")

            services.install_unit = fail_after_copy
            with self.assertRaisesRegex(RuntimeError, "daemon-reload"):
                self._install(plan, repo, root, "activate", services=services)
            pending = json.loads((root / "pending-restarts" / (deployment.DASHBOARD + ".json")).read_bytes())
            self.assertTrue(pending["restart"])
            services.install_unit = original
            self._install(plan, repo, root, "activate", services=services)
            self.assertEqual(len(services.install_calls), 2)
            self.assertEqual(services.restart_calls, [deployment.DASHBOARD])

    def test_inactive_service_pending_reload_retry_does_not_start_it(self):
        unit = "audiobooks.service"
        with self.fixture() as repo, tempfile.TemporaryDirectory() as name:
            root = Path(name) / "packages"
            services = self._services(active=set())
            plan = deployment.build_plan(repo, selected=[unit])
            original = services.install_unit

            def fail_after_copy(selected, source):
                original(selected, source)
                raise RuntimeError("simulated inactive daemon-reload failure")

            services.install_unit = fail_after_copy
            with self.assertRaisesRegex(RuntimeError, "inactive daemon-reload"):
                self._install(plan, repo, root, "activate", services=services, selected=[unit])
            pending_path = root / "pending-restarts" / (unit + ".json")
            pending = json.loads(pending_path.read_bytes())
            self.assertEqual(pending, {
                "release": plan["release"],
                "digest": plan["manifest"]["services"][unit],
                "restart": False,
            })
            self.assertNotIn(unit, services.active)
            self.assertEqual(services.restart_calls, [])

            services.install_unit = original
            result = self._install(plan, repo, root, "activate", services=services, selected=[unit])
            self.assertEqual(result["restarted"], [])
            self.assertEqual(len(services.install_calls), 2)
            self.assertEqual(services.restart_calls, [])
            self.assertNotIn(unit, services.active)
            self.assertFalse(pending_path.exists())
            self.assertTrue((root / "service-state" / (unit + ".json")).is_file())

    def test_update_preserves_intentionally_inactive_service(self):
        with self.fixture() as repo, tempfile.TemporaryDirectory() as name:
            root = Path(name) / "packages"
            services = self._services()
            self._install(deployment.build_plan(repo), repo, root, "activate", services=services)
            services.active.remove(deployment.DASHBOARD)
            services.restart_calls.clear()
            (repo / "pi/apps/van_dashboard/van_dashboard.py").write_text("CHANGED = True\n")
            plan = deployment.build_plan(repo)
            result = self._install(plan, repo, root, "update", services=services)
            self.assertEqual(result["restarted"], [])
            self.assertEqual(services.restart_calls, [])

    def test_legacy_backup_is_never_overwritten_including_dropins(self):
        with self.fixture() as repo, tempfile.TemporaryDirectory() as name:
            root = Path(name) / "packages"
            services = self._services()
            benign = b"[Service]\nRestartSec=19\n"
            services.dropins_by_unit[deployment.DASHBOARD] = {"site.conf": benign}
            plan = deployment.build_plan(repo)
            self._install(plan, repo, root, "activate", services=services)
            backup = root / "pre-package-units" / (deployment.DASHBOARD + ".backup")
            unit_backup = backup / deployment.DASHBOARD
            dropin_backup = backup / (deployment.DASHBOARD + ".d") / "site.conf"
            unit_backup.write_bytes(b"operator unit sentinel")
            dropin_backup.write_bytes(b"operator drop-in sentinel")
            services.dropins_by_unit[deployment.DASHBOARD] = {"site.conf": b"[Service]\nRestartSec=20\n"}
            self._install(plan, repo, root, "activate", services=services)
            self.assertEqual(unit_backup.read_bytes(), b"operator unit sentinel")
            self.assertEqual(dropin_backup.read_bytes(), b"operator drop-in sentinel")
            self.assertEqual(len(services.saved_legacy), 1)

    def test_foreign_unit_or_overriding_dropin_refuses_all_selected_before_cutover(self):
        for case in ("foreign-unit", "dropin"):
            with self.subTest(case=case), self.fixture() as repo, tempfile.TemporaryDirectory() as name:
                root = Path(name) / "packages"
                services = self._services(active=set())
                selected = [deployment.DASHBOARD, "video-library.service"]
                if case == "foreign-unit":
                    services.units["video-library.service"] = b"[Service]\nExecStart=/bin/foreign\n"
                else:
                    services.dropins_by_unit["video-library.service"] = {
                        "override.conf": b"[Service]\nExecStart=\nExecStart=/bin/foreign\n"
                    }
                before = dict(services.units)
                with self.assertRaises(ValueError):
                    self._install(deployment.build_plan(repo), repo, root, "activate",
                                  services=services, selected=selected)
                self.assertFalse((root / "current").exists())
                self.assertFalse((root / "pre-package-units").exists())
                self.assertFalse((root / "pending-restarts").exists())
                self.assertEqual(services.units, before)
                self.assertEqual(services.install_calls, [])
                self.assertEqual(services.restart_calls, [])

    def test_old_dashboard_activation_marker_migrates_from_recognized_manifest(self):
        with self.fixture() as repo, tempfile.TemporaryDirectory() as name:
            root = Path(name) / "packages"
            services = self._services()
            first = deployment.build_plan(repo)
            self._install(first, repo, root, "stage", services=services, selected=None)
            deployment.replace_link(root, "current", "releases/" + first["release"])
            deployment.atomic_json(root / "activated.json", {"release": first["release"]})
            old_backup = root / "pre-package-units" / deployment.DASHBOARD
            old_backup.parent.mkdir()
            old_backup.write_bytes(flat_unit(deployment.DASHBOARD))
            services.units[deployment.DASHBOARD] = package_unit(deployment.DASHBOARD)
            (repo / "pi/apps/van_dashboard/van_dashboard.py").write_text("CHANGED = True\n")
            second = deployment.build_plan(repo)
            result = self._install(second, repo, root, "update", services=services)
            self.assertEqual(result["restarted"], [deployment.DASHBOARD])
            state = json.loads((root / "service-state" / (deployment.DASHBOARD + ".json")).read_bytes())
            self.assertEqual(state["release"], second["release"])
            self.assertEqual(old_backup.read_bytes(), flat_unit(deployment.DASHBOARD))
            self.assertFalse((root / "pre-package-units" / (deployment.DASHBOARD + ".backup")).exists())

    def test_legacy_dashboard_migration_uses_marker_release_after_other_service_advances_current(self):
        with self.fixture() as repo, tempfile.TemporaryDirectory() as name:
            root = Path(name) / "packages"
            services = self._services()

            release_a = deployment.build_plan(repo)
            self._install(release_a, repo, root, "stage", services=services, selected=None)
            deployment.replace_link(root, "current", "releases/" + release_a["release"])
            deployment.atomic_json(root / "activated.json", {"release": release_a["release"]})
            old_backup = root / "pre-package-units" / deployment.DASHBOARD
            old_backup.parent.mkdir()
            old_backup.write_bytes(flat_unit(deployment.DASHBOARD))
            services.units[deployment.DASHBOARD] = package_unit(deployment.DASHBOARD)

            dashboard = repo / "pi/apps/van_dashboard/van_dashboard.py"
            dashboard.write_text("SOURCE_MARKER = 'dashboard release B'\n")
            release_b = deployment.build_plan(repo)
            self.assertNotEqual(
                release_a["manifest"]["services"][deployment.DASHBOARD],
                release_b["manifest"]["services"][deployment.DASHBOARD],
            )

            video = "video-library.service"
            video_result = self._install(
                release_b, repo, root, "activate", services=services, selected=[video]
            )
            self.assertEqual(video_result["restarted"], [])
            self.assertEqual(deployment.current_release(root).name, release_b["release"])
            self.assertEqual(
                json.loads((root / "activated.json").read_bytes()),
                {"release": release_a["release"]},
            )
            self.assertFalse(
                (root / "service-state" / (deployment.DASHBOARD + ".json")).exists()
            )

            services.install_calls.clear()
            services.restart_calls.clear()
            dashboard_result = self._install(
                release_b, repo, root, "update", services=services,
                selected=[deployment.DASHBOARD],
            )
            self.assertEqual(dashboard_result["restarted"], [deployment.DASHBOARD])
            self.assertEqual(services.restart_calls, [deployment.DASHBOARD])
            self.assertEqual(services.install_calls, [])
            self.assertEqual(
                json.loads(
                    (root / "service-state" / (deployment.DASHBOARD + ".json")).read_bytes()
                ),
                {
                    "release": release_b["release"],
                    "digest": release_b["manifest"]["services"][deployment.DASHBOARD],
                },
            )
            self.assertEqual(
                json.loads((root / "activated.json").read_bytes()),
                {"release": release_b["release"]},
            )

    def test_old_dashboard_marker_with_unrecognized_manifest_refuses_safely(self):
        with self.fixture() as repo, tempfile.TemporaryDirectory() as name:
            root = Path(name) / "packages"
            services = self._services()
            first = deployment.build_plan(repo)
            self._install(first, repo, root, "stage", services=services, selected=None)
            release = root / "releases" / first["release"]
            manifest = json.loads((release / "manifest.json").read_bytes())
            manifest["services"].pop(deployment.DASHBOARD)
            (release / "manifest.json").write_bytes(deployment.encoded(manifest))
            deployment.replace_link(root, "current", "releases/" + first["release"])
            deployment.atomic_json(root / "activated.json", {"release": first["release"]})
            services.units[deployment.DASHBOARD] = package_unit(deployment.DASHBOARD)
            before = os.readlink(root / "current")
            with self.assertRaises(ValueError):
                self._install(deployment.build_plan(repo), repo, root, "update", services=services)
            self.assertEqual(os.readlink(root / "current"), before)
            self.assertEqual(services.install_calls, [])
            self.assertEqual(services.restart_calls, [])

    def test_update_refuses_each_service_after_flat_rollback_even_with_backup(self):
        for unit in deployment.SERVICES:
            with self.subTest(unit=unit), self.fixture() as repo, tempfile.TemporaryDirectory() as name:
                root = Path(name) / "packages"
                services = self._services(active=set())
                # Establish the global dashboard marker as production already has it.
                selected = list(deployment.SERVICES) if unit != deployment.DASHBOARD else [deployment.DASHBOARD]
                self._install(deployment.build_plan(repo), repo, root, "activate",
                              services=services, selected=selected)
                state = root / "service-state" / (unit + ".json")
                state.unlink()
                services.units[unit] = flat_unit(unit, "operator rollback")
                before_link = os.readlink(root / "current")
                before_unit = services.units[unit]
                with self.assertRaisesRegex(ValueError, "explicit --activate"):
                    self._install(deployment.build_plan(repo), repo, root, "update",
                                  services=services, selected=[unit])
                self.assertEqual(os.readlink(root / "current"), before_link)
                self.assertEqual(services.units[unit], before_unit)
                self.assertEqual(services.restart_calls, [])

    def test_invalid_archives_never_change_current(self):
        with self.fixture() as repo, tempfile.TemporaryDirectory() as name:
            root = Path(name) / "packages"
            services = self._services()
            plan = deployment.build_plan(repo)
            self._install(plan, repo, root, "activate", services=services)
            before = self._current_snapshot(root)
            manifest = plan["manifest"]
            first_relative = next(iter(manifest["files"]))
            last_relative = next(reversed(manifest["files"]))
            invalid_inputs = (
                ("corrupt", io.BytesIO(self._manual_archive(
                    manifest, repo, replacements={first_relative: b"not expected"})), (ValueError,)),
                ("traversal", io.BytesIO(self._manual_archive(
                    manifest, repo, extra=("../outside.py", b"escape"))), (ValueError,)),
                ("symlink", io.BytesIO(self._manual_archive(
                    manifest, repo, symlink=first_relative)), (ValueError,)),
                ("incomplete", io.BytesIO(self._manual_archive(
                    manifest, repo, omit=(last_relative,))), (ValueError,)),
                ("interrupted", InterruptedArchive(
                    self._archive(plan, repo), max(1024, len(self._archive(plan, repo)) // 2)),
                 (OSError, tarfile.TarError)),
            )
            for label, stream, errors in invalid_inputs:
                with self.subTest(label=label):
                    with self.assertRaises(errors):
                        deployment.install_release(stream, root, "update", services=services,
                                                   selected=[deployment.DASHBOARD])
                    self.assertEqual(self._current_snapshot(root), before)

    def test_unsupported_current_path_is_refused_without_changes(self):
        with self.fixture() as repo, tempfile.TemporaryDirectory() as name:
            root = Path(name) / "packages"
            releases = root / "releases"
            releases.mkdir(parents=True)
            (root / "current").symlink_to("releases/not-a-release")
            with self.assertRaises(ValueError):
                self._install(deployment.build_plan(repo), repo, root, "update", services=self._services())
            self.assertEqual(os.readlink(root / "current"), "releases/not-a-release")
            self.assertEqual(list(releases.iterdir()), [])

    def test_receiver_refuses_symlinked_release_directories(self):
        with self.fixture() as repo:
            plan = deployment.build_plan(repo)
            for component in ("root", "releases", "release"):
                with self.subTest(component=component), tempfile.TemporaryDirectory() as name:
                    base = Path(name)
                    root = base / "packages"
                    outside = base / "outside"
                    outside.mkdir()
                    if component == "root":
                        root.symlink_to(outside, target_is_directory=True)
                    else:
                        root.mkdir()
                        if component == "releases":
                            (root / "releases").symlink_to(outside, target_is_directory=True)
                        else:
                            (root / "releases").mkdir()
                            (root / "releases" / plan["release"]).symlink_to(outside, target_is_directory=True)
                    with self.assertRaises(ValueError):
                        self._install(plan, repo, root, "stage", selected=None)
                    self.assertEqual(list(outside.iterdir()), [])
                    self.assertFalse((root / "current").exists())

    def test_mount_points_keeps_same_device_mounts_and_decodes_paths(self):
        mountinfo = (
            "36 25 0:31 / / rw,relatime - apfs /dev/disk1s1 rw\n"
            "37 36 0:31 /bind /tmp/same\\040device rw - apfs /dev/disk1s1 rw\n"
        )
        with mock.patch.object(Path, "read_text", return_value=mountinfo):
            self.assertEqual(deployment.mount_points(), [Path("/"), Path("/tmp/same device")])
        for label, value in (
            ("empty", ""), ("malformed", "too few fields\n"),
            ("nonabsolute", "37 36 0:31 /bind relative rw - apfs disk rw\n"),
        ):
            with self.subTest(label=label), mock.patch.object(Path, "read_text", return_value=value):
                with self.assertRaises(ValueError):
                    deployment.mount_points()
        with mock.patch.object(Path, "read_text", side_effect=PermissionError("mountinfo unreadable")):
            with self.assertRaises(PermissionError):
                deployment.mount_points()

    def test_collect_releases_retains_four_independent_running_releases(self):
        with self._gc_root(count=13) as (root, releases, services):
            (root / "current").symlink_to(f"releases/{releases[0].name}")
            (root / "previous").symlink_to(f"releases/{releases[1].name}")
            installed = releases[2]
            for unit, release in zip(deployment.SERVICES, releases[3:7]):
                services.active.add(unit)
                services.set_running_release(unit, release)
            result = self._collect_gc(root, services, installed)
            expected = {
                release.name for release in
                [*releases[0:7], *releases[-deployment.RETAIN_NEWEST:]]
            }
            actual = {path.name for path in (root / "releases").iterdir()}
            self.assertEqual(result["status"], "ok")
            self.assertEqual(actual, expected)
            self.assertEqual(set(result["kept"]), expected)

    def test_collect_releases_deletes_at_most_sixteen_per_run(self):
        with self._gc_root(count=22) as (root, releases, services):
            first = self._collect_gc(root, services, releases[-1])
            self.assertEqual(first["status"], "ok")
            self.assertEqual(len(first["removed"]), 16)
            self.assertEqual(len(first["deferred"]), 3)
            second = self._collect_gc(root, services, releases[-1])
            self.assertEqual(second["status"], "ok")
            self.assertEqual(len(second["removed"]), 3)
            self.assertEqual(second["deferred"], [])
            self.assertEqual(
                {path.name for path in (root / "releases").iterdir()},
                {release.name for release in releases[-3:]},
            )

    def test_stage_activate_and_update_success_collect_old_releases(self):
        with self.fixture() as repo, tempfile.TemporaryDirectory() as name:
            root = Path(name).resolve() / "packages"
            old = [self._make_release(root, f"old-{index}", (index + 1) * 1_000_000_000)
                   for index in range(5)]
            backup = root / "backups/operator-copy"
            backup.parent.mkdir()
            backup.write_bytes(b"backup sentinel")
            services = self._services()
            with mock.patch.object(deployment, "mount_points", return_value=[Path("/")]):
                staged = self._install(deployment.build_plan(repo), repo, root, "stage",
                                       services=FakeSystemServices(active=set()), selected=None)
                self.assertEqual(staged["gc"]["status"], "ok")
                self.assertFalse(old[0].exists())
                first = deployment.build_plan(repo)
                activated = self._install(first, repo, root, "activate", services=services)
                self.assertEqual(activated["gc"]["status"], "ok")
                dashboard = repo / "pi/apps/van_dashboard/van_dashboard.py"
                dashboard.write_text("SOURCE_MARKER = 'gc update'\n")
                second = deployment.build_plan(repo)
                updated = self._install(second, repo, root, "update", services=services)
            self.assertEqual(updated["gc"]["status"], "ok")
            self.assertEqual(os.readlink(root / "current"), f"releases/{second['release']}")
            self.assertEqual(os.readlink(root / "previous"), f"releases/{first['release']}")
            self.assertEqual(backup.read_bytes(), b"backup sentinel")

    def test_collect_releases_skips_unsafe_filesystem_ambiguity(self):
        cases = (
            "same-device-mount", "mountinfo-error", "release-symlink", "nested-symlink",
            "unexpected-name", "invalid-manifest", "foreign-file", "writable-node",
            "hardlinked-node", "foreign-owner", "different-device", "missing-previous-target",
        )
        for case in cases:
            with self.subTest(case=case), self._gc_root() as (root, releases, services):
                candidate = releases[0]
                mounts = [Path("/")]
                outside_sentinels = []
                if case == "same-device-mount":
                    mounts.append(root / "releases" / releases[1].name / "bind")
                elif case == "mountinfo-error":
                    mounts = PermissionError("mountinfo unreadable")
                elif case == "release-symlink":
                    outside = root / "outside-release"
                    outside.mkdir()
                    sentinel = outside / "sentinel"
                    sentinel.write_bytes(b"outside")
                    outside_sentinels.append((sentinel, b"outside"))
                    shutil.rmtree(candidate)
                    candidate.symlink_to(outside, target_is_directory=True)
                elif case == "nested-symlink":
                    outside = root / "outside.py"
                    outside.write_bytes(b"outside")
                    outside_sentinels.append((outside, b"outside"))
                    (candidate / "payload.py").unlink()
                    (candidate / "payload.py").symlink_to(outside)
                elif case == "unexpected-name":
                    (root / "releases/backup-copy").mkdir()
                elif case == "invalid-manifest":
                    (candidate / "manifest.json").write_bytes(b"{not json")
                elif case == "foreign-file":
                    (candidate / "operator-notes.txt").write_bytes(b"keep me")
                elif case == "writable-node":
                    (candidate / "payload.py").chmod(0o666)
                elif case == "hardlinked-node":
                    outside = root / "outside-hardlink.py"
                    payload = candidate / "payload.py"
                    os.link(payload, outside)
                    outside_sentinels.append((outside, payload.read_bytes()))
                elif case == "missing-previous-target":
                    (root / "previous").symlink_to("releases/" + "f" * 24)

                backup = root / "backups/sentinel"
                backup.parent.mkdir(exist_ok=True)
                backup.write_bytes(b"untouched")
                before = {path.name for path in (root / "releases").iterdir()}
                if case in ("foreign-owner", "different-device"):
                    original_lstat = Path.lstat

                    def ambiguous_lstat(path, *args, **kwargs):
                        info = original_lstat(path, *args, **kwargs)
                        if path == candidate:
                            fields = list(info)
                            fields[4 if case == "foreign-owner" else 2] += 1
                            return os.stat_result(fields)
                        return info

                    with mock.patch.object(Path, "lstat", ambiguous_lstat):
                        result = self._collect_gc(root, services, releases[-1], mount_points=mounts)
                else:
                    result = self._collect_gc(root, services, releases[-1], mount_points=mounts)
                self.assertEqual(result["status"], "skipped")
                self.assertEqual(result["removed"], [])
                self.assertEqual({path.name for path in (root / "releases").iterdir()}, before)
                self.assertEqual(backup.read_bytes(), b"untouched")
                for sentinel, expected in outside_sentinels:
                    self.assertEqual(sentinel.read_bytes(), expected)

    def test_collect_releases_skips_ambiguous_records_for_every_service(self):
        cases = (
            "missing-record", "unreadable-record", "pid-mismatch", "invocation-mismatch",
            "package-mismatch", "transitional-state", "service-query-error",
        )
        for unit in deployment.SERVICES:
            for case in cases:
                with self.subTest(unit=unit, case=case), self._gc_root() as (root, releases, services):
                    services.active.add(unit)
                    services.set_running_release(unit, releases[0])
                    valid_state = services.service_state(unit)
                    if case == "missing-record":
                        services.running_records[unit] = None
                    elif case == "unreadable-record":
                        services.running_record_errors[unit] = PermissionError("record unreadable")
                    elif case == "pid-mismatch":
                        services.state_overrides[unit] = valid_state
                        services.running_records[unit]["pid"] += 1
                    elif case == "invocation-mismatch":
                        services.state_overrides[unit] = valid_state
                        services.running_records[unit]["invocation_id"] = "f" * 32
                    elif case == "package-mismatch":
                        services.running_records[unit]["package_path"] = str(root / "outside/pi")
                    elif case == "transitional-state":
                        services.state_overrides[unit] = {
                            "ActiveState": "activating",
                            "MainPID": valid_state["MainPID"],
                            "InvocationID": valid_state["InvocationID"],
                        }
                    elif case == "service-query-error":
                        services.state_errors[unit] = RuntimeError("systemctl unavailable")
                    before = {path.name for path in (root / "releases").iterdir()}
                    result = self._collect_gc(root, services, releases[-1])
                    self.assertEqual(result["status"], "skipped")
                    self.assertEqual(result["removed"], [])
                    self.assertEqual({path.name for path in (root / "releases").iterdir()}, before)

    def test_collect_releases_skips_absent_unheld_or_replaced_lock(self):
        import fcntl

        for case in ("absent", "unheld", "replaced"):
            with self.subTest(case=case), self._gc_root() as (root, releases, services):
                lock_path = root / ".install.lock"
                lock = lock_path.open("a")
                try:
                    if case != "unheld":
                        fcntl.flock(lock, fcntl.LOCK_EX)
                    if case == "absent":
                        lock_path.unlink()
                    elif case == "replaced":
                        lock_path.unlink()
                        lock_path.write_bytes(b"replacement")
                    before = {path.name for path in (root / "releases").iterdir()}
                    with mock.patch.object(deployment, "mount_points", return_value=[Path("/")]):
                        result = deployment.collect_releases(root, lock, services, releases[-1])
                finally:
                    lock.close()
                self.assertEqual(result["status"], "skipped")
                self.assertEqual(result["removed"], [])
                self.assertEqual({path.name for path in (root / "releases").iterdir()}, before)

    def test_gc_deletion_error_does_not_undo_activation_or_restart(self):
        with self.fixture() as repo, tempfile.TemporaryDirectory() as name:
            root = Path(name).resolve() / "packages"
            backup = root / "backups/operator-copy"
            backup.parent.mkdir(parents=True)
            backup.write_bytes(b"backup sentinel")
            services = self._services()
            with mock.patch.object(deployment, "mount_points", return_value=[Path("/")]):
                first = deployment.build_plan(repo)
                self._install(first, repo, root, "activate", services=services)
                candidates = [self._make_release(root, f"delete-error-{index}",
                                                 (index + 1) * 1_000_000_000)
                              for index in range(5)]
                dashboard = repo / "pi/apps/van_dashboard/van_dashboard.py"
                dashboard.write_text("SOURCE_MARKER = 'restart before gc error'\n")
                second = deployment.build_plan(repo)
                services.restart_calls.clear()
                real_rmtree = shutil.rmtree

                def fail_candidate(path, *args, **kwargs):
                    if Path(path) == candidates[0]:
                        raise OSError("simulated release deletion failure")
                    return real_rmtree(path, *args, **kwargs)

                with mock.patch.object(deployment.shutil, "rmtree", side_effect=fail_candidate):
                    result = self._install(second, repo, root, "update", services=services)
            self.assertEqual(result["restarted"], [deployment.DASHBOARD])
            self.assertEqual(result["gc"]["status"], "skipped")
            self.assertIn("simulated release deletion failure", result["gc"]["reason"])
            self.assertEqual(deployment.current_release(root).name, second["release"])
            self.assertTrue(candidates[0].is_dir())
            self.assertEqual(backup.read_bytes(), b"backup sentinel")

    def test_gc_tracks_actual_runtime_across_unrelated_updates_and_external_restart(self):
        with self.fixture() as repo, tempfile.TemporaryDirectory() as name:
            root = Path(name).resolve() / "packages"
            services = self._services()
            audiobook = repo / "pi/apps/audiobooks/audiobook_server.py"
            with mock.patch.object(deployment, "mount_points", return_value=[Path("/")]):
                first = deployment.build_plan(repo)
                self._install(first, repo, root, "activate", services=services)
                original_runtime = root / "releases" / first["release"]
                services.restart_calls.clear()
                for index in range(6):
                    audiobook.write_text(f"SOURCE_MARKER = 'unrelated-{index}'\n")
                    result = self._install(deployment.build_plan(repo), repo, root, "update", services=services)
                    self.assertEqual(result["restarted"], [])
                self.assertTrue(original_runtime.is_dir())
                external_runtime = deployment.current_release(root)
                services.restart(deployment.DASHBOARD)
                services.restart_calls.clear()
                for index in range(6, 12):
                    audiobook.write_text(f"SOURCE_MARKER = 'unrelated-{index}'\n")
                    result = self._install(deployment.build_plan(repo), repo, root, "update", services=services)
                    self.assertEqual(result["restarted"], [])
            self.assertFalse(original_runtime.exists())
            self.assertTrue(external_runtime.is_dir())
            self.assertNotEqual(deployment.current_release(root), external_runtime)

    def test_system_services_service_state_parses_and_rejects_queries(self):
        services = deployment.SystemServices()
        completed = subprocess.CompletedProcess([], 0, stdout=(
            "ActiveState=active\nMainPID=4321\nInvocationID=0123456789abcdef0123456789abcdef\n"
        ))
        with mock.patch.object(deployment.subprocess, "run", return_value=completed):
            self.assertEqual(services.service_state("audiobooks.service"), {
                "ActiveState": "active", "MainPID": "4321",
                "InvocationID": "0123456789abcdef0123456789abcdef",
            })
        for output in (
            "ActiveState=active\nMainPID=4321\n",
            "ActiveState=active\nMainPID=4321\nInvocationID=abc\nOther=value\n",
        ):
            with self.subTest(output=output), mock.patch.object(
                deployment.subprocess, "run",
                return_value=subprocess.CompletedProcess([], 0, stdout=output),
            ):
                with self.assertRaises(ValueError):
                    services.service_state("audiobooks.service")

    def test_system_services_dropins_require_exact_local_effective_set(self):
        unit = "audiobooks.service"
        with tempfile.TemporaryDirectory() as name:
            units = Path(name).resolve()
            (units / unit).write_bytes(flat_unit(unit))
            directory = units / (unit + ".d")
            directory.mkdir()
            local = directory / "site.conf"
            local.write_bytes(b"[Service]\nRestartSec=17\n")
            services = deployment.SystemServices(units=units)
            good = subprocess.CompletedProcess([], 0, stdout=(
                f"FragmentPath={units / unit}\nDropInPaths={local}\n"
            ))
            with mock.patch.object(deployment.subprocess, "run", return_value=good):
                self.assertEqual(services.dropins(unit), {"site.conf": local.read_bytes()})
            for output in (
                f"FragmentPath=/usr/lib/systemd/system/{unit}\nDropInPaths={local}\n",
                f"FragmentPath={units / unit}\nDropInPaths=/run/systemd/system/foreign.conf\n",
                f"FragmentPath={units / unit}\n",
            ):
                with self.subTest(output=output), mock.patch.object(
                    deployment.subprocess, "run",
                    return_value=subprocess.CompletedProcess([], 0, stdout=output),
                ):
                    with self.assertRaises(ValueError):
                        services.dropins(unit)

    def test_dropin_launch_overrides_are_rejected(self):
        unit = deployment.DASHBOARD
        dangerous = (
            "ExecStart", "ExecStartPre", "ExecStopPost", "EnvironmentFile",
            "UnsetEnvironment", "PassEnvironment", "RootDirectory", "RootImage",
            "User", "RuntimeDirectory", "RuntimeDirectoryMode",
        )
        for key in dangerous:
            with self.subTest(key=key), self.assertRaises(ValueError):
                deployment.validate_dropins(unit, {"override.conf": f"[Service]\n{key}=value\n".encode()})
        for variable in ("PYTHONPATH", "PYTHONHOME", "PYTHONDONTWRITEBYTECODE"):
            with self.subTest(variable=variable), self.assertRaises(ValueError):
                deployment.validate_dropins(
                    unit, {"override.conf": f"[Service]\nEnvironment={variable}=value\n".encode()}
                )
        deployment.validate_dropins(unit, {"safe.conf": b"[Service]\nRestartSec=20\n"})

    def test_system_services_save_legacy_preserves_unit_and_dropins_without_overwrite(self):
        unit = "audiobooks.service"
        with tempfile.TemporaryDirectory() as name:
            units = Path(name) / "units"
            units.mkdir()
            original = flat_unit(unit)
            (units / unit).write_bytes(original)
            destination = Path(name) / "backups" / (unit + ".backup")
            services = deployment.SystemServices(units=units)
            services.save_legacy(unit, destination, {"site.conf": b"drop-in original"})
            self.assertEqual((destination / unit).read_bytes(), original)
            self.assertEqual((destination / (unit + ".d/site.conf")).read_bytes(), b"drop-in original")
            (units / unit).write_bytes(b"new live unit")
            services.save_legacy(unit, destination, {"site.conf": b"new drop-in"})
            self.assertEqual((destination / unit).read_bytes(), original)
            self.assertEqual((destination / (unit + ".d/site.conf")).read_bytes(), b"drop-in original")

    def test_system_services_reads_unit_specific_running_record_and_rejects_symlink(self):
        unit = "audiobooks.service"
        with tempfile.TemporaryDirectory(prefix="running-record-") as name:
            run_root = Path(name).resolve()
            runtime = run_root / "audiobooks"
            runtime.mkdir()
            record_path = runtime / "package-release"
            record = {
                "package_path": "/packages/releases/" + "1" * 24 + "/pi",
                "pid": 4321,
                "invocation_id": "a" * 32,
            }
            record_path.write_bytes(deployment.encoded(record))
            services = deployment.SystemServices()
            real_path = Path
            with mock.patch.object(deployment, "Path", side_effect=lambda value: run_root if value == "/run" else real_path(value)):
                self.assertEqual(services.read_running_record(unit), record)
            target = runtime / "target"
            target.write_bytes(deployment.encoded(record))
            record_path.unlink()
            record_path.symlink_to(target)
            with mock.patch.object(deployment, "Path", side_effect=lambda value: run_root if value == "/run" else real_path(value)):
                with self.assertRaises(ValueError):
                    services.read_running_record(unit)

    def test_package_runtime_records_pinned_package_and_identity(self):
        from pi import package_runtime

        with tempfile.TemporaryDirectory(prefix="package-runtime-") as name:
            runtime = Path(name).resolve()
            release = runtime / "packages/releases" / ("1" * 24)
            (release / "pi").mkdir(parents=True)
            invocation_id = "a" * 32
            pi_package = sys.modules["pi"]
            with mock.patch.object(pi_package, "__path__", [str(release / "pi")]), mock.patch.dict(
                os.environ, {"INVOCATION_ID": invocation_id}, clear=False
            ):
                package_runtime.record_running_release(runtime)
            record = json.loads((runtime / "package-release").read_bytes())
            self.assertEqual(record, {
                "package_path": str(release / "pi"),
                "pid": os.getpid(),
                "invocation_id": invocation_id,
            })

    def test_dashboard_entrypoint_delegates_runtime_record_with_dashboard_warning_prefix(self):
        with tempfile.TemporaryDirectory(prefix="dashboard-entrypoint-") as name:
            runtime = Path(name).resolve()
            production_main = mock.Mock()
            recorder = mock.Mock()
            modules = self._entrypoint_modules(runtime, production_main)
            runtime_module = types.ModuleType("pi.package_runtime")
            runtime_module.record_running_release = recorder
            modules["pi.package_runtime"] = runtime_module
            with mock.patch.dict(sys.modules, modules):
                namespace = runpy.run_path(
                    str(DASHBOARD_ENTRYPOINT), run_name="pi.apps.van_dashboard.__main__"
                )
            recorder.assert_called_once_with(runtime, warning_prefix="dashboard")
            production_main.assert_called_once_with()
            self.assertIn("record_package_release", namespace)

    def test_package_runtime_warns_but_does_not_raise_when_publish_fails(self):
        from pi import package_runtime

        with tempfile.TemporaryDirectory(prefix="package-runtime-") as name:
            runtime = Path(name).resolve()
            stderr = io.StringIO()
            with mock.patch.object(os, "replace", side_effect=OSError("replace denied")), mock.patch(
                "sys.stderr", stderr
            ):
                package_runtime.record_running_release(runtime, warning_prefix="dashboard")
            self.assertIn(
                "WARNING: cannot record dashboard package release: replace denied", stderr.getvalue()
            )
            self.assertEqual(list(runtime.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
