"""Bootstrap the actual reviewed predecessor; never inject a pre-updated trigger."""
import os
from pathlib import Path
import signal
import subprocess
import sys
import unittest

import backup_bootstrap_plan as plan
from pi.tests.backup.bootstrap_linux_fixture import (
    BACKUP, REPO, PREDECESSOR, bootstrap_commands, capture_guarded_previous,
    predecessor_files, real_predecessor, run_bootstrap,
)
from pi.tests.backup.deploy_fixtures import DeploymentFixture


class BootstrapPlanTests(unittest.TestCase):
    def test_provider_edits_are_preserved_and_changed_anchors_refuse(self):
        original = subprocess.check_output(['git', '-C', str(REPO), 'show',
                                            PREDECESSOR + ':pi/scripts/backup/offsite_backup.sh'])
        provider = b'\n# provider change\nprovider_extension() { printf "preserved"; }\n'
        patched = plan.patch('offsite_backup.sh', original + provider, BACKUP)
        self.assertTrue(patched.endswith(provider))
        self.assertEqual(plan.patch('offsite_backup.sh', patched, BACKUP), patched)
        with self.assertRaisesRegex(ValueError, 'Unsupported predecessor'):
            plan.patch('offsite_backup.sh', original.replace(b'    refuse ', b'    changed_refuse '), BACKUP)


