"""Real advisory-lock regressions against an isolated pre-created registry."""
import grp
import os
from pathlib import Path
import pwd
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import backup_locks


BACKUP_DIR = Path(__file__).resolve().parents[2] / 'scripts' / 'backup'


class BackupLockTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.registry = self.root / 'registry'
        self.registry.mkdir(mode=0o770)
        self.registry.chmod(0o770)
        self.legacy = self.root / 'legacy.lock'
        self.legacy.touch(mode=0o660)
        self.legacy.chmod(0o660)
        for lock_class, names in (
                (backup_locks.LockClass.JOB, backup_locks.JOB_KINDS),
                (backup_locks.LockClass.DISK, backup_locks.DISK_LABELS),
                (backup_locks.LockClass.DATA, backup_locks.DATA_NAMES),
                (backup_locks.LockClass.CLOUD, (backup_locks.CLOUD_SLOT,))):
            for name in names:
                path = self.registry / f'{lock_class.value}-{name}.lock'
                path.touch(mode=0o660)
                path.chmod(0o660)
        owner = pwd.getpwuid(os.getuid()).pw_name
        group = grp.getgrgid(os.getgid()).gr_name
        self.environment = {
            'VANPI_BACKUP_LOCK_DIR': str(self.registry),
            'VANPI_BACKUP_JOB_LOCK': str(self.legacy),
            'VANPI_BACKUP_JOB_LOCK_OWNER': owner,
            'VANPI_BACKUP_JOB_LOCK_GROUP': group,
            'VANPI_BACKUP_JOB_LOCK_MODE': '660',
            'VANPI_BACKUP_LOCK_DIR_MODE': '770',
        }
        patch = mock.patch.dict(os.environ, self.environment)
        patch.start()
        self.addCleanup(patch.stop)

    def child_env(self):
        environment = dict(os.environ)
        existing = environment.get('PYTHONPATH')
        environment['PYTHONPATH'] = str(BACKUP_DIR) + (os.pathsep + existing if existing else '')
        return environment

    def holder(self, lock_class, name):
        code = (
            'import sys,backup_locks; '
            f'cm=backup_locks.held(backup_locks.LockClass.{lock_class.name},{name!r}); '
            'cm.__enter__(); print("ready",flush=True); sys.stdin.read(); cm.__exit__(None,None,None)')
        process = subprocess.Popen([sys.executable, '-c', code], env=self.child_env(),
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True)
        ready = process.stdout.readline().strip()
        if ready != 'ready':
            error = process.stderr.read()
            self.fail(f'lock holder did not start: {ready!r} {error}')
        self.addCleanup(self.stop_holder, process)
        return process

    @staticmethod
    def stop_holder(process):
        if process.poll() is None:
            if process.stdin and not process.stdin.closed:
                process.stdin.close()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream and not stream.closed:
                stream.close()

    def contender(self, lock_class, name):
        code = (
            'import sys,backup_locks\n'
            'klass=getattr(backup_locks.LockClass,sys.argv[1])\n'
            'try:\n'
            '  with backup_locks.held(klass,sys.argv[2]): pass\n'
            'except backup_locks.LockBusy: sys.exit(3)\n')
        return subprocess.run([sys.executable, '-c', code, lock_class.name, name],
                              env=self.child_env(), capture_output=True, text=True, timeout=5)

    def test_cloud_slot_excludes_a_second_process(self):
        holder = self.holder(backup_locks.LockClass.CLOUD, backup_locks.CLOUD_SLOT)
        self.assertEqual(self.contender(backup_locks.LockClass.CLOUD,
                                       backup_locks.CLOUD_SLOT).returncode, 3)
        self.stop_holder(holder)
        available = self.contender(backup_locks.LockClass.CLOUD, backup_locks.CLOUD_SLOT)
        self.assertEqual(available.returncode, 0, available.stderr)

    def test_exclusive_data_lock_defers_then_succeeds_after_release(self):
        holder = self.holder(backup_locks.LockClass.DATA, 'borg-repo')
        path = backup_locks.lock_path(backup_locks.LockClass.DATA, 'borg-repo')
        with self.assertRaises(backup_locks.LockBusy):
            with backup_locks.held(backup_locks.LockClass.DATA, 'borg-repo'):
                self.fail('busy data lock was acquired')
        self.assertTrue(path.is_file())
        self.stop_holder(holder)
        with backup_locks.held(backup_locks.LockClass.DATA, 'borg-repo'):
            pass

    def test_require_held_rejects_wrong_descriptor(self):
        wrong = self.root / 'wrong.lock'
        wrong.touch()
        fd = os.open(wrong, os.O_RDWR)
        self.addCleanup(os.close, fd)
        with self.assertRaisesRegex(RuntimeError, 'inherited lock descriptor'):
            backup_locks.require_held(
                fd, backup_locks.lock_path(backup_locks.LockClass.JOB, 'time-machine'))

    def test_inherited_lock_fds_returns_only_open_lock_descriptors(self):
        disk_path = backup_locks.lock_path(backup_locks.LockClass.DISK, 'bigboi')
        legacy_fd = backup_locks.open_lock(self.legacy, legacy=True)
        disk_fd = backup_locks.open_lock(disk_path)
        unrelated = os.open(self.root / 'unrelated', os.O_RDWR | os.O_CREAT, 0o600)
        self.addCleanup(os.close, unrelated)
        self.addCleanup(os.close, disk_fd)
        self.addCleanup(os.close, legacy_fd)
        self.assertEqual(backup_locks.inherited_lock_fds(), sorted((legacy_fd, disk_fd)))

    @unittest.skipUnless(sys.platform.startswith('linux') and Path('/proc/self/fd').is_dir(),
                         'lock inheritance requires Linux /proc')
    def test_pass_fds_keeps_every_resource_lock_open_in_child(self):
        locks = ((backup_locks.LockClass.DISK, 'bigboi'),
                 (backup_locks.LockClass.CLOUD, backup_locks.CLOUD_SLOT),
                 (backup_locks.LockClass.DATA, 'borg-repo'))
        with (backup_locks.held(*locks[0]) as disk_fd,
              backup_locks.held(*locks[1]) as cloud_fd,
              backup_locks.held(*locks[2]) as data_fd):
            inherited = backup_locks.inherited_lock_fds()
            self.assertTrue({disk_fd, cloud_fd, data_fd} <= set(inherited))
            child = subprocess.Popen(['/bin/sleep', '5'], pass_fds=tuple(inherited))
            self.addCleanup(self.stop_holder, child)
            for lock_class, name in locks:
                self.assertTrue(backup_locks.process_holds(
                    child.pid, backup_locks.lock_path(lock_class, name)))

    def test_open_lock_rejects_symlinks_wrong_modes_and_missing_files(self):
        source = backup_locks.lock_path(backup_locks.LockClass.DATA, 'borg-repo')
        symlink = self.registry / 'data-link.lock'
        symlink.symlink_to(source)
        with self.assertRaises(RuntimeError):
            backup_locks.open_lock(symlink)
        source.chmod(0o600)
        with self.assertRaises(RuntimeError):
            backup_locks.open_lock(source)
        source.chmod(0o660)
        missing = self.registry / 'data-missing.lock'
        with self.assertRaises(OSError):
            backup_locks.open_lock(missing)
        self.assertFalse(missing.exists())

    @unittest.skipUnless(sys.platform.startswith('linux') and Path('/proc/self/fd').is_dir(),
                         'live descriptor enumeration requires Linux /proc')
    def test_linux_enumerates_real_shell_job_and_legacy_holders(self):
        priority = self.root / 'priority.py'
        priority.write_text('import sys\nsys.exit(0)\n')
        environment = self.child_env()
        environment['VANPI_BACKUP_PRIORITY_TOOL'] = str(priority)
        environment['VANPI_BACKUP_JOB_LOCK_STAT'] = '/usr/bin/stat'
        environment['VANPI_BACKUP_JOB_LOCK_FLOCK'] = '/usr/bin/flock'
        config = BACKUP_DIR / 'backup_conf.sh'

        job = self.shell_holder(config, 'acquire_backup_job borg bigboi', environment)
        self.assertEqual(backup_locks.active_jobs().get('borg'), job.pid)
        self.stop_holder(job)
        self.assertNotIn('borg', backup_locks.active_jobs())

        legacy = self.shell_holder(config, 'acquire_job_lock disk', environment)
        self.assertEqual(backup_locks.legacy_exclusive_holder(), legacy.pid)
        self.stop_holder(legacy)
        self.assertIsNone(backup_locks.legacy_exclusive_holder())

    def shell_holder(self, config, acquisition, environment):
        script = f'source "$1"; {acquisition}; printf "ready\\n"; cat >/dev/null'
        process = subprocess.Popen(['/bin/bash', '-c', script, 'holder', str(config)],
                                   env=environment, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True)
        ready = process.stdout.readline().strip()
        if ready != 'ready':
            error = process.stderr.read()
            self.fail(f'lock holder did not start: {ready!r} {error}')
        self.addCleanup(self.stop_holder, process)
        return process


if __name__ == '__main__':
    unittest.main()
