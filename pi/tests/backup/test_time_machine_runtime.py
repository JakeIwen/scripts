"""Capture gates must work on a clean boot and survive another service start."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


PI = Path(__file__).resolve().parents[2]
CONF = PI / 'tmpfiles.d/vanpi-samba-drain.conf'
UNIT = PI / 'services/vanpi-time-machine-icloud.service'


class RuntimeContractTests(unittest.TestCase):
    def test_service_prepares_its_shipped_capture_directory_before_the_worker(self):
        preparation = ('ExecStartPre=/usr/bin/systemd-tmpfiles --create '
                       '/etc/tmpfiles.d/vanpi-samba-drain.conf')
        self.assertIn(preparation, UNIT.read_text().splitlines())
        self.assertTrue(CONF.is_file())
        plan = subprocess.run(['bash', '-c', 'source "$1"; local_backup_sources "$2"',
                               'test', str(PI / 'scripts/backup/backup_deploy_plan.sh'), 'tm'],
                              check=True, capture_output=True, text=True)
        self.assertIn('pi/tmpfiles.d/vanpi-samba-drain.conf', plan.stdout.splitlines())


@unittest.skipUnless(sys.platform.startswith('linux') and os.geteuid() == 0,
                     'Requires Linux and root to verify native systemd runtime-directory ownership.')
class RuntimeProvisioningTests(unittest.TestCase):
    def test_clean_boot_creation_and_repeated_setup_preserve_owned_capture_gate(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            command = ['/usr/bin/systemd-tmpfiles', '--root=' + str(root), '--create', str(CONF)]
            directory = root / 'run/lock/vanpi-samba-drain'
            self.assertFalse(directory.exists())
            subprocess.run(command, check=True, capture_output=True, text=True)
            metadata = directory.lstat()
            self.assertTrue(directory.is_dir())
            self.assertFalse(directory.is_symlink())
            self.assertEqual((metadata.st_uid, metadata.st_gid, metadata.st_mode & 0o777), (0,0,0o755))
            marker = directory / 'mbp2tbkup'
            marker.write_text('owned-capture-gate\n')
            identity = marker.stat().st_ino
            subprocess.run(command, check=True, capture_output=True, text=True)
            self.assertEqual(marker.read_text(), 'owned-capture-gate\n')
            self.assertEqual(marker.stat().st_ino, identity)


if __name__ == '__main__':
    unittest.main()
