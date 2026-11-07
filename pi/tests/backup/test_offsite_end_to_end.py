"""Offsite runs end to end through the real launcher, snapshot and worker (Linux only).

Covers design Verification items 1-4: the deployment fence and registry guard
during a run, snapshot isolation when the live tree moves away, and the
ignition drain (abort_backup.sh, ordinary and emergency) against a run's real
lock holds, including a drain of a disk the run does not hold.

offsite_backup.sh runs from a fixture copy of the scripts tree inside an
unprivileged user namespace (`unshare -r`: root there, with the same inodes and
flocks), takes its real locks in a private registry, has the real
offsite_snapshot.py copy and verify the closure, and execs the frozen worker.
The copy differs from the checkout only in data paths (secrets, the ignition
flag, backup preferences, and the worker's Config, which its entry point reads
from the fixture JSON), in an optional gate before the snapshot copy, and in
the device-facing helpers (restic and the mount, Samba, ntfy and uplink
helpers), which are the offsite_fake_command double run from the snapshot. The
installer fence with its registry guard, and the drain, run unmodified against
the same registry from outside the namespace, as installers and
umount_disks.sh do.
"""
import fcntl
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import time
import unittest

from pi.tests.backup.offsite_fixtures import REPO_SCRIPTS
from pi.tests.backup.offsite_remote_fixtures import RemoteFixture
from pi.tests.backup.test_local_backup_deploy import REGISTRY_SOURCE, DeploymentFixture
from pi.tests.backup.test_offsite_launcher import UNSHARE, userns_available

LABEL = 'FORT-MPLS-5TB'
FUSER = Path('/usr/bin/fuser')
SECRETS = '/home/pi/secrets/.bash_variables'
IGNITION = 'IGNITION_FLAG=/home/pi/hooks/ignition_is_on'
PREFERENCES = '/etc/vanpi-backup-settings.json'
MAIN = "\nif __name__ == '__main__':\n    raise SystemExit(main())\n"
WORKER_ENTRY = '''
if __name__ == '__main__':
    # Fixture entry: Config's data paths come from the fixture JSON; restic and
    # the helpers still resolve from this snapshot, as in production.
    from pathlib import Path
    from offsite_config import command_paths
    from offsite_remote import remote_commands
    values = json.loads(Path(os.environ['OFFSITE_CONFIG']).read_text())
    for key in ('state', 'password', 'mount_root', 'by_id', 'drain', 'ignition', 'borg'):
        values[key] = Path(values[key])
    code = os.environ.get('OFFSITE_CODE_ROOT') or None
    values['code_root'] = Path(code) if code else None
    if code:
        frozen = {**command_paths(code), **remote_commands(code)}
        values['commands'].update({name: frozen[name] for name in ('restic', 'mounts', 'samba', 'notify', 'uplink')})
    raise SystemExit(main(config=Config(**values)))
'''
SNAPSHOT_ENTRY = '''
if __name__ == '__main__':
    # Fixture gate: hold the launcher inside its snapshot window when asked.
    gate = os.environ.get('OFFSITE_E2E_SNAPSHOT_GATE')
    if gate:
        Path(gate + '.waiting').touch()
        deadline = time.monotonic() + 300
        while not Path(gate).exists() and time.monotonic() < deadline:
            time.sleep(0.05)
    raise SystemExit(main())
'''


def rebase(path, *replacements):
    text = path.read_text()
    for old, new, count in replacements:
        if text.count(old) != count:
            raise AssertionError(f'{path.name}: expected {count} of {old!r}; update the fixture')
        text = text.replace(old, new)
    path.write_text(text)


def holds_file(pid, path):
    """Whether a process has the lock file open, matched by inode as the drain does."""
    expected = path.stat()
    for entry in Path(f'/proc/{pid}/fd').iterdir():
        try:
            actual = entry.stat()
        except OSError:
            continue
        if (actual.st_dev, actual.st_ino) == (expected.st_dev, expected.st_ino):
            return True
    return False


def alive(pid):
    """False once the process is gone or a zombie awaiting its new parent's reaping."""
    try:
        return 'State:\tZ' not in Path(f'/proc/{pid}/status').read_text()
    except FileNotFoundError:
        return False


