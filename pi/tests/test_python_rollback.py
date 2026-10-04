from __future__ import annotations

import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile
import unittest


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPOSITORY_ROOT / "pi" / "rollback_python_service.sh"

SERVICES = {
    "van-dashboard.service": ("van_dashboard.py", "/usr/bin/python3"),
    "video-library.service": ("video_library_server.py", "/usr/bin/python3"),
    "audiobooks.service": ("audiobook_server.py", "/usr/bin/python3"),
    "bme280-mqtt.service": ("bme280_mqtt.py", "/home/pi/pyvenv/bin/python"),
}


class RollbackFixture:
    """Run the owner script with its fixed Pi roots redirected into a temp tree."""

    def __enter__(self):
        self._temporary = tempfile.TemporaryDirectory(prefix="python-rollback-")
        self.base = Path(self._temporary.name).resolve()
        self.root = self.base / "packages"
        self.units = self.base / "systemd"
        self.flat_root = self.base / "flat"
        self.bin = self.base / "bin"
        self.log = self.base / "commands.log"
        for path in (self.root, self.units, self.flat_root, self.bin):
            path.mkdir(parents=True)

        self._write_command(
            "readlink",
            """#!/tmp/scripts-venv/bin/python
import os
import sys

if len(sys.argv) != 3 or sys.argv[1] != '-e':
    raise SystemExit(2)
path = os.path.realpath(sys.argv[2])
if not os.path.exists(path):
    raise SystemExit(1)
print(path)
""",
        )
        self._write_command("flock", "#!/bin/sh\nexit 0\n")
        self._write_command(
            "sudo",
            """#!/bin/sh
printf 'sudo %s\\n' "$*" >> "$ROLLBACK_LOG"
case "$1" in
  /usr/bin/install)
    shift
    exec /usr/bin/install "$@"
    ;;
  /usr/bin/systemctl)
    exit 0
    ;;
  *)
    exit 97
    ;;
esac
""",
        )

        source = SCRIPT.read_text(encoding="utf-8")
        source = source.replace(
            "root=/home/pi/scripts/python-packages",
            f"root={self.root}",
        )
        source = source.replace("units=/etc/systemd/system", f"units={self.units}")
        source = source.replace(
            "flat_root=/home/pi/scripts/python-automation",
            f"flat_root={self.flat_root}",
        )
        source = source.replace("/usr/bin/readlink", str(self.bin / "readlink"))
        source = source.replace("/usr/bin/flock", str(self.bin / "flock"))
        self.script = self.base / "rollback_python_service.sh"
        self.script.write_text(source, encoding="utf-8")
        self.script.chmod(0o700)
        self.env = os.environ.copy()
        self.env["PATH"] = f"{self.bin}:{self.env.get('PATH', '')}"
        self.env["ROLLBACK_LOG"] = str(self.log)
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self._temporary.cleanup()

    def _write_command(self, name: str, content: str) -> None:
        path = self.bin / name
        path.write_text(content, encoding="utf-8")
        path.chmod(stat.S_IRWXU)

    def prepare_service(self, unit: str, *, old_dashboard_backup: bool = False):
        flat, interpreter = SERVICES[unit]
        backup_root = self.root / "pre-package-units"
        if old_dashboard_backup:
            backup_dir = backup_root
        else:
            backup_dir = backup_root / f"{unit}.backup"
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup = backup_dir / unit
        expected_flat = self.flat_root / flat
        unit_bytes = (
            "[Unit]\n"
            f"Description=fixture {unit}\n"
            "[Service]\n"
            f"ExecStart={interpreter} {expected_flat}\n"
            "Restart=always\n"
        ).encode()
        backup.write_bytes(unit_bytes)

        current = self.units / unit
        current.write_bytes(b"package unit that must be replaced\n")
        self.flat_root.joinpath(flat).write_bytes(b"flat recovery sentinel\x00\n")
        return backup, current, expected_flat, unit_bytes

    def run(self, unit: str, flat: str):
        return subprocess.run(
            ["bash", str(self.script), unit, flat],
            cwd=self.base,
            env=self.env,
            capture_output=True,
            text=True,
            check=False,
        )

    @staticmethod
    def retired_path(result: subprocess.CompletedProcess[str]) -> Path:
        match = re.search(r"retired state: (.+)\n?$", result.stdout)
        if not match:
            raise AssertionError(f"rollback did not print retired state: {result.stdout!r}")
        return Path(match.group(1))

    def command_log(self) -> str:
        return self.log.read_text(encoding="utf-8") if self.log.exists() else ""


