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
    "pi/apps/audiobooks": ("audiobook_server.py",),
    "pi/apps/bme280": ("bme280_mqtt.py", "bme280_testread.py"),
    "pi/apps/van_dashboard": (
        "__main__.py",
        "van_dashboard.py",
        "van_dashboard_projects.py",
        "react_dashboard_preview.py",
    ),
    "pi/apps/van_dashboard/routes": ("__init__.py", "common.py", "projects.py"),
    "pi/apps/video_library": ("video_library_server.py",),
    "pi/apps/video_library/players": ("vlc_player.py", "sonos_volume.py"),
    "pi/scripts/python": ("ip_info.py", "vlc_property.py"),
    "shared/python": ("shared_tool.py", "sonos_tasks.py"),
}


class FakeSystemServices:
    """An in-memory service manager for receiver-side installation tests."""

    def __init__(self, unit_bytes, active=None, restart_failures=0):
        self.units = {deployment.UNIT: unit_bytes}
        self.active = set(active or ())
        self.restart_failures = restart_failures
        self.install_calls = []
        self.restart_calls = []
        self.saved_legacy = []
        self.release_root = None
        self.main_pid = 4100
        self.invocation_number = 0
        self.running_record = None
        self.dashboard_state_override = None
        self.dashboard_state_error = None
        self.running_record_error = None

    def bind_release_root(self, root):
        self.release_root = root.resolve()

    def set_running_release(self, release):
        self.main_pid += 1
        self.invocation_number += 1
        invocation_id = f"{self.invocation_number:032x}"
        self.running_record = {
            "package_path": str(release.resolve() / "pi"),
            "pid": self.main_pid,
            "invocation_id": invocation_id,
        }

    def read_unit(self, unit):
        return self.units[unit]

    def save_legacy(self, destination):
        self.saved_legacy.append(destination)
        destination.mkdir(mode=0o700)
        (destination / deployment.UNIT).write_bytes(self.units[deployment.UNIT])

    def install_unit(self, unit, source):
        self.install_calls.append((unit, source))
        self.units[unit] = source.read_bytes()

    def is_active(self, unit):
        return unit in self.active

    def dashboard_state(self):
        if self.dashboard_state_error is not None:
            raise self.dashboard_state_error
        if self.dashboard_state_override is not None:
            return dict(self.dashboard_state_override)
        if deployment.UNIT not in self.active:
            return {"ActiveState": "inactive", "MainPID": "0", "InvocationID": ""}
        invocation_id = (
            self.running_record["invocation_id"]
            if self.running_record is not None
            else f"{self.invocation_number:032x}"
        )
        return {
            "ActiveState": "active",
            "MainPID": str(self.main_pid),
            "InvocationID": invocation_id,
        }

    def read_running_record(self):
        if self.running_record_error is not None:
            raise self.running_record_error
        if self.running_record is None:
            raise FileNotFoundError("dashboard running record is absent")
        return dict(self.running_record)

    def restart(self, unit):
        self.restart_calls.append(unit)
        if self.restart_failures:
            self.restart_failures -= 1
            raise RuntimeError("simulated restart failure")
        if unit == deployment.UNIT and self.release_root is not None:
            current = deployment.current_release(self.release_root)
            if current is None:
                raise RuntimeError("dashboard restart requires a current release")
            self.set_running_release(current)


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

        unit = root / "pi" / "services" / deployment.UNIT
        unit.parent.mkdir(parents=True, exist_ok=True)
        unit.write_bytes(
            b"[Service]\nExecStart=/usr/bin/python3 -P -m pi.apps.van_dashboard\n"
            b"Description=fixture v1\n"
        )

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
    def _services():
        old_unit = (
            b"[Service]\n"
            b"ExecStart=/home/pi/scripts/python-automation/van_dashboard.py\n"
            b"Description=old receiver unit\n"
        )
        return FakeSystemServices(
            old_unit,
            active={
                "audiobooks.service",
                "bme280-mqtt.service",
                "video-library.service",
                deployment.UNIT,
            },
        )

    @staticmethod
    def _install(plan, repo, root, mode, services=None, flat_root=None):
        if services is not None and hasattr(services, "bind_release_root"):
            services.bind_release_root(root)
        stream = io.BytesIO(PythonDeploymentTests._archive(plan, repo))
        return deployment.install_release(
            stream,
            root,
            mode,
            services=services,
            flat_root=flat_root,
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
                manifest_member = tarfile.TarInfo("manifest.json")
                manifest_member.size = len(raw)
                manifest_member.mode = 0o644
                archive.addfile(manifest_member, io.BytesIO(raw))
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
        manifest = {
            "schema": 1,
            "files": {"payload.py": deployment.digest(payload)},
        }
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
            services = FakeSystemServices(b"fixture unit", active=set())
            services.bind_release_root(root)
            yield root, releases, services

    @staticmethod
    def _collect_gc(root, services, installed, mount_points=None):
        import fcntl

        mounts = [Path("/")] if mount_points is None else mount_points
        if isinstance(mounts, BaseException):
            mount_patch = mock.patch.object(
                deployment, "mount_points", side_effect=mounts
            )
        else:
            mount_patch = mock.patch.object(
                deployment, "mount_points", return_value=mounts
            )
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
            expected.add("pi/services/" + deployment.UNIT)
            expected.update(
                f"{directory}/{name}"
                for directory, names in FIXTURE_MODULES.items()
                for name in names
            )
            self.assertEqual(set(files), expected)
            self.assertEqual(files, sorted(files))
            self.assertTrue(all(not Path(path).is_absolute() for path in files))

            plan = deployment.build_plan(repo, mode="stage")
            self.assertEqual(plan["mode"], "stage")
            self.assertEqual(plan["manifest"]["files"], {
                relative: deployment.digest((repo / relative).read_bytes())
                for relative in sorted(expected)
            })
            self.assertEqual(plan["manifest"]["provenance"]["checkout"], str(repo))
            self.assertEqual(
                plan["release"],
                deployment.digest(deployment.encoded(plan["manifest"]))[:24],
            )
            self.assertTrue(
                all(
                    Path(item["source"]).is_relative_to(repo)
                    and Path(item["source"]) == repo / item["relative"]
                    for item in plan["sources"]
                )
            )

            archive_bytes = self._archive(plan, repo)
            with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:") as archive:
                members = archive.getmembers()
                self.assertEqual(members[0].name, "manifest.json")
                self.assertEqual(
                    {member.name for member in members},
                    {"manifest.json"} | expected,
                )
                archive_manifest = json.loads(archive.extractfile(members[0]).read())
                self.assertEqual(archive_manifest, plan["manifest"])

            changed = repo / "pi" / "apps" / "audiobooks" / "audiobook_server.py"
            changed.write_text("SOURCE_MARKER = 'changed after planning'\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                deployment.make_archive(plan, repo, io.BytesIO())

    def test_source_files_rejects_symlinked_source(self):
        with self.fixture() as repo:
            outside = repo / "outside.py"
            outside.write_text("SOURCE_MARKER = 'outside'\n", encoding="utf-8")
            link = repo / "pi" / "scripts" / "python" / "unsafe.py"
            link.symlink_to(outside)
            with self.assertRaises(ValueError):
                deployment.source_files(repo)

    def test_minimal_checkout_dry_run_is_local_and_uses_fixture_provenance(self):
        with self.fixture(copy_script=True) as repo:
            fake_bin, marker = self._network_poison(repo)
            environment = os.environ.copy()
            environment["PATH"] = f"{fake_bin}:/usr/bin:/bin"
            result = subprocess.run(
                [sys.executable, str(repo / "pi" / "deploy_python.py"), "--dry-run"],
                cwd=repo,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            plan = json.loads(result.stdout)
            self.assertEqual(plan["manifest"]["provenance"]["checkout"], str(repo))
            self.assertTrue(
                all(
                    Path(item["source"]).is_relative_to(repo)
                    for item in plan["sources"]
                )
            )
            self.assertNotIn(str(REPOSITORY_ROOT), result.stdout)
            self.assertFalse(marker.exists())

            legacy_result = subprocess.run(
                [
                    sys.executable,
                    str(repo / "pi" / "deploy_python.py"),
                    "--dry-run",
                    "--legacy-flatten",
                ],
                cwd=repo,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(legacy_result.returncode, 0, legacy_result.stderr)
            legacy_plan = json.loads(legacy_result.stdout)
            self.assertTrue(legacy_plan["legacy_destinations"])
            self.assertFalse(
                any(
                    "pi/apps/van_dashboard/" in source
                    for source in legacy_plan["legacy_destinations"]
                )
            )
            self.assertFalse(marker.exists())

            for option, expected_mode in (("--activate", "activate"), ("--update", "update")):
                mode_result = subprocess.run(
                    [
                        sys.executable,
                        str(repo / "pi" / "deploy_python.py"),
                        "--dry-run",
                        option,
                    ],
                    cwd=repo,
                    env=environment,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(mode_result.returncode, 0, mode_result.stderr)
                self.assertEqual(json.loads(mode_result.stdout)["mode"], expected_mode)
                self.assertFalse(marker.exists())

    def test_actual_repository_dry_run_contains_only_direct_allowlisted_sources(self):
        with tempfile.TemporaryDirectory(prefix="python-deployment-network-") as name:
            poison_root = Path(name)
            fake_bin, marker = self._network_poison(poison_root)
            environment = os.environ.copy()
            environment["PATH"] = f"{fake_bin}:/usr/bin:/bin"
            result = subprocess.run(
                [sys.executable, str(DEPLOY_SCRIPT), "--dry-run"],
                cwd=REPOSITORY_ROOT,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            plan = json.loads(result.stdout)
            actual_files = set(deployment.source_files(REPOSITORY_ROOT))
            expected_python = {
                f"{directory}/{path.name}"
                for directory in deployment.MODULE_DIRS
                for path in (REPOSITORY_ROOT / directory).glob("*.py")
            }
            expected = (
                expected_python
                | set(deployment.INITIALIZERS)
                | set(deployment.ASSETS)
                | {"pi/services/" + deployment.UNIT}
            )
            self.assertEqual(set(plan["manifest"]["files"]), expected)
            self.assertEqual(actual_files, expected)
            for relative in expected:
                parts = PurePosixPath(relative).parts
                self.assertNotIn("van_compute", parts)
                self.assertNotIn("tests", parts)
                self.assertNotIn("secrets", parts)
                self.assertNotIn("frontend", parts)
                self.assertNotIn("node_modules", parts)
                self.assertNotIn("__pycache__", parts)
                self.assertNotIn(".pytest_cache", parts)
            self.assertFalse(marker.exists())

    def test_legacy_basename_collisions_fail(self):
        with self.fixture() as repo:
            for directory in ("pi/apps/audiobooks", "pi/apps/bme280"):
                path = repo / directory / "same_basename.py"
                path.write_text("SOURCE_MARKER = 'collision'\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                deployment.build_plan(repo, mode="legacy")

    def test_stage_leaves_flat_sentinels_untouched_and_has_no_current(self):
        with self.fixture() as repo, tempfile.TemporaryDirectory() as name:
            root = Path(name) / "packages"
            flat_root = Path(name) / "flat"
            flat_root.mkdir()
            sentinel = flat_root / "audiobook_server.py"
            sentinel.write_bytes(b"old flat sentinel")
            plan = deployment.build_plan(repo, mode="stage")
            result = self._install(plan, repo, root, "stage", flat_root=flat_root)
            self.assertEqual(result["restarted"], [])
            self.assertFalse((root / "current").exists())
            self.assertEqual(sentinel.read_bytes(), b"old flat sentinel")
            self.assertTrue((root / "releases" / plan["release"] / "manifest.json").is_file())

    def test_first_activation_requires_explicit_mode_and_saves_old_unit(self):
        with self.fixture() as repo, tempfile.TemporaryDirectory() as name:
            root = Path(name) / "packages"
            flat_root = Path(name) / "flat"
            services = self._services()
            plan = deployment.build_plan(repo, mode="stage")
            archive = self._archive(plan, repo)
            with self.assertRaises(ValueError):
                deployment.install_release(
                    io.BytesIO(archive), root, "update", services=services, flat_root=flat_root
                )
            self.assertFalse((root / "current").exists())
            self.assertFalse((root / "pre-package-units").exists())
            self.assertEqual(services.restart_calls, [])

            result = deployment.install_release(
                io.BytesIO(archive), root, "activate", services=services, flat_root=flat_root
            )
            self.assertEqual(result["restarted"], [deployment.UNIT])
            self.assertEqual(
                (root / "pre-package-units" / deployment.UNIT).read_bytes(),
                b"[Service]\n"
                b"ExecStart=/home/pi/scripts/python-automation/van_dashboard.py\n"
                b"Description=old receiver unit\n",
            )
            current = deployment.current_release(root)
            self.assertIsNotNone(current)
            self.assertEqual(os.readlink(root / "current"), f"releases/{plan['release']}")
            manifest = json.loads((current / "manifest.json").read_bytes())
            for relative, expected in manifest["files"].items():
                path = current / relative
                self.assertTrue(path.is_file())
                self.assertEqual(deployment.digest(path.read_bytes()), expected)
            self.assertEqual(
                (root / "activated.json").read_bytes(),
                deployment.encoded({"release": plan["release"]}),
            )

    def test_reinstalling_same_release_does_not_restart(self):
        with self.fixture() as repo, tempfile.TemporaryDirectory() as name:
            root = Path(name) / "packages"
            services = self._services()
            plan = deployment.build_plan(repo)
            self._install(plan, repo, root, "activate", services=services)
            services.restart_calls.clear()
            services.install_calls.clear()

            result = self._install(plan, repo, root, "activate", services=services)
            self.assertEqual(result["restarted"], [])
            self.assertEqual(services.restart_calls, [])
            self.assertEqual(services.install_calls, [])
            result = self._install(plan, repo, root, "update", services=services)
            self.assertEqual(result["restarted"], [])
            self.assertEqual(services.restart_calls, [])
            self.assertEqual(services.install_calls, [])

    def test_dashboard_module_change_restarts_without_reinstalling_unit(self):
        with self.fixture() as repo, tempfile.TemporaryDirectory() as name:
            root = Path(name) / "packages"
            services = self._services()
            first = deployment.build_plan(repo)
            self._install(first, repo, root, "activate", services=services)
            services.restart_calls.clear()
            services.install_calls.clear()
            dashboard = repo / "pi" / "apps" / "van_dashboard" / "van_dashboard.py"
            dashboard.write_text("SOURCE_MARKER = 'dashboard v2'\n", encoding="utf-8")
            second = deployment.build_plan(repo)
            self.assertNotEqual(first["release"], second["release"])
            self.assertNotEqual(
                first["manifest"]["services"][deployment.UNIT],
                second["manifest"]["services"][deployment.UNIT],
            )
            result = self._install(second, repo, root, "update", services=services)
            self.assertEqual(result["restarted"], [deployment.UNIT])
            self.assertEqual(services.restart_calls, [deployment.UNIT])
            self.assertEqual(services.install_calls, [])

    def test_unrelated_audiobook_change_does_not_restart_dashboard(self):
        with self.fixture() as repo, tempfile.TemporaryDirectory() as name:
            root = Path(name) / "packages"
            services = self._services()
            first = deployment.build_plan(repo)
            self._install(first, repo, root, "activate", services=services)
            services.restart_calls.clear()
            services.install_calls.clear()
            audiobook = repo / "pi" / "apps" / "audiobooks" / "audiobook_server.py"
            audiobook.write_text("SOURCE_MARKER = 'audiobook v2'\n", encoding="utf-8")
            second = deployment.build_plan(repo)
            self.assertNotEqual(first["release"], second["release"])
            self.assertEqual(
                first["manifest"]["services"][deployment.UNIT],
                second["manifest"]["services"][deployment.UNIT],
            )
            result = self._install(second, repo, root, "update", services=services)
            self.assertEqual(result["restarted"], [])
            self.assertEqual(services.restart_calls, [])
            self.assertEqual(services.install_calls, [])
            self.assertEqual(os.readlink(root / "current"), f"releases/{second['release']}")

    def test_unit_change_installs_unit_and_restarts_dashboard(self):
        with self.fixture() as repo, tempfile.TemporaryDirectory() as name:
            root = Path(name) / "packages"
            services = self._services()
            first = deployment.build_plan(repo)
            self._install(first, repo, root, "activate", services=services)
            services.restart_calls.clear()
            services.install_calls.clear()
            unit = repo / "pi" / "services" / deployment.UNIT
            unit.write_bytes(
                b"[Service]\nExecStart=/usr/bin/python3 -P -m pi.apps.van_dashboard\n"
                b"Description=fixture v2\n"
            )
            second = deployment.build_plan(repo)
            self.assertEqual(
                first["manifest"]["services"][deployment.UNIT],
                second["manifest"]["services"][deployment.UNIT],
            )
            result = self._install(second, repo, root, "update", services=services)
            self.assertEqual(result["restarted"], [deployment.UNIT])
            self.assertEqual(services.restart_calls, [deployment.UNIT])
            self.assertEqual([call[0] for call in services.install_calls], [deployment.UNIT])
            self.assertEqual(services.units[deployment.UNIT], unit.read_bytes())

    def test_failed_restart_leaves_pending_marker_and_retries(self):
        with self.fixture() as repo, tempfile.TemporaryDirectory() as name:
            root = Path(name) / "packages"
            services = self._services()
            services.restart_failures = 1
            plan = deployment.build_plan(repo)
            archive = self._archive(plan, repo)
            with self.assertRaises(RuntimeError):
                deployment.install_release(
                    io.BytesIO(archive), root, "activate", services=services
                )
            self.assertEqual(
                (root / "pending-restart").read_text(encoding="utf-8"),
                plan["release"] + "\n",
            )
            self.assertFalse((root / "activated.json").exists())
            self.assertIsNotNone(deployment.current_release(root))

            result = deployment.install_release(
                io.BytesIO(archive), root, "activate", services=services
            )
            self.assertEqual(result["restarted"], [deployment.UNIT])
            self.assertFalse((root / "pending-restart").exists())
            self.assertEqual(
                json.loads((root / "activated.json").read_bytes()),
                {"release": plan["release"]},
            )
            self.assertEqual(services.restart_calls, [deployment.UNIT, deployment.UNIT])

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
            with self.assertRaises(RuntimeError):
                self._install(plan, repo, root, "activate", services=services)
            self.assertEqual(services.restart_calls, [])
            services.install_unit = original
            self._install(plan, repo, root, "activate", services=services)
            self.assertEqual(len(services.install_calls), 2)
            self.assertEqual(services.restart_calls, [deployment.UNIT])
            self.assertFalse((root / "pending-restart").exists())

    def test_update_preserves_inactive_dashboard(self):
        with self.fixture() as repo, tempfile.TemporaryDirectory() as name:
            root = Path(name) / "packages"
            services = self._services()
            self._install(deployment.build_plan(repo), repo, root, "activate", services=services)
            services.active.remove(deployment.UNIT)
            services.restart_calls.clear()
            (repo / "pi/apps/van_dashboard/van_dashboard.py").write_text("CHANGED = True\n")
            plan = deployment.build_plan(repo)
            result = self._install(plan, repo, root, "update", services=services)
            self.assertEqual(result["restarted"], [])
            self.assertEqual(services.restart_calls, [])
            self.assertEqual(deployment.current_release(root).name, plan["release"])

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
            corrupt = self._manual_archive(
                manifest,
                repo,
                replacements={first_relative: b"not the expected bytes"},
            )
            traversal = self._manual_archive(
                manifest,
                repo,
                extra=("../outside.py", b"SOURCE_MARKER = 'escape'\n"),
            )
            symlink = self._manual_archive(
                manifest,
                repo,
                symlink=first_relative,
            )
            incomplete = self._manual_archive(manifest, repo, omit=(last_relative,))
            interrupted_data = self._archive(plan, repo)
            interrupted = InterruptedArchive(
                interrupted_data, max(1024, len(interrupted_data) // 2)
            )

            invalid_inputs = (
                ("corrupt", io.BytesIO(corrupt), (ValueError,)),
                ("traversal", io.BytesIO(traversal), (ValueError,)),
                ("symlink", io.BytesIO(symlink), (ValueError,)),
                ("incomplete", io.BytesIO(incomplete), (ValueError,)),
                ("interrupted", interrupted, (OSError, tarfile.TarError)),
            )
            for label, stream, errors in invalid_inputs:
                with self.subTest(label=label):
                    with self.assertRaises(errors):
                        deployment.install_release(
                            stream, root, "update", services=services
                        )
                    self.assertEqual(self._current_snapshot(root), before)

    def test_unsupported_current_path_is_refused_without_changes(self):
        with self.fixture() as repo, tempfile.TemporaryDirectory() as name:
            root = Path(name) / "packages"
            releases = root / "releases"
            releases.mkdir(parents=True)
            current = root / "current"
            current.symlink_to("releases/not-a-release")
            before = os.readlink(current)
            plan = deployment.build_plan(repo)
            with self.assertRaises(ValueError):
                self._install(plan, repo, root, "update", services=self._services())
            self.assertEqual(os.readlink(current), before)
            self.assertEqual(sorted(path.name for path in releases.iterdir()), [])

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
                        self._install(plan, repo, root, "stage")
                    self.assertEqual(list(outside.iterdir()), [])
                    self.assertFalse((root / "current").exists())

    def test_blueprint_changes_restart_but_unrelated_initializers_do_not(self):
        with self.fixture() as repo, tempfile.TemporaryDirectory() as name:
            root = Path(name) / "packages"
            services = self._services()
            self._install(deployment.build_plan(repo), repo, root, "activate", services=services)
            for relative, expected in (
                ("shared/__init__.py", []),
                ("pi/scripts/__init__.py", []),
                ("pi/apps/van_dashboard/routes/common.py", [deployment.UNIT]),
            ):
                with self.subTest(relative=relative):
                    services.restart_calls.clear()
                    path = repo / relative
                    path.write_text(path.read_text() + "CHANGED = True\n")
                    result = self._install(deployment.build_plan(repo), repo, root, "update", services=services)
                    self.assertEqual(result["restarted"], expected)
                    self.assertEqual(services.restart_calls, expected)

    def test_hosted_project_modules_are_dashboard_release_dependencies(self):
        project_sources = (
            "pi/apps/van_dashboard/van_dashboard_projects.py",
            "pi/apps/van_dashboard/routes/projects.py",
        )
        actual = deployment.build_plan(REPOSITORY_ROOT)
        for relative in project_sources:
            self.assertIn(relative, actual["manifest"]["files"])
            self.assertNotIn(relative, actual["manifest"]["legacy"])
        with self.fixture() as repo, tempfile.TemporaryDirectory() as name:
            root = Path(name) / "packages"
            services = self._services()
            previous = deployment.build_plan(repo)
            self._install(previous, repo, root, "activate", services=services)
            for relative in project_sources:
                with self.subTest(relative=relative):
                    services.restart_calls.clear()
                    services.install_calls.clear()
                    path = repo / relative
                    path.write_text(path.read_text() + "CHANGED = True\n")
                    current = deployment.build_plan(repo)
                    self.assertNotEqual(previous["manifest"]["services"][deployment.UNIT],
                                        current["manifest"]["services"][deployment.UNIT])
                    result = self._install(current, repo, root, "update", services=services)
                    self.assertEqual(result["restarted"], [deployment.UNIT])
                    self.assertEqual(services.restart_calls, [deployment.UNIT])
                    self.assertEqual(services.install_calls, [])
                    previous = current

    def test_legacy_flatten_only_updates_flat_safe_files_and_active_changed_services(self):
        with self.fixture() as repo, tempfile.TemporaryDirectory() as name:
            root = Path(name) / "packages"
            flat_root = Path(name) / "flat"
            flat_root.mkdir()
            services = self._services()
            plan = deployment.build_plan(repo, mode="legacy")
            dashboard_sources = {
                relative
                for relative in plan["manifest"]["files"]
                if relative.startswith("pi/apps/van_dashboard/")
            }
            self.assertTrue(dashboard_sources)
            self.assertFalse(
                dashboard_sources.intersection(plan["manifest"]["legacy"])
            )
            result = self._install(
                plan,
                repo,
                root,
                "legacy",
                services=services,
                flat_root=flat_root,
            )
            self.assertEqual(
                result["restarted"],
                [
                    "audiobooks.service",
                    "bme280-mqtt.service",
                    "video-library.service",
                ],
            )
            expected_flat = {
                str(Path(destination).relative_to(deployment.FLAT_ROOT))
                for destination in plan["legacy_destinations"].values()
            }
            actual_flat = {
                path.relative_to(flat_root).as_posix()
                for path in flat_root.rglob("*")
                if path.is_file()
            }
            self.assertEqual(actual_flat, expected_flat)
            self.assertFalse(any("van_dashboard" in relative for relative in actual_flat))
            flat_before_dashboard = {
                path.relative_to(flat_root).as_posix(): path.read_bytes()
                for path in flat_root.rglob("*")
                if path.is_file()
            }

            services.restart_calls.clear()
            dashboard = repo / "pi" / "apps" / "van_dashboard" / "van_dashboard.py"
            dashboard.write_text("SOURCE_MARKER = 'dashboard legacy ignored'\n", encoding="utf-8")
            dashboard_plan = deployment.build_plan(repo, mode="legacy")
            self.assertEqual(
                self._install(
                    dashboard_plan,
                    repo,
                    root,
                    "legacy",
                    services=services,
                    flat_root=flat_root,
                )["restarted"],
                [],
            )
            self.assertEqual(services.restart_calls, [])
            self.assertEqual(
                {
                    path.relative_to(flat_root).as_posix(): path.read_bytes()
                    for path in flat_root.rglob("*")
                    if path.is_file()
                },
                flat_before_dashboard,
            )

            audiobook = repo / "pi" / "apps" / "audiobooks" / "audiobook_server.py"
            audiobook.write_text("SOURCE_MARKER = 'audiobook legacy v2'\n", encoding="utf-8")
            audiobook_plan = deployment.build_plan(repo, mode="legacy")
            services.restart_calls.clear()
            result = self._install(
                audiobook_plan,
                repo,
                root,
                "legacy",
                services=services,
                flat_root=flat_root,
            )
            self.assertEqual(result["restarted"], ["audiobooks.service"])
            self.assertEqual(services.restart_calls, ["audiobooks.service"])

    def test_legacy_player_changes_restart_only_active_video_service(self):
        with self.fixture() as repo, tempfile.TemporaryDirectory() as name:
            root = Path(name) / "packages"
            flat = Path(name) / "flat"
            services = self._services()
            self._install(deployment.build_plan(repo), repo, root, "legacy",
                          services=services, flat_root=flat)
            for player, active in (("vlc_player.py", True), ("sonos_volume.py", True),
                                   ("vlc_player.py", False)):
                with self.subTest(player=player, active=active):
                    if not active:
                        services.active.remove("video-library.service")
                    services.restart_calls.clear()
                    path = repo / "pi/apps/video_library/players" / player
                    path.write_text(path.read_text() + "CHANGED = True\n")
                    plan = deployment.build_plan(repo, mode="legacy")
                    self.assertEqual(plan["manifest"]["legacy"][path.relative_to(repo).as_posix()], player)
                    result = self._install(plan, repo, root, "legacy",
                                           services=services, flat_root=flat)
                    expected = ["video-library.service"] if active else []
                    self.assertEqual(result["restarted"], expected)
                    self.assertEqual(services.restart_calls, expected)
                    self.assertEqual((flat / player).read_bytes(), path.read_bytes())

    def test_legacy_players_share_basename_collision_guard(self):
        with self.fixture() as repo:
            (repo / "pi/apps/video_library/vlc_player.py").write_text("COLLISION = True\n")
            with self.assertRaisesRegex(ValueError, "basename collision"):
                deployment.build_plan(repo, mode="legacy")

    def test_legacy_refuses_symlinked_destination_directory(self):
        with self.fixture() as repo, tempfile.TemporaryDirectory() as name:
            root = Path(name) / "packages"
            flat = Path(name) / "flat"
            outside = Path(name) / "outside"
            flat.mkdir()
            outside.mkdir()
            (flat / "static").symlink_to(outside, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "legacy symlink"):
                self._install(deployment.build_plan(repo), repo, root, "legacy",
                              services=self._services(), flat_root=flat)
            self.assertEqual(list(outside.iterdir()), [])
            self.assertEqual([p.name for p in flat.iterdir()], ["static"])

    def test_legacy_failed_restart_retries_after_files_already_match(self):
        with self.fixture() as repo, tempfile.TemporaryDirectory() as name:
            root = Path(name) / "packages"
            flat = Path(name) / "flat"
            services = self._services()
            services.restart_failures = 1
            plan = deployment.build_plan(repo, mode="legacy")
            with self.assertRaises(RuntimeError):
                self._install(plan, repo, root, "legacy", services=services, flat_root=flat)
            journal = root / "legacy-pending-restarts.json"
            self.assertEqual(json.loads(journal.read_bytes()), sorted(services.active - {deployment.UNIT}))
            services.active.clear()
            services.restart_calls.clear()
            result = self._install(plan, repo, root, "legacy", services=services, flat_root=flat)
            self.assertEqual(result["restarted"], ["audiobooks.service", "bme280-mqtt.service", "video-library.service"])
            self.assertEqual(services.restart_calls, result["restarted"])
            self.assertFalse(journal.exists())
            services.restart_calls.clear()
            self._install(plan, repo, root, "legacy", services=services, flat_root=flat)
            self.assertEqual(services.restart_calls, [])

    def test_legacy_partial_copy_preserves_restart_intent(self):
        with self.fixture() as repo, tempfile.TemporaryDirectory() as name:
            root = Path(name) / "packages"
            flat = Path(name) / "flat"
            services = self._services()
            plan = deployment.build_plan(repo, mode="legacy")
            real_copy = shutil.copyfile
            copies = []

            def interrupted_copy(source, destination):
                copies.append(destination)
                if len(copies) == 2:
                    raise OSError("simulated copy interruption")
                return real_copy(source, destination)

            with mock.patch.object(deployment.shutil, "copyfile", side_effect=interrupted_copy):
                with self.assertRaisesRegex(OSError, "copy interruption"):
                    self._install(plan, repo, root, "legacy", services=services, flat_root=flat)
            self.assertEqual(services.restart_calls, [])
            self.assertTrue((root / "legacy-pending-restarts.json").is_file())
            self.assertTrue((flat / "audiobook_server.py").is_file())
            result = self._install(plan, repo, root, "legacy", services=services, flat_root=flat)
            self.assertEqual(result["restarted"], ["audiobooks.service", "bme280-mqtt.service", "video-library.service"])
            self.assertFalse((root / "legacy-pending-restarts.json").exists())

    def test_legacy_flatten_updates_inactive_owned_service_without_restart(self):
        with self.fixture() as repo, tempfile.TemporaryDirectory() as name:
            root = Path(name) / "packages"
            flat_root = Path(name) / "flat"
            flat_root.mkdir()
            services = self._services()
            services.active.remove("audiobooks.service")
            initial = deployment.build_plan(repo, mode="legacy")
            self._install(
                initial,
                repo,
                root,
                "legacy",
                services=services,
                flat_root=flat_root,
            )
            services.restart_calls.clear()
            audiobook = repo / "pi" / "apps" / "audiobooks" / "audiobook_server.py"
            audiobook.write_text("SOURCE_MARKER = 'inactive audiobook v2'\n", encoding="utf-8")
            changed = deployment.build_plan(repo, mode="legacy")
            result = self._install(
                changed,
                repo,
                root,
                "legacy",
                services=services,
                flat_root=flat_root,
            )
            self.assertEqual(result["restarted"], [])
            self.assertEqual(services.restart_calls, [])
            self.assertEqual(
                (flat_root / "audiobook_server.py").read_bytes(),
                audiobook.read_bytes(),
            )

    def test_mount_points_keeps_same_device_mounts_and_decodes_paths(self):
        mountinfo = (
            "36 25 0:31 / / rw,relatime - apfs /dev/disk1s1 rw\n"
            "37 36 0:31 /bind /tmp/same\\040device rw - apfs /dev/disk1s1 rw\n"
        )
        with mock.patch.object(Path, "read_text", return_value=mountinfo):
            self.assertEqual(
                deployment.mount_points(),
                [Path("/"), Path("/tmp/same device")],
            )

        for label, mountinfo in (
            ("empty", ""),
            ("malformed", "too few fields\n"),
            ("nonabsolute", "37 36 0:31 /bind relative rw - apfs disk rw\n"),
        ):
            with self.subTest(label=label), mock.patch.object(
                Path, "read_text", return_value=mountinfo
            ):
                with self.assertRaises(ValueError):
                    deployment.mount_points()
        with mock.patch.object(
            Path, "read_text", side_effect=PermissionError("mountinfo unreadable")
        ):
            with self.assertRaises(PermissionError):
                deployment.mount_points()

    def test_collect_releases_retains_every_protected_release(self):
        with self._gc_root(count=10) as (root, releases, services):
            (root / "current").symlink_to(f"releases/{releases[0].name}")
            (root / "previous").symlink_to(f"releases/{releases[1].name}")
            installed = releases[2]
            services.active.add(deployment.UNIT)
            services.set_running_release(releases[3])

            info = deployment.check_release_tree(
                releases[0], root.lstat().st_dev
            )
            self.assertTrue(os.path.samestat(info, releases[0].lstat()))

            result = self._collect_gc(root, services, installed)
            expected = {
                releases[0].name,
                releases[1].name,
                releases[2].name,
                releases[3].name,
                releases[7].name,
                releases[8].name,
                releases[9].name,
            }
            actual = {path.name for path in (root / "releases").iterdir()}
            self.assertEqual(result["status"], "ok")
            self.assertEqual(actual, expected)
            self.assertEqual(set(result["kept"]), expected)
            self.assertEqual(
                set(result["removed"]),
                {releases[4].name, releases[5].name, releases[6].name},
            )

    def test_collect_releases_deletes_at_most_sixteen_per_run(self):
        with self._gc_root(count=22) as (root, releases, services):
            first = self._collect_gc(root, services, releases[-1])
            self.assertEqual(first["status"], "ok")
            self.assertEqual(len(first["removed"]), 16)
            self.assertEqual(len(first["deferred"]), 3)
            self.assertEqual(len(list((root / "releases").iterdir())), 6)

            second = self._collect_gc(root, services, releases[-1])
            self.assertEqual(second["status"], "ok")
            self.assertEqual(len(second["removed"]), 3)
            self.assertEqual(second["deferred"], [])
            self.assertEqual(
                {path.name for path in (root / "releases").iterdir()},
                {release.name for release in releases[-3:]},
            )

    def test_stage_and_legacy_success_collect_old_releases(self):
        for mode in ("stage", "legacy"):
            with self.subTest(mode=mode), self.fixture() as repo, tempfile.TemporaryDirectory() as name:
                root = Path(name).resolve() / "packages"
                old = [
                    self._make_release(root, f"{mode}-{index}", (index + 1) * 1_000_000_000)
                    for index in range(5)
                ]
                backup = root / "backups" / "operator-copy"
                backup.parent.mkdir()
                backup.write_bytes(b"backup sentinel")
                flat = Path(name).resolve() / "flat"
                flat.mkdir()
                flat_sentinel = flat / "foreign.keep"
                flat_sentinel.write_bytes(b"flat sentinel")
                services = FakeSystemServices(b"fixture unit", active=set())
                plan = deployment.build_plan(repo, mode=mode)

                with mock.patch.object(deployment, "mount_points", return_value=[Path("/")]):
                    result = self._install(
                        plan, repo, root, mode, services=services, flat_root=flat
                    )

                self.assertEqual(result["gc"]["status"], "ok")
                self.assertFalse(old[0].exists())
                self.assertFalse(old[1].exists())
                self.assertTrue((root / "releases" / plan["release"]).is_dir())
                self.assertFalse((root / "current").exists())
                self.assertEqual(backup.read_bytes(), b"backup sentinel")
                self.assertEqual(flat_sentinel.read_bytes(), b"flat sentinel")

    def test_activate_and_update_success_collect_old_releases(self):
        with self.fixture() as repo, tempfile.TemporaryDirectory() as name:
            root = Path(name).resolve() / "packages"
            old = [
                self._make_release(root, f"activate-{index}", (index + 1) * 1_000_000_000)
                for index in range(5)
            ]
            backup = root / "backups" / "operator-copy"
            backup.parent.mkdir()
            backup.write_bytes(b"backup sentinel")
            flat = Path(name).resolve() / "flat"
            flat.mkdir()
            flat_sentinel = flat / "foreign.keep"
            flat_sentinel.write_bytes(b"flat sentinel")
            services = self._services()

            with mock.patch.object(deployment, "mount_points", return_value=[Path("/")]):
                first = deployment.build_plan(repo)
                activated = self._install(
                    first, repo, root, "activate", services=services, flat_root=flat
                )
                self.assertEqual(activated["gc"]["status"], "ok")
                self.assertFalse(old[0].exists())

                update_candidates = [
                    self._make_release(root, f"update-{index}", (index + 10) * 1_000_000_000)
                    for index in range(5)
                ]
                services.restart_calls.clear()
                audiobook = repo / "pi/apps/audiobooks/audiobook_server.py"
                audiobook.write_text("SOURCE_MARKER = 'gc update'\n")
                second = deployment.build_plan(repo)
                updated = self._install(
                    second, repo, root, "update", services=services, flat_root=flat
                )

            self.assertEqual(updated["gc"]["status"], "ok")
            self.assertFalse(update_candidates[0].exists())
            self.assertEqual(services.restart_calls, [])
            self.assertEqual(os.readlink(root / "current"), f"releases/{second['release']}")
            self.assertEqual(os.readlink(root / "previous"), f"releases/{first['release']}")
            self.assertEqual(backup.read_bytes(), b"backup sentinel")
            self.assertEqual(flat_sentinel.read_bytes(), b"flat sentinel")

    def test_collect_releases_skips_unsafe_filesystem_ambiguity(self):
        cases = (
            "same-device-mount",
            "mountinfo-error",
            "release-symlink",
            "nested-symlink",
            "unexpected-name",
            "invalid-manifest",
            "foreign-file",
            "writable-node",
            "hardlinked-node",
            "foreign-owner",
            "different-device",
            "missing-previous-target",
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
                    (root / "releases" / "backup-copy").mkdir()
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

                backup = root / "backups" / "sentinel"
                backup.parent.mkdir(exist_ok=True)
                backup.write_bytes(b"untouched")
                before = {path.name for path in (root / "releases").iterdir()}
                if case in ("foreign-owner", "different-device"):
                    original_lstat = Path.lstat

                    def ambiguous_lstat(path, *args, **kwargs):
                        info = original_lstat(path, *args, **kwargs)
                        if path == candidate:
                            fields = list(info)
                            index = 4 if case == "foreign-owner" else 2
                            fields[index] = fields[index] + 1
                            return os.stat_result(fields)
                        return info

                    with mock.patch.object(Path, "lstat", ambiguous_lstat):
                        result = self._collect_gc(
                            root, services, releases[-1], mount_points=mounts
                        )
                else:
                    result = self._collect_gc(
                        root, services, releases[-1], mount_points=mounts
                    )

                self.assertEqual(result["status"], "skipped")
                self.assertEqual(result["removed"], [])
                self.assertEqual(
                    {path.name for path in (root / "releases").iterdir()}, before
                )
                self.assertEqual(backup.read_bytes(), b"untouched")
                for sentinel, expected in outside_sentinels:
                    self.assertEqual(sentinel.read_bytes(), expected)

    def test_collect_releases_skips_ambiguous_active_service_records(self):
        cases = (
            "missing-record",
            "unreadable-record",
            "pid-mismatch",
            "invocation-mismatch",
            "package-mismatch",
            "transitional-state",
            "service-query-error",
        )
        for case in cases:
            with self.subTest(case=case), self._gc_root() as (root, releases, services):
                services.active.add(deployment.UNIT)
                services.set_running_release(releases[0])
                valid_state = services.dashboard_state()
                if case == "missing-record":
                    services.running_record = None
                elif case == "unreadable-record":
                    services.running_record_error = PermissionError("record unreadable")
                elif case == "pid-mismatch":
                    services.dashboard_state_override = valid_state
                    services.running_record["pid"] += 1
                elif case == "invocation-mismatch":
                    services.dashboard_state_override = valid_state
                    services.running_record["invocation_id"] = "f" * 32
                elif case == "package-mismatch":
                    services.running_record["package_path"] = str(root / "outside" / "pi")
                elif case == "transitional-state":
                    services.dashboard_state_override = {
                        "ActiveState": "activating",
                        "MainPID": valid_state["MainPID"],
                        "InvocationID": valid_state["InvocationID"],
                    }
                elif case == "service-query-error":
                    services.dashboard_state_error = RuntimeError("systemctl unavailable")

                before = {path.name for path in (root / "releases").iterdir()}
                result = self._collect_gc(root, services, releases[-1])
                self.assertEqual(result["status"], "skipped")
                self.assertEqual(result["removed"], [])
                self.assertEqual(
                    {path.name for path in (root / "releases").iterdir()}, before
                )

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
                    with mock.patch.object(
                        deployment, "mount_points", return_value=[Path("/")]
                    ):
                        result = deployment.collect_releases(
                            root, lock, services, releases[-1]
                        )
                finally:
                    lock.close()

                self.assertEqual(result["status"], "skipped")
                self.assertEqual(result["removed"], [])
                self.assertEqual(
                    {path.name for path in (root / "releases").iterdir()}, before
                )

    def test_gc_deletion_error_does_not_undo_activation_or_restart(self):
        with self.fixture() as repo, tempfile.TemporaryDirectory() as name:
            root = Path(name).resolve() / "packages"
            flat = Path(name).resolve() / "flat"
            flat.mkdir()
            flat_sentinel = flat / "foreign.keep"
            flat_sentinel.write_bytes(b"flat sentinel")
            backup = root / "backups" / "operator-copy"
            backup.parent.mkdir(parents=True)
            backup.write_bytes(b"backup sentinel")
            services = self._services()
            with mock.patch.object(deployment, "mount_points", return_value=[Path("/")]):
                first = deployment.build_plan(repo)
                self._install(first, repo, root, "activate", services=services, flat_root=flat)
                candidates = [
                    self._make_release(root, f"delete-error-{index}", (index + 1) * 1_000_000_000)
                    for index in range(5)
                ]
                dashboard = repo / "pi/apps/van_dashboard/van_dashboard.py"
                dashboard.write_text("SOURCE_MARKER = 'restart before gc error'\n")
                second = deployment.build_plan(repo)
                services.restart_calls.clear()
                real_rmtree = shutil.rmtree

                def fail_candidate(path, *args, **kwargs):
                    if Path(path) == candidates[0]:
                        raise OSError("simulated release deletion failure")
                    return real_rmtree(path, *args, **kwargs)

                with mock.patch.object(
                    deployment.shutil, "rmtree", side_effect=fail_candidate
                ):
                    result = self._install(
                        second, repo, root, "update", services=services, flat_root=flat
                    )

            self.assertEqual(result["release"], str(root / "releases" / second["release"]))
            self.assertEqual(result["restarted"], [deployment.UNIT])
            self.assertEqual(services.restart_calls, [deployment.UNIT])
            self.assertEqual(result["gc"]["status"], "skipped")
            self.assertIn("simulated release deletion failure", result["gc"]["reason"])
            self.assertEqual(deployment.current_release(root).name, second["release"])
            self.assertEqual(
                Path(services.running_record["package_path"]).parent.name,
                second["release"],
            )
            self.assertEqual(
                json.loads((root / "activated.json").read_bytes()),
                {"release": second["release"]},
            )
            self.assertTrue(candidates[0].is_dir())
            self.assertEqual(backup.read_bytes(), b"backup sentinel")
            self.assertEqual(flat_sentinel.read_bytes(), b"flat sentinel")

    def test_gc_tracks_actual_runtime_across_updates_and_external_restart(self):
        with self.fixture() as repo, tempfile.TemporaryDirectory() as name:
            root = Path(name).resolve() / "packages"
            services = self._services()
            audiobook = repo / "pi/apps/audiobooks/audiobook_server.py"
            with mock.patch.object(deployment, "mount_points", return_value=[Path("/")]):
                first = deployment.build_plan(repo)
                self._install(first, repo, root, "activate", services=services)
                original_runtime = root / "releases" / first["release"]
                services.restart_calls.clear()

                latest = first
                for index in range(6):
                    audiobook.write_text(f"SOURCE_MARKER = 'unrelated-{index}'\n")
                    latest = deployment.build_plan(repo)
                    result = self._install(
                        latest, repo, root, "update", services=services
                    )
                    self.assertEqual(result["restarted"], [])

                self.assertTrue(original_runtime.is_dir())
                self.assertNotEqual(deployment.current_release(root), original_runtime)
                self.assertNotEqual(
                    (root / os.readlink(root / "previous")).resolve(),
                    original_runtime,
                )
                self.assertEqual(services.restart_calls, [])

                external_runtime = deployment.current_release(root)
                services.restart(deployment.UNIT)
                self.assertEqual(
                    Path(services.running_record["package_path"]).parent,
                    external_runtime,
                )
                services.restart_calls.clear()
                for index in range(6, 12):
                    audiobook.write_text(f"SOURCE_MARKER = 'unrelated-{index}'\n")
                    latest = deployment.build_plan(repo)
                    result = self._install(
                        latest, repo, root, "update", services=services
                    )
                    self.assertEqual(result["restarted"], [])

            self.assertFalse(original_runtime.exists())
            self.assertTrue(external_runtime.is_dir())
            self.assertNotEqual(deployment.current_release(root), external_runtime)
            self.assertNotEqual(
                (root / os.readlink(root / "previous")).resolve(),
                external_runtime,
            )
            self.assertEqual(services.restart_calls, [])

    def test_system_services_dashboard_state_parses_and_rejects_queries(self):
        services = deployment.SystemServices()
        completed = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=(
                "ActiveState=active\n"
                "MainPID=4321\n"
                "InvocationID=0123456789abcdef0123456789abcdef\n"
            ),
        )
        with mock.patch.object(deployment.subprocess, "run", return_value=completed):
            self.assertEqual(
                services.dashboard_state(),
                {
                    "ActiveState": "active",
                    "MainPID": "4321",
                    "InvocationID": "0123456789abcdef0123456789abcdef",
                },
            )

        for output in (
            "ActiveState=active\nMainPID=4321\n",
            "ActiveState=active\nMainPID=4321\nInvocationID=abc\nOther=value\n",
        ):
            with self.subTest(output=output), mock.patch.object(
                deployment.subprocess,
                "run",
                return_value=subprocess.CompletedProcess([], 0, stdout=output),
            ):
                with self.assertRaises(ValueError):
                    services.dashboard_state()
        with mock.patch.object(
            deployment.subprocess,
            "run",
            side_effect=subprocess.CalledProcessError(1, ["systemctl", "show"]),
        ):
            with self.assertRaises(subprocess.CalledProcessError):
                services.dashboard_state()

    def test_system_services_reads_regular_running_record_and_rejects_symlink(self):
        with tempfile.TemporaryDirectory(prefix="running-record-") as name:
            runtime = Path(name).resolve()
            record_path = runtime / "package-release"
            record = {
                "package_path": "/packages/releases/" + "1" * 24 + "/pi",
                "pid": 4321,
                "invocation_id": "a" * 32,
            }
            record_path.write_bytes(deployment.encoded(record))
            services = deployment.SystemServices()
            with mock.patch.object(deployment, "RUNNING_RECORD", record_path):
                self.assertEqual(services.read_running_record(), record)

            target = runtime / "record-target"
            target.write_bytes(deployment.encoded(record))
            link = runtime / "record-link"
            link.symlink_to(target)
            with mock.patch.object(deployment, "RUNNING_RECORD", link):
                with self.assertRaises(ValueError):
                    services.read_running_record()

    def test_dashboard_entrypoint_change_is_deployed_and_restarts_once(self):
        with self.fixture() as repo, tempfile.TemporaryDirectory() as name:
            root = Path(name).resolve() / "packages"
            services = self._services()
            with mock.patch.object(deployment, "mount_points", return_value=[Path("/")]):
                first = deployment.build_plan(repo)
                self._install(first, repo, root, "activate", services=services)
                services.restart_calls.clear()
                services.install_calls.clear()
                entrypoint = repo / "pi/apps/van_dashboard/__main__.py"
                entrypoint.write_text("SOURCE_MARKER = 'entrypoint v2'\n")
                second = deployment.build_plan(repo)
                result = self._install(
                    second, repo, root, "update", services=services
                )

            self.assertNotEqual(
                first["manifest"]["services"][deployment.UNIT],
                second["manifest"]["services"][deployment.UNIT],
            )
            self.assertEqual(result["restarted"], [deployment.UNIT])
            self.assertEqual(services.restart_calls, [deployment.UNIT])
            self.assertEqual(services.install_calls, [])
            self.assertEqual(
                (root / "releases" / second["release"] / "pi/apps/van_dashboard/__main__.py").read_bytes(),
                entrypoint.read_bytes(),
            )

    def test_dashboard_entrypoint_records_pinned_package_and_identity(self):
        with tempfile.TemporaryDirectory(prefix="dashboard-entrypoint-") as name:
            base = Path(name).resolve()
            root = base / "packages"
            release_one = root / "releases" / ("1" * 24)
            release_two = root / "releases" / ("2" * 24)
            (release_one / "pi").mkdir(parents=True)
            (release_two / "pi").mkdir(parents=True)
            (root / "current").symlink_to(f"releases/{release_two.name}")
            runtime = base / "run"
            runtime.mkdir()
            invocation_id = "a" * 32
            production_main = mock.Mock()
            modules = self._entrypoint_modules(runtime, production_main)
            pi_package = sys.modules["pi"]

            with mock.patch.object(
                pi_package, "__path__", [str(release_one / "pi")]
            ), mock.patch.dict(sys.modules, modules), mock.patch.dict(
                os.environ, {"INVOCATION_ID": invocation_id}, clear=False
            ):
                runpy.run_path(
                    str(DASHBOARD_ENTRYPOINT),
                    run_name="pi.apps.van_dashboard.__main__",
                )

            record = json.loads((runtime / "package-release").read_bytes())
            self.assertEqual(record["package_path"], str(release_one / "pi"))
            self.assertEqual(record["pid"], os.getpid())
            self.assertEqual(record["invocation_id"], invocation_id)
            self.assertEqual((root / "current").resolve(), release_two)
            production_main.assert_called_once_with()

    def test_dashboard_entrypoint_warns_but_runs_when_record_publish_fails(self):
        with tempfile.TemporaryDirectory(prefix="dashboard-entrypoint-") as name:
            base = Path(name).resolve()
            release = base / "packages" / "releases" / ("1" * 24)
            (release / "pi").mkdir(parents=True)
            runtime = base / "run"
            runtime.mkdir()
            production_main = mock.Mock()
            modules = self._entrypoint_modules(runtime, production_main)
            pi_package = sys.modules["pi"]
            stderr = io.StringIO()

            with mock.patch.object(
                pi_package, "__path__", [str(release / "pi")]
            ), mock.patch.dict(sys.modules, modules), mock.patch.object(
                os, "replace", side_effect=OSError("replace denied")
            ), mock.patch("sys.stderr", stderr):
                runpy.run_path(
                    str(DASHBOARD_ENTRYPOINT),
                    run_name="pi.apps.van_dashboard.__main__",
                )

            self.assertIn(
                "WARNING: cannot record dashboard package release: replace denied",
                stderr.getvalue(),
            )
            production_main.assert_called_once_with()
            self.assertEqual(list(runtime.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
