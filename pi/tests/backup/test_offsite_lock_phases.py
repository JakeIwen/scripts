"""Offsite disk phases and guarded releases through the public CLI: real flocks, fake devices.

Per-phase disk holds, and the guarantee that a stop frees the target even while
a release stalls and stays final once seen.
"""
import json
import os
import signal
import time
import unittest

from backup_locks import LockClass
from pi.tests.backup.offsite_lock_fixtures import LockFixtureCase

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


if __name__ == '__main__':
    unittest.main()
