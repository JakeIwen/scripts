"""Interrupted backup deployments: durable journal, job refusal, automatic recovery.

Real processes throughout. The sudo shim SIGKILLs the installer helper after a
chosen number of publications, so no trap runs; the recovery trigger is a copy
of the checkout's backup_window.sh with only the two deployment-state modules beside it (the
fixture's live tree holds placeholders, so recovery can only use the copy
journaled with the transaction); drains run the checkout's abort_backup.sh.
"""
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time
import unittest

from pi.tests.backup.deploy_fixtures import DeploymentFixture, HELPER


BACKUP = HELPER.parent / 'scripts' / 'backup'
SECRETS = '/home/pi/secrets/.bash_variables'
IGNITION = 'IGNITION_FLAG=/home/pi/hooks/ignition_is_on'
# Two of the four timers, so recovery must restart exactly the recorded set.
ACTIVE = ('vanpi-icloud-backup.timer', 'vanpi-time-machine-icloud-resume.timer')
REFUSED = 'did not finish; backup jobs refuse'
# Each entry point's admission call, as the entry scripts make it.
JOB_ENTRIES = {
    'pi_backup.sh': 'acquire_backup_job borg bigboi movingparts',
    'exfat_snapshot.sh': 'acquire_backup_job exfat hdd1tb EXFAT512',
    'icloud_backup.sh --run (Pi cloud worker)': 'acquire_backup_job pi-cloud bigboi',
    'time_machine_icloud.sh (Mac cloud worker)': 'acquire_backup_job time-machine mbp2tbkup',
    'clone_now.sh': 'acquire_backup_job clone',
    'restore, hotspare init, offsite prepare-disk': 'acquire_job_lock',
}
EMERGENCY_GRACE = 8
EMERGENCY_KILL_WAIT = 3


@unittest.skipUnless(sys.platform.startswith('linux') and Path('/proc/locks').is_file()
                     and Path('/usr/bin/fuser').is_file(),
                     'deployment recovery needs Linux flock, /proc, GNU coreutils and fuser')
