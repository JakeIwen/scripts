"""Public deployment contracts against fixture Git repos and real Linux files."""
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

HELPER = Path(__file__).resolve().parents[2] / 'backup_local_deploy.sh'
POLICIES_WITH_HASHES = {'revision', 'conf', 'cloud', 'status'}


def digest(data):
    return hashlib.sha256(data).hexdigest()


class DeploymentTestCase(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.env = dict(os.environ)
        for key in tuple(self.env):
            if key.startswith(('VANPI_BACKUP_', 'VANPI_TM_PREVIOUS_', 'LOCAL_BACKUP_')) or key in {
                    'BASH_ENV', 'ENV', 'SHELLOPTS', 'BASHOPTS'}:
                self.env.pop(key, None)


    def shell(self, code, *args, timeout=30):
        return subprocess.run(['/bin/bash', '-c', 'set -euo pipefail; source "$1"; shift; ' + code,
                               'test', str(HELPER), *map(str, args)], env=self.env,
                              capture_output=True, text=True, timeout=timeout)


    def plan(self, profile='tm'):
        result = self.shell('local_backup_plan "$1"', profile)
        self.assertEqual(result.returncode, 0, result.stderr)
        return [row.split('|') for row in result.stdout.splitlines()]


    def fixture_git(self, missing=()):
        repo = self.root / 'repo'
        repo.mkdir()
        sources = {row[0] for profile in ('pi', 'tm') for row in self.plan(profile)}
        for name in sources - set(missing):
            path = repo / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f'first {name}\n')
        subprocess.run(['git', 'init', '-q', str(repo)], check=True)
        self.git_commit(repo, 'first')
        first = subprocess.check_output(['git', '-C', str(repo), 'rev-parse', 'HEAD'], text=True).strip()
        for name in sources:
            path = repo / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f'second {name}\n')
        self.git_commit(repo, 'second')
        return repo, first


    def git_commit(self, repo, message):
        subprocess.run(['git', '-C', str(repo), 'add', '.'], check=True)
        subprocess.run(['git', '-C', str(repo), '-c', 'core.hooksPath=/dev/null',
                        '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid',
                        'commit', '-qm', message], check=True)


    def predecessors(self, repo, profile='tm'):
        result = self.shell('local_backup_previous_hashes "$1" "$2"', repo, profile)
        self.assertEqual(result.returncode, 0, result.stderr)
        return dict(item.split(':') for item in result.stdout.rstrip(',').split(','))


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
                    ('stale_mount_recovery.sh', 'mount_disks.sh')]:
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




# The shim delegates actual filesystem commands. Failures can occur before or
# after rename, or on restoration; a fresh fd proves cleanup still owns the fence.
SUDO_SHIM = r'''#!/bin/bash
set -eu
command=$1
shift
last=${!#}
if [[ "$command" == /usr/bin/mv ]]; then
  source_path=${@: -2:1}
  if [[ "$source_path" == *.deploy.* || "$source_path" == *.rollback.* ]]; then
    exec 7<"$VANPI_BACKUP_JOB_LOCK"
    if /usr/bin/flock -n 7; then echo 'mutation without fence' >&2; exit 97; fi
    exec 7<&-
  fi
  if [[ "$source_path" == *.deploy.* ]]; then
    count=0; [[ ! -e "$TEST_ROOT/count" ]] || read -r count < "$TEST_ROOT/count"
    count=$((count+1)); echo "$count" > "$TEST_ROOT/count"
    if (( count == 1 )); then
      [[ $(wc -l < "$TEST_ROOT/stage/previous/manifest") == "$TEST_EXPECT_RECORDS" ]] || exit 95
      while IFS='|' read -r src dest old old_owner old_mode new owner mode action temp; do
        [[ "$action" == keep || -f "$temp" ]] || exit 96
      done < "$TEST_ROOT/stage/previous/manifest"
    fi
    if [[ "$count" == "${TEST_FAIL_STEP:-0}" && "${TEST_FAIL_WHEN:-after}" == before ]]; then
      : > "$TEST_ROOT/failed"; exit 41
    fi
    "$command" "$@"
    echo "publish:$last" >> "$TEST_ROOT/events"
    if [[ "$count" == "${TEST_FAIL_STEP:-0}" ]]; then
      : > "$TEST_ROOT/failed"
      if [[ "${TEST_FAIL_WHEN:-after}" == signal ]]; then kill -TERM "$PPID"; fi
      exit 42
    fi
    exit 0
  elif [[ "$source_path" == *.rollback.* ]]; then
    echo "rollback:$last" >> "$TEST_ROOT/events"
    if [[ -n "${TEST_ROLLBACK_SIGNAL:-}" && ! -e "$TEST_ROOT/cleanup-signal" ]]; then
      : > "$TEST_ROOT/cleanup-signal"
      kill -"$TEST_ROLLBACK_SIGNAL" "$PPID"
      /bin/sleep 0.05
    fi
    [[ "${TEST_FAIL_ROLLBACK:-0}" != 1 ]] || exit 43
  fi
fi
exec "$command" "$@"
'''

