"""Offsite resource locks through the public CLI: real flocks, fake devices.

Covers the cloud-slot policy, the Borg repository deferral and bounded wait,
the local-job policy and admission refusals. Disk phases and releases are in
test_offsite_lock_phases.py, restore targets and snapshot isolation in
test_offsite_lock_remote.py.
"""
import json
import os
import signal
import sys
import time
import unittest

import backup_locks
from backup_locks import LockBusy, LockClass
from pi.tests.backup.offsite_lock_fixtures import LockFixtureCase

class CloudSlotTests(LockFixtureCase):
    def test_default_policy_keeps_the_cloud_slot_for_the_whole_run(self):
        self.update(hang=True)
        child = self.start('borg')
        self.wait_for((self.f.root / 'backup-started').exists, child, 'backup never started')
        with self.assertRaises(LockBusy):
            with backup_locks.held(LockClass.CLOUD, backup_locks.CLOUD_SLOT):
                pass
        restic = [row for row in self.f.executions() if row['name'] == 'restic']
        self.assertTrue(restic[-1]['env']['VANPI_BACKUP_CLOUD_SLOT_FD'])
        child.send_signal(signal.SIGTERM)
        child.communicate(timeout=25)
        with backup_locks.held(LockClass.CLOUD, backup_locks.CLOUD_SLOT):
            pass

    def test_a_running_cloud_upload_refuses_the_run_before_any_work(self):
        fd = self.hold(LockClass.CLOUD, backup_locks.CLOUD_SLOT)
        self.addCleanup(os.close, fd)
        before = len(self.f.calls())
        result = self.backup('borg')
        self.assertEqual(result.returncode, 1)
        self.assertIn('iCloud upload slot is busy', result.stderr)
        self.assertEqual(self.f.calls()[before:], [])
        self.assertEqual(self.f.snapshots(), [], 'the run must remove its snapshot even when refused')


class BorgRepositoryTests(LockFixtureCase):
    def test_busy_repository_defers_borg_to_the_end_then_waits_for_it(self):
        self.f.configure(borg_wait=60)
        holder = self.hold(LockClass.DATA, 'borg-repo')
        child = self.start('borg', 'movingparts')
        self.wait_for(lambda: self.restic_backups() == ['movingparts'] and
                      self.status()['sources']['movingparts']['result'] == 'ok', child, 'movingparts did not run first')
        time.sleep(1.5)
        self.assertEqual(self.restic_backups(), ['movingparts'], 'borg ran while the repository was busy')
        # The wait holds no bigboi lock, so a finishing Borg could unmount it meanwhile.
        os.close(self.hold(LockClass.DISK, 'bigboi'))
        os.close(holder)
        out, err = child.communicate(timeout=30)
        self.assertEqual(child.returncode, 0, out + err)
        self.assertIn('borg: Borg repository is busy; deferred', out)
        self.assertEqual(self.restic_backups(), ['movingparts', 'borg'])
        self.assertEqual({row['result'] for row in json.loads(out.splitlines()[-1])['sources'].values()}, {'ok'})

    def test_bigboi_drained_during_the_wait_fails_only_borg_with_its_cause(self):
        self.f.configure(borg_wait=60)
        holder = self.hold(LockClass.DATA, 'borg-repo')
        child = self.start('borg', 'movingparts')
        self.wait_for(lambda: self.restic_backups() == ['movingparts'] and
                      self.status()['sources']['movingparts']['result'] == 'ok', child, 'movingparts did not run first')
        drain = self.hold(LockClass.DISK, 'bigboi')
        self.addCleanup(os.close, drain)
        os.close(holder)
        out, err = child.communicate(timeout=30)
        self.assertEqual(child.returncode, 1, out + err)
        borg = json.loads(out.splitlines()[-1])['sources']['borg']
        self.assertEqual(borg['result'], 'failed')
        self.assertIn('bigboi is being unmounted', borg['cause'])
        self.assertEqual(self.restic_backups(), ['movingparts'])

    def test_offsite_borg_phase_holds_the_repository_against_nightly_borg(self):
        self.update(hang=True)
        child = self.start('borg')
        self.wait_for((self.f.root / 'backup-started').exists, child, 'backup never started')
        with self.assertRaises(LockBusy):
            with backup_locks.held(LockClass.DATA, 'borg-repo', timeout=0.5):
                pass
        child.send_signal(signal.SIGTERM)
        child.communicate(timeout=25)
        with backup_locks.held(LockClass.DATA, 'borg-repo', timeout=0.5):
            pass

    def test_exceeded_budget_fails_only_the_borg_source(self):
        self.f.configure(borg_wait=1.5)
        holder = self.hold(LockClass.DATA, 'borg-repo')
        self.addCleanup(os.close, holder)
        result = self.backup('movingparts', 'borg')
        self.assertEqual(result.returncode, 1, result.stderr)
        state = json.loads(result.stdout.splitlines()[-1])
        self.assertEqual(state['result'], 'failed')
        self.assertEqual(state['sources']['movingparts']['result'], 'ok')
        self.assertEqual(state['sources']['borg']['result'], 'failed')
        self.assertIn('stayed busy', state['sources']['borg']['cause'])
        self.assertEqual(self.restic_backups(), ['movingparts'])

    def interrupted_during_wait(self, stop):
        self.f.configure(borg_wait=60)
        holder = self.hold(LockClass.DATA, 'borg-repo')
        self.addCleanup(os.close, holder)
        child = self.start('borg')
        self.assertIn('deferred', child.stdout.readline())
        time.sleep(0.5)
        stop(child)
        started = time.monotonic()
        out, err = child.communicate(timeout=25)
        self.assertLess(time.monotonic() - started, 30, 'the wait did not notice the stop promptly')
        self.assertEqual(child.returncode, 143, out + err)
        return json.loads(out.splitlines()[-1])['sources']['borg']

    def test_sigterm_during_the_wait_interrupts_the_run(self):
        row = self.interrupted_during_wait(lambda child: child.send_signal(signal.SIGTERM))
        self.assertEqual(row['result'], 'interrupted')
        self.assertIn('SIGTERM', row['cause'])

    def test_ignition_during_the_wait_interrupts_the_run(self):
        row = self.interrupted_during_wait(lambda child: self.f.cfg.ignition.touch())
        self.assertEqual(row['result'], 'interrupted')
        self.assertIn('Ignition', row['cause'])


