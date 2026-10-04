#!/usr/bin/env python3
"""Compatibility checks for the retired video deployment wrapper."""

from __future__ import annotations

import stat
import subprocess
import tempfile
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
DEPLOY_PATH = REPOSITORY_ROOT / "pi" / "deploy_video_library.sh"
ALIAS_PATH = REPOSITORY_ROOT / "pi" / "scripts" / "alias_media.sh"
UNIT_PATH = REPOSITORY_ROOT / "pi" / "services" / "video-library.service"
DOC_PATH = REPOSITORY_ROOT / "pi" / "docs" / "media" / "VIDEO_LIBRARY.md"


class VideoIdentityDeploymentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.deploy = DEPLOY_PATH.read_text(encoding="utf-8")
        cls.alias = ALIAS_PATH.read_text(encoding="utf-8")
        cls.unit = UNIT_PATH.read_text(encoding="utf-8")
        cls.docs = DOC_PATH.read_text(encoding="utf-8")

    def test_shell_entrypoints_parse_and_are_executable(self):
        for path in (DEPLOY_PATH, ALIAS_PATH):
            with self.subTest(path=path):
                subprocess.run(["bash", "-n", str(path)], check=True)
                self.assertTrue(path.stat().st_mode & stat.S_IXUSR)

    def test_retired_deployer_refuses_without_network_or_file_operations(self):
        self.assertIn(
            "python3 pi/deploy_python.py --update --service video-library.service",
            self.deploy,
        )
        self.assertIn("saved pre-package-unit rollback procedure", self.deploy)
        for forbidden in ("ssh", "scp", "systemctl", "mktemp", "git -C"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, self.deploy)

        with tempfile.TemporaryDirectory() as temporary:
            result = subprocess.run(
                ["/bin/bash", str(DEPLOY_PATH)],
                check=False,
                capture_output=True,
                text=True,
                env={"PATH": temporary},
            )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(
            "python3 pi/deploy_python.py --update --service video-library.service",
            result.stderr,
        )

    def test_rollback_refuses_and_points_to_saved_package_unit_rollback(self):
        with tempfile.TemporaryDirectory() as temporary:
            result = subprocess.run(
                ["/bin/bash", str(DEPLOY_PATH), "--rollback"],
                check=False,
                capture_output=True,
                text=True,
                env={"PATH": temporary},
            )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("saved pre-package-unit rollback procedure", result.stderr)
        self.assertIn("pi/docs/deployment.md", result.stderr)

    def test_video_service_declares_identity_modules(self):
        for module in ("video_asset_catalog.py", "video_qbittorrent.py"):
            with self.subTest(module=module):
                self.assertIn(
                    "ExecStartPre=/usr/bin/test -r "
                    f"/home/pi/scripts/python-packages/current/pi/apps/video_library/{module}",
                    self.unit,
                )

    def test_documented_media_test_gate_includes_history_failure_boundaries(self):
        self.assertIn("pi.tests.media.test_video_history_edges", self.docs)

    def test_alias_jobs_are_atomic_detached_queued_and_share_one_lock(self):
        self.assertEqual(self.alias.count("/run/lock/alias-media.lock"), 1)
        self.assertIn("/usr/bin/flock 9", self.alias)
        self.assertNotIn("/usr/bin/flock -n 9", self.alias)
        self.assertIn('if [[ "$mode" == new ]]', self.alias)
        self.assertIn('alias_folders "/mnt/movingparts"', self.alias)
        self.assertIn("/usr/bin/mountpoint -q /mnt/bigboi", self.alias)
        self.assertNotIn("mountpoint -q /mnt/bigboi/mp_backup", self.alias)
        self.assertIn(") </dev/null >>/home/pi/log/alias_media.log 2>&1 &", self.alias)
        self.assertIn("waiting here never blocks", self.alias)
        self.assertIn('mktemp -d "$src/.links-stage.XXXXXX"', self.alias)
        self.assertIn('mktemp -d "$src/links/.alias-new.XXXXXX"', self.alias)
        self.assertNotIn("-delete", self.alias)

    def test_alias_notification_is_loopback_only_and_bounded(self):
        self.assertIn("--connect-timeout 1 --max-time 2", self.alias)
        self.assertIn(
            "http://127.0.0.1:8789/api/torrents/reconcile",
            self.alias,
        )
        self.assertIn("-H 'X-Van-Video: 1' -X POST", self.alias)
        self.assertNotIn("qBittorrent.conf", self.alias)


if __name__ == "__main__":
    unittest.main()
