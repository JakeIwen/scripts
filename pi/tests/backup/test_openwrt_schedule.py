"""Exercise independent router scheduling without contacting a router."""
import fcntl
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


@unittest.skipUnless(sys.platform == 'linux', 'Linux flock and GNU date')
class OpenWrtScheduleTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.snapshot = self.root / 'snapshots'
        self.snapshot.mkdir(mode=0o700)
        self.stamp = self.root / 'stamp'
        self.stamp.touch()
        config = self.root / 'config.sh'
        config.write_text(f'OPENWRT_SNAPSHOT_DIR={self.snapshot}\n'
                          f'OPENWRT_BACKUP_STAMP={self.stamp}\n'
                          f'OPENWRT_BACKUP_KEY={self.root}/missing-key\n'
                          f'OPENWRT_BACKUP_KNOWN_HOSTS={self.root}/missing-hosts\n')
        progress = self.root / 'progress.sh'
        progress.write_text(':\n')
        self.env = {**os.environ, 'OPENWRT_BACKUP_CONF': str(config),
                    'OPENWRT_BACKUP_PROGRESS': str(progress)}
        for key in ('BASH_ENV', 'ENV', 'SHELLOPTS', 'BASHOPTS'):
            self.env.pop(key, None)
        self.script = Path(__file__).resolve().parents[2] / 'scripts/backup/openwrt_backup.sh'

    def run_export(self, *args):
        return subprocess.run(['/bin/bash', str(self.script), *args], env=self.env,
                              capture_output=True, text=True, timeout=5)

    def test_current_daily_export_skips_without_router_or_cloud_access(self):
        result = self.run_export('--daily')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('already succeeded today', result.stdout)

    def test_stale_daily_and_explicit_refresh_attempt_export_preserving_stamp(self):
        old = self.stamp.stat().st_mtime - 86400
        for args in ((), ('--daily',)):
            os.utime(self.stamp, (old, old))
            result = self.run_export(*args)
            self.assertEqual(result.returncode, 1)
            self.assertEqual(self.stamp.stat().st_mtime, old)

    def test_concurrent_export_waits_then_rechecks_success_stamp(self):
        self.stamp.unlink()
        fd = os.open(self.snapshot, os.O_RDONLY)
        self.addCleanup(os.close, fd)
        fcntl.flock(fd, fcntl.LOCK_EX)
        child = subprocess.Popen(['/bin/bash', str(self.script), '--daily'], env=self.env,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            with self.assertRaises(subprocess.TimeoutExpired):
                child.wait(timeout=0.2)
            self.stamp.touch()
            fcntl.flock(fd, fcntl.LOCK_UN)
            out, err = child.communicate(timeout=5)
            self.assertEqual(child.returncode, 0, err)
            self.assertIn('already succeeded today', out)
        finally:
            if child.poll() is None:
                child.kill()
                child.communicate()

    def test_unknown_arguments_refused(self):
        self.assertEqual(self.run_export('--force').returncode, 2)


if __name__ == '__main__':
    unittest.main()