class RecoveryCase(DeploymentFixture):
    def setUp(self):
        super().setUp()
        self.fx = self.fixture()
        self.env['TEST_ACTIVE_TIMERS'] = ' '.join(ACTIVE)

    @property
    def marker(self):
        return self.state_dir(self.fx) / 'pending'

    def marker_fields(self):
        return dict(line.split('=', 1) for line in self.marker.read_text().splitlines())

    def events(self):
        path = self.fx[0] / 'events'
        return path.read_text() if path.exists() else ''

    def published(self):
        return [row for row in self.plan() if row[2] != 'preserve']

    def kill_after(self, step, prepare=False):
        key = 'TEST_KILL_PREPARE' if prepare else 'TEST_KILL_STEP'
        self.env[key] = str(step)
        result = self.apply(self.fx)
        del self.env[key]
        self.assertEqual(result.returncode, -signal.SIGKILL, result.stderr)
        self.assertTrue(self.marker.is_file(), 'a killed run left no durable marker')
        return result

    def reset_stage(self):
        """A rerun gets a fresh stage, as each installer run does."""
        shutil.rmtree(self.fx[1] / 'transaction', ignore_errors=True)
        for name in ('count', 'prepared'):
            (self.fx[0] / name).unlink(missing_ok=True)

    def window(self, in_window=False):
        """The hourly trigger: backup_window.sh beside only the state module and stubs."""
        sandbox = self.fx[0] / 'window'
        if not sandbox.exists():
            sandbox.mkdir()
            for name in ('backup_window.sh', 'backup_deploy_trigger.sh', 'backup_deploy_state.sh'):
                shutil.copy2(BACKUP / name, sandbox / name)
            for job in ('pi_backup.sh', 'exfat_snapshot.sh'):
                path = sandbox / job
                path.write_text('#!/bin/bash\necho "job:${0##*/}" >> "$TEST_ROOT/events"\n')
                path.chmod(0o700)
        (sandbox / 'backup_settings_control.py').write_text(f'raise SystemExit({0 if in_window else 3})\n')
        return subprocess.run(['/bin/bash', str(sandbox / 'backup_window.sh'), '--scheduled'],
                              env=self.env, capture_output=True, text=True, timeout=180)

    def scripts(self):
        """The checkout's admission and drain scripts, with data paths rebased."""
        tree = self.fx[0] / 'scripts'
        if not tree.exists():
            tree.mkdir()
            for name in ('backup_conf.sh', 'backup_deploy_state.sh', 'backup_locks.sh', 'backup_lock_io.sh',
                         'backup_lock_registry.sh', 'abort_backup.sh'):
                shutil.copy2(BACKUP / name, tree / name)
            conf = tree / 'backup_conf.sh'
            text = conf.read_text()
            for old, new in ((SECRETS, str(self.fx[0] / 'no-secrets')),
                             (IGNITION, 'IGNITION_FLAG=' + str(self.fx[0] / 'ignition'))):
                self.assertIn(old, text)
                text = text.replace(old, new)
            conf.write_text(text)
            (tree / 'backup_settings_control.py').write_text('')
            (tree / 'backup_priority_control.py').write_text('raise SystemExit(0)\n')
        return tree

    def admit(self, call):
        return subprocess.run(['/bin/bash', '-c', '. "$1/backup_conf.sh"; ' + call, 'admit', str(self.scripts())],
                              env=self.env, capture_output=True, text=True, timeout=60)

    def assert_jobs_refused(self):
        for entry, call in JOB_ENTRIES.items():
            with self.subTest(entry=entry):
                result = self.admit(call)
                self.assertNotEqual(result.returncode, 0, entry)
                self.assertIn(REFUSED, result.stderr)

    def assert_started_exactly_active_timers(self):
        started = {line.split()[-1] for line in self.events().splitlines() if line.startswith('systemctl:start ')}
        self.assertEqual(started, set(ACTIVE))

    def assert_recovered(self, journaled=True):
        self.assert_restored(self.fx, active=len(ACTIVE), journaled=journaled)
        self.assert_started_exactly_active_timers()
        self.assertFalse((self.fx[0] / 'alerts').exists())

    def drain(self):
        started = time.monotonic()
        result = subprocess.run(['/bin/bash', str(self.scripts() / 'abort_backup.sh'), '--emergency'],
                                env=self.env, capture_output=True, text=True, timeout=60)
        return result, time.monotonic() - started