class LauncherFixture(RemoteFixture):
    """A prepared fixture drive and sources, then a launcher-ready copy of the scripts tree."""
    # Upper bound only: the Pi runs these beside real backups.
    command_timeout = 300

    def __init__(self):
        super().__init__()
        result = self.prepare()
        if result.returncode:
            raise AssertionError('fixture prepare-disk failed: ' + result.stderr + result.stdout)
        self.sources()
        # The launcher takes these locks itself from here on.
        fcntl.flock(self.fd, fcntl.LOCK_UN)
        fcntl.flock(self.job_fd, fcntl.LOCK_UN)
        self.configure(owner=0)  # The worker is root inside the user namespace.
        self.preferences = self.root / 'backup-settings.json'
        self.preferences.write_text(json.dumps({'version': 1, 'values': {'borg.daily': 21}}))
        self.preferences.chmod(0o600)
        self.started = self.root / 'backup-started'
        backup = self.live / 'backup'
        shutil.copy2(REPO_SCRIPTS / 'backup/offsite_backup.sh', backup / 'offsite_backup.sh')
        self.install_stub_helpers()
        rebase(backup / 'backup_conf.sh', (SECRETS, str(self.root / 'no-secrets'), 2),
               (IGNITION, f'IGNITION_FLAG={self.cfg.ignition}', 1), (PREFERENCES, str(self.preferences), 2))
        rebase(backup / 'backup_settings.py', (PREFERENCES, str(self.preferences), 1))
        rebase(backup / 'offsite_backup.py', (MAIN, WORKER_ENTRY, 1))
        rebase(backup / 'offsite_snapshot.py', (MAIN, SNAPSHOT_ENTRY, 1))

    def lock(self, name):
        return self.registry.registry / f'{name}.lock'

    def launch(self, *arguments, **extra):
        environment = {'PATH': f'{self.bin}:/usr/bin:/bin', 'LANG': 'C.UTF-8', 'PYTHONDONTWRITEBYTECODE': '1',
                       'OFFSITE_FIXTURE': str(self.fixture), 'OFFSITE_CONFIG': str(self.config_file),
                       'OFFSITE_STATE_ROOT': str(self.cfg.state), **self.registry.environment,
                       # Fixture files owned by the test user appear root-owned in the namespace.
                       'VANPI_BACKUP_JOB_LOCK_OWNER': 'root', 'VANPI_BACKUP_JOB_LOCK_GROUP': 'root', **extra}
        return subprocess.Popen([UNSHARE, '-r', '/bin/bash', str(self.live / 'backup/offsite_backup.sh'), *arguments],
                                env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    def drain(self, *arguments, **extra):
        environment = {'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8', 'PYTHONDONTWRITEBYTECODE': '1',
                       **self.registry.environment, **extra}
        return subprocess.run(['/bin/bash', str(self.live / 'backup/abort_backup.sh'), *arguments], env=environment,
                              capture_output=True, text=True, timeout=self.command_timeout)

    def update(self, **values):
        self.reload()
        self.state.update(values)
        self.save()

    def unmount(self, *labels):
        self.reload()
        for label in labels:
            self.state['mounts'].pop(str(self.cfg.mount_root / label), None)
        self.save()

    def reset(self):
        for name in ('executions.jsonl', 'commands.jsonl', 'backup-started'):
            (self.root / name).unlink(missing_ok=True)

    def status(self):
        return json.loads((self.cfg.state / 'status.json').read_text())

    @staticmethod
    def free(path):
        fd = os.open(path, os.O_RDONLY)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        finally:
            os.close(fd)
        return True

    def kill_fixture_process(self, pid):
        """SIGKILL a process this fixture started, only while its command line still names the fixture."""
        try:
            command = Path(f'/proc/{pid}/cmdline').read_bytes()
        except OSError:
            return
        if str(self.root).encode() in command:
            os.kill(pid, signal.SIGKILL)


@unittest.skipUnless(userns_available() and FUSER.is_file(),
                     'needs Linux flock, fdinfo, fuser and unprivileged user namespaces')
class OffsiteEndToEndTests(DeploymentFixture):
    def setUp(self):
        super().setUp()
        self.f = LauncherFixture()
        self.addCleanup(self.f.close)
        self.release = self.f.root / 'release'
        self.processes = []
        self.addCleanup(self.stop_processes)

    def launch(self, *arguments, **extra):
        process = self.f.launch(*arguments, **extra)
        self.processes.append(process)
        return process

    def stop_processes(self):
        self.release.touch()
        for process in self.processes:
            if process.poll() is None:
                process.terminate()
            try:
                process.communicate(timeout=120)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate()

    def finish(self, process):
        out, err = process.communicate(timeout=self.f.command_timeout)
        return out + err

    def wait_for(self, condition, process, message, timeout=240):
        deadline = time.monotonic() + timeout
        while not condition():
            if process.poll() is not None:
                self.fail(f'{message}: exited {process.returncode}: ' + ''.join(process.communicate()))
            self.assertLess(time.monotonic(), deadline, message)
            time.sleep(0.1)

    def deployment(self):
        """A staged cohort whose fence and registry guard use the run's registry and legacy lock."""
        deployment = self.fixture()
        # The launcher's registry names its own (empty) deploy-state dir; the deployment's bootstrap
        # readiness and journal live in the one fixture() seeded, so keep that one.
        state = self.env['VANPI_BACKUP_DEPLOY_STATE_DIR']
        self.env.update(self.f.registry.environment, VANPI_BACKUP_DEPLOY_STATE_DIR=state)
        return deployment

    def backup(self, *sources, remote=False, **extra):
        arguments = ['backup', *(['--remote'] if remote else []), '--label', LABEL]
        for source in sources:
            arguments += ['--only', source]
        return self.launch(*arguments, **extra)

    def test_fence_fails_fast_only_in_the_snapshot_window_then_publishes_during_the_run(self):
        gate = self.f.root / 'snapshot-gate'
        self.f.update(hold_until=str(self.release))
        run = self.backup('movingparts', OFFSITE_E2E_SNAPSHOT_GATE=str(gate))
        self.wait_for(Path(f'{gate}.waiting').exists, run, 'the launcher never reached its snapshot')
        started = time.monotonic()
        fenced = self.apply(self.deployment())
        self.assertLess(time.monotonic() - started, 60, 'the fence waited instead of failing fast')
        self.assertNotEqual(fenced.returncode, 0)
        self.assertIn('Backup lock became busy; retry deployment later.', fenced.stderr)
        gate.touch()
        self.wait_for(self.f.started.exists, run, 'restic never started')
        # The guard sees the run's real holds: a staged registry without job-offsite is refused.
        refusal = self.deployment()
        rebase(refusal[1] / REGISTRY_SOURCE, (' offsite)', ')', 1))
        refused = self.apply(refusal)
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn('without job-offsite while it is held', refused.stderr)
        published = self.apply(self.deployment())
        self.assertEqual(published.returncode, 0, published.stdout + published.stderr)
        self.assertIn('Cohort committed', published.stdout)
        self.assertIsNone(run.poll(), 'the deployment disturbed the run')
        self.release.touch()
        output = self.finish(run)
        self.assertEqual(run.returncode, 0, output)
        self.assertEqual(self.f.status()['result'], 'ok')

    def test_run_finishes_from_its_snapshot_after_the_live_tree_moves(self):
        moved = Path(f'{self.f.live}.moved')
        code = self.f.cfg.state / 'code'
        for remote, sources in ((False, ('time-machine', 'movingparts', 'borg')), (True, ('borg', 'movingparts'))):
            with self.subTest(remote=remote):
                # Unmounted sources make the helper run, and source the conf, after the move.
                self.f.unmount('movingparts', 'bigboi')
                self.f.reset()
                self.f.update(move_live=str(self.f.live), manifest_ok=None, source_conf=True, sourced_conf=[])
                run = self.backup(*sources, remote=remote)
                output = self.finish(run)
                self.assertEqual(run.returncode, 0, output)
                self.assertTrue(moved.is_dir(), 'the live tree never moved')
                self.f.reload()
                self.assertTrue(self.f.state['manifest_ok'])
                frozen = [row for row in self.f.executions() if row['name'] in ('restic', 'mounts', 'samba', 'notify')
                          or row['path'].endswith('icloud_uplink.py')]
                self.assertIn('notify', {row['name'] for row in frozen})
                for row in frozen:
                    self.assertTrue(Path(row['path']).is_relative_to(code), row)
                if remote:
                    self.assertTrue(any(row['path'].endswith('icloud_uplink.py') for row in frozen))
                sourced = self.f.state['sourced_conf']
                self.assertTrue(any(entry['live_present'] is False for entry in sourced), sourced)
                for entry in sourced:
                    # The snapshot's backup_conf.sh ran its own settings helper on the preferences.
                    self.assertEqual((entry['status'], entry['keep_daily']), (0, '21'), entry)
                    self.assertTrue(Path(entry['directory']).is_relative_to(code), entry)
                self.assertEqual(self.f.snapshots(), [])
                moved.rename(self.f.live)

    def test_ignition_drain_stops_the_run_then_takes_the_offsite_disk(self):
        self.f.update(hang=True)
        run = self.backup('movingparts')
        self.wait_for(self.f.started.exists, run, 'restic never started')
        drained = self.f.drain(LABEL)
        self.assertEqual(drained.returncode, 0, drained.stdout + drained.stderr)
        self.assertIn(f'asking backup/restore (pid {run.pid}) to stop before unmount', drained.stdout)
        output = self.finish(run)
        self.assertEqual(run.returncode, 143, output)
        status = self.f.status()
        self.assertEqual((status['result'], status['sources']['movingparts']['result']),
                         ('interrupted', 'interrupted'))
        for name in ('job-offsite', f'disk-{LABEL}', 'disk-movingparts', 'cloud-upload'):
            self.assertTrue(self.f.free(self.f.lock(name)), name)

    def test_emergency_drain_kills_an_orphan_holding_the_inherited_disk_lock(self):
        record = self.f.root / 'orphan.pid'
        self.f.update(hang=True, orphan=str(record))
        run = self.backup('movingparts')
        self.wait_for(record.exists, run, 'restic never left an orphan')
        orphan = int(record.read_text())
        self.addCleanup(self.f.kill_fixture_process, orphan)
        self.assertTrue(holds_file(orphan, self.f.lock(f'disk-{LABEL}')))
        # A long grace lets the TERMed parent finish first, so only the orphan is killed.
        drained = self.f.drain('--emergency', LABEL, ABORT_BACKUP_EMERGENCY_GRACE_SECONDS='20',
                               ABORT_BACKUP_EMERGENCY_KILL_WAIT_SECONDS='15')
        self.assertEqual(drained.returncode, 0, drained.stdout + drained.stderr)
        self.assertIn(f'asking backup/restore (pid {run.pid}) to stop before unmount', drained.stdout)
        self.assertIn(f'killing lock holders for {LABEL}', drained.stdout)
        output = self.finish(run)
        self.assertEqual(run.returncode, 143, output)
        deadline = time.monotonic() + 60
        while alive(orphan):
            self.assertLess(time.monotonic(), deadline, 'the orphan survived the emergency drain')
            time.sleep(0.1)
        for name in ('job-offsite', f'disk-{LABEL}', 'disk-movingparts', 'cloud-upload'):
            self.assertTrue(self.f.free(self.f.lock(name)), name)

    def test_draining_a_disk_the_run_does_not_hold_leaves_it_alone(self):
        self.f.update(hold_until=str(self.release))
        run = self.backup('movingparts')
        self.wait_for(self.f.started.exists, run, 'restic never started')
        drained = self.f.drain('hdd1tb')
        self.assertEqual(drained.returncode, 0, drained.stdout + drained.stderr)
        self.assertNotIn('asking backup/restore', drained.stdout)
        self.assertIsNone(run.poll(), 'the drain stopped a run that does not hold hdd1tb')
        self.release.touch()
        output = self.finish(run)
        self.assertEqual(run.returncode, 0, output)
        status = self.f.status()
        self.assertEqual((status['result'], status['sources']['movingparts']['result']), ('ok', 'ok'))


if __name__ == '__main__':
    unittest.main()