class PolicyTests(LockFixtureCase):
    def test_oom_fallback_excludes_the_named_local_jobs(self):
        self.update(excluded_local_jobs=['borg', 'exfat'])
        fd = self.hold(LockClass.JOB, 'borg')
        result = self.backup('movingparts')
        self.assertEqual(result.returncode, 1)
        self.assertIn('The borg backup job is running', result.stderr)
        os.close(fd)
        self.update(hang=True)
        child = self.start('movingparts')
        self.wait_for((self.f.root / 'backup-started').exists, child, 'backup never started')
        for kind in ('borg', 'exfat'):
            with self.subTest(kind=kind), self.assertRaises(LockBusy):
                with backup_locks.held(LockClass.JOB, kind):
                    pass
        child.send_signal(signal.SIGTERM)
        child.communicate(timeout=25)

    def test_default_policy_lets_nightly_borg_and_exfat_run_beside_a_seed(self):
        self.update(hang=True)
        child = self.start('movingparts')
        self.wait_for((self.f.root / 'backup-started').exists, child, 'backup never started')
        for kind in ('borg', 'exfat'):
            with backup_locks.held(LockClass.JOB, kind):
                pass
        with backup_locks.held(LockClass.DISK, 'bigboi', exclusive=False):
            pass
        child.send_signal(signal.SIGTERM)
        child.communicate(timeout=25)


class AdmissionTests(LockFixtureCase):
    def test_prepare_disk_mounts_and_registers_through_legacy_admission(self):
        mount = next(row for row in self.f.executions() if row['args'] == ['mount', self.f.label])
        self.assertEqual(mount['env']['OFFSITE_ADMISSION'], 'prepare')
        self.assertIsNone(mount['env']['VANPI_BACKUP_DISK_LOCK_FDS'])
        self.assertEqual(json.loads((self.f.cfg.state / 'repos.json').read_text())['repos'][self.f.label],
                         self.f.marker())

    def test_inherited_legacy_descriptor_is_refused(self):
        result = self.backup('borg', extra_fds=(self.f.fd,))
        self.assertEqual(result.returncode, 1)
        self.assertIn('inherited other backup lock descriptors', result.stderr)
        self.assertFalse(any(row[0] == 'restic' and 'backup' in row for row in self.f.calls()))

    def test_worker_must_run_from_its_snapshot(self):
        live = os.pathsep.join(path for path in os.environ.get('PYTHONPATH', '').split(os.pathsep) if path)
        result = self.backup('borg', env={'PYTHONPATH': live})
        self.assertEqual(result.returncode, 1)
        self.assertIn('not running from its code snapshot', result.stderr)
        result = self.backup('borg', env={'OFFSITE_CODE_ROOT': ''})
        self.assertEqual(result.returncode, 1)
        self.assertIn('must execute from its code snapshot', result.stderr)

    def test_snapshot_is_removed_after_the_run(self):
        result = self.backup('borg')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.f.snapshots(), [])

    @unittest.skipUnless(sys.platform == 'linux', 'kernel inherited-flock ownership requires Linux /proc fdinfo')
    def test_unheld_job_descriptor_is_refused(self):
        fd = os.open(self.f.job_path, os.O_RDONLY)
        self.addCleanup(os.close, fd)
        result = self.backup('borg', env={'VANPI_BACKUP_JOB_LOCK_FD': str(fd)}, extra_fds=(fd,))
        self.assertEqual(result.returncode, 1)
        self.assertIn('does not hold', result.stderr)


if __name__ == '__main__':
    unittest.main()
