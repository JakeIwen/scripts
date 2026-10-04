#!/usr/bin/env python3
"""Offline environment contracts for the Pi Sonos shell wrapper."""

from __future__ import annotations

import subprocess
from pathlib import Path
import tempfile
import unittest


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPOSITORY_ROOT / "sns.sh"
PACKAGE_ROOT = "/home/pi/scripts/python-packages/current"
PACKAGE_PATHS = ":".join(
    (
        PACKAGE_ROOT,
        f"{PACKAGE_ROOT}/shared/python",
        f"{PACKAGE_ROOT}/pi/scripts/python",
    )
)
FLAT_PATH = "/home/pi/scripts/python-automation"


class SonosEnvironmentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="pi-sns-test-")
        self.addCleanup(self.temporary.cleanup)
        self.bin = Path(self.temporary.name) / "bin"
        self.bin.mkdir()
        (self.bin / "amixer").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        # sns.sh invokes python3 -c; expose only the resulting environment.
        (self.bin / "python3").write_text(
            "#!/bin/sh\nprintf '%s|%s\\n' \"$PYTHONPATH\" \"$PYTHONDONTWRITEBYTECODE\"\n",
            encoding="utf-8",
        )
        for command in ("amixer", "python3"):
            (self.bin / command).chmod(0o755)

    def run_wrapper(self, pythonpath: str | None) -> str:
        environment = {"PATH": str(self.bin)}
        if pythonpath is not None:
            environment["PYTHONPATH"] = pythonpath
        result = subprocess.run(
            ["/bin/bash", str(SCRIPT), "pink_noise"],
            check=False,
            capture_output=True,
            text=True,
            env=environment,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        path, bytecode = result.stdout.splitlines()[-1].split("|", 1)
        self.assertEqual(bytecode, "1")
        return path

    def test_empty_environment_uses_package_paths(self):
        self.assertEqual(self.run_wrapper(None), PACKAGE_PATHS)

    def test_existing_package_sonos_path_is_preserved(self):
        inherited = f"{PACKAGE_ROOT}/shared/python:/custom/video"
        self.assertEqual(self.run_wrapper(inherited), inherited)

    def test_frozen_flat_video_environment_is_preserved_for_rollback(self):
        self.assertEqual(self.run_wrapper(FLAT_PATH), FLAT_PATH)

    def test_arbitrary_video_environment_is_preserved_before_package_fallback(self):
        inherited = "/custom/video"
        self.assertEqual(self.run_wrapper(inherited), f"{inherited}:{PACKAGE_PATHS}")


if __name__ == "__main__":
    unittest.main()
