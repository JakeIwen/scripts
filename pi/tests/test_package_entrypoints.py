import contextlib
import io
import json
import os
from pathlib import Path
import runpy
import tempfile
import unittest
from unittest import mock

import pi
from pi import package_runtime


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


class PackageEntrypointTests(unittest.TestCase):
    def test_entrypoints_record_then_dispatch_original_main(self):
        expected = {
            "video_library": (
                "pi.apps.video_library.__main__",
                "/run/video-library",
                "pi.apps.video_library.video_library_server",
            ),
            "audiobooks": (
                "pi.apps.audiobooks.__main__",
                "/run/audiobooks",
                "pi.apps.audiobooks.audiobook_server",
            ),
            "bme280": (
                "pi.apps.bme280.__main__",
                "/run/bme280-mqtt",
                "pi.apps.bme280.bme280_mqtt",
            ),
        }
        for app, (run_name, runtime_dir, production_module) in expected.items():
            path = REPOSITORY_ROOT / "pi" / "apps" / app / "__main__.py"
            with self.subTest(app=app), mock.patch.object(
                package_runtime, "record_running_release"
            ) as record, mock.patch("runpy.run_module") as dispatch:
                runpy.run_path(str(path), run_name=run_name)

            record.assert_called_once_with(runtime_dir)
            dispatch.assert_called_once_with(production_module, run_name="__main__")

    def test_records_all_service_runtime_directories(self):
        expected = {
            "video-library": "/run/video-library",
            "audiobooks": "/run/audiobooks",
            "bme280-mqtt": "/run/bme280-mqtt",
        }
        with tempfile.TemporaryDirectory(prefix="package-records-") as name:
            base = Path(name)
            with mock.patch.dict(os.environ, {"INVOCATION_ID": "i" * 32}):
                for service, runtime_name in expected.items():
                    runtime = base / service
                    runtime.mkdir()
                    package_runtime.record_running_release(runtime)
                    record = json.loads(
                        (runtime / "package-release").read_text(encoding="utf-8")
                    )
                    self.assertEqual(record["package_path"], pi.__path__[0])
                    self.assertEqual(record["pid"], os.getpid())
                    self.assertEqual(record["invocation_id"], "i" * 32)
                    self.assertEqual(runtime_name, "/run/" + service)

    def test_record_warning_does_not_stop_service_start(self):
        with tempfile.TemporaryDirectory(prefix="package-record-warning-") as name:
            runtime = Path(name)
            stderr = io.StringIO()
            with mock.patch.object(
                package_runtime.os, "replace", side_effect=OSError("replace denied")
            ), contextlib.redirect_stderr(stderr):
                package_runtime.record_running_release(runtime)

            self.assertIn(
                "WARNING: cannot record package release: replace denied",
                stderr.getvalue(),
            )
            self.assertEqual(list(runtime.iterdir()), [])

    def test_record_keeps_the_imported_release_when_current_changes(self):
        original_path = pi.__path__
        with tempfile.TemporaryDirectory(prefix="package-record-pin-") as name:
            base = Path(name).resolve()
            first = base / "releases" / "first"
            second = base / "releases" / "second"
            (first / "pi").mkdir(parents=True)
            (second / "pi").mkdir(parents=True)
            current = base / "current"
            current.symlink_to("releases/first")
            runtime = base / "run"
            runtime.mkdir()
            try:
                pi.__path__ = [str(first / "pi")]
                current.unlink()
                current.symlink_to("releases/second")
                package_runtime.record_running_release(runtime)
            finally:
                pi.__path__ = original_path

            record = json.loads(
                (runtime / "package-release").read_text(encoding="utf-8")
            )
            self.assertEqual(record["package_path"], str(first / "pi"))
            self.assertEqual(current.resolve(), second)

    def test_units_use_package_entrypoints_and_preserve_runtime_settings(self):
        expected = {
            "video-library.service": (
                "/usr/bin/python3 -P -m pi.apps.video_library",
                "/home/pi/scripts/python-packages/current:/home/pi/scripts/python-packages/current/shared/python",
                "video-library",
            ),
            "audiobooks.service": (
                "/usr/bin/python3 -P -m pi.apps.audiobooks",
                "/home/pi/scripts/python-packages/current",
                "audiobooks",
            ),
            "bme280-mqtt.service": (
                "/home/pi/pyvenv/bin/python -P -m pi.apps.bme280",
                "/home/pi/scripts/python-packages/current",
                "bme280-mqtt",
            ),
        }
        for unit_name, (command, pythonpath, runtime_dir) in expected.items():
            unit = (REPOSITORY_ROOT / "pi" / "services" / unit_name).read_text(
                encoding="utf-8"
            )
            with self.subTest(unit=unit_name):
                self.assertIn(f"ExecStart={command}\n", unit)
                self.assertIn(f"Environment=PYTHONPATH={pythonpath}\n", unit)
                self.assertIn("Environment=PYTHONDONTWRITEBYTECODE=1\n", unit)
                self.assertIn(f"RuntimeDirectory={runtime_dir}\n", unit)
                self.assertIn("RuntimeDirectoryMode=0750\n", unit)
                self.assertIn("Restart=always\n", unit)
                self.assertIn("User=pi\n", unit)

        video = (REPOSITORY_ROOT / "pi/services/video-library.service").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            "ExecStartPre=/usr/bin/test -r "
            "/home/pi/scripts/python-packages/current/pi/apps/video_library/players/vlc_player.py",
            video,
        )
        self.assertIn(
            "ExecStartPre=/usr/bin/test -r "
            "/home/pi/scripts/python-packages/current/pi/apps/video_library/players/sonos_volume.py",
            video,
        )
        self.assertIn(
            "ExecStartPre=/usr/bin/test -r "
            "/home/pi/scripts/python-packages/current/shared/python/sonos_tasks.py",
            video,
        )
        self.assertIn("ExecStartPre=/usr/bin/test -r /home/pi/sns.sh\n", video)
        self.assertIn("ExecStartPre=/usr/bin/test -x /usr/bin/vlc\n", video)
        self.assertIn("Environment=DISPLAY=:0\n", video)
        self.assertIn("Environment=XDG_RUNTIME_DIR=/run/user/1000\n", video)
        self.assertIn(
            "Environment=DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus\n",
            video,
        )
        self.assertNotIn("/home/pi/scripts/python-automation/", video)


if __name__ == "__main__":
    unittest.main()
