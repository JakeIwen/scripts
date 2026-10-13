"""Real shell drift checks against isolated deployment trees."""
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


HELPER = Path(__file__).resolve().parents[2] / 'backup_local_deploy.sh'


class LocalBackupDeployTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.live, self.source = self.root / 'live', self.root / 'source'
        self.live.mkdir(); self.source.mkdir()
        self.env = dict(os.environ)
        for key in ('BASH_ENV', 'ENV', 'SHELLOPTS', 'BASHOPTS'):
            self.env.pop(key, None)
        if not shutil.which('sha256sum'):
            shim = self.root / 'sha256sum'
            shim.write_text('#!/bin/sh\nexec /usr/bin/shasum -a 256 "$@"\n')
            shim.chmod(0o700)
            self.env['PATH'] = str(self.root) + os.pathsep + self.env['PATH']
        names = self.shell('printf "%s\\n" "${LOCAL_BACKUP_CHECKED[@]}"')
        self.assertEqual(names.returncode, 0, names.stderr)
        self.names = names.stdout.splitlines()
        self.previous = {}
        for name in self.names:
            (self.live / name).write_text('old\n')
            (self.source / name).write_text('new\n')
            self.previous[name] = hashlib.sha256(b'old\n').hexdigest()

    def shell(self, operation, *args):
        return subprocess.run(['/bin/bash', '-c', 'set -euo pipefail; source "$1"; shift; ' + operation,
                               'test', str(HELPER), *args], env=self.env,
                              capture_output=True, text=True, timeout=10)

    def verify(self):
        previous = ','.join(f'{name}:{digest}' for name, digest in self.previous.items())
        return self.shell('verify_local_backup_payload "$1" "$2" "$3"',
                          str(self.source), previous, str(self.live))

    def test_every_shipped_fairness_file_is_checked(self):
        shipped = self.shell('printf "%s\\n" "${LOCAL_FAIRNESS_FILES[@]}"').stdout.splitlines()
        self.assertTrue(set(shipped).issubset(self.names))
        self.assertIn('protected_json.py', self.names)
        self.assertEqual(self.verify().returncode, 0)
        for name in self.names:
            with self.subTest(name=name):
                (self.live / name).write_text('local edit\n')
                self.assertNotEqual(self.verify().returncode, 0)
                (self.live / name).write_text('old\n')

    def test_first_install_idempotence_and_new_file_drift(self):
        name = 'local_fairness.py'
        self.previous[name] = 'absent'
        (self.live / name).unlink()
        self.assertEqual(self.verify().returncode, 0)
        (self.live / name).write_bytes((self.source / name).read_bytes())
        self.assertEqual(self.verify().returncode, 0)
        (self.live / name).write_text('local edit\n')
        self.assertNotEqual(self.verify().returncode, 0)

    def test_required_missing_or_symlinked_file_refused(self):
        name = 'local_fairness.py'
        (self.live / name).unlink()
        self.assertNotEqual(self.verify().returncode, 0)
        self.previous[name] = 'absent'
        (self.live / name).symlink_to(self.root / 'missing')
        self.assertNotEqual(self.verify().returncode, 0)

    def test_previous_revision_records_missing_paths_and_rejects_bad_revision(self):
        repo = self.root / 'repo'
        config = repo / 'pi/scripts/backup/backup_conf.sh'
        config.parent.mkdir(parents=True)
        config.write_text('fixture configuration\n')
        subprocess.run(['git', 'init', '-q', str(repo)], check=True)
        subprocess.run(['git', '-C', str(repo), 'add', '.'], check=True)
        subprocess.run(['git', '-C', str(repo), '-c', 'core.hooksPath=/dev/null',
                        '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid',
                        'commit', '-qm', 'fixture'], check=True)
        self.env['VANPI_BACKUP_PREVIOUS_REVISION'] = 'HEAD'
        self.env.pop('VANPI_BACKUP_PREVIOUS_CONF_SHA256', None)
        result = self.shell('local_backup_previous_hashes "$1"', str(repo))
        self.assertEqual(result.returncode, 0, result.stderr)
        entries = dict(item.split(':') for item in result.stdout.rstrip(',').split(','))
        self.assertEqual(entries['local_fairness.py'], 'absent')
        self.assertRegex(entries['backup_conf.sh'], '^[0-9a-f]{64}$')
        self.env['VANPI_BACKUP_PREVIOUS_REVISION'] = 'nonexistent-deployment-revision'
        self.assertNotEqual(self.shell('local_backup_previous_hashes "$1"',
                           str(repo)).returncode, 0)


if __name__ == '__main__':
    unittest.main()
