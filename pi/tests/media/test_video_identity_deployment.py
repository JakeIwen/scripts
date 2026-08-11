#!/usr/bin/env python3
"""Compatibility checks for the opt-in video identity deployment path."""

from __future__ import annotations

import hashlib
import os
import re
import sqlite3
import stat
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
DEPLOY_PATH = REPOSITORY_ROOT / "pi" / "deploy_video_library.sh"
ALIAS_PATH = REPOSITORY_ROOT / "pi" / "scripts" / "alias_media.sh"
UNIT_PATH = REPOSITORY_ROOT / "pi" / "services" / "video-library.service"
DOC_PATH = REPOSITORY_ROOT / "pi" / "docs" / "media" / "VIDEO_LIBRARY.md"
CATALOG_DIRECTORY = REPOSITORY_ROOT / "pi" / "apps" / "video_library"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _python_heredoc(script: str) -> str:
    marker = "ssh $mux \"$target\" /usr/bin/python3 - <<'PY'\n"
    start = script.index(marker) + len(marker)
    end = script.index("\nPY\n", start)
    return script[start:end]


class VideoIdentityDeploymentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.deploy = _read(DEPLOY_PATH)
        cls.alias = _read(ALIAS_PATH)
        cls.unit = _read(UNIT_PATH)
        cls.docs = _read(DOC_PATH)

    def test_shell_entrypoints_parse_and_are_executable(self):
        for path in (DEPLOY_PATH, ALIAS_PATH):
            with self.subTest(path=path):
                subprocess.run(["bash", "-n", str(path)], check=True)
                self.assertTrue(path.stat().st_mode & stat.S_IXUSR)

    def test_deploy_is_media_scoped_and_rollback_materializes_pinned_baseline(self):
        self.assertIn('rollback_ref="${VAN_VIDEO_ROLLBACK_REF:-5168ce5}"', self.deploy)
        self.assertIn('git -C "$repo_root" show "$rollback_ref:$relative"', self.deploy)
        self.assertIn('if [[ "$mode" == rollback ]]', self.deploy)
        self.assertNotRegex(
            self.deploy,
            r"stage_file\s+(?:pi/secrets|pi/configs/smb\.conf|pi/apps/van_compute)",
        )
        self.assertNotIn("scripts/update_services.sh", self.deploy)
        self.assertIn("Install only the explicit media manifest", self.deploy)
        self.assertNotRegex(
            self.deploy,
            r"rm\s+[^\n]*(?:progress(?:\.pre-v2)?\.sqlite3|video_(?:asset_catalog|qbittorrent)\.py)",
        )
        self.assertNotIn("qBittorrent.conf", self.deploy)

        match = re.search(r"baseline_files=\(\n(?P<body>.*?)\n\)", self.deploy, re.S)
        self.assertIsNotNone(match)
        baseline_files = [
            line.strip()
            for line in match.group("body").splitlines()
            if line.strip()
        ]
        self.assertIn("pi/.bashrc", baseline_files)
        self.assertNotIn("pi/scripts/alias_media.sh", baseline_files)
        self.assertIn("Keep the data-safe, detached alias builder", self.deploy)
        for relative in baseline_files:
            with self.subTest(relative=relative):
                subprocess.run(
                    [
                        "git",
                        "-C",
                        str(REPOSITORY_ROOT),
                        "cat-file",
                        "-e",
                        f"5168ce5:{relative}",
                    ],
                    check=True,
                    capture_output=True,
                    text=True,
                )

    def test_pre_v2_snapshot_matches_catalog_name_and_is_immutable(self):
        sys.path.insert(0, str(CATALOG_DIRECTORY))
        try:
            import video_asset_catalog
        finally:
            sys.path.pop(0)

        payload = _python_heredoc(self.deploy)
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            database = home / ".local/share/van-video-library/progress.sqlite3"
            database.parent.mkdir(parents=True)
            with closing(sqlite3.connect(database)) as connection:
                connection.execute(
                    "CREATE TABLE progress (media_key TEXT PRIMARY KEY, position REAL)"
                )
                connection.execute("INSERT INTO progress VALUES ('movie', 123.0)")
                connection.commit()

            environment = os.environ.copy()
            environment["HOME"] = str(home)
            subprocess.run(
                [sys.executable, "-c", payload],
                check=True,
                env=environment,
                capture_output=True,
                text=True,
            )

            backup = database.with_name("progress.pre-v2.sqlite3")
            checksum = backup.with_name(backup.name + ".sha256")
            self.assertEqual(
                backup,
                Path(video_asset_catalog._backup_path(database)),
            )
            self.assertTrue(backup.is_file())
            self.assertTrue(checksum.is_file())
            self.assertFalse(database.with_name("progress.sqlite3.pre-v2.sqlite3").exists())
            self.assertEqual(stat.S_IMODE(backup.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(checksum.stat().st_mode), 0o600)
            expected_digest = hashlib.sha256(backup.read_bytes()).hexdigest()
            self.assertEqual(checksum.read_text(encoding="ascii").strip(), expected_digest)
            original_snapshot = backup.read_bytes()

            with closing(sqlite3.connect(database)) as connection:
                connection.execute("UPDATE progress SET position = 999.0")
                connection.commit()
            subprocess.run(
                [sys.executable, "-c", payload],
                check=True,
                env=environment,
                capture_output=True,
                text=True,
            )
            self.assertEqual(backup.read_bytes(), original_snapshot)

    def test_pre_v2_snapshot_fails_closed_on_incomplete_or_changed_pair(self):
        payload = _python_heredoc(self.deploy)
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            database = home / ".local/share/van-video-library/progress.sqlite3"
            database.parent.mkdir(parents=True)
            with closing(sqlite3.connect(database)) as connection:
                connection.execute("CREATE TABLE progress (media_key TEXT PRIMARY KEY)")
                connection.commit()
            environment = os.environ.copy()
            environment["HOME"] = str(home)
            subprocess.run(
                [sys.executable, "-c", payload],
                check=True,
                env=environment,
                capture_output=True,
                text=True,
            )

            backup = database.with_name("progress.pre-v2.sqlite3")
            checksum = backup.with_name(backup.name + ".sha256")
            checksum.unlink()
            missing = subprocess.run(
                [sys.executable, "-c", payload],
                check=False,
                env=environment,
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(missing.returncode, 0)
            self.assertIn("incomplete pre-v2 backup pair", missing.stderr)

            checksum.write_text(hashlib.sha256(backup.read_bytes()).hexdigest() + "\n")
            with backup.open("ab") as handle:
                handle.write(b"changed")
            changed = subprocess.run(
                [sys.executable, "-c", payload],
                check=False,
                env=environment,
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(changed.returncode, 0)
            self.assertIn("checksum mismatch", changed.stderr)

    def test_health_check_is_bounded_and_retried(self):
        self.assertIn("while [ \"$attempt\" -le 15 ]", self.deploy)
        self.assertIn("--connect-timeout 1 --max-time 2", self.deploy)
        self.assertIn("did not become healthy after 15 attempts", self.deploy)
        self.assertIn("sleep 1", self.deploy)

    def test_ssh_control_socket_uses_a_short_dedicated_tmp_directory(self):
        self.assertIn("local_mux_dir=\"$(mktemp -d /tmp/van-video-ssh.XXXXXX)\"", self.deploy)
        self.assertIn("ControlPath=$local_mux_dir/socket", self.deploy)
        self.assertNotIn("ControlPath=$local_stage", self.deploy)

    def test_documented_media_test_gate_includes_history_failure_boundaries(self):
        self.assertIn("pi.tests.media.test_video_history_edges", self.docs)

    def test_service_fails_preflight_if_either_identity_module_is_missing(self):
        for module in ("video_asset_catalog.py", "video_qbittorrent.py"):
            with self.subTest(module=module):
                self.assertIn(
                    "ExecStartPre=/usr/bin/test -r "
                    f"/home/pi/scripts/python-automation/{module}",
                    self.unit,
                )

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
        self.assertIn("mktemp -d \"$src/.links-stage.XXXXXX\"", self.alias)
        self.assertIn("mktemp -d \"$src/links/.alias-new.XXXXXX\"", self.alias)
        self.assertNotIn("-delete", self.alias)

    def test_alias_notification_is_loopback_only_and_bounded(self):
        self.assertIn("--connect-timeout 1 --max-time 2", self.alias)
        self.assertIn("http://127.0.0.1:8789/api/torrents/reconcile", self.alias)
        self.assertIn("-H 'X-Van-Video: 1' -X POST", self.alias)
        self.assertNotIn("qBittorrent.conf", self.alias)


if __name__ == "__main__":
    unittest.main()
