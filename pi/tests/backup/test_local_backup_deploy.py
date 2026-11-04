"""Public deployment contracts against fixture Git repos and real Linux files."""
import fcntl
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import unittest

from pi.tests.backup.deploy_fixtures import (DeploymentFixture, DeploymentTestCase, HELPER,
                                             POLICIES_WITH_HASHES, REGISTRY_SOURCE, digest)


class DeploymentPlanTests(DeploymentTestCase):
    def test_single_allowlist_drives_transfer_and_dependency_order(self):
        for profile in ('pi', 'tm'):
            records = self.plan(profile)
            sources = [row[0] for row in records]
            destinations = [row[1] for row in records]
            self.assertEqual(len(destinations), len(set(destinations)))
            self.assertEqual(self.shell('local_backup_sources "$1"', profile).stdout.splitlines(), sources)
            for dependency, consumer in [('backup_lock_io.sh', 'backup_locks.sh'),
                    ('backup_lock_registry.sh', 'backup_locks.sh'),
                    ('backup_locks.sh', 'backup_conf.sh'), ('umount_disks_fence.sh', 'umount_disks.sh'),
                    ('stale_mount_recovery.sh', 'mount_disks.sh'),
                    ('time_machine_store.py', 'offsite_repository.py'),
                    ('offsite_config.py', 'offsite_repository.py'),
                    ('offsite_repository.py', 'offsite_run.py'),
                    ('offsite_samba.py', 'offsite_run.py'),
                    ('offsite_prepare.py', 'offsite_backup.py'),
                    ('offsite_run.py', 'offsite_backup.py'),
                    ('offsite_repository.py', 'offsite_snapshot.py'),
                    ('offsite_snapshot.py', 'offsite_locks.py'),
                    ('offsite_locks.py', 'offsite_backup.py'),
                    ('offsite_snapshot.py', 'offsite_backup.sh'),
                    ('offsite_mounts.sh', 'offsite_backup.sh'),
                    ('offsite_backup.py', 'offsite_backup.sh')]:
                names = [Path(name).name for name in sources]
                self.assertLess(names.index(dependency), names.index(consumer))
            for source in sources:
                self.assertTrue((HELPER.parent.parent / source).is_file(), source)
            for name in ('mount_disks.sh', 'umount_disks.sh', 'internet_switches.sh'):
                row = next(row for row in records if row[0] == f'pi/scripts/{name}')
                self.assertEqual(row[4:], ['preserve', 'required'])
        self.assertNotEqual(self.shell('local_backup_plan arbitrary').returncode, 0)


    def test_every_reviewed_hash_uses_previous_revision_including_tmpfiles(self):
        repo, previous = self.fixture_git()
        self.env['VANPI_BACKUP_PREVIOUS_REVISION'] = previous
        for profile in ('pi', 'tm'):
            hashes = self.predecessors(repo, profile)
            for source, _destination, policy, *_rest in self.plan(profile):
                expected = digest(f'first {source}\n'.encode()) if policy in POLICIES_WITH_HASHES else 'managed'
                self.assertEqual(hashes[source], expected)
        self.env['VANPI_BACKUP_PREVIOUS_REVISION'] = 'does-not-exist'
        self.assertNotEqual(self.shell('local_backup_previous_hashes "$1" tm', repo).returncode, 0)


    def test_reviewed_hash_overrides_remain_explicit_and_validated(self):
        repo, previous = self.fixture_git()
        self.env['VANPI_BACKUP_PREVIOUS_REVISION'] = previous
        overrides = {'VANPI_BACKUP_PREVIOUS_CONF_SHA256': 'backup_conf.sh',
                     'VANPI_TM_PREVIOUS_CLOUD_SHA256': 'icloud_backup.py',
                     'VANPI_TM_PREVIOUS_STATUS_SHA256': 'icloud_status.py'}
        for variable, name in overrides.items():
            self.env[variable] = digest(name.encode())
        hashes = self.predecessors(repo)
        for variable, name in overrides.items():
            self.assertEqual(hashes['pi/scripts/backup/' + name], self.env[variable])
        self.env['VANPI_TM_PREVIOUS_STATUS_SHA256'] = 'not-a-reviewed-hash'
        self.assertNotEqual(self.shell('local_backup_previous_hashes "$1" tm', repo).returncode, 0)


    def test_previous_tree_absence_and_no_lifecycle_first_install(self):
        missing = 'pi/scripts/backup/backup_deploy_transaction.sh'
        repo, previous = self.fixture_git((missing,))
        self.env['VANPI_BACKUP_PREVIOUS_REVISION'] = previous
        self.assertEqual(self.predecessors(repo)[missing], 'absent')
        subprocess.run(['git', '-C', str(repo), 'rm', '-q', 'pi/scripts/mount_disks.sh'], check=True)
        self.git_commit(repo, 'missing lifecycle')
        self.env['VANPI_BACKUP_PREVIOUS_REVISION'] = 'HEAD'
        self.assertNotEqual(self.shell('local_backup_previous_hashes "$1" tm', repo).returncode, 0)




