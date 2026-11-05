"""Deployment fixtures: a fixture Git repo, stage and live trees, sudo/systemctl shims.

The installers run some of these tests on the Pi, so every fixture keeps its
locks, registry, deployment state and live tree under its own temporary root.
"""
import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

import backup_locks
import backup_bootstrap_plan
from pi.tests.backup.bootstrap_fixtures import seed_bootstrap

HELPER = Path(__file__).resolve().parents[2] / 'backup_local_deploy.sh'
POLICIES_WITH_HASHES = {'revision', 'conf', 'cloud', 'status'}
REGISTRY_SOURCE = 'pi/scripts/backup/backup_lock_registry.sh'
TRANSACTION_ID = re.compile(r'[0-9]{8}T[0-9]{6}Z-[1-9][0-9]*')


def digest(data):
    return hashlib.sha256(data).hexdigest()


class DeploymentTestCase(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.env = dict(os.environ)
        for key in tuple(self.env):
            if key.startswith(('VANPI_BACKUP_', 'VANPI_BOOTSTRAP_', 'VANPI_TM_PREVIOUS_', 'LOCAL_BACKUP_')) or key in {
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


# The shim delegates actual filesystem commands. Failures can occur before or
# after rename, or on restoration; a fresh fd proves cleanup still owns the fence.
# TEST_KILL_STEP / TEST_KILL_PREPARE SIGKILL the installer (no trap runs) after
# that many publications or prepared temporaries; TEST_BLOCK_STEP stalls it in a
# foreground command so a TERM stays pending; TEST_READY_STEP marks progress.
SUDO_SHIM = r'''#!/bin/bash
set -eu
command=$1
shift
last=${!#}
if [[ "$command" == /usr/bin/install && "$last" == *.deploy.* ]]; then
  "$command" "$@"
  prepared=0; [[ ! -e "$TEST_ROOT/prepared" ]] || read -r prepared < "$TEST_ROOT/prepared"
  prepared=$((prepared+1)); echo "$prepared" > "$TEST_ROOT/prepared"
  if [[ "$prepared" == "${TEST_KILL_PREPARE:-0}" ]]; then kill -KILL "$PPID"; fi
  exit 0
fi
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
      # Nothing is published before a complete journal and a publishing marker.
      marker="$VANPI_BACKUP_DEPLOY_STATE_DIR/pending"
      grep -qx 'phase=publishing' "$marker" || exit 94
      manifest="$VANPI_BACKUP_DEPLOY_STATE_DIR/$(sed -n 's/^id=//p' "$marker")/previous/manifest"
      [[ $(wc -l < "$manifest") == "$TEST_EXPECT_RECORDS" ]] || exit 95
      while IFS='|' read -r src dest old old_owner old_mode new owner mode action temp; do
        [[ "$action" == keep || -f "$temp" ]] || exit 96
      done < "$manifest"
    fi
    [[ -z "${TEST_PUBLISH_DELAY:-}" ]] || /bin/sleep "$TEST_PUBLISH_DELAY"
    if [[ "$count" == "${TEST_FAIL_STEP:-0}" && "${TEST_FAIL_WHEN:-after}" == before ]]; then
      : > "$TEST_ROOT/failed"; exit 41
    fi
    "$command" "$@"
    echo "publish:$last" >> "$TEST_ROOT/events"
    [[ "$count" != "${TEST_READY_STEP:-0}" ]] || : > "$TEST_ROOT/ready"
    if [[ "$count" == "${TEST_KILL_STEP:-0}" ]]; then kill -KILL "$PPID"; exit 0; fi
    if [[ "$count" == "${TEST_BLOCK_STEP:-0}" ]]; then : > "$TEST_ROOT/ready"; /bin/sleep 60; fi
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
    if [[ -n "${TEST_KILL_ROLLBACK_SUFFIX:-}" && "$last" == *"$TEST_KILL_ROLLBACK_SUFFIX" ]]; then
      "$command" "$@"
      kill -KILL "$PPID"
      exit 0
    fi
  fi
fi
exec "$command" "$@"
'''

SYSTEMCTL_SHIM = r'''#!/bin/bash
set -eu
if [[ "$1" == show ]]; then
  # TEST_ACTIVE_TIMERS lists the active timers; by default every timer is.
  case " ${TEST_ACTIVE_TIMERS:-$2} " in
    *" $2 "*) [[ "$2" == *.timer ]] && echo active || echo inactive ;;
    *) echo inactive ;;
  esac
  exit 0
fi
echo "systemctl:$*" >> "$TEST_ROOT/events"
if [[ "$1" == start && "${TEST_KILL_TIMER_START:-0}" == 1 ]]; then kill -KILL "$PPID"; fi
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
            elif Path(source).name in backup_bootstrap_plan.GUARDS:
                new = (HELPER.parent.parent / source).read_bytes()
                old = new + b'\n# fixture predecessor\n'
            elif source == REGISTRY_SOURCE:
                # The fence reads the staged registry; keep it real.
                new = (HELPER.parent.parent / source).read_bytes()
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
        registry = root / 'registry'
        registry.mkdir(); registry.chmod(0o770)
        for lock_class, names in backup_locks.LOCK_NAMES.items():
            for name in names:
                path = registry / f'{lock_class.value}-{name}.lock'
                path.touch(); path.chmod(0o660)
        notify = root / 'notify'
        notify.write_text('#!/bin/bash\nprintf "%s|%s|%s\\n" "$NTFY_TOPIC_VAR" "$1" "$3" >> "$TEST_ROOT/alerts"\n')
        notify.chmod(0o700)
        self.env.update(VANPI_BACKUP_DEPLOY_STATE_DIR=str(root / 'deploy-state'),
                        VANPI_BACKUP_DEPLOY_STATE_OWNER=f'{owner}:{group}',
                        VANPI_BACKUP_DEPLOY_NOTIFY=str(notify))
        self.env.update(TEST_ROOT=str(root), TEST_EXPECT_RECORDS=str(len(records)), LOCAL_BACKUP_ROOT=str(live),
                        LOCAL_BACKUP_SUDO=str(root / 'sudo'), LOCAL_BACKUP_SYSTEMCTL=str(root / 'systemctl'),
                        LOCAL_BACKUP_PI_OWNER=f'{owner}:{group}', LOCAL_BACKUP_ROOT_OWNER=f'{owner}:{group}',
                        VANPI_BACKUP_JOB_LOCK=str(lock), VANPI_BACKUP_JOB_LOCK_OWNER=owner,
                        VANPI_BACKUP_JOB_LOCK_GROUP=group, VANPI_BACKUP_JOB_LOCK_MODE='660',
                        VANPI_BACKUP_LOCK_DIR=str(registry), VANPI_BACKUP_LOCK_DIR_MODE='770')
        seed_bootstrap(root, live, self.env)
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


    @staticmethod
    def state_dir(fixture):
        return fixture[0] / 'deploy-state'


    def transaction(self, fixture):
        """The newest journaled transaction directory of this fixture."""
        records = sorted(path for path in self.state_dir(fixture).iterdir()
                         if path.is_dir() and TRANSACTION_ID.fullmatch(path.name))
        self.assertTrue(records, 'no deployment transaction was journaled')
        return records[-1]


    def assert_restored(self, fixture, active=4, journaled=True):
        """Live files, owners and modes as before; journaled=False for a run killed while preparing."""
        root, _stage, live, records, _previous, snapshots = fixture
        transaction = self.transaction(fixture)
        if journaled:
            self.assertEqual(len((transaction / 'previous/manifest').read_text().splitlines()), len(records))
        for destination, original in snapshots.items():
            path = live / destination.lstrip('/')
            if original is None:
                self.assertFalse(path.exists(), destination)
                continue
            info = path.stat()
            self.assertEqual((path.read_bytes(), info.st_mode & 0o7777), original, destination)
            self.assertEqual((info.st_uid, info.st_gid), (os.getuid(), os.getgid()), destination)
            if journaled:
                saved = transaction / 'previous/tree' / destination.lstrip('/')
                self.assertEqual((saved.read_bytes(), saved.stat().st_mode & 0o7777), original, destination)
        self.assertFalse(list(live.rglob('*.deploy.*')))
        self.assertFalse(list(live.rglob('*.rollback.*')))
        self.assertFalse((self.state_dir(fixture) / 'pending').exists(), 'restored cohort kept its marker')
        events = (root / 'events').read_text()
        if 'publish:' in events:
            self.assertLess(events.index('systemctl:daemon-reload'), events.index('systemctl:start '))
        self.assertEqual(events.count('systemctl:start '), active)
