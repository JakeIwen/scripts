import subprocess
import stat
import tempfile
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
BACKUP_DIR = REPOSITORY_ROOT / "pi" / "scripts" / "backup"


class BackupDeploymentTests(unittest.TestCase):
    def test_lock_io_dependency_exists(self):
        self.assertTrue((BACKUP_DIR / "backup_lock_io.sh").is_file())

    def test_clone_fit_budget_uses_configured_capacity(self):
        config_path = BACKUP_DIR / "backup_conf.sh"
        with tempfile.TemporaryDirectory() as tempdir:
            setting = Path(tempdir) / "clone_card_nominal_gb"
            setting.write_text("128\n", encoding="utf-8")
            configured = subprocess.run(
                [
                    "/bin/bash",
                    "-c",
                    'source "$1"; printf "%s %s\\n" '
                    '"$CLONE_CARD_NOMINAL_GB" "$ROOT_USED_MAX_GB"',
                    "test",
                    str(config_path),
                ],
                check=True,
                capture_output=True,
                text=True,
                env={"CLONE_CARD_NOMINAL_GB_FILE": str(setting)},
            )
        self.assertEqual(configured.stdout, "128 104\n")

    def test_rootfs_helpers_exist_and_are_executable(self):
        shipped = [
            BACKUP_DIR / "rootfs_budget.sh",
            REPOSITORY_ROOT / "pi" / "scripts" / "rootfs_tidy.sh",
            REPOSITORY_ROOT / "shared" / "sh" / "cleanup_codex_releases.py",
        ]

        for path in shipped:
            self.assertTrue(path.is_file(), path)
            self.assertTrue(path.stat().st_mode & stat.S_IXUSR, path)


if __name__ == "__main__":
    unittest.main()