@unittest.skipUnless(sys.platform.startswith('linux'), 'transaction requires Linux flock and coreutils')
class DeploymentPreflightTests(DeploymentFixture):
    def test_every_reviewed_file_rejects_drift_before_live_mutation(self):
        fixture = self.fixture()
        root, stage, live, records, _previous, snapshots = fixture
        self.assertEqual(self.preflight(fixture).returncode, 0)
        for source, destination, policy, *_ in records:
            if policy not in POLICIES_WITH_HASHES:
                continue
            with self.subTest(source=source):
                path = live / destination.lstrip('/')
                path.write_text('unreviewed edit\n')
                self.assertNotEqual(self.preflight(fixture).returncode, 0)
                path.write_bytes(snapshots[destination][0])
        self.assertFalse((root / 'events').exists())
        self.assertFalse((stage / 'previous').exists())


    def test_absent_predecessor_accepts_missing_or_exact_staged_only(self):
        fixture = self.fixture()
        _root, stage, live, _records, previous, _snapshots = fixture
        source = 'pi/scripts/backup/backup_deploy_transaction.sh'
        path = live / 'home/pi/scripts/backup/backup_deploy_transaction.sh'
        previous[source] = 'absent'
        path.unlink()
        self.assertEqual(self.preflight(fixture).returncode, 0)
        path.write_bytes((stage / source).read_bytes()); path.chmod(0o750)
        self.assertEqual(self.preflight(fixture).returncode, 0)
        path.write_text('unexpected\n')
        self.assertNotEqual(self.preflight(fixture).returncode, 0)
        path.unlink(); path.symlink_to(self.root / 'absent')
        self.assertNotEqual(self.preflight(fixture).returncode, 0)


    def test_lifecycle_missing_or_wrong_owner_fails_closed(self):
        fixture = self.fixture()
        _root, _stage, live, _records, _previous, _snapshots = fixture
        self.env['LOCAL_BACKUP_PI_OWNER'] = 'nobody:nogroup'
        self.assertNotEqual(self.preflight(fixture).returncode, 0)
        self.env['LOCAL_BACKUP_PI_OWNER'] = f'{self.env["VANPI_BACKUP_JOB_LOCK_OWNER"]}:{self.env["VANPI_BACKUP_JOB_LOCK_GROUP"]}'
        (live / 'home/pi/scripts/mount_disks.sh').unlink()
        self.assertNotEqual(self.preflight(fixture).returncode, 0)


    def test_both_profiles_publish_complete_private_backup_and_preserve_settings(self):
        for profile in ('tm', 'pi'):
            fixture = self.fixture(profile)
            root, stage, live, records, _previous, snapshots = fixture
            result = self.apply(fixture, profile)
            self.assertEqual(result.returncode, 0, result.stderr)
            manifest = (stage / 'previous/manifest').read_text().splitlines()
            self.assertEqual(len(manifest), len(records))
            self.assertEqual((stage / 'previous').stat().st_mode & 0o777, 0o700)
            for source, destination, policy, *_ in records:
                saved = stage / 'previous/tree' / destination.lstrip('/')
                self.assertEqual((saved.read_bytes(), saved.stat().st_mode & 0o7777), snapshots[destination])
                actual = (live / destination.lstrip('/')).read_bytes()
                if policy == 'preserve':
                    self.assertEqual(actual, snapshots[destination][0])
                elif policy == 'defaults':
                    import json
                    self.assertEqual(json.loads(actual), {'setting': 1, 'new_default': 2})
                else:
                    self.assertEqual(actual, (stage / source).read_bytes())
            self.assertNotIn('rollback:', (root / 'events').read_text())


