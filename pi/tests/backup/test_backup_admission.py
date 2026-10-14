"""Real descriptors, public worker entries and client inheritance at admission."""
import fcntl
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest import mock

import backup_admission as admission
import backup_locks as locks
import icloud_backup as cloud
from .backup_lock_fixtures import temporary_registry

BACKUP_DIR = Path(__file__).resolve().parents[2] / 'scripts/backup'
REQUIRED_METADATA = ('VANPI_BACKUP_JOB_KIND', 'VANPI_BACKUP_LOCK_DIR',
                     'VANPI_BACKUP_LEGACY_LOCK_FD', 'VANPI_BACKUP_JOB_LOCK_FD',
                     'VANPI_BACKUP_CLOUD_SLOT_FD', 'VANPI_BACKUP_DISK_LOCK_FDS')
CLIENT_CONFIG = {'guard_interval_seconds': 0.05, 'no_progress_timeout_seconds': 5}


@unittest.skipUnless(sys.platform.startswith('linux'), 'admission proof requires Linux fdinfo')
class AdmissionTests(unittest.TestCase):
    def setUp(self):
        self.registry = self.enterContext(temporary_registry())

    def test_matching_unheld_descriptor_never_acquires_or_borrows_a_peer_hold(self):
        path = locks.lock_path(locks.LockClass.JOB, locks.TIME_MACHINE)
        fd = locks.open_lock(path)
        self.addCleanup(os.close, fd)
        with self.assertRaises(locks.LockBusy):
            locks.require_held(fd, path)
        with locks.held(locks.LockClass.JOB, locks.TIME_MACHINE) as owner:
            locks.require_held(owner, path)
            with self.assertRaises(locks.LockBusy):
                locks.require_held(fd, path)
        locks.try_flock(fd, False)
        with self.assertRaises(locks.LockBusy):
            locks.require_held(fd, path)
        # Failure must not upgrade the shared hold to exclusive.
        with locks.held(locks.LockClass.JOB, locks.TIME_MACHINE, exclusive=False):
            locks.require_held(fd, path, exclusive=False)

    def test_every_metadata_field_is_mandatory_and_modes_are_verified(self):
        with self.registry.admitted(locks.PI_CLOUD) as bundle:
            for key in REQUIRED_METADATA:
                environment = dict(os.environ); environment.pop(key)
                with self.subTest(key=key), mock.patch.dict(os.environ, environment, clear=True):
                    with self.assertRaisesRegex(RuntimeError, 'admission is missing'):
                        admission.from_environment(locks.PI_CLOUD)
            for held, wrong_mode in ((bundle.legacy, fcntl.LOCK_EX), (bundle.job, fcntl.LOCK_SH),
                                     (bundle.cloud, fcntl.LOCK_SH), (bundle.disks[0][1], fcntl.LOCK_EX)):
                with self.subTest(path=held.path.name):
                    fcntl.flock(held.fd, wrong_mode)
                    with self.assertRaises(locks.LockBusy):
                        admission.from_environment(locks.PI_CLOUD)
                    fcntl.flock(held.fd, fcntl.LOCK_SH if wrong_mode == fcntl.LOCK_EX else fcntl.LOCK_EX)

    def test_bad_job_disk_and_descriptor_metadata_fail_closed(self):
        with self.registry.admitted(locks.PI_CLOUD) as bundle:
            for key, value in (
                    ('VANPI_BACKUP_JOB_KIND', locks.TIME_MACHINE),
                    ('VANPI_BACKUP_JOB_LOCK_FD', 'not-a-number'),
                    ('VANPI_BACKUP_CLOUD_SLOT_FD', str(bundle.job.fd)),
                    ('VANPI_BACKUP_DISK_LOCK_FDS', f'mbp2tbkup={bundle.disks[0][1].fd}'),
                    ('VANPI_BACKUP_DISK_LOCK_FDS', 'bigboi=1'),
                    ('VANPI_BACKUP_DISK_LOCK_FDS', 'bigboi=999999'),
                    ('VANPI_BACKUP_DISK_LOCK_FDS', 'bigboi=3,bigboi=4')):
                with self.subTest(key=key, value=value), mock.patch.dict(os.environ, {key: value}):
                    with self.assertRaises((OSError, RuntimeError)):
                        admission.from_environment(locks.PI_CLOUD)
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, 'cloud slot descriptor'):
                locks.require_cloud_slot()

    def test_latched_bundle_and_checked_repository_hold_reach_real_client(self):
        with self.registry.admitted(locks.PI_CLOUD) as bundle, admission.use(bundle):
            with locks.held(locks.LockClass.DATA, 'borg-repo') as repository:
                descriptors = admission.client_fds(repository_fd=repository)
                paths = [bundle.legacy.path, bundle.job.path, bundle.cloud.path, bundle.disks[0][1].path,
                         bundle.registry / 'data-borg-repo.lock']
                expected = sorted((p.stat().st_dev, p.stat().st_ino) for p in paths)
                code = ('import os,sys; '
                        'print(sorted((os.fstat(int(fd)).st_dev,os.fstat(int(fd)).st_ino) for fd in sys.argv[1:]))')
                result = cloud.run([sys.executable, '-c', code, *map(str, descriptors)], CLIENT_CONFIG,
                                   parked=False, repository_fd=repository)
                self.assertEqual(result.strip(), repr(expected))
            with self.assertRaises(RuntimeError):
                admission.client_fds(repository_fd=bundle.job.fd)
            repository = locks.open_lock(bundle.registry / 'data-borg-repo.lock')
            try:
                with self.assertRaises(locks.LockBusy):
                    admission.client_fds(repository_fd=repository)
            finally:
                os.close(repository)
            # Clients use the captured identity even if admission environment is gone.
            environment = {k: v for k, v in os.environ.items() if not k.startswith('VANPI_BACKUP_')}
            with mock.patch.dict(os.environ, environment, clear=True):
                self.assertEqual(admission.client_fds(), bundle.client_fds())
                self.assertEqual(cloud.run([sys.executable, '-c', 'print("ok")'],
                                          CLIENT_CONFIG, parked=False), 'ok\n')

    def test_context_resets_and_account_commands_are_explicitly_outside_run(self):
        with self.registry.admitted(locks.PI_CLOUD) as bundle:
            with self.assertRaisesRegex(ValueError, 'interrupted'):
                with admission.use(bundle):
                    raise ValueError('interrupted')
            with self.assertRaisesRegex(RuntimeError, 'admission context'):
                cloud.run([sys.executable, '-c', 'print("unexpected")'], CLIENT_CONFIG, parked=False)
            self.assertEqual(cloud.run([sys.executable, '-c', 'print("account")'],
                                      CLIENT_CONFIG, parked=False, account=True), 'account\n')

    def test_disk_release_keeps_child_owned_inode_until_child_exits(self):
        with self.registry.admitted(locks.PI_CLOUD) as bundle, admission.use(bundle):
            child = subprocess.Popen(['/bin/cat'], stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                     pass_fds=admission.client_fds())
            try:
                admission.release_disk('bigboi')
                with self.assertRaisesRegex(RuntimeError, 'released its disk'):
                    admission.client_fds()
                with self.assertRaises(locks.LockBusy):
                    with locks.held(locks.LockClass.DISK, 'bigboi'):
                        self.fail('child lost inherited disk ownership')
            finally:
                child.stdin.close(); child.wait(timeout=5)
            with locks.held(locks.LockClass.DISK, 'bigboi'):
                pass

    def test_real_shell_exports_both_cloud_admissions(self):
        priority = self.registry.root / 'priority.py'
        priority.write_text('raise SystemExit(0)\n')
        environment = dict(os.environ, VANPI_BACKUP_PRIORITY_TOOL=str(priority), PYTHONPATH=str(BACKUP_DIR))
        code = ('import backup_admission as a,sys; b=a.from_environment(sys.argv[1]); '
                'print(b.kind,len(b.client_fds()))')
        script = ('source "$1"; acquire_backup_job "$2" "$3" || exit 2; '
                  'acquire_backup_lock cloud upload || exit 3; exec "$4" -c "$5" "$2"')
        for kind, labels in admission.CLOUD_DISKS.items():
            with self.subTest(kind=kind):
                result = subprocess.run(['/bin/bash', '-uc', script, 'test', str(BACKUP_DIR/'backup_conf.sh'),
                                         kind, labels[0], sys.executable, code], env=environment,
                                        capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(), f'{kind} 4')


