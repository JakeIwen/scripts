"""Real shell drift checks against isolated deployment trees."""
import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest


HELPER = Path(__file__).resolve().parents[2] / 'backup_local_deploy.sh'
INSTALLERS = (HELPER.parent / 'deploy_icloud_backup.sh', HELPER.parent / 'deploy_time_machine_icloud.sh')


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
        if subprocess.run(['/usr/bin/stat', '--version'], capture_output=True).returncode != 0:
            # BSD stat prints the same "owner group mode" triple the helper reads from GNU stat.
            shim = self.root / 'gnu-stat'
            shim.write_text('#!/bin/sh\nexec /usr/bin/stat -f "%Su %Sg %Lp" "$3"\n')
            shim.chmod(0o700)
            self.env['LOCAL_BACKUP_STAT'] = str(shim)
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

    def array(self, name):
        return self.shell('printf "%s\\n" "${' + name + '[@]}"').stdout.split()

    def lifecycle_env(self, live):
        owner, group, _mode = self.shell('"$LOCAL_BACKUP_STAT" -c "%U %G %a" "$1"', str(live)).stdout.split()
        return dict(self.env, LOCAL_BACKUP_SUDO='', LOCAL_BACKUP_LIFECYCLE_OWNER=f'{owner}:{group}')

    def test_every_shipped_local_backup_file_is_checked(self):
        shipped = self.array('LOCAL_BACKUP_FILES')
        self.assertTrue(set(shipped).issubset(self.names))
        self.assertIn('protected_json.py', self.names)
        self.assertEqual(self.verify().returncode, 0)
        for name in self.names:
            with self.subTest(name=name):
                (self.live / name).write_text('local edit\n')
                self.assertNotEqual(self.verify().returncode, 0)
                (self.live / name).write_text('old\n')

    def test_installers_ship_only_the_shared_payload_and_verify_lifecycle_before_publishing(self):
        payload, overridden = set(self.array('LOCAL_BACKUP_PAYLOAD')), set(self.array('LOCAL_BACKUP_OVERRIDDEN'))
        self.assertTrue(set(self.names) <= payload)
        self.assertEqual(payload - set(self.names),
                         overridden | {'ICLOUD_RESTORE.txt', 'TIME_MACHINE_ICLOUD_RESTORE.txt'})
        for installer in INSTALLERS:
            with self.subTest(installer=installer.name):
                text = installer.read_text()
                referenced = set(re.findall(r'scripts/backup/([A-Za-z0-9_.]+)', text))
                self.assertTrue(referenced <= payload, referenced - payload)
                remote = text[text.index("<<'REMOTE'"):]
                self.assertLess(remote.index('verify_lifecycle_scripts '), remote.index('/usr/bin/install -o'))
                self.assertIn('install_lifecycle_scripts ', remote)

    def test_first_install_idempotence_and_new_file_drift(self):
        name = 'backup_locks.py'
        self.previous[name] = 'absent'
        (self.live / name).unlink()
        self.assertEqual(self.verify().returncode, 0)
        (self.live / name).write_bytes((self.source / name).read_bytes())
        self.assertEqual(self.verify().returncode, 0)
        (self.live / name).write_text('local edit\n')
        self.assertNotEqual(self.verify().returncode, 0)

    def test_required_missing_or_symlinked_file_refused(self):
        name = 'backup_locks.py'
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
        self.assertEqual(entries['backup_locks.py'], 'absent')
        self.assertRegex(entries['backup_conf.sh'], '^[0-9a-f]{64}$')
        self.env['VANPI_BACKUP_PREVIOUS_REVISION'] = 'nonexistent-deployment-revision'
        self.assertNotEqual(self.shell('local_backup_previous_hashes "$1"',
                           str(repo)).returncode, 0)
    def test_previous_tmpfiles_hash_uses_requested_fixture_revision(self):
        repo = self.root / 'repo'
        tmpfiles = repo / 'pi/tmpfiles.d/vanpi-backup.conf'
        tmpfiles.parent.mkdir(parents=True)
        tmpfiles.write_text('first registry\n')
        subprocess.run(['git', 'init', '-q', str(repo)], check=True)
        subprocess.run(['git', '-C', str(repo), 'add', '.'], check=True)
        commit = ['git', '-C', str(repo), '-c', 'core.hooksPath=/dev/null',
                  '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '-qm']
        subprocess.run([*commit, 'first'], check=True)
        first = subprocess.run(['git', '-C', str(repo), 'rev-parse', 'HEAD'], check=True,
                               capture_output=True, text=True).stdout.strip()
        tmpfiles.write_text('second registry\n')
        subprocess.run(['git', '-C', str(repo), 'add', '.'], check=True)
        subprocess.run([*commit, 'second'], check=True)
        self.env['VANPI_BACKUP_PREVIOUS_REVISION'] = first
        result = self.shell('local_backup_previous_tmpfiles_hash "$1"', str(repo))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), hashlib.sha256(b'first registry\n').hexdigest())
        self.env['VANPI_BACKUP_PREVIOUS_REVISION'] = 'missing-revision'
        self.assertNotEqual(self.shell('local_backup_previous_tmpfiles_hash "$1"',
                                      str(repo)).returncode, 0)

    def test_tmpfiles_drift_accepts_previous_or_staged_content_only(self):
        source = self.root / 'staged-tmpfiles'
        live = self.root / 'live-tmpfiles'
        source.write_text('new registry\n')
        live.write_text('old registry\n')
        previous = hashlib.sha256(b'old registry\n').hexdigest()
        operation = 'verify_local_backup_tmpfiles "$1" "$2" "$3"'
        self.assertEqual(self.shell(operation, str(source), previous, str(live)).returncode, 0)
        live.write_bytes(source.read_bytes())
        self.assertEqual(self.shell(operation, str(source), previous, str(live)).returncode, 0)
        live.write_text('local edit\n')
        refused = self.shell(operation, str(source), previous, str(live))
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn('Live tmpfiles registry differs; inspect before deploying.', refused.stderr)

    @unittest.skipUnless(sys.platform.startswith('linux'), 'lifecycle install uses GNU coreutils')
    def test_lifecycle_script_install_checks_drift_and_preserves_mode(self):
        live, staged, previous = self.root / 'umount_disks.sh', self.root / 'staged.sh', self.root / 'previous'
        previous.mkdir()
        live.write_text('old\n'); live.chmod(0o770); staged.write_text('new\n')
        env = self.lifecycle_env(live)
        digest = hashlib.sha256(b'old\n').hexdigest()
        run = lambda: subprocess.run(['/bin/bash', '-c', 'set -euo pipefail; source "$1"; install_lifecycle_script "$2" "$3" "$4" "$5"',
                                      'test', str(HELPER), str(staged), str(live), digest, str(previous)],
                                     env=env, capture_output=True, text=True, timeout=10)
        self.assertEqual(run().returncode, 0)
        self.assertEqual(live.read_text(), 'new\n')
        self.assertEqual(live.stat().st_mode & 0o777, 0o770)
        self.assertEqual((previous / 'umount_disks.sh').read_text(), 'old\n')
        self.assertEqual(run().returncode, 0, 'a staged-identical live file is idempotent')
        live.write_text('local edit\n')
        self.assertNotEqual(run().returncode, 0, 'drifted live lifecycle script must be refused')
        self.assertEqual(live.read_text(), 'local edit\n')

    def lifecycle_trees(self):
        names = self.array('LIFECYCLE_SCRIPTS')
        self.assertEqual(names, ['umount_disks.sh', 'internet_switches.sh'])
        staged, live, saved = self.root / 'staged', self.root / 'scripts', self.root / 'previous'
        staged.mkdir(); live.mkdir(); saved.mkdir()
        for name in names:
            (staged / name).write_text(f'new {name}\n')
            (live / name).write_text(f'old {name}\n'); (live / name).chmod(0o770)
        digest = lambda name: hashlib.sha256(f'old {name}\n'.encode()).hexdigest()
        previous = ','.join(f'{name}:{digest(name)}' for name in names)
        return names, staged, live, saved, previous, self.lifecycle_env(live / names[0])

    def test_lifecycle_preflight_rejects_drift_or_ownership_without_touching_either_tree(self):
        names, staged, live, saved, previous, env = self.lifecycle_trees()
        verify = lambda env=env: subprocess.run(
            ['/bin/bash', '-c', 'set -euo pipefail; source "$1"; verify_lifecycle_scripts "$2" "$3" "$4"',
             'test', str(HELPER), str(staged), previous, str(live)], env=env, capture_output=True, text=True, timeout=10)
        accepted = verify()
        self.assertEqual(accepted.returncode, 0, accepted.stderr)
        (live / names[1]).write_text('local edit\n')
        refused = verify()
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn('Live code differs', refused.stderr)
        self.assertEqual((live / names[0]).read_text(), f'old {names[0]}\n')
        self.assertEqual((live / names[1]).read_text(), 'local edit\n')
        self.assertEqual(sorted(path.name for path in staged.iterdir()), sorted(names))
        self.assertEqual(list(saved.iterdir()), [])
        (live / names[1]).write_text(f'old {names[1]}\n')
        refused = verify(dict(env, LOCAL_BACKUP_LIFECYCLE_OWNER='nobody:nogroup'))
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn('ownership differs', refused.stderr)

    @unittest.skipUnless(sys.platform.startswith('linux'), 'lifecycle install uses GNU coreutils')
    def test_lifecycle_scripts_install_from_one_hash_list(self):
        names, staged, live, saved, previous, env = self.lifecycle_trees()
        installed = subprocess.run(
            ['/bin/bash', '-c', 'set -euo pipefail; source "$1"; install_lifecycle_scripts "$2" "$3" "$4" "$5"',
             'test', str(HELPER), str(staged), previous, str(saved), str(live)], env=env, capture_output=True, text=True, timeout=10)
        self.assertEqual(installed.returncode, 0, installed.stderr)
        for name in names:
            self.assertEqual((live / name).read_text(), f'new {name}\n')
            self.assertEqual((live / name).stat().st_mode & 0o777, 0o770)
            self.assertEqual((saved / name).read_text(), f'old {name}\n')

if __name__ == '__main__':
    unittest.main()