@unittest.skipUnless(sys.platform.startswith('linux'), 'transaction requires Linux flock and coreutils')
class DeploymentRollbackTests(DeploymentFixture):
    def test_pi_only_publications_restore_settings_and_units(self):
        # Shared destinations are exercised by the TM rollback cases below.
        # Cover the Pi profile's additional config/defaults and unit records too.
        tm_sources = {row[0] for row in self.plan('tm')}
        for step, row in enumerate(self.plan('pi'), 1):
            if row[0] in tm_sources:
                continue
            with self.subTest(source=row[0]):
                fixture = self.fixture('pi')
                self.env.update(TEST_FAIL_STEP=str(step), TEST_FAIL_WHEN='after')
                result = self.apply(fixture, 'pi')
                self.assertNotEqual(result.returncode, 0)
                self.assert_restored(fixture)

    def test_partial_and_complete_publication_failures_restore_previous_cohort(self):
        count = sum(row[2] != 'preserve' for row in self.plan())
        for step in (1, count):
            with self.subTest(step=step):
                fixture = self.fixture()
                self.env.update(TEST_FAIL_STEP=str(step), TEST_FAIL_WHEN='after')
                result = self.apply(fixture)
                self.assertNotEqual(result.returncode, 0)
                self.assert_restored(fixture)


    def test_failure_before_rename_and_termination_also_restore(self):
        for when in ('before', 'signal'):
            fixture = self.fixture()
            self.env.update(TEST_FAIL_STEP='3', TEST_FAIL_WHEN=when)
            result = self.apply(fixture)
            self.assertNotEqual(result.returncode, 0)
            self.assert_restored(fixture)

    def test_repeated_termination_cannot_interrupt_fenced_rollback(self):
        for signal in ('TERM', 'HUP', 'INT'):
            with self.subTest(signal=signal):
                fixture = self.fixture()
                self.env.update(TEST_FAIL_STEP='3', TEST_FAIL_WHEN='signal',
                                TEST_ROLLBACK_SIGNAL=signal)
                result = self.apply(fixture)
                self.assertEqual(result.returncode, 143, result.stderr)
                self.assertTrue((fixture[0] / 'cleanup-signal').is_file())
                self.assert_restored(fixture)

    def test_repeated_termination_preserves_incomplete_rollback_result(self):
        fixture = self.fixture()
        self.env.update(TEST_FAIL_STEP='3', TEST_FAIL_WHEN='signal',
                        TEST_ROLLBACK_SIGNAL='TERM', TEST_FAIL_ROLLBACK='1')
        result = self.apply(fixture)
        self.assertEqual(result.returncode, 70, result.stderr)
        self.assertTrue((fixture[0] / 'cleanup-signal').is_file())
        self.assertNotIn('systemctl:start ', (fixture[0] / 'events').read_text())


    def test_incomplete_restore_or_reload_never_restarts_timers(self):
        for failure in ('TEST_FAIL_ROLLBACK', 'TEST_FAIL_RELOAD'):
            fixture = self.fixture()
            self.env.update(TEST_FAIL_STEP='3', TEST_FAIL_WHEN='after')
            self.env[failure] = '1'
            result = self.apply(fixture)
            self.assertEqual(result.returncode, 70, result.stderr)
            self.assertNotIn('systemctl:start ', (fixture[0] / 'events').read_text())
            self.env.pop(failure)


    def test_new_file_is_removed_on_rollback(self):
        fixture = self.fixture()
        _root, _stage, live, _records, previous, snapshots = fixture
        source, destination = 'pi/scripts/backup/backup_lock_io.sh', '/home/pi/scripts/backup/backup_lock_io.sh'
        (live / destination.lstrip('/')).unlink()
        previous[source] = 'absent'; snapshots[destination] = None
        self.env['TEST_FAIL_STEP'] = '2'
        result = self.apply(fixture)
        self.assertNotEqual(result.returncode, 0)
        self.assert_restored(fixture)


    def test_settings_edit_after_preflight_is_preserved_and_reported(self):
        fixture = self.fixture('pi')
        root, stage, live, _records, previous, _snapshots = fixture
        path = live / 'etc/vanpi-icloud-backup.json'
        result = self.shell('local_backup_begin "$1" pi "$2"; printf "{\\"external\\": true}\\n" > "$3"; local_backup_publish; local_backup_commit',
                            stage, self.hashes(previous), path, timeout=180)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(path.read_text(), '{"external": true}\n')
        self.assertEqual((root / 'events').read_text().count('systemctl:start '), 4)


    def test_postcommit_activation_error_keeps_coherent_new_cohort(self):
        fixture = self.fixture()
        result = self.apply(fixture, tail='local_backup_commit; exit 45')
        self.assertEqual(result.returncode, 45)
        self.assertNotIn('rollback:', (fixture[0] / 'events').read_text())