SYSTEMCTL_SHIM = r'''#!/bin/bash
set -eu
if [[ "$1" == show ]]; then
  case "$2" in *.timer) echo active ;; *) echo inactive ;; esac
  exit 0
fi
echo "systemctl:$*" >> "$TEST_ROOT/events"
if [[ "$1" == daemon-reload && "${TEST_FAIL_RELOAD:-0}" == 1 && -e "$TEST_ROOT/failed" ]]; then exit 44; fi
exit 0
'''


class DeploymentFixture(DeploymentTestCase):
    def fixture(self, profile='tm'):
        root = Path(tempfile.mkdtemp(dir=self.root))
        stage, live = root / 'stage', root / 'live'
        stage.mkdir(); live.mkdir()
        records = self.plan(profile)
        previous, snapshots = {}, {}
        for source, destination, policy, _owner, mode, _first in records:
            staged, deployed = stage / source, live / destination.lstrip('/')
            staged.parent.mkdir(parents=True, exist_ok=True)
            deployed.parent.mkdir(parents=True, exist_ok=True)
            old, new = f'old {source}\n'.encode(), f'new {source}\n'.encode()
            if policy in ('preserve', 'defaults'):
                old, new = b'{"setting": 1}\n', b'{"setting": 0, "new_default": 2}\n'
            staged.write_bytes(new); deployed.write_bytes(old)
            deployed.chmod(0o770 if mode == 'preserve' else int(mode, 8))
            previous[source] = digest(old) if policy in POLICIES_WITH_HASHES else 'managed'
            snapshots[destination] = (old, deployed.stat().st_mode & 0o7777)
        owner = subprocess.check_output(['/usr/bin/id', '-un'], text=True).strip()
        group = subprocess.check_output(['/usr/bin/id', '-gn'], text=True).strip()
        for name, content in [('sudo', SUDO_SHIM), ('systemctl', SYSTEMCTL_SHIM)]:
            path = root / name
            path.write_text(content); path.chmod(0o700)
        lock = root / 'legacy'
        lock.touch(); lock.chmod(0o660)
        self.env.update(TEST_ROOT=str(root), TEST_EXPECT_RECORDS=str(len(records)), LOCAL_BACKUP_ROOT=str(live),
                        LOCAL_BACKUP_SUDO=str(root / 'sudo'), LOCAL_BACKUP_SYSTEMCTL=str(root / 'systemctl'),
                        LOCAL_BACKUP_PI_OWNER=f'{owner}:{group}', LOCAL_BACKUP_ROOT_OWNER=f'{owner}:{group}',
                        VANPI_BACKUP_JOB_LOCK=str(lock), VANPI_BACKUP_JOB_LOCK_OWNER=owner,
                        VANPI_BACKUP_JOB_LOCK_GROUP=group, VANPI_BACKUP_JOB_LOCK_MODE='660')
        return root, stage, live, records, previous, snapshots


    @staticmethod
    def hashes(previous):
        return ','.join(f'{name}:{value}' for name, value in previous.items())


    def preflight(self, fixture):
        _root, stage, _live, _records, previous, _snapshots = fixture
        shutil.rmtree(stage / 'transaction', ignore_errors=True)
        return self.shell('local_backup_preflight "$1" tm "$2"', stage, self.hashes(previous))


    def apply(self, fixture, profile='tm', tail='local_backup_commit'):
        _root, stage, _live, _records, previous, _snapshots = fixture
        return self.shell('local_backup_begin "$1" "$2" "$3"; local_backup_publish; ' + tail,
                          stage, profile, self.hashes(previous), timeout=180)


    def assert_restored(self, fixture):
        root, stage, live, records, _previous, snapshots = fixture
        self.assertEqual(len((stage / 'previous/manifest').read_text().splitlines()), len(records))
        for destination, original in snapshots.items():
            path = live / destination.lstrip('/')
            if original is None:
                self.assertFalse(path.exists(), destination)
            else:
                self.assertEqual((path.read_bytes(), path.stat().st_mode & 0o7777), original, destination)
                saved = stage / 'previous/tree' / destination.lstrip('/')
                self.assertEqual((saved.read_bytes(), saved.stat().st_mode & 0o7777), original, destination)
        self.assertFalse(list(live.rglob('*.deploy.*')))
        self.assertFalse(list(live.rglob('*.rollback.*')))
        events = (root / 'events').read_text()
        if 'publish:' in events:
            self.assertLess(events.index('systemctl:daemon-reload'), events.index('systemctl:start '))
        self.assertEqual(events.count('systemctl:start '), 4)


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
        # Shared destinations are exercised at every TM publication below.
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
                self.assertIn('previous cohort restored and verified', result.stderr)
                self.assert_restored(fixture)

    def test_failure_after_every_publication_restores_complete_previous_cohort(self):
        count = sum(row[2] != 'preserve' for row in self.plan())
        for step in range(1, count + 1):
            with self.subTest(step=step):
                fixture = self.fixture()
                self.env.update(TEST_FAIL_STEP=str(step), TEST_FAIL_WHEN='after')
                result = self.apply(fixture)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('previous cohort restored and verified', result.stderr)
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
                self.assertIn('previous cohort restored and verified', result.stderr)
                self.assertTrue((fixture[0] / 'cleanup-signal').is_file())
                self.assert_restored(fixture)

    def test_repeated_termination_preserves_incomplete_rollback_result(self):
        fixture = self.fixture()
        self.env.update(TEST_FAIL_STEP='3', TEST_FAIL_WHEN='signal',
                        TEST_ROLLBACK_SIGNAL='TERM', TEST_FAIL_ROLLBACK='1')
        result = self.apply(fixture)
        self.assertEqual(result.returncode, 70, result.stderr)
        self.assertIn('ROLLBACK INCOMPLETE', result.stderr)
        self.assertTrue((fixture[0] / 'cleanup-signal').is_file())
        self.assertNotIn('systemctl:start ', (fixture[0] / 'events').read_text())


    def test_incomplete_restore_or_reload_never_restarts_timers(self):
        for failure in ('TEST_FAIL_ROLLBACK', 'TEST_FAIL_RELOAD'):
            fixture = self.fixture()
            self.env.update(TEST_FAIL_STEP='3', TEST_FAIL_WHEN='after')
            self.env[failure] = '1'
            result = self.apply(fixture)
            self.assertEqual(result.returncode, 70, result.stderr)
            self.assertIn('ROLLBACK INCOMPLETE', result.stderr)
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
        self.assertIn('previous cohort restored and verified', result.stderr)
        self.assert_restored(fixture)


    def test_settings_edit_after_preflight_is_preserved_and_reported(self):
        fixture = self.fixture('pi')
        root, stage, live, _records, previous, _snapshots = fixture
        path = live / 'etc/vanpi-icloud-backup.json'
        result = self.shell('local_backup_begin "$1" pi "$2"; printf "{\\"external\\": true}\\n" > "$3"; local_backup_publish; local_backup_commit',
                            stage, self.hashes(previous), path, timeout=180)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Live file changed after preflight', result.stderr)
        self.assertEqual(path.read_text(), '{"external": true}\n')
        self.assertEqual((root / 'events').read_text().count('systemctl:start '), 4)


    def test_postcommit_activation_error_keeps_coherent_new_cohort(self):
        fixture = self.fixture()
        result = self.apply(fixture, tail='local_backup_commit; exit 45')
        self.assertEqual(result.returncode, 45)
        self.assertIn('Cohort committed; activation incomplete', result.stderr)
        self.assertNotIn('rollback:', (fixture[0] / 'events').read_text())


if __name__ == '__main__':
    unittest.main()
