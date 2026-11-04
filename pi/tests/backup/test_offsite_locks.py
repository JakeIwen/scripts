"""Offsite resource locks through the public CLI: real flocks, fake devices.

Covers the cloud-slot policy, the Borg repository deferral and bounded wait,
per-phase disk holds and their guarded release (a stop frees the target even
while a release stalls, and stays final once seen), restore targets, and
admission refusals.
"""
import json
import os
from pathlib import Path
import signal
import sys
import time
import unittest

import backup_locks
from backup_locks import LockBusy, LockClass
from pi.tests.backup.offsite_fixtures import OffsiteFixture
from pi.tests.backup.offsite_remote_fixtures import RemoteFixture


class LockFixtureCase(unittest.TestCase):
    fixture = OffsiteFixture

    def setUp(self):
        self.f = self.fixture()
        self.addCleanup(self.f.close)
        result = self.f.prepare()
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.f.sources()

    def update(self, **values):
        self.f.reload()
        self.f.state.update(values)
        self.f.save()

    def unmount(self, label):
        self.f.reload()
        self.f.state['mounts'].pop(str(self.f.cfg.mount_root / label))
        self.f.save()

    def hold(self, lock_class, name, exclusive=True):
        fd = backup_locks.open_lock(backup_locks.lock_path(lock_class, name))
        backup_locks.try_flock(fd, exclusive)
        return fd

    def backup(self, *sources, **kwargs):
        args = ['backup', '--label', self.f.label]
        for source in sources:
            args.extend(('--only', source))
        return self.f.run(*args, **kwargs)

    def start(self, *sources):
        args = ['backup', '--label', self.f.label]
        for source in sources:
            args.extend(('--only', source))
        child = self.f.start(*args)
        self.addCleanup(self.stop, child)
        return child

    def stop(self, child):
        if child.poll() is None:
            child.kill()
            child.communicate()

    def wait_for(self, condition, child, message, timeout=60):
        deadline = time.monotonic() + timeout
        while not condition():
            if child.poll() is not None:
                self.fail(message + ': ' + ''.join(child.communicate()))
            self.assertLess(time.monotonic(), deadline, message)
            time.sleep(0.05)

    def free(self, lock_class, name):
        """A non-blocking exclusive probe through a descriptor on the existing lock file."""
        try:
            with backup_locks.held(lock_class, name):
                return True
        except LockBusy:
            return False

    def stall_release(self, label):
        """Make the mount helper's release of label block until the returned proceed file exists."""
        stalled, proceed = self.f.root / 'release.stalled', self.f.root / 'release.proceed'
        self.update(release_stall={'label': label, 'stalled': str(stalled), 'proceed': str(proceed)})
        self.addCleanup(proceed.touch)  # a failing test never leaves the stub waiting
        return stalled, proceed

    def releases(self):
        return [row for row in self.f.executions() if row['name'] == 'mounts' and row['args'][0] == 'release']

    def restic_backups(self):
        return [row[row.index('--tag') + 1] for row in self.f.calls() if row[0] == 'restic' and 'backup' in row
                and '--stdin' not in row]

    def status(self):
        return json.loads(self.f.run('status').stdout)


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


