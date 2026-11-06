"""Local SSH/SCP boundary for the real broad-sync client, staging and receiver.

Only fixed fixture paths are used; no SSH process or supervisor is contacted.
"""
from pathlib import Path
import shutil
import subprocess

from pi.tests import test_update_services as receiver
import backup_bootstrap_plan as plan

REPO = Path(__file__).resolve().parents[3]
BACKUP = REPO / 'pi/scripts/backup'

TRANSPORT = r'''#!/usr/bin/python3
import fcntl, os, pathlib, shlex, shutil, subprocess, sys, uuid
root = pathlib.Path(os.environ['SYNC_TEST_ROOT'])
repo = root / 'checkout'
host = pathlib.Path(os.environ['VANPI_BOOTSTRAP_ROOT'])
def event(text):
    with (root / 'transport-events').open('a') as stream: stream.write(text + '\n')
def track(path):
    with (root / 'remote-stages').open('a') as stream: stream.write(str(path) + '\n')
def fence():
    with open(os.environ['VANPI_BACKUP_JOB_LOCK'], 'rb') as stream:
        try: fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError: event('publication-blocked'); return
        raise RuntimeError('unrelated upload escaped the sender fence')
def mapped(path):
    return pathlib.Path(path) if path.startswith('/tmp/') else host / path.lstrip('/')
args = sys.argv[1:]
while args and args[0].startswith('-'):
    args = args[2:] if args[0] == '-o' else args[1:]
if pathlib.Path(sys.argv[0]).name == 'scp':
    fence(); event('unrelated-upload')
    *sources, target = args
    destination = mapped(target.split(':', 1)[1])
    destination.mkdir(parents=True, exist_ok=True)
    for source in sources:
        source = pathlib.Path(source)
        if source.is_dir():
            shutil.copytree(source, destination / source.name, dirs_exist_ok=True)
            if source.name in ('scripts', 'services', 'tmpfiles.d', '.backup-sync'):
                shutil.copytree(source, root / 'outgoing' / source.name, dirs_exist_ok=True)
        else: shutil.copy2(source, destination / source.name)
    raise SystemExit(0)
target, *command = args
if len(command) == 1: command = shlex.split(command[0])
if command[:2] == ['/usr/bin/mktemp', '-d']:
    stage = pathlib.Path('/tmp/vanpi-sync-check.' + uuid.uuid4().hex[:6])
    stage.mkdir(mode=0o700); track(stage); print(stage)
elif command[0] == '/bin/tar':
    raise SystemExit(subprocess.call(command))
elif command[0] == '/usr/bin/sudo':
    command = command[2:]
    if command[-1] == 'check' and os.environ.get('SYNC_INJECT_PENDING'):
        pathlib.Path(os.environ['VANPI_BACKUP_DEPLOY_STATE_DIR'], 'pending').write_text('injected gap\n')
    os.execv(command[0], command)
elif command[:2] == ['git', 'hash-object']:
    print(subprocess.check_output(['git', '-C', str(repo), 'hash-object', 'pi/.bashrc'], text=True).strip())
elif command[0] == 'test':
    raise SystemExit(0)
elif command[0] == 'true':
    fence()
elif command[:2] == ['mkdir', '-p']:
    fence(); destination = mapped(command[2]); destination.mkdir(parents=True, exist_ok=True)
    if command[2].startswith('/tmp/systemd-tmp'): track(destination)
elif command[0] == 'bash':
    fence(); raise SystemExit(subprocess.call(['/bin/bash', *command[1:]]))
elif command[0] == 'mktemp':
    fence(); print(root / 'smb.conf.tmp')
else:
    fence(); event('other-remote:' + command[0])
'''

PYTHON = '''#!/bin/sh
case "$*" in
  *pi.generate_backup_lock_registry*|*--check-consumers*) exit 0 ;;
  *--list-units*) printf 'video-library.service\\n'; exit 0 ;;
  *--update*) printf 'packages\\n' >> "$SYNC_TEST_ROOT/transport-events"; exit 0 ;;
esac
exec /usr/bin/python3 "$@"
'''