class WorkerEntryTests(unittest.TestCase):
    def test_public_run_entries_refuse_missing_admission_without_any_persistent_or_client_action(self):
        with temporary_registry() as registry:
            environment = {k: v for k, v in os.environ.items() if not k.startswith('VANPI_BACKUP_')}
            environment['PYTHONPATH'] = str(BACKUP_DIR)
            for module, state_name in (('icloud_backup', 'STATE_DIR'), ('time_machine_icloud', 'STATE')):
                state = registry.root / module
                marker = registry.root / 'client-started'
                code = '''import importlib,sys
from pathlib import Path
worker=importlib.import_module(sys.argv[1])
setattr(worker,sys.argv[2],Path(sys.argv[3]))
marker=Path(sys.argv[4])
def forbidden(*args,**kwargs):
 marker.touch()
 raise RuntimeError('unexpected client launch')
worker.subprocess.Popen=forbidden
worker.subprocess.run=forbidden
sys.argv=['worker','--run']
worker.main()
'''
                result = subprocess.run([sys.executable, '-c', code, module, state_name, str(state), str(marker)],
                                        env=environment, capture_output=True, text=True, timeout=10)
                with self.subTest(worker=module):
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn('admission is missing', result.stderr)
                    self.assertFalse(state.exists(), 'admission refusal created worker state')
                    self.assertFalse(marker.exists(), 'admission refusal launched a client')


if __name__ == '__main__':
    unittest.main()
