"""Post-flock admission, private journals, durable commit and clone entry regressions."""
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import unittest

from pi.tests.backup.admission_gate import admission_gate
from pi.tests.backup.test_backup_deploy_recovery import RecoveryCase, BACKUP, JOB_ENTRIES


class AdmissionAndFinalizeTests(RecoveryCase):
    def test_every_shell_admission_rechecks_after_the_real_flock(self):
        for entry, call in JOB_ENTRIES.items():
            with self.subTest(entry=entry):
                self.marker.unlink(missing_ok=True)
                with admission_gate(self.fx[0]) as (gate_env, interrupt):
                    process = subprocess.Popen(['/bin/bash', '-c', '. "$1"; ' + call + ' && echo ADMITTED',
                                                'admit', str(self.scripts() / 'backup_conf.sh')],
                                               env=dict(self.env, **gate_env), stdout=subprocess.PIPE,
                                               stderr=subprocess.PIPE, text=True)
                    try:
                        interrupt(Path(self.env['VANPI_BACKUP_JOB_LOCK']), self.state_dir(self.fx))
                        out, err = process.communicate(timeout=20)
                    finally:
                        if process.poll() is None:
                            process.kill()
                            process.communicate()
                self.assertNotEqual(process.returncode, 0, out)
                self.assertNotIn('ADMITTED', out)
                self.assertIn('did not finish; backup jobs refuse', err)

    def test_kill_during_committed_timer_restoration_only_finalizes(self):
        self.env['TEST_KILL_TIMER_START'] = '1'
        result = self.apply(self.fx)
        self.env.pop('TEST_KILL_TIMER_START')
        self.assertEqual(result.returncode, -signal.SIGKILL, result.stderr)
        self.assertEqual(self.marker_fields()['phase'], 'committed')
        self.assert_jobs_refused()
        result = self.window()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('rollback:', self.events())
        self.assertFalse(self.marker.exists())
        self.assert_started_exactly_active_timers()

    def test_failed_destination_fsync_keeps_recovery_intent(self):
        sync = self.fx[0] / 'sync'
        sync.write_text('#!/bin/bash\n'
                        'if [[ "$*" == *"/home/pi/scripts/backup/backup_window.sh" ]]; then exit 1; fi\n'
                        'exec /usr/bin/sync "$@"\n')
        sync.chmod(0o755)
        self.env['LOCAL_BACKUP_SYNC'] = str(sync)
        result = self.apply(self.fx)
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(self.marker.exists())
        self.assertNotEqual(self.marker_fields()['phase'], 'committed')
        self.assert_jobs_refused()
        self.env.pop('LOCAL_BACKUP_SYNC')
        result = self.window()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.marker.exists())

    def test_direct_clone_refuses_pending_and_preserves_a_verified_parent_hold(self):
        scripts = self.scripts()
        shutil.copyfile(BACKUP / 'backup_locks.py', scripts / 'backup_locks.py')
        clone = scripts / 'clone_to_sd.sh'
        clone.write_text((BACKUP / clone.name).read_text().replace(
            '. /home/pi/scripts/backup/backup_conf.sh', '. "' + str(scripts / 'backup_conf.sh') + '"'))
        policy = scripts.parent / 'disk_policy.sh'
        policy.write_text('disk_policy_resolve_exact_label() { echo CLONE_ADMITTED; return 1; }\n')
        self.marker.write_text('id=20261104T000000Z-42\n')
        refused = subprocess.run(['/bin/bash', str(clone), 'hotspare-a'], env=self.env,
                                 capture_output=True, text=True, timeout=20)
        self.assertNotEqual(refused.returncode, 0)
        self.assertNotIn('CLONE_ADMITTED', refused.stdout)
        self.assertIn('did not finish; backup jobs refuse', refused.stderr)
        self.marker.unlink()
        # An inherited shared Borg hold and exclusive initializer hold must not
        # be reacquired, downgraded, unlocked or replaced by the child clone.
        for parent in ('acquire_backup_job borg bigboi movingparts', 'acquire_job_lock'):
            command = '. "$1/backup_conf.sh"; ' + parent + ' || exit; /bin/bash "$2" hotspare-a; '
            command += '/usr/bin/python3 "$1/backup_locks.py" --check-legacy-hold'
            result = subprocess.run(['/bin/bash', '-c', command, 'clone-parent', str(scripts), str(clone)],
                                    env=self.env, capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('CLONE_ADMITTED', result.stdout)
        standalone = subprocess.run(['/bin/bash', str(clone), 'hotspare-a'], env=self.env,
                                    capture_output=True, text=True, timeout=20)
        self.assertEqual(standalone.returncode, 2, standalone.stderr)
        self.assertIn('CLONE_ADMITTED', standalone.stdout)


@unittest.skipUnless(sys.platform.startswith('linux') and os.geteuid() != 0,
                     'multi-UID regression starts as an unprivileged Linux installer')
class PrivateJournalTests(RecoveryCase):
    def test_installer_and_private_journal_have_different_real_uids(self):
        if subprocess.run(['/usr/bin/sudo', '-n', '/bin/true'], capture_output=True).returncode:
            self.skipTest('requires passwordless test sudo for a separate root-owned journal')
        root, _stage, live, *_ = self.fx
        paths = [self.state_dir(self.fx), Path(self.env['VANPI_BACKUP_DEPLOY_BOOTSTRAP_DIR']),
                 live / 'etc/cron.d', live / 'etc/systemd/system']
        owner = self.env['VANPI_BACKUP_JOB_LOCK_OWNER'] + ':' + self.env['VANPI_BACKUP_JOB_LOCK_GROUP']
        self.addCleanup(subprocess.run, ['/usr/bin/sudo', '-n', '/usr/bin/chown', '-R', owner, str(root)], check=True)
        subprocess.run(['/usr/bin/sudo', '-n', '/usr/bin/chown', '-R', 'root:root', *map(str, paths)], check=True)
        self.env['VANPI_BACKUP_DEPLOY_STATE_OWNER'] = 'root:root'
        self.env['LOCAL_BACKUP_SUDO'] = '/usr/bin/sudo -n -E ' + str(root / 'sudo')
        result = self.apply(self.fx)
        self.assertEqual(result.returncode, 0, result.stderr)
        transaction = self.transaction(self.fx)
        self.assertEqual(transaction.stat().st_uid, 0)
        self.assertEqual(transaction.stat().st_mode & 0o777, 0o700)
        denied = subprocess.run(['/bin/cat', str(transaction / 'previous/manifest')], capture_output=True)
        self.assertNotEqual(denied.returncode, 0, 'journal accidentally became readable to the installer')
        self.assertFalse(self.marker.exists())


if __name__ == '__main__':
    unittest.main()
