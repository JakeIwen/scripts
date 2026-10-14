"""Generation and runtime parity of the fixed backup resource vocabulary."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from pi.scripts.backup import backup_locks as locks


ROOT = Path(__file__).resolve().parents[3]
ARTIFACTS = ('scripts/backup/backup_lock_registry.sh', 'tmpfiles.d/vanpi-backup.conf')


class BackupRegistryTests(unittest.TestCase):
    def generate(self, root, *arguments):
        environment = dict(os.environ, PYTHONDONTWRITEBYTECODE='1',
                           PYTHONPATH=str(ROOT),
                           VANPI_BACKUP_LOCK_DIR='/not-the-production-registry',
                           VANPI_BACKUP_JOB_LOCK='/not-the-production-legacy')
        return subprocess.run([sys.executable, '-m', 'pi.generate_backup_lock_registry', *arguments],
                              cwd=root, env=environment, capture_output=True, text=True, timeout=10)

    def fixture(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        for relative in (*ARTIFACTS, '__init__.py', 'generate_backup_lock_registry.py',
                         'scripts/backup/backup_locks.py'):
            destination = root / 'pi' / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / 'pi' / relative, destination)
        return root

    def test_checked_in_artifacts_match_environment_independent_generation(self):
        result = self.generate(ROOT, '--check')
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_check_refuses_drift_without_writing_and_generation_repairs_it(self):
        root = self.fixture()
        paths = [root / 'pi' / relative for relative in ARTIFACTS]
        paths[0].unlink()
        paths[1].write_text('stale output\n')
        previous = paths[1].stat().st_mtime_ns
        result = self.generate(root, '--check')
        self.assertEqual(result.returncode, 1, result.stderr)
        for relative in ARTIFACTS:
            self.assertIn(relative, result.stderr)
        self.assertFalse(paths[0].exists())
        self.assertEqual(paths[1].read_text(), 'stale output\n')
        self.assertEqual(paths[1].stat().st_mtime_ns, previous)
        result = self.generate(root)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.generate(root, '--check').returncode, 0)
        for relative in ARTIFACTS:
            self.assertEqual((root / 'pi' / relative).read_bytes(), (ROOT / 'pi' / relative).read_bytes())

    @unittest.skipUnless(sys.platform.startswith('linux'), 'shell registry requires Bash 4')
    def test_shell_python_tmpfiles_paths_and_disk_policy_agree(self):
        script = r'''
set -eu
. pi/scripts/backup/backup_locks.sh
for name in "${BACKUP_JOB_KINDS[@]}"; do backup_lock_path job "$name"; done
for name in "${BACKUP_DISK_LABELS[@]}"; do backup_lock_path disk "$name"; done
for name in "${BACKUP_DATA_NAMES[@]}"; do backup_lock_path data "$name"; done
backup_lock_path cloud "$BACKUP_CLOUD_SLOT"
! backup_lock_path disk unknown
. pi/scripts/disk_policy.sh
for name in "${MOUNT_LABELS[@]}" "${MANUAL_MOUNT_LABELS[@]}" "${HDD_LABELS[@]}"; do
  backup_lock_path disk "$name" >/dev/null
done
'''
        environment = dict(os.environ, VANPI_BACKUP_LOCK_DIR='/run/lock/vanpi-backup')
        result = subprocess.run(['/bin/bash', '-c', script], cwd=ROOT, env=environment,
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        expected = [f'/run/lock/vanpi-backup/{kind.value}-{name}.lock'
                    for kind, names in locks.LOCK_NAMES.items() for name in names]
        self.assertEqual(result.stdout.splitlines(), expected)
        records = [line.split() for line in (ROOT / 'pi' / ARTIFACTS[1]).read_text().splitlines()
                   if line and not line.startswith('#')]
        self.assertEqual([row[1] for row in records if row[1].startswith('/run/lock/vanpi-backup/')],
                         expected)


if __name__ == '__main__':
    unittest.main()
