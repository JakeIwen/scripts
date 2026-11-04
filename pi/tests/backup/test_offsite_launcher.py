"""offsite_backup.sh lock order, refusals and frozen-code exec through real flock.

The launcher runs from a fixture copy of the scripts tree whose snapshot helper
and Python entry points are stubs that record the descriptors they inherited.
Only data paths (secrets, ignition flag) are rebased in the copied
backup_conf.sh. Mutating commands require EUID 0, so they run in an
unprivileged user namespace (`unshare -r`): files keep their real inodes and
locks, but appear root-owned there.
"""
import fcntl
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest


SCRIPTS = Path(__file__).resolve().parents[2] / 'scripts'
COPIED = ('backup/offsite_backup.sh', 'backup/backup_conf.sh', 'backup/backup_locks.sh',
          'backup/backup_lock_io.sh', 'backup/backup_lock_registry.sh', 'backup/backup_locks.py')
SECRETS = '/home/pi/secrets/.bash_variables'
IGNITION = 'IGNITION_FLAG=/home/pi/hooks/ignition_is_on'
UNSHARE = '/usr/bin/unshare'
LEGACY_BUSY = 'A restore, hotspare initialization, prepare-disk or backup deployment is active.'
RUN_BUSY = 'Another offsite run is active.'
RECORDED_ENVIRONMENT = ('OFFSITE_CODE_ROOT', 'VANPI_BACKUP_JOB_KIND', 'VANPI_BACKUP_JOB_LOCK_FD',
                        'VANPI_BACKUP_JOB_LOCK_PATH', 'VANPI_BACKUP_LOCK_DIR', 'OFFSITE_IGNITION_FLAG',
                        'BORG_REPO', 'VANPI_BACKUP_DISK_LOCK_FDS', 'VANPI_BACKUP_LEGACY_LOCK_FD',
                        'VANPI_BACKUP_CLOUD_SLOT_FD', 'OFFSITE_ADMISSION')

HOLD_CHECK = '''
import os
import backup_locks as locks


def holds(fd, path, exclusive):
    try:
        locks.require_held(fd, path, exclusive=exclusive)
    except (OSError, RuntimeError):
        return False
    return True


def job_path():
    return locks.lock_path(locks.LockClass.JOB, 'offsite')
'''

SNAPSHOT_STUB = HOLD_CHECK + '''
import fcntl
import json
import shutil
import sys
from pathlib import Path

here = Path(__file__).resolve().parent
probe = os.open(locks.legacy_lock(), os.O_RDONLY)
try:
    fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
    fence_blocked = False
except BlockingIOError:
    fence_blocked = True
finally:
    os.close(probe)
record = {'argv': sys.argv[1:], 'job_fd': os.environ.get('VANPI_BACKUP_JOB_LOCK_FD'),
          'lock_dir': os.environ.get('VANPI_BACKUP_LOCK_DIR'), 'fence_blocked': fence_blocked,
          'job_exclusive': holds(10, job_path(), True), 'legacy_shared': holds(9, locks.legacy_lock(), False)}
Path(os.environ['STUB_RECORDS'], 'snapshot.json').write_text(json.dumps(record))
if os.environ.get('STUB_SNAPSHOT_FAIL'):
    sys.stderr.write('stub snapshot refused\\n')
    raise SystemExit(3)
root = Path(os.environ['OFFSITE_STATE_ROOT']) / 'code' / ('run-' + str(os.getpid()))
(root / 'backup').mkdir(parents=True)
shutil.copy2(here / 'frozen_offsite_backup.py', root / 'backup' / 'offsite_backup.py')
shutil.copy2(here / 'backup_locks.py', root / 'backup' / 'backup_locks.py')
print(root)
'''

PROGRAM_STUB = HOLD_CHECK + '''
import json
import sys
import time
from pathlib import Path


def fd_open(fd):
    try:
        os.fstat(fd)
    except OSError:
        return False
    return True


records = Path(os.environ['STUB_RECORDS'])
offsite_holders = [int(entry) for entry in os.listdir('/proc/self/fd')
                   if holds(int(entry), job_path(), True)]
record = {'role': ROLE, 'pid': os.getpid(), 'argv': sys.argv[1:], 'path0': sys.path[0],
          'file': str(Path(__file__).resolve()), 'fd9_open': fd_open(9),
          'legacy_exclusive_fd9': holds(9, locks.legacy_lock(), True),
          'job_fd10_exclusive': holds(10, job_path(), True), 'offsite_holders': offsite_holders,
          'environment': {name: os.environ.get(name) for name in RECORDED}}
(records / (ROLE + '.json')).write_text(json.dumps(record))
if os.environ.get('STUB_HOLD'):
    (records / (ROLE + '.ready')).touch()
    deadline = time.monotonic() + 30
    while not (records / 'release').exists() and time.monotonic() < deadline:
        time.sleep(0.05)
raise SystemExit(int(os.environ.get('STUB_EXIT', '0')))
'''


