import io
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import tarfile
import tempfile
from contextlib import contextmanager
import unittest
from unittest import mock

from pi import deploy_python as deployment


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DEPLOY_SCRIPT = REPOSITORY_ROOT / "pi" / "deploy_python.py"

FIXTURE_MODULES = {
    "pi/apps/audiobooks": ("audiobook_server.py",),
    "pi/apps/bme280": ("bme280_mqtt.py", "bme280_testread.py"),
    "pi/apps/van_dashboard": ("van_dashboard.py", "react_dashboard_preview.py"),
    "pi/apps/van_dashboard/routes": ("__init__.py", "common.py"),
    "pi/apps/video_library": ("video_library_server.py",),
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

    def restart(self, unit):
        self.restart_calls.append(unit)
        if self.restart_failures:
            self.restart_failures -= 1
            raise RuntimeError("simulated restart failure")


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


if __name__ == "__main__":
    unittest.main()