class PythonRollbackTests(unittest.TestCase):
    def test_script_passes_bash_syntax_check(self):
        subprocess.run(["bash", "-n", str(SCRIPT)], check=True)

    def test_each_service_restores_saved_unit_and_retires_only_its_state(self):
        for unit, (flat, _interpreter) in SERVICES.items():
            with self.subTest(unit=unit), RollbackFixture() as fixture:
                backup, current, flat_path, expected_unit = fixture.prepare_service(unit)
                service_state = fixture.root / "service-state" / f"{unit}.json"
                pending_state = fixture.root / "pending-restarts" / f"{unit}.json"
                unrelated_state = fixture.root / "service-state" / "other.service.json"
                unrelated_pending = (
                    fixture.root / "pending-restarts" / "other.service.json"
                )
                unrelated_unit = fixture.units / "other.service"
                for path, contents in (
                    (service_state, b"service state"),
                    (pending_state, b"pending state"),
                    (unrelated_state, b"unrelated state"),
                    (unrelated_pending, b"unrelated pending"),
                    (unrelated_unit, b"unrelated unit"),
                ):
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(contents)
                flat_before = flat_path.read_bytes()

                result = fixture.run(unit, flat)

                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(current.read_bytes(), expected_unit)
                retired = fixture.retired_path(result)
                self.assertEqual(
                    (retired / "service-state" / f"{unit}.json").read_bytes(),
                    b"service state",
                )
                self.assertEqual(
                    (retired / "pending-restarts" / f"{unit}.json").read_bytes(),
                    b"pending state",
                )
                self.assertFalse(service_state.exists())
                self.assertFalse(pending_state.exists())
                self.assertEqual(unrelated_state.read_bytes(), b"unrelated state")
                self.assertEqual(unrelated_pending.read_bytes(), b"unrelated pending")
                self.assertEqual(unrelated_unit.read_bytes(), b"unrelated unit")
                self.assertEqual(flat_path.read_bytes(), flat_before)
                log = fixture.command_log()
                self.assertIn("systemctl daemon-reload", log)
                self.assertIn(f"systemctl restart {unit}", log)

    def test_dashboard_old_backup_retires_legacy_host_markers(self):
        with RollbackFixture() as fixture:
            unit = "van-dashboard.service"
            flat = "van_dashboard.py"
            _backup, current, flat_path, expected_unit = fixture.prepare_service(
                unit, old_dashboard_backup=True
            )
            generic = fixture.root / "service-state" / f"{unit}.json"
            pending = fixture.root / "pending-restarts" / f"{unit}.json"
            activated = fixture.root / "activated.json"
            old_pending = fixture.root / "pending-restart"
            for path, contents in (
                (generic, b"generic"),
                (pending, b"pending"),
                (activated, b"activated"),
                (old_pending, b"old pending"),
            ):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(contents)
            flat_before = flat_path.read_bytes()

            result = fixture.run(unit, flat)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(current.read_bytes(), expected_unit)
            retired = fixture.retired_path(result)
            self.assertEqual((retired / "activated.json").read_bytes(), b"activated")
            self.assertEqual(
                (retired / "pending-restart").read_bytes(), b"old pending"
            )
            self.assertEqual(
                (retired / "service-state" / f"{unit}.json").read_bytes(), b"generic"
            )
            self.assertEqual(
                (retired / "pending-restarts" / f"{unit}.json").read_bytes(),
                b"pending",
            )
            self.assertFalse(activated.exists())
            self.assertFalse(old_pending.exists())
            self.assertEqual(flat_path.read_bytes(), flat_before)

    def test_changed_dropins_refuse_before_any_mutation(self):
        with RollbackFixture() as fixture:
            unit = "video-library.service"
            flat = "video_library_server.py"
            _backup, current, flat_path, _expected_unit = fixture.prepare_service(unit)
            backup_dropin = (
                fixture.root / "pre-package-units" / f"{unit}.backup" / f"{unit}.d"
            )
            live_dropin = fixture.units / f"{unit}.d"
            backup_dropin.mkdir(parents=True)
            live_dropin.mkdir(parents=True)
            (backup_dropin / "override.conf").write_text("saved\n", encoding="utf-8")
            (live_dropin / "override.conf").write_text("changed\n", encoding="utf-8")
            state = fixture.root / "service-state" / f"{unit}.json"
            state.parent.mkdir(parents=True)
            state.write_bytes(b"must remain")
            current_before = current.read_bytes()
            flat_before = flat_path.read_bytes()

            result = fixture.run(unit, flat)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Drop-ins changed", result.stderr)
            self.assertEqual(current.read_bytes(), current_before)
            self.assertEqual(flat_path.read_bytes(), flat_before)
            self.assertEqual(state.read_bytes(), b"must remain")
            self.assertFalse(any(fixture.root.glob("rollback-state.*")))
            self.assertEqual(fixture.command_log(), "")

    def test_unknown_service_script_pair_refuses_without_touching_fixture(self):
        with RollbackFixture() as fixture:
            result = fixture.run("unknown.service", "unknown.py")

            self.assertEqual(result.returncode, 2)
            self.assertIn("Unknown service/script pair", result.stderr)
            self.assertEqual(fixture.command_log(), "")

    def test_missing_or_foreign_backup_unit_refuses_without_mutation(self):
        for foreign in (False, True):
            with self.subTest(foreign=foreign), RollbackFixture() as fixture:
                unit = "audiobooks.service"
                flat = "audiobook_server.py"
                _backup, current, flat_path, _expected_unit = fixture.prepare_service(unit)
                backup = fixture.root / "pre-package-units" / f"{unit}.backup" / unit
                if not foreign:
                    backup.unlink()
                else:
                    foreign_unit = fixture.base / "foreign-unit"
                    foreign_unit.write_bytes(b"foreign backup")
                    backup.unlink()
                    backup.symlink_to(foreign_unit)
                current_before = current.read_bytes()
                flat_before = flat_path.read_bytes()

                result = fixture.run(unit, flat)

                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(current.read_bytes(), current_before)
                self.assertEqual(flat_path.read_bytes(), flat_before)
                self.assertEqual(fixture.command_log(), "")
                self.assertFalse(any(fixture.root.glob("rollback-state.*")))

    def test_foreign_backup_command_refuses_even_when_file_is_regular(self):
        with RollbackFixture() as fixture:
            unit = "bme280-mqtt.service"
            flat = "bme280_mqtt.py"
            backup, current, flat_path, _expected_unit = fixture.prepare_service(unit)
            backup.write_bytes(
                (
                    "[Service]\n"
                    f"ExecStart=/usr/bin/python3 {fixture.flat_root / flat}\n"
                ).encode()
            )
            current_before = current.read_bytes()
            flat_before = flat_path.read_bytes()

            result = fixture.run(unit, flat)

            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(current.read_bytes(), current_before)
            self.assertEqual(flat_path.read_bytes(), flat_before)
            self.assertEqual(fixture.command_log(), "")


if __name__ == "__main__":
    unittest.main()