def userns_available():
    if not sys.platform.startswith('linux') or not Path('/proc/self/fdinfo').is_dir():
        return False
    if not (Path('/usr/bin/flock').is_file() and Path(UNSHARE).is_file()):
        return False
    return subprocess.run([UNSHARE, '-r', '/bin/true'], capture_output=True).returncode == 0


@unittest.skipUnless(userns_available(), 'needs Linux flock, fdinfo and unprivileged user namespaces')
class OffsiteLauncherTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.tree = self.root / 'scripts'
        for relative in COPIED:
            destination = self.tree / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(SCRIPTS / relative, destination)
        conf = self.tree / 'backup/backup_conf.sh'
        text = conf.read_text()
        self.ignition = self.root / 'ignition_is_on'
        for old, new in ((SECRETS, str(self.root / 'no-secrets')),
                         (IGNITION, f'IGNITION_FLAG={self.ignition}')):
            self.assertEqual(text.count(old), 1 if old == IGNITION else 2, old)
            text = text.replace(old, new)
        conf.write_text(text)
        backup = self.tree / 'backup'
        (backup / 'offsite_snapshot.py').write_text(SNAPSHOT_STUB)
        for role, name in (('live', 'offsite_backup.py'), ('frozen', 'frozen_offsite_backup.py')):
            (backup / name).write_text(f'ROLE = {role!r}\nRECORDED = {RECORDED_ENVIRONMENT!r}\n' + PROGRAM_STUB)
        (backup / 'backup_priority_control.py').write_text(
            'import os, pathlib\n'
            "pathlib.Path(os.environ['STUB_RECORDS'], 'priority').write_text(__file__)\n")
        (backup / 'backup_settings_control.py').write_text('')
        self.records = self.root / 'records'
        self.state = self.root / 'state'
        self.registry = self.root / 'registry'
        self.legacy = self.root / 'legacy.lock'
        for directory in (self.records, self.state, self.registry):
            directory.mkdir(mode=0o770)
            directory.chmod(0o770)
        self.legacy.touch(mode=0o660)
        self.legacy.chmod(0o660)
        names = subprocess.run(['/bin/bash', '-c', '. "$1"; printf "%s\\n" "${BACKUP_JOB_KINDS[@]/#/job-}" '
                                '"${BACKUP_DISK_LABELS[@]/#/disk-}" "${BACKUP_DATA_NAMES[@]/#/data-}" '
                                '"cloud-$BACKUP_CLOUD_SLOT"', 'registry', str(backup / 'backup_lock_registry.sh')],
                               capture_output=True, text=True, check=True).stdout.split()
        for name in names:
            path = self.registry / f'{name}.lock'
            path.touch(mode=0o660)
            path.chmod(0o660)
        self.job_lock = self.registry / 'job-offsite.lock'
        common = {'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8', 'STUB_RECORDS': str(self.records),
                  'OFFSITE_STATE_ROOT': str(self.state), 'VANPI_BACKUP_LOCK_DIR': str(self.registry),
                  'VANPI_BACKUP_JOB_LOCK': str(self.legacy), 'VANPI_BACKUP_JOB_LOCK_MODE': '660',
                  'VANPI_BACKUP_LOCK_DIR_MODE': '770', 'PYTHONDONTWRITEBYTECODE': '1',
                  # Stale caller metadata must never reach the run's children.
                  'VANPI_BACKUP_DISK_LOCK_FDS': 'bigboi=3', 'OFFSITE_ADMISSION': 'prepare'}
        # Inside the user namespace the pi-owned fixture files appear root-owned.
        self.inside = dict(common, VANPI_BACKUP_JOB_LOCK_OWNER='root', VANPI_BACKUP_JOB_LOCK_GROUP='root')
        user = subprocess.check_output(['/usr/bin/id', '-un'], text=True).strip()
        group = subprocess.check_output(['/usr/bin/id', '-gn'], text=True).strip()
        self.outside = dict(common, VANPI_BACKUP_JOB_LOCK_OWNER=user, VANPI_BACKUP_JOB_LOCK_GROUP=group)
        self.processes = []
        self.addCleanup(self.stop_processes)

    def stop_processes(self):
        (self.records / 'release').touch()
        for process in self.processes:
            try:
                process.communicate(timeout=20)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate()

    def launch(self, *arguments, root=True, **extra):
        command = ['/bin/bash', str(self.tree / 'backup/offsite_backup.sh'), *arguments]
        if root:
            command = [UNSHARE, '-r', *command]
        process = subprocess.Popen(command, env=dict(self.inside if root else self.outside, **extra),
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.processes.append(process)
        return process

    def run_launcher(self, *arguments, root=True, **extra):
        process = self.launch(*arguments, root=root, **extra)
        out, err = process.communicate(timeout=30)
        return subprocess.CompletedProcess(process.args, process.returncode, out, err)

    def wait_ready(self, role, process):
        deadline = time.monotonic() + 20
        while not (self.records / f'{role}.ready').exists():
            if process.poll() is not None or time.monotonic() > deadline:
                out, err = process.communicate()
                self.fail(f'{role} stub did not start: {process.returncode} {out} {err}')
            time.sleep(0.05)

    def record(self, name):
        return json.loads((self.records / f'{name}.json').read_text())

    def hold(self, path, exclusive=True):
        fd = os.open(path, os.O_RDONLY)
        self.addCleanup(self.close_quietly, fd)
        fcntl.flock(fd, (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB)
        return fd

    @staticmethod
    def close_quietly(fd):
        try:
            os.close(fd)
        except OSError:
            pass

    def free(self, path, exclusive=True):
        fd = os.open(path, os.O_RDONLY)
        try:
            fcntl.flock(fd, (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        finally:
            os.close(fd)
        return True

    def acquire_job_lock(self):
        script = 'source "$1"; acquire_job_lock'
        return subprocess.run(['/bin/bash', '-c', script, 'test', str(self.tree / 'backup/backup_conf.sh')],
                              env=self.outside, capture_output=True, text=True, timeout=20)

    def test_mutating_commands_snapshot_under_shared_legacy_then_run_frozen_code_on_fd_10(self):
        arguments = {'backup': ('--label', 'FORT-MPLS-5TB'), 'check': ('--label', 'FORT-MPLS-5TB', '--remote'),
                     'receipts': ('--label', 'nas'), 'restore-test': ('--label', 'nas', '--receipt', 'r')}
        for command, extra in arguments.items():
            with self.subTest(command=command):
                for path in self.records.iterdir():
                    path.unlink()
                result = self.run_launcher(command, *extra, STUB_EXIT='7')
                self.assertEqual(result.returncode, 7, result.stderr)
                snapshot = self.record('snapshot')
                self.assertEqual(snapshot, {'argv': ['create'], 'job_fd': '10', 'lock_dir': str(self.registry),
                                            'fence_blocked': True, 'job_exclusive': True, 'legacy_shared': True})
                frozen = self.record('frozen')
                code_root = Path(frozen['environment']['OFFSITE_CODE_ROOT'])
                self.assertEqual(code_root.parent, self.state / 'code')
                self.assertEqual(frozen['path0'], str(code_root / 'backup'))
                self.assertEqual(frozen['file'], str(code_root / 'backup/offsite_backup.py'))
                self.assertEqual(frozen['argv'], [command, *extra])
                self.assertEqual(frozen['pid'], int(self.job_lock.read_text()))
                self.assertTrue(frozen['job_fd10_exclusive'])
                self.assertFalse(frozen['fd9_open'])
                self.assertEqual(frozen['environment'], {
                    'OFFSITE_CODE_ROOT': str(code_root), 'VANPI_BACKUP_JOB_KIND': 'offsite',
                    'VANPI_BACKUP_JOB_LOCK_FD': '10', 'VANPI_BACKUP_JOB_LOCK_PATH': str(self.job_lock),
                    'VANPI_BACKUP_LOCK_DIR': str(self.registry), 'OFFSITE_IGNITION_FLAG': str(self.ignition),
                    'BORG_REPO': '/mnt/bigboi/borg/vanpi-encrypted', 'VANPI_BACKUP_DISK_LOCK_FDS': None,
                    'VANPI_BACKUP_LEGACY_LOCK_FD': None, 'VANPI_BACKUP_CLOUD_SLOT_FD': None,
                    'OFFSITE_ADMISSION': None})
                self.assertFalse((self.records / 'live.json').exists())
                self.assertTrue(self.free(self.job_lock) and self.free(self.legacy))

    def test_running_offsite_admits_the_fence_but_excludes_exclusive_maintenance(self):
        run = self.launch('backup', '--label', 'FORT-MPLS-5TB', STUB_HOLD='1')
        self.wait_ready('frozen', run)
        # The deployment fence (legacy exclusive) is free once the snapshot exists.
        self.assertTrue(self.free(self.legacy))
        self.assertFalse(self.free(self.job_lock, exclusive=False))
        maintenance = self.acquire_job_lock()
        self.assertNotEqual(maintenance.returncode, 0)
        self.assertIn('an offsite run is active', maintenance.stderr)
        self.assertTrue(self.free(self.legacy), 'refused maintenance kept the legacy lock')
        prepare = self.run_launcher('prepare-disk', '--device', '/dev/null', '--label', 'FORT-MPLS-5TB')
        self.assertEqual(prepare.returncode, 1)
        self.assertIn('an offsite run is active', prepare.stderr)
        self.assertFalse((self.records / 'live.json').exists())
        second = self.run_launcher('backup', '--label', 'FORT-MPLS-5TB')
        self.assertEqual((second.returncode, second.stderr), (1, RUN_BUSY + '\n'))
        (self.records / 'release').touch()
        self.assertEqual(run.wait(timeout=20), 0)
        self.assertTrue(self.free(self.job_lock))
        self.assertEqual(self.acquire_job_lock().returncode, 0)

    def test_exclusive_legacy_holder_refuses_before_snapshot(self):
        self.hold(self.legacy)
        result = self.run_launcher('backup', '--label', 'FORT-MPLS-5TB')
        self.assertEqual((result.returncode, result.stderr), (1, LEGACY_BUSY + '\n'))
        self.assertFalse((self.records / 'snapshot.json').exists())
        self.assertTrue(self.free(self.job_lock))

    def test_busy_identity_refuses_before_legacy_or_pid_record(self):
        self.job_lock.write_text('sentinel\n')
        self.hold(self.job_lock)
        result = self.run_launcher('check', '--label', 'FORT-MPLS-5TB')
        self.assertEqual((result.returncode, result.stderr), (1, RUN_BUSY + '\n'))
        self.assertEqual(self.job_lock.read_text(), 'sentinel\n')
        self.assertFalse((self.records / 'snapshot.json').exists())
        self.assertTrue(self.free(self.legacy))

    def test_ignition_and_snapshot_failure_stop_before_any_run(self):
        self.ignition.touch()
        result = self.run_launcher('backup', '--label', 'FORT-MPLS-5TB')
        self.assertEqual((result.returncode, result.stderr), (1, 'Ignition is on; park before offsite work.\n'))
        self.assertFalse((self.records / 'snapshot.json').exists())
        self.ignition.unlink()
        result = self.run_launcher('backup', '--label', 'FORT-MPLS-5TB', STUB_SNAPSHOT_FAIL='1')
        self.assertEqual((result.returncode, result.stderr), (1, 'stub snapshot refused\n'))
        self.assertTrue((self.records / 'snapshot.json').exists())
        self.assertFalse((self.records / 'frozen.json').exists())
        self.assertTrue(self.free(self.job_lock) and self.free(self.legacy))

    def test_prepare_disk_holds_legacy_and_offsite_identity_and_runs_live_code(self):
        self.job_lock.write_text('sentinel\n')
        run = self.launch('prepare-disk', '--device', '/dev/null', '--label', 'FORT-MPLS-5TB', STUB_HOLD='1')
        self.wait_ready('live', run)
        live = self.record('live')
        self.assertEqual(live['path0'], str(self.tree / 'backup'))
        self.assertTrue(live['legacy_exclusive_fd9'])
        self.assertEqual(len(live['offsite_holders']), 1)
        self.assertIsNone(live['environment']['OFFSITE_CODE_ROOT'])
        self.assertEqual((self.records / 'priority').read_text(),
                         str(self.tree / 'backup/backup_priority_control.py'))
        self.assertFalse((self.records / 'snapshot.json').exists())
        backup = self.run_launcher('backup', '--label', 'FORT-MPLS-5TB')
        self.assertEqual((backup.returncode, backup.stderr), (1, RUN_BUSY + '\n'))
        self.assertFalse(self.free(self.legacy, exclusive=False))
        (self.records / 'release').touch()
        self.assertEqual(run.wait(timeout=20), 0)
        self.assertTrue(self.free(self.job_lock) and self.free(self.legacy))
        self.assertEqual(self.job_lock.read_text(), 'sentinel\n', 'prepare-disk wrote a run PID record')

    def test_read_only_and_unprivileged_commands_take_no_locks(self):
        self.hold(self.legacy)
        self.hold(self.job_lock)
        result = self.run_launcher('status', root=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.record('live')['argv'], ['status'])
        self.job_lock.write_text('sentinel\n')
        for command in ('backup', 'prepare-disk', 'dashboard'):
            result = self.run_launcher(command, root=False)
            self.assertEqual((result.returncode, result.stderr), (1, 'Run offsite_backup.sh with sudo.\n'))
        self.assertEqual(self.job_lock.read_text(), 'sentinel\n')
        self.assertFalse((self.records / 'snapshot.json').exists())


if __name__ == '__main__':
    unittest.main()