@unittest.skipUnless(sys.platform.startswith('linux') and Path('/proc/locks').is_file(),
                     'the registry guard reads Linux /proc/locks')
class RegistryShrinkGuardTests(DeploymentFixture):
    def hold(self, name, exclusive=True):
        fd = os.open(Path(self.env['VANPI_BACKUP_LOCK_DIR']) / f'{name}.lock', os.O_RDONLY)
        self.addCleanup(os.close, fd)
        fcntl.flock(fd, (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB)

    @staticmethod
    def stage_registry(stage, edit):
        path = stage / REGISTRY_SOURCE
        text = path.read_text()
        changed = edit(text)
        assert changed != text
        path.write_text(changed)

    def assert_refused_before_mutation(self, fixture, result, message):
        root, stage, live, _records, _previous, snapshots = fixture
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(message, result.stderr)
        events = (root / 'events').read_text() if (root / 'events').exists() else ''
        self.assertNotIn('publish:', events)
        self.assertNotIn('systemctl:stop', events)
        self.assertFalse((stage / 'previous').exists())
        for destination, (old, _mode) in snapshots.items():
            self.assertEqual((live / destination.lstrip('/')).read_bytes(), old, destination)

    def test_refuses_a_staged_registry_without_any_held_lock_class(self):
        cases = [('job-offsite', True, lambda text: text.replace(' offsite)', ')')),
                 ('disk-FORT-MPLS-5TB', False, lambda text: text.replace(' FORT-MPLS-5TB', '')),
                 ('data-borg-repo', True, lambda text: text.replace('(borg-repo)', '()')),
                 ('cloud-upload', True, lambda text: re.sub(r'(?m)^BACKUP_CLOUD_SLOT=.*$', 'BACKUP_CLOUD_SLOT=', text))]
        for name, exclusive, edit in cases:
            with self.subTest(lock=name):
                fixture = self.fixture()
                self.stage_registry(fixture[1], edit)
                self.hold(name, exclusive)
                result = self.apply(fixture)
                self.assert_refused_before_mutation(fixture, result, f'without {name} while it is held')

    def test_publishes_during_a_run_that_holds_only_resource_locks(self):
        # An offsite run's locks, but not the legacy lock the fence takes.
        fixture = self.fixture()
        for name, exclusive in (('job-offsite', True), ('disk-FORT-MPLS-5TB', False), ('cloud-upload', True)):
            self.hold(name, exclusive)
        # Holders are found without opening lock files: none is readable now.
        for path in Path(self.env['VANPI_BACKUP_LOCK_DIR']).iterdir():
            path.chmod(0)
        result = self.apply(fixture)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_unheld_entries_may_go_and_unsafe_inputs_fail_closed(self):
        fixture = self.fixture()
        self.stage_registry(fixture[1], lambda text: text.replace(' offsite)', ')'))
        result = self.apply(fixture)
        self.assertEqual(result.returncode, 0, result.stderr)
        for change, message in (('proc', 'Cannot read'), ('registry', 'Cannot read the staged lock registry'),
                                ('symlink', 'Backup lock directory is unsafe')):
            with self.subTest(change=change):
                fixture = self.fixture()
                if change == 'proc':
                    self.env['LOCAL_BACKUP_PROC_LOCKS'] = str(self.root / 'missing-locks')
                elif change == 'registry':
                    (fixture[1] / REGISTRY_SOURCE).write_text('exit 1\n')
                else:
                    registry = Path(self.env['VANPI_BACKUP_LOCK_DIR'])
                    registry.rename(registry.with_name('saved-registry'))
                    registry.symlink_to(registry.with_name('saved-registry'))
                result = self.apply(fixture)
                self.env.pop('LOCAL_BACKUP_PROC_LOCKS', None)
                self.assert_refused_before_mutation(fixture, result, message)
        fixture = self.fixture()
        shutil.rmtree(self.env['VANPI_BACKUP_LOCK_DIR'])
        result = self.apply(fixture)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