class DiskPhaseTests(LockFixtureCase):
    def test_source_disk_is_held_before_mounting_and_released_after_its_phase(self):
        self.unmount('bigboi')
        result = self.backup('borg', 'movingparts')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        rows = self.f.executions()
        mount = next(row for row in rows if row['name'] == 'mounts' and row['args'] == ['mount', 'bigboi'])
        self.assertEqual(mount['env']['OFFSITE_ADMISSION'], 'job')
        self.assertIn('bigboi=', mount['env']['VANPI_BACKUP_DISK_LOCK_FDS'])
        names = [(row['name'], row['args'][:2]) for row in rows]
        release = names.index(('mounts', ['release', 'bigboi']))
        movingparts = next(index for index, row in enumerate(rows) if row['name'] == 'restic'
                           and 'movingparts' in row['args'])
        self.assertLess(release, movingparts, 'bigboi stayed mounted for the whole run')
        released = next(row for row in rows if row['args'] == ['release', 'bigboi'])
        self.assertNotIn('bigboi=', released['env']['VANPI_BACKUP_DISK_LOCK_FDS'])
        self.f.reload()
        self.assertNotIn(str(self.f.cfg.mount_root / 'bigboi'), self.f.state['mounts'])

    def test_another_holder_keeps_the_disk_mounted_without_failing(self):
        self.unmount('bigboi')
        fd = self.hold(LockClass.DISK, 'bigboi', exclusive=False)
        self.addCleanup(os.close, fd)
        result = self.backup('borg')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        state = json.loads(result.stdout)
        self.assertEqual(state['result'], 'ok')
        self.assertIn('bigboi: left mounted; another job holds its disk lock', state['cleanup_notes'])
        self.f.reload()
        self.assertIn(str(self.f.cfg.mount_root / 'bigboi'), self.f.state['mounts'])

    def test_a_disk_a_drain_already_unmounted_is_recorded_not_failed(self):
        self.unmount('bigboi')
        self.update(drained_before_release='bigboi')
        result = self.backup('borg')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('bigboi: already unmounted (by a drain or another job)', json.loads(result.stdout)['cleanup_notes'])

    def test_a_failed_release_fails_the_run_but_keeps_the_snapshot_result(self):
        self.unmount('bigboi')
        self.update(release_failure='bigboi')
        result = self.backup('borg')
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        state = json.loads(result.stdout)
        self.assertEqual(state['result'], 'failed')
        self.assertEqual(state['sources']['borg']['result'], 'ok')
        self.assertTrue(any(error.startswith('bigboi:') for error in state['cleanup_errors']))

    def test_exclusively_held_source_disk_fails_that_source_without_mounting(self):
        self.unmount('bigboi')
        fd = self.hold(LockClass.DISK, 'bigboi')
        self.addCleanup(os.close, fd)
        result = self.backup('borg', 'movingparts')
        self.assertEqual(result.returncode, 1)
        state = json.loads(result.stdout)
        self.assertIn('bigboi is being unmounted, drained or prepared', state['sources']['borg']['cause'])
        self.assertEqual(state['sources']['movingparts']['result'], 'ok')
        self.assertNotIn(['mounts', 'mount', 'bigboi'], self.f.calls())

    def test_target_mounted_by_the_run_is_released_through_the_guarded_helper(self):
        self.unmount(self.f.label)
        result = self.backup('movingparts')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        mount = [row for row in self.f.executions() if row['args'] == ['mount', self.f.label]][-1]
        self.assertIn(f'{self.f.label}=', mount['env']['VANPI_BACKUP_DISK_LOCK_FDS'])
        self.assertEqual(self.f.calls()[-2], ['mounts', 'release', self.f.label])
        self.assertEqual(self.f.calls()[-1][0], 'notify')

    def test_every_resource_lock_is_closed_before_the_notification(self):
        self.unmount('bigboi')
        self.unmount(self.f.label)
        result = self.backup('borg', 'movingparts')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.f.reload()
        self.assertEqual(self.f.state['busy_at_notify'], [])

    def test_ignited_drain_holding_the_target_stops_the_run_before_any_source(self):
        fd = self.hold(LockClass.DISK, self.f.label)
        self.addCleanup(os.close, fd)
        result = self.backup('borg')
        self.assertEqual(result.returncode, 1)
        self.assertIn(f'{self.f.label} is being unmounted, drained or prepared', json.loads(result.stdout)['cause'])
        self.assertEqual(self.restic_backups(), [])

    def test_local_check_holds_the_target_disk_and_the_cloud_slot(self):
        result = self.f.run('check', '--label', self.f.label)
        self.assertEqual(result.returncode, 0, result.stderr)
        check = next(row for row in self.f.executions() if row['name'] == 'restic' and 'check' in row['args'])
        self.assertIn(f'{self.f.label}=', check['env']['VANPI_BACKUP_DISK_LOCK_FDS'])
        self.assertTrue(check['env']['VANPI_BACKUP_CLOUD_SLOT_FD'])


