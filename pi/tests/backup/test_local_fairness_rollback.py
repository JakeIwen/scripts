"""Archive-only rollback against real historical readers and temporary locks."""
import contextlib
import fcntl
import importlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
from unittest import mock

import backup_priority as priority
import local_fairness as fairness
from pi.tests.backup.priority_fixtures import PriorityFixture, GEN
from time_machine_store import atomic_json

REPO = Path(__file__).resolve().parents[3]
SOURCE = REPO / 'pi/scripts/backup'
OLD_FILES = ('backup_conf.sh', 'backup_window.sh', 'backup_priority.py', 'cloud_backup_control.py',
             'pi_backup.sh', 'exfat_snapshot.sh', 'local_fairness.py', 'local_fairness_window.py',
             'time_machine_store.py', 'icloud_status.py', 'icloud_progress.py')


class RollbackTests(PriorityFixture):
    def setUp(self):
        super().setUp()
        self.previous = self.root / 'previous'
        self.live = self.root / 'live'; self.live.mkdir()
        for name in OLD_FILES:
            shutil.copy2(SOURCE / name, self.live / name)
        self.proc = self.root / 'proc'; self.proc.mkdir()
        self.cloud_status = {job.service: 'ActiveState=inactive\nMainPID=0\nControlPID=0\n'
                             for job in self.jobs.values()}
        self.cloud_probes = []
        self.forbidden_work = mock.Mock(side_effect=AssertionError('A fenced window must not run work or sleep'))
        self.lock = self.root / 'backup.lock'; self.lock.touch(); self.lock.chmod(0o660)
        clock = mock.patch.object(fairness, 'boot_clock', return_value=fairness.BootClock(
            '11111111-1111-1111-1111-111111111111', 1000))
        clock.start(); self.addCleanup(clock.stop)
        priority.set_policy('time-machine')
        self.stamps = {kind: self.root / kind.value for kind in fairness.LocalJob}
        self.proofs = self.root / 'frozen'; self.proofs.mkdir()
        atomic_json(self.proofs / 'manifest.json', {'generation': GEN, 'frozen': True})
        atomic_json(self.proofs / 'verified-objects.json', {'completed-proof': {'verified_at': 1}})

    def turn(self, success=None):
        (self.directory / 'local-fairness.json').unlink(missing_ok=True)
        turn = fairness.reserve(self.stamps, fairness.FairnessConfig(), log=lambda _: None)
        if success is not None:
            fairness.finish(turn['token'], success, 'test completion')
        return turn

    def command(self, args, **kwargs):
        self.assertEqual(args[:2], ['/usr/bin/systemctl', 'show'])
        self.assertTrue(kwargs['check'])
        self.cloud_probes.append(args[2])
        evidence = self.cloud_status[args[2]]
        if isinstance(evidence, Exception):
            raise evidence
        return subprocess.CompletedProcess(args, 0, evidence)

    def archive(self, check_only=False):
        module = importlib.import_module('local_fairness_rollback')
        return module.prepare_rollback(check_only=check_only, directory=self.directory,
            lock_path=self.lock, proc_root=self.proc, command=self.command,
            lock_owner=os.geteuid(), lock_group=os.getegid())

    def fenced_window(self):
        from local_fairness_window import Window
        return Window(self.stamps, fairness.FairnessConfig(), command=self.forbidden_work,
            popen=self.forbidden_work, clock=self.clock, sleep=self.forbidden_work)

    def install_old_fixture(self):
        # Simulate the operator's code-first restoration, not a helper operation.
        for name in OLD_FILES:
            result = subprocess.run(['git', '-C', str(REPO), 'show', 'c3849a5:pi/scripts/backup/' + name],
                                    capture_output=True)
            if result.returncode:
                self.skipTest('historical c3849a5 source unavailable in this staged payload')
            (self.live / name).write_bytes(result.stdout)

    def old_entrypoints(self):
        code = '''import json,sys,time
from pathlib import Path
import backup_priority as p
import cloud_backup_control as c
import local_fairness as f
root=Path(sys.argv[1]); p.DIRECTORY=root/'priority'
time.time=lambda:float(sys.argv[2])
p.JOBS={m:p.CloudJob(j.label,j.service,root/m.value,root/(m.value+'.json')) for m,j in p.JOBS.items()}
job=p.JOBS[p.PriorityMode.TIME_MACHINE]
operations=[]
result={'record':f.read_record(), 'paused':c.is_paused(job.directory)}
result['admitted']=c.admit_worker(job.directory)
result['resume']=c.control('resume-if-due',directory=job.directory,service=job.service,
                          command=lambda args,**kwargs:operations.append(args[1]))
result['operations']=operations
print(json.dumps(result))
'''
        return subprocess.run([sys.executable, '-c', code, str(self.root), str(self.now)], cwd=self.live,
            env={**os.environ, 'PYTHONPATH': str(self.live)}, capture_output=True, text=True, timeout=60)

    def test_code_first_then_archive_recovers_completed_backoff_and_waiting_records(self):
        self.install_old_fixture()
        protected = [self.directory / 'priority.json', self.proofs / 'manifest.json',
                     self.proofs / 'verified-objects.json', *(self.live / name for name in OLD_FILES)]
        for job in self.jobs.values():
            atomic_json(job.directory / 'pause.json', {'paused_until': 'indefinite'})
            protected.extend((job.directory / 'pause.json', job.directory / 'state.json'))
        originals = {path: path.read_bytes() for path in protected}
        for success in (True, False, None):
            with self.subTest(success=success):
                self.turn(success)
                record = (self.directory / 'local-fairness.json').read_bytes()
                before = self.old_entrypoints()
                self.assertNotEqual(before.returncode, 0)
                self.assertIn('Invalid local fairness record', before.stderr)
                result = self.archive()
                archive = Path(result['archive'])
                self.assertTrue(archive.parent.name.startswith('local-fairness-rollback-'))
                self.assertEqual(archive.read_bytes(), record)
                self.assertFalse((self.directory / 'local-fairness.json').exists())
                after = self.old_entrypoints()
                self.assertEqual(after.returncode, 0, after.stderr)
                value = json.loads(after.stdout)
                self.assertIsNone(value['record'])
                self.assertTrue(value['paused'])
                self.assertFalse(value['admitted'])
                self.assertEqual(value['operations'], [])
                self.assertEqual({path: path.read_bytes() for path in protected}, originals)

    def assert_refused_without_changes(self, error):
        paths = [self.directory / 'local-fairness.json', self.directory / 'priority.json']
        paths.extend(self.live / name for name in OLD_FILES)
        before = {path: path.read_bytes() for path in paths}
        with self.assertRaises(error):
            self.archive()
        self.assertEqual({path: path.read_bytes() for path in paths}, before)
        self.assertEqual(list(self.directory.glob('local-fairness-rollback-*')), [])

    def test_busy_normal_backup_lock_refuses_archival(self):
        self.turn()
        with self.lock.open('rb') as active_job:
            fcntl.flock(active_job, fcntl.LOCK_EX)
            self.assert_refused_without_changes(BlockingIOError)

    def test_supervisor_gate_fences_archival_and_new_window_admission(self):
        self.turn()
        with fairness.supervisor_slot(self.directory):
            self.assert_refused_without_changes(BlockingIOError)
            with self.assertRaises(BlockingIOError):
                self.fenced_window().run()
        self.forbidden_work.assert_not_called()

    def test_backup_or_lifecycle_process_refuses_archival(self):
        self.turn()
        process = self.proc / '123'; process.mkdir()
        for name in ('local_fairness_window.py', 'backup_window.sh', 'pi_backup.sh',
                     'exfat_snapshot.sh', 'borg', 'time_machine_icloud.py', 'icloud_backup.sh'):
            with self.subTest(name=name):
                (process / 'cmdline').write_bytes(b'/bin/bash\0/home/pi/scripts/backup/' + name.encode() + b'\0')
                self.assert_refused_without_changes(RuntimeError)

    def test_missing_process_evidence_refuses_archival(self):
        self.turn()
        (self.proc / '123').mkdir()
        self.assert_refused_without_changes(RuntimeError)

    def test_unsafe_normal_lock_refuses_without_creating_or_following_it(self):
        self.turn()
        self.lock.chmod(0o644)
        self.assert_refused_without_changes(ValueError)
        self.lock.unlink()
        self.assert_refused_without_changes(FileNotFoundError)
        self.assertFalse(self.lock.exists())
        self.lock.symlink_to(self.root / 'absent')
        self.assert_refused_without_changes(OSError)
        self.lock.unlink()
        os.mkfifo(self.lock, 0o660)
        self.assert_refused_without_changes(ValueError)

    def test_archives_are_private_unique_and_missing_state_is_idempotent(self):
        self.turn(True)
        first = self.archive()['archive']
        first_bytes = Path(first).read_bytes()
        self.turn(False)
        second = self.archive()['archive']
        self.assertNotEqual(first, second)
        self.assertEqual(Path(first).read_bytes(), first_bytes)
        self.assertEqual(Path(first).parent.stat().st_mode & 0o777, 0o700)
        self.assertEqual(Path(first).stat().st_mode & 0o777, 0o600)
        self.assertIsNone(self.archive()['archive'])
        self.assertEqual(len(list(self.directory.glob('local-fairness-rollback-*'))), 2)

    def test_new_supervisors_are_fenced_during_archival(self):
        module = importlib.import_module('local_fairness_rollback')
        self.turn()
        archive_record = module.archive_record
        def inspect_and_archive(directory):
            with self.assertRaises(BlockingIOError):
                self.fenced_window().run()
            return archive_record(directory)
        with mock.patch.object(module, 'archive_record', side_effect=inspect_and_archive):
            self.assertIsNotNone(self.archive()['archive'])

    def test_exec_stop_post_prevents_archival_even_without_a_lock_holder(self):
        self.turn(True)
        service = self.jobs[priority.PriorityMode.TIME_MACHINE].service
        self.cloud_status[service] = 'ActiveState=deactivating\nMainPID=0\nControlPID=123\n'
        self.assert_refused_without_changes(RuntimeError)
        self.assertIn(service, self.cloud_probes)

    def test_both_cloud_services_require_complete_quiescence_evidence(self):
        self.turn(True)
        for service in self.cloud_status:
            for evidence in ('ActiveState=inactive\nMainPID=0\nControlPID=123\n',
                             'ActiveState=inactive\nMainPID=0\n',
                             'ActiveState=deactivating\nMainPID=0\nControlPID=0\n',
                             subprocess.TimeoutExpired('systemctl', 10)):
                with self.subTest(service=service, evidence=evidence):
                    self.cloud_status[service] = evidence
                    self.assert_refused_without_changes((RuntimeError, subprocess.TimeoutExpired))
            self.cloud_status[service] = 'ActiveState=inactive\nMainPID=0\nControlPID=0\n'

    def test_check_only_does_not_archive_or_modify_state(self):
        self.turn()
        record = (self.directory / 'local-fairness.json').read_bytes()
        self.assertEqual(self.archive(check_only=True), {'checked': True, 'archive': None})
        self.assertEqual((self.directory / 'local-fairness.json').read_bytes(), record)
        self.assertEqual(list(self.directory.glob('local-fairness-rollback-*')), [])
        self.assertEqual(set(self.cloud_probes), {job.service for job in self.jobs.values()})

    def test_cli_rejects_code_copy_arguments(self):
        module = importlib.import_module('local_fairness_rollback')
        for option in ('--previous', '--live'):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as refused:
                module.main([option, str(self.previous)])
            self.assertEqual(refused.exception.code, 2)

    def test_archive_does_not_require_readable_boot_identity(self):
        self.turn()
        with mock.patch.object(fairness, 'boot_clock', side_effect=PermissionError):
            self.assertIsNotNone(self.archive()['archive'])

    def test_old_unheld_cloud_resume_remains_available_after_code_first_archive(self):
        self.install_old_fixture()
        self.turn(False)
        self.archive()
        result = self.old_entrypoints()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['operations'], ['start'])


if __name__ == '__main__':
    unittest.main()