def make(case, healthy=True):
    fixture = receiver.UpdateServicesTests()
    fixture.setUp()
    case.addCleanup(fixture.doCleanups)
    root = fixture.root
    repo = root / 'checkout'
    paths = subprocess.check_output(['/bin/bash', '-c', '. "$1"; local_backup_plan pi; local_backup_plan tm',
                                     'plan', str(BACKUP / 'backup_deploy_plan.sh')], text=True)
    protected = {}
    for row in paths.splitlines():
        source, destination, *_ = row.split('|')
        staged = repo / source
        staged.parent.mkdir(parents=True, exist_ok=True)
        staged.write_text('staged cohort: ' + source + '\n')
        live = fixture.host_root / destination.lstrip('/')
        live.parent.mkdir(parents=True, exist_ok=True)
        live.write_text('protected predecessor: ' + destination + '\n')
        protected[live] = None
    for name in plan.SYNC_FILES:
        shutil.copyfile(BACKUP / name, repo / 'pi/scripts/backup' / name)
    for name in ('__init__.py', 'backup_sync_client.py'):
        shutil.copyfile(REPO / 'pi' / name, repo / 'pi' / name)
    for name in ('sync_scripts.sh', 'sync_workflow.sh', 'sync_storage_managed.exclude'):
        text = (REPO / 'pi' / name).read_text()
        text = text.replace("pi_ip='pi@vanpi'", "pi_ip='pi@fixture.invalid'")
        (repo / 'pi' / name).write_text(text.replace('dsc="/Users/jacobr/dev/scripts"', 'dsc="' + str(repo) + '"'))
    shutil.copyfile(REPO / 'pi/scripts/update_services.sh', repo / 'pi/scripts/update_services.sh')
    shutil.copyfile(REPO / 'pi/scripts/connectivity_status.py', repo / 'pi/scripts/connectivity_status.py')
    for name in ('new.service', 'changed.service'):
        shutil.copyfile(fixture.stage / 'services' / name, repo / 'pi/services' / name)
    extras = ['pi/.bashrc', 'pi/canbus_funcs.sh', 'pi/sns.sh', 'pi/keepalive.txt', 'pi/configs/.bash_defaults',
              'pi/configs/rsync-exclude-media.txt', 'pi/configs/smb.conf', 'pi/configs/labwc/autostart',
              'pi/configs/labwc/blank-when-idle.sh', 'pi/hooks/fixture', 'pi/secrets/fixture', 'shared/sh/fixture.sh', 'shared/sh/mount_disks.sh']
    for name in extras:
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('fixture ' + name + '\n')
    subprocess.run(['git', 'init', '-q', str(repo)], check=True)
    host = fixture.bootstrap_ready() if healthy else None
    for path in protected:
        protected[path] = path.read_bytes()
    helper = fixture.live['scripts'] / 'connectivity_status.py'
    helper.write_text('# old helper\n')
    compute = root / 'compute/macbook/scripts/install_van_compute_worker.zsh'
    compute.parent.mkdir(parents=True)
    compute.write_text('#!/bin/sh\nprintf "compute\\n" >> "$SYNC_TEST_ROOT/transport-events"\n')
    compute.chmod(0o755)
    for name, text in (('ssh', TRANSPORT), ('scp', TRANSPORT), ('python3', PYTHON)):
        path = root / 'bin' / name
        path.write_text(text)
        path.chmod(0o755)
    env = dict(fixture.env, SYNC_TEST_ROOT=str(root), VAN_COMPUTE_CHECKOUT=str(root / 'compute'))
    return fixture, repo, env, host, protected


def cleanup_stages(root):
    record = root / 'remote-stages'
    if record.exists():
        for name in record.read_text().splitlines():
            path = Path(name)
            if path.parent == Path('/tmp') and path.name.startswith(('vanpi-sync-check.', 'systemd-tmp.')):
                if path.exists() and not path.is_symlink():
                    shutil.rmtree(path)


def run(repo, env):
    return subprocess.run(['/bin/bash', str(repo / 'pi/sync_scripts.sh')],
                          cwd=repo.parent, env=env, capture_output=True, text=True, timeout=90)