class StopDuringReleaseTests(LockFixtureCase):
    """A drain TERMs the run and waits only briefly for the target: no release may keep it."""

    def test_stop_during_a_source_phase_frees_the_target_before_its_stalled_release(self):
        self.unmount('movingparts')
        self.unmount(self.f.label)
        stalled, proceed = self.stall_release('movingparts')
        self.update(hang=True)
        child = self.start('movingparts')
        self.wait_for((self.f.root / 'backup-started').exists, child, 'backup never started')
        self.assertFalse(self.free(LockClass.DISK, self.f.label), 'the run should hold its target')
        stopped = time.monotonic()
        child.send_signal(signal.SIGTERM)
        self.wait_for(stalled.exists, child, 'the deferred source release never ran')
        self.wait_for(lambda: self.free(LockClass.DISK, self.f.label), child,
                      'the target stayed locked behind a stalled release', timeout=10)
        self.assertLess(time.monotonic() - stopped, 10)
        self.assertIsNone(child.poll(), 'the release should still be stalled')
        proceed.touch()
        out, err = child.communicate(timeout=60)
        self.assertEqual(child.returncode, 143, out + err)
        self.assertEqual([row['args'][1] for row in self.releases()], ['movingparts', self.f.label])
        self.f.reload()
        self.assertEqual(self.f.state['released'], ['movingparts', self.f.label])
        self.assertEqual(json.loads(out.splitlines()[-1])['sources']['movingparts']['result'], 'interrupted')

    def test_stop_while_a_release_stalls_frees_the_target_and_starts_no_further_source(self):
        self.unmount('movingparts')
        stalled, proceed = self.stall_release('movingparts')
        child = self.start('movingparts', 'borg')
        self.wait_for(stalled.exists, child, 'movingparts was never released')
        self.assertFalse(self.free(LockClass.DISK, self.f.label), 'the run should hold its target')
        child.send_signal(signal.SIGTERM)
        self.wait_for(lambda: self.free(LockClass.DISK, self.f.label), child,
                      'the target stayed locked behind a stalled release', timeout=10)
        self.assertIsNone(child.poll(), 'the release should still be stalled')
        proceed.touch()
        out, err = child.communicate(timeout=60)
        self.assertEqual(child.returncode, 143, out + err)
        state = json.loads(out.splitlines()[-1])
        self.assertEqual(state['sources']['movingparts']['result'], 'ok')
        self.assertEqual(state['sources']['borg']['result'], 'interrupted')
        self.assertEqual(self.restic_backups(), ['movingparts'])
        self.f.reload()
        self.assertIn('movingparts', self.f.state['released'])
        # The helper inherits only job-offsite, so a stalled unmount holds no disk or slot.
        for row in self.releases():
            self.assertEqual(row['env']['VANPI_BACKUP_DISK_LOCK_FDS'], '', row)
            self.assertIsNone(row['env']['VANPI_BACKUP_CLOUD_SLOT_FD'], row)

    def test_an_ignition_flag_that_clears_during_a_release_still_stops_the_run(self):
        # The stop closed the run's holds, so a later source would run without its target lock or slot.
        self.unmount('movingparts')
        stalled, proceed = self.stall_release('movingparts')
        child = self.start('movingparts', 'borg')
        self.wait_for(stalled.exists, child, 'movingparts was never released')
        self.f.cfg.ignition.touch()
        self.wait_for(lambda: self.free(LockClass.DISK, self.f.label), child,
                      'the ignition stop never freed the target', timeout=10)
        self.f.cfg.ignition.unlink()
        proceed.touch()
        out, err = child.communicate(timeout=60)
        self.assertEqual(child.returncode, 143, out + err)
        state = json.loads(out.splitlines()[-1])
        self.assertEqual(state['result'], 'interrupted')
        self.assertIn('Ignition is on', state['cause'])
        self.assertEqual(state['sources']['movingparts']['result'], 'ok')
        self.assertEqual(state['sources']['borg']['result'], 'interrupted')
        self.assertEqual(self.restic_backups(), ['movingparts'])
        self.assertEqual([row['args'][1] for row in self.releases()], ['movingparts', self.f.label])

    def test_term_during_the_last_release_interrupts_the_run_and_releases_its_target(self):
        # The target was already mounted, so only a stopped run releases it.
        self.unmount('movingparts')
        stalled, proceed = self.stall_release('movingparts')
        child = self.start('movingparts')
        self.wait_for(stalled.exists, child, 'movingparts was never released')
        child.send_signal(signal.SIGTERM)
        self.wait_for(lambda: self.free(LockClass.DISK, self.f.label), child,
                      'the target stayed locked behind a stalled release', timeout=10)
        proceed.touch()
        out, err = child.communicate(timeout=60)
        self.assertEqual(child.returncode, 143, out + err)
        state = json.loads(out.splitlines()[-1])
        self.assertEqual(state['result'], 'interrupted')
        self.assertIn('SIGTERM', state['cause'])
        self.assertEqual(state['sources']['movingparts']['result'], 'ok')
        self.assertEqual([row['args'][1] for row in self.releases()], ['movingparts', self.f.label])
        self.f.reload()
        self.assertEqual(self.f.state['released'], ['movingparts', self.f.label])
        self.assertEqual([row[1] for row in self.f.calls() if row[0] == 'notify'], ['vanpi offsite interrupted'])

    def test_check_releases_its_target_holding_no_resource_descriptor(self):
        self.unmount(self.f.label)
        result = self.f.run('check', '--label', self.f.label)
        self.assertEqual(result.returncode, 0, result.stderr)
        [release] = self.releases()
        self.assertEqual(release['args'], ['release', self.f.label])
        self.assertEqual(release['env']['VANPI_BACKUP_DISK_LOCK_FDS'], '')
        self.assertIsNone(release['env']['VANPI_BACKUP_CLOUD_SLOT_FD'])


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