class InterruptedPublicationTests(RecoveryCase):
    def test_kill_at_any_publication_step_is_restored_by_the_hourly_trigger(self):
        count = len(self.published())
        for step in (1, 2, count // 2, count - 1, count):
            with self.subTest(step=step):
                if step != 1:
                    self.fx = self.fixture()
                self.kill_after(step)
                self.assertEqual(self.marker_fields()['phase'], 'publishing')
                self.assertEqual(self.events().count('publish:'), step)
                self.assertNotIn('systemctl:start ', self.events())
                self.assert_jobs_refused()
                result = self.window()
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn('Recovered interrupted backup deployment', result.stdout)
                self.assertNotIn('job:', self.events())
                self.assert_recovered()
                for entry, call in JOB_ENTRIES.items():
                    self.assertEqual(self.admit(call).returncode, 0, entry)

    def test_kill_while_preparing_restarts_timers_and_removes_temporaries(self):
        self.kill_after(5, prepare=True)
        self.assertEqual(self.marker_fields()['phase'], 'preparing')
        self.assertEqual(len(list(self.fx[2].rglob('*.deploy.*'))), 5)
        self.assert_jobs_refused()
        result = self.window(in_window=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_recovered(journaled=False)
        # Inside the window the jobs start, after recovery.
        events = self.events()
        self.assertLess(events.index('systemctl:start '), events.index('job:pi_backup.sh'))

    def test_kill_after_commit_keeps_the_new_cohort(self):
        result = self.apply(self.fx, tail='local_backup_commit; kill -KILL $$')
        self.assertEqual(result.returncode, -signal.SIGKILL, result.stderr)
        self.assertFalse(self.marker.exists())
        self.assertEqual(self.window().returncode, 0)
        self.assertNotIn('rollback:', self.events())
        for source, destination, *_ in self.published():
            self.assertEqual((self.fx[2] / destination.lstrip('/')).read_bytes(), (self.fx[1] / source).read_bytes())
        self.assertEqual(self.admit(JOB_ENTRIES['pi_backup.sh']).returncode, 0)

    def test_killed_recovery_resumes_from_its_fixed_temporary_names(self):
        self.kill_after(4)
        # A recovery killed between staging and renaming a restore leaves this.
        destination = self.published()[3][1]
        leftover = self.fx[2] / (destination.lstrip('/') + '.rollback.' + self.marker_fields()['temp'])
        leftover.write_text('half-restored\n')
        result = self.window()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_recovered()

    def test_failed_recovery_stays_fail_closed_and_alerts_once_a_day(self):
        self.kill_after(3)
        tampered = self.fx[2] / self.published()[1][1].lstrip('/')
        published = tampered.read_bytes()
        tampered.write_text('edited during the outage\n')
        for attempt in (1, 2):
            with self.subTest(attempt=attempt):
                result = self.window()
                self.assertEqual(result.returncode, 70, result.stderr)
                self.assertIn('RECOVERY INCOMPLETE', result.stderr)
                self.assertTrue(self.marker.is_file())
                self.assertNotIn('systemctl:start ', self.events())
                self.assert_jobs_refused()
                alerts = (self.fx[0] / 'alerts').read_text().splitlines()
                self.assertEqual(len(alerts), 1)
                self.assertTrue(alerts[0].startswith('NTFY_WARNING_URL|vanpi backup deployment|high'), alerts)
        tampered.write_bytes(published)
        result = self.window()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_restored(self.fx, active=len(ACTIVE))
        self.assertFalse((self.state_dir(self.fx) / 'alerted').exists())

    def test_next_installer_refuses_an_unrecovered_transaction_then_recovers_and_publishes(self):
        self.kill_after(3)
        recorded = self.marker_fields()['id']
        tampered = self.fx[2] / self.published()[1][1].lstrip('/')
        published = tampered.read_bytes()
        tampered.write_text('edited during the outage\n')
        self.reset_stage()
        result = self.apply(self.fx)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('could not be recovered; refusing to publish over it', result.stderr)
        self.assertEqual(self.events().count('publish:'), 3)
        self.assertNotIn('systemctl:stop', self.events().split('publish:')[-1])
        self.assertEqual(self.marker_fields()['id'], recorded)
        tampered.write_bytes(published)
        self.reset_stage()
        result = self.apply(self.fx)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Recovered interrupted backup deployment ' + recorded, result.stdout)
        self.assertFalse(self.marker.exists())
        for source, destination, *_ in self.published():
            self.assertEqual((self.fx[2] / destination.lstrip('/')).read_bytes(), (self.fx[1] / source).read_bytes())

    def test_drains_and_disk_claims_never_wait_on_a_recorded_transaction(self):
        self.kill_after(2)
        result, elapsed = self.drain()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertLess(elapsed, EMERGENCY_GRACE)
        claim = self.admit('hold_backup_disks_exclusive FORT-MPLS-5TB bigboi')
        self.assertEqual(claim.returncode, 0, claim.stderr)

    def test_finished_transactions_are_pruned_and_unsafe_records_refused(self):
        state = self.state_dir(self.fx)
        state.mkdir(mode=0o755)
        old = [state / f'2026010{day}T000000Z-{100 + day}' for day in range(1, 6)]
        for path in old:
            path.mkdir(mode=0o700)
        (state / 'notes').mkdir()
        result = self.apply(self.fx)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([path.exists() for path in old], [False, True, True, True, True])
        self.assertTrue((state / 'notes').is_dir())
        self.assertEqual(sorted((self.transaction(self.fx) / 'recovery').iterdir()),
                         sorted(self.transaction(self.fx) / 'recovery' / name for name in (
                             'backup_deploy_recovery.sh', 'backup_deploy_state.sh', 'backup_deploy_trigger.sh',
                             'backup_deploy_plan.sh',
                             'backup_deploy_transaction.sh', 'backup_locks.sh', 'backup_lock_io.sh',
                             'backup_lock_registry.sh')))
        self.fx = self.fixture()
        state = self.state_dir(self.fx)
        state.mkdir(mode=0o755)
        (state / '20260101T000000Z-1').mkdir(mode=0o770)
        (state / '20260101T000000Z-1').chmod(0o770)
        result = self.apply(self.fx)
        self.assertIn('Unsafe deployment record', result.stderr)
        self.assertNotIn('publish:', self.events())
        self.assertNotIn('systemctl:stop', self.events())


class DeployOrderTests(RecoveryCase):
    """The first deployment of this mechanism meets code that predates it, both ways round."""

    def test_publishers_without_a_journal_leave_jobs_admitted(self):
        # An older helper or the broad sync writes no state directory at all,
        # and finished transactions leave only their records behind.
        for call in JOB_ENTRIES.values():
            self.assertEqual(self.admit(call).returncode, 0, call)
        self.assertEqual(self.apply(self.fx).returncode, 0)
        self.assertFalse(self.marker.exists())
        for call in JOB_ENTRIES.values():
            self.assertEqual(self.admit(call).returncode, 0, call)

    def test_new_helper_recovers_with_no_journal_aware_code_live(self):
        # Every live cohort file is a placeholder, including the state module the
        # first record publishes, so the trigger can only use the journaled copy.
        self.kill_after(1)
        published = self.fx[2] / self.published()[0][1].lstrip('/')
        self.assertTrue(published.read_text().startswith('new '))
        result = self.window()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_recovered()


class DrainTests(RecoveryCase):
    def start_installer(self, **values):
        self.env.update({key: str(value) for key, value in values.items()})
        _root, stage, _live, _records, previous, _snapshots = self.fx
        installer = subprocess.Popen(
            ['/bin/bash', '-c', 'set -euo pipefail; source "$1"; shift; '
             'local_backup_begin "$1" tm "$2"; local_backup_publish; local_backup_commit',
             'test', str(HELPER), str(stage), self.hashes(previous)],
            env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.addCleanup(self.stop, installer)
        deadline = time.monotonic() + 120
        while not (self.fx[0] / 'ready').exists():
            self.assertIsNone(installer.poll(), 'installer exited before the drain')
            self.assertLess(time.monotonic(), deadline, 'installer never reached the drain step')
            time.sleep(0.05)
        return installer

    @staticmethod
    def stop(process):
        if process.poll() is None:
            process.kill()
        process.communicate()

    def test_drain_terms_the_installer_through_its_pid_record_and_rollback_beats_the_grace(self):
        installer = self.start_installer(TEST_PUBLISH_DELAY=0.2, TEST_READY_STEP=3)
        result, elapsed = self.drain()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f'asking backup/restore (pid {installer.pid}) to stop before unmount', result.stdout)
        self.assertNotIn('killing lock holders', result.stdout)
        self.assertLess(elapsed, EMERGENCY_GRACE)
        _out, err = installer.communicate(timeout=30)
        self.assertEqual(installer.returncode, 143, err)
        self.assertIn('previous cohort restored and verified', err)
        self.assert_recovered()

    def test_emergency_drain_still_kills_after_the_unchanged_grace(self):
        installer = self.start_installer(TEST_BLOCK_STEP=3)
        result, elapsed = self.drain()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f'did not stop within {EMERGENCY_GRACE}s; killing lock holders', result.stdout)
        self.assertGreaterEqual(elapsed, EMERGENCY_GRACE)
        self.assertLess(elapsed, EMERGENCY_GRACE + EMERGENCY_KILL_WAIT)
        installer.communicate(timeout=30)
        self.assertEqual(installer.returncode, -signal.SIGKILL)
        self.assertEqual(self.marker_fields()['phase'], 'publishing')
        self.assert_jobs_refused()
        result = self.window()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_recovered()


if __name__ == '__main__':
    unittest.main()