@unittest.skipUnless(sys.platform.startswith('linux'), 'real bootstrap fences require Linux flock/fdinfo')
class BootstrapTests(DeploymentFixture):
    @classmethod
    def setUpClass(cls):
        cls.old = predecessor_files()

    def setUp(self):
        super().setUp()
        self.bundle = self.root / 'bundle'
        plan.build(REPO, PREDECESSOR, self.bundle)
        self.reset_fixture()

    def reset_fixture(self):
        self.fx = real_predecessor(self, self.fixture(), self.old)
        self.command = bootstrap_commands(self, self.fx, self.bundle)

    def run_host(self, action, **fault):
        args = [action, str(self.bundle)] if action == 'apply' else [action]
        return run_bootstrap(self, self.command, *args, **fault)

    def assert_held(self):
        self.assertTrue((self.state_dir(self.fx) / 'maintenance').is_file())
        cron = (self.fx[0] / 'cron-root').read_text()
        self.assertIn(plan.HOLD_PREFIX + '0 * * * *', cron)
        for service in plan.SERVICES:
            dropin = self.fx[2] / ('etc/systemd/system/' + service + '.d/backup-deploy-bootstrap.conf')
            self.assertIn('ConditionPathExists=!', dropin.read_text())

    def ready(self):
        result = self.run_host('apply')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_held()
        result = self.run_host('release')
        self.assertEqual(result.returncode, 0, result.stderr)
        capture_guarded_previous(self.fx)

    def recover(self):
        # This is the installed independent entry, not backup_window.sh or a
        # sandbox whose trigger the test supplied after publication was killed.
        script = Path(self.env['VANPI_BACKUP_DEPLOY_BOOTSTRAP_DIR']) / 'recover'
        return subprocess.run(['/bin/bash', str(script)], env=self.env,
                              capture_output=True, text=True, timeout=90)

    def test_each_bootstrap_guard_publication_can_be_killed_and_rerun_without_reopening(self):
        for index, name in enumerate(plan.GUARDS):
            with self.subTest(name=name):
                if index:
                    self.reset_fixture()
                result = self.run_host('apply', KILL_REPLACE_SUFFIX='/home/pi/scripts/backup/' + name)
                self.assertEqual(result.returncode, -signal.SIGKILL, result.stderr)
                self.assert_held()
                result = self.run_host('apply')
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assert_held()
                result = self.run_host('release')
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertFalse((self.state_dir(self.fx) / 'maintenance').exists())

    def test_effective_service_condition_is_required_before_any_guard_edit(self):
        path = self.fx[2] / 'home/pi/scripts/backup/backup_conf.sh'
        previous = path.read_bytes()
        result = self.run_host('apply', TEST_MISSING_CONDITION='1')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('condition is not effective', result.stderr)
        self.assertEqual(path.read_bytes(), previous)
        self.assertTrue((self.state_dir(self.fx) / 'maintenance').exists())
        self.assertFalse((self.state_dir(self.fx) / 'bootstrap-ready.json').exists())
        self.assertEqual(self.run_host('apply').returncode, 0)
        self.assert_held()

    def test_release_killed_after_restoring_cron_keeps_maintenance_until_retry(self):
        self.assertEqual(self.run_host('apply').returncode, 0)
        result = self.run_host('release', KILL_CRON_RESTORE='root')
        self.assertEqual(result.returncode, -signal.SIGKILL, result.stderr)
        self.assertTrue((self.state_dir(self.fx) / 'maintenance').exists())
        self.assertNotIn(plan.HOLD_PREFIX, (self.fx[0] / 'cron-root').read_text())
        conf = self.fx[2] / 'home/pi/scripts/backup/backup_conf.sh'
        refused = subprocess.run(['/bin/bash', '-c', '. "$1"; acquire_job_lock', 'held', str(conf)],
                                 env=self.env, capture_output=True, text=True, timeout=10)
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn('bootstrap maintenance is held', refused.stderr)
        self.assertEqual(self.run_host('release').returncode, 0)
        self.assertFalse((self.state_dir(self.fx) / 'maintenance').exists())

    def test_fsync_failure_and_provider_drift_keep_maintenance_held(self):
        result = self.run_host('apply', FAIL_FSYNC_SUFFIX='/home/pi/scripts/backup')
        self.assertNotEqual(result.returncode, 0)
        self.assert_held()
        self.assertEqual(self.run_host('apply').returncode, 0)
        changed = self.fx[2] / 'home/pi/scripts/backup/offsite_backup.sh'
        changed.write_text(changed.read_text().replace('backup_deploy_refuse_jobs || exit 1', 'false'))
        result = self.run_host('release')
        self.assertNotEqual(result.returncode, 0)
        self.assert_held()

    def test_drifted_reviewed_predecessor_refuses_without_overwriting_provider(self):
        changed = self.fx[2] / 'home/pi/scripts/backup/offsite_backup.sh'
        provider = changed.read_bytes() + b'\n# unreviewed provider change\n'
        changed.write_bytes(provider)
        result = self.run_host('apply')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Predecessor drift', result.stderr)
        self.assertEqual(changed.read_bytes(), provider)
        self.assert_held()

    def test_unbootstrapped_installer_refuses_before_stopping_timers(self):
        result = self.apply(self.fx)
        self.assertNotEqual(result.returncode, 0)
        events = self.fx[0] / 'events'
        self.assertFalse(events.exists() and 'systemctl:stop' in events.read_text())
        self.assertFalse((self.state_dir(self.fx) / 'pending').exists())

    def test_every_first_cohort_publication_recovers_through_independent_entry(self):
        count = sum(row[2] != 'preserve' for row in self.fx[3])
        for step in range(1, count + 1):
            with self.subTest(step=step):
                if step != 1:
                    self.reset_fixture()
                self.ready()
                self.env['TEST_KILL_STEP'] = str(step)
                result = self.apply(self.fx)
                self.env.pop('TEST_KILL_STEP')
                self.assertEqual(result.returncode, -signal.SIGKILL, result.stderr)
                result = self.recover()
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assert_restored(self.fx)

    def test_recovery_killed_after_restoring_old_window_keeps_its_next_trigger(self):
        self.ready()
        self.env['TEST_KILL_STEP'] = '12'
        result = self.apply(self.fx)
        self.env.pop('TEST_KILL_STEP')
        self.assertEqual(result.returncode, -signal.SIGKILL, result.stderr)
        self.env['TEST_KILL_ROLLBACK_SUFFIX'] = '/backup_window.sh'
        result = self.recover()
        self.env.pop('TEST_KILL_ROLLBACK_SUFFIX')
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue((self.state_dir(self.fx) / 'pending').exists())
        self.assertEqual((self.fx[2] / 'home/pi/scripts/backup/backup_window.sh').read_bytes(),
                         self.old['pi/scripts/backup/backup_window.sh'])
        result = self.recover()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_restored(self.fx)


if __name__ == '__main__':
    unittest.main()