class RestoreTargetTests(LockFixtureCase):
    fixture = RemoteFixture

    def receipt(self):
        result = self.f.run('backup', '--remote', '--label', self.f.label, '--only', 'borg')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout)['sources']['borg']['receipt']

    def restore(self, target):
        target.mkdir(parents=True)
        return self.f.run('restore-test', '--label', self.f.label, '--receipt', self.receipt(),
                          '--path', str(self.f.cfg.borg / 'sample'), '--target', str(target))

    def restic_restore(self):
        return [row for row in self.f.executions() if row['name'] == 'restic' and 'restore' in row['args']][-1]

    def test_target_on_a_managed_disk_holds_that_disk_through_the_restore(self):
        result = self.restore(self.f.cfg.mount_root / 'bigboi' / 'restore-test')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('bigboi=', self.restic_restore()['env']['VANPI_BACKUP_DISK_LOCK_FDS'])

    def test_target_on_the_root_filesystem_takes_no_disk_lock(self):
        result = self.restore(self.f.root / 'restore-test')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.restic_restore()['env']['VANPI_BACKUP_DISK_LOCK_FDS'], '')

    def test_root_filesystem_target_inside_the_mount_root_is_refused(self):
        # movingparts mounting later would cover the target, unlocked.
        self.unmount('movingparts')
        result = self.restore(self.f.cfg.mount_root / 'movingparts' / 'restore-probe')
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn('on the root filesystem (its disk is not mounted)', result.stderr)
        self.assertFalse(any(row['name'] == 'restic' and 'restore' in row['args'] for row in self.f.executions()))

    def test_target_on_another_filesystem_is_refused(self):
        other = self.f.root / 'other-mount'
        self.f.reload()
        self.f.state['mounts'][str(other)] = str(self.f.root / 'other-device')
        self.f.save()
        result = self.restore(other / 'restore-test')
        self.assertEqual(result.returncode, 1)
        self.assertIn('root filesystem or a managed backup disk', result.stderr)


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


class IsolationTests(LockFixtureCase):
    fixture = RemoteFixture

    def test_run_uses_only_its_snapshot_after_the_live_tree_moves(self):
        self.f.install_stub_helpers()
        self.unmount('bigboi')
        for remote in (False, True):
            with self.subTest(remote=remote):
                (self.f.root / 'executions.jsonl').unlink(missing_ok=True)
                self.update(move_live=str(self.f.live), manifest_ok=None,
                            snapshot_commands=['restic', 'mounts', 'samba', 'notify', 'uplink'])
                child = self.start('borg', 'time-machine', 'movingparts') if not remote else None
                if remote:
                    child = self.f.start('backup', '--remote', '--label', self.f.label,
                                         '--only', 'borg', '--only', 'movingparts')
                    self.addCleanup(self.stop, child)
                out, err = child.communicate(timeout=self.f.command_timeout)
                self.assertEqual(child.returncode, 0, out + err)
                moved = Path(str(self.f.live) + '.moved')
                self.assertTrue(moved.exists())
                self.f.reload()
                self.assertTrue(self.f.state['manifest_ok'])
                frozen = [row for row in self.f.executions() if row['name'] in
                          ('restic', 'mounts', 'samba', 'notify') or row['path'].endswith('icloud_uplink.py')]
                code = self.f.cfg.state / 'code'
                self.assertTrue(frozen)
                for row in frozen:
                    self.assertTrue(Path(row['path']).is_relative_to(code), row)
                self.assertIn('notify', {row['name'] for row in frozen})
                if remote:
                    self.assertTrue(any(row['path'].endswith('icloud_uplink.py') for row in frozen))
                moved.rename(self.f.live)


if __name__ == '__main__':
    unittest.main()
