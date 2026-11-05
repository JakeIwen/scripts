"""Real predecessor and command-boundary fixtures for bootstrap/recovery tests."""
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile

import backup_bootstrap_host as host
import backup_bootstrap_plan as plan
from pi.tests.backup.bootstrap_fixtures import BACKUP
from pi.tests.backup.deploy_fixtures import digest, POLICIES_WITH_HASHES

REPO = BACKUP.parents[2]
PREDECESSOR = 'fdf473d88a50d7cd08abbc456303513034c66398'

# No fake filesystem writes: the adapters replace only the external scheduler.
CRONTAB = '''#!/usr/bin/env python3
import os, pathlib, sys
user = sys.argv[sys.argv.index('-u') + 1]
p = pathlib.Path(os.environ['BOOTSTRAP_TEST_ROOT']) / ('cron-' + user)
if sys.argv[-1] == '-l':
    if not p.exists():
        print('no crontab for ' + user, file=sys.stderr); raise SystemExit(1)
    print(p.read_text(), end='')
else:
    data = sys.stdin.read()
    p.write_text(data)
    if os.environ.get('KILL_CRON_RESTORE') == user and '# vanpi-deploy-bootstrap-held ' not in data:
        os.kill(os.getppid(), 9)
'''
BUSCTL = '''#!/usr/bin/env python3
import json, os, sys
if 'get-property' in sys.argv:
    row = ['ConditionPathExists', False, True, os.environ['VANPI_BACKUP_DEPLOY_STATE_DIR'] + '/maintenance', 0]
    print(json.dumps({'type': 'a(sbbsi)', 'data': [] if os.environ.get('TEST_MISSING_CONDITION') else [row]}))
else:
    print(json.dumps({'type': 'o', 'data': ['/org/freedesktop/systemd1/unit/fixture']}))
'''
SYSTEMCTL = '''#!/bin/bash
if [[ "$1" == show ]]; then printf 'no\\n'; exit 0; fi
printf '%s\\n' "$*" >> "$BOOTSTRAP_TEST_ROOT/systemctl-events"
'''

# Call the real primitive, then kill the process. This exposes interruptions at
# publication boundaries without substituting transaction/locking semantics.
FAULT_DRIVER = '''import os, runpy, sys
original_replace = os.replace
original_fsync = os.fsync
def replace(source, target):
    original_replace(source, target)
    needle = os.environ.get('KILL_REPLACE_SUFFIX')
    if needle and str(target).endswith(needle):
        os.kill(os.getpid(), 9)
def fsync(fd):
    needle = os.environ.get('FAIL_FSYNC_SUFFIX')
    if needle and os.readlink('/proc/self/fd/' + str(fd)).endswith(needle):
        raise OSError('injected durability failure')
    return original_fsync(fd)
os.replace, os.fsync = replace, fsync
sys.argv = sys.argv[1:]
runpy.run_path(sys.argv[0], run_name='__main__')
'''


def predecessor_files():
    raw = subprocess.check_output(['git', '-C', str(REPO), 'archive', PREDECESSOR, 'pi'])
    with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
        return {member.name: archive.extractfile(member).read() for member in archive if member.isfile()}


def real_predecessor(case, fixture, predecessor):
    """Replace placeholder cohort bytes by actual predecessor/current source bytes."""
    root, stage, live, records, previous, snapshots = fixture
    for source, destination, policy, owner, mode, first in records:
        target = live / destination.lstrip('/')
        old = predecessor.get(source)
        new = (REPO / source).read_bytes()
        (stage / source).write_bytes(new)
        if old is None:
            target.unlink(missing_ok=True)
            snapshots[destination] = None
        else:
            target.write_bytes(old)
            snapshots[destination] = (old, target.stat().st_mode & 0o7777)
        previous[source] = (digest(old) if old is not None else 'absent') if policy in POLICIES_WITH_HASHES else 'managed'
    # These fixtures model the host before ANY bootstrap admission code exists.
    shutil.rmtree(case.env['VANPI_BACKUP_DEPLOY_STATE_DIR'])
    shutil.rmtree(case.env['VANPI_BACKUP_DEPLOY_BOOTSTRAP_DIR'])
    (live / plan.CRON_PATH.lstrip('/')).unlink()
    for service in plan.SERVICES:
        (live / ('etc/systemd/system/' + service + '.d/backup-deploy-bootstrap.conf')).unlink()
    return fixture


def bootstrap_commands(case, fixture, bundle):
    root, _stage, live, *_ = fixture
    root.joinpath('cron-root').write_text('scripts=/home/pi/scripts\n0 * * * * "$scripts/backup/backup_window.sh" --scheduled\n')
    root.joinpath('cron-pi').write_text('# unrelated jobs remain untouched\n')
    for name, text in (('crontab', CRONTAB), ('bootstrap-systemctl', SYSTEMCTL), ('busctl', BUSCTL)):
        path = root / name
        path.write_text(text)
        path.chmod(0o755)
    case.env.update(BOOTSTRAP_TEST_ROOT=str(root), VANPI_BOOTSTRAP_CRONTAB=str(root / 'crontab'),
                    VANPI_BOOTSTRAP_SYSTEMCTL=str(root / 'bootstrap-systemctl'),
                    VANPI_BOOTSTRAP_BUSCTL=str(root / 'busctl'),
                    VANPI_BOOTSTRAP_PI_USER=case.env['VANPI_BACKUP_JOB_LOCK_OWNER'],
                    VANPI_BOOTSTRAP_PI_GROUP=case.env['VANPI_BACKUP_JOB_LOCK_GROUP'])
    driver = root / 'fault-driver.py'
    driver.write_text(FAULT_DRIVER)
    return ['/usr/bin/python3', '-B', str(driver), str(bundle / 'runner/backup_bootstrap_host.py'),
            '--root', str(live), '--maintenance-confirmed']


def run_bootstrap(case, command, *args, **fault):
    env = dict(case.env, **fault)
    env['PYTHONPATH'] = str(Path(command[3]).parent)  # the real staged runtime, not live code
    return subprocess.run(command + list(args), env=env, capture_output=True, text=True, timeout=60)


def capture_guarded_previous(fixture):
    _root, _stage, live, records, previous, snapshots = fixture
    for source, destination, policy, *_ in records:
        target = live / destination.lstrip('/')
        if target.exists():
            snapshots[destination] = (target.read_bytes(), target.stat().st_mode & 0o7777)
            if policy in POLICIES_WITH_HASHES:
                previous[source] = digest(target.read_bytes())
