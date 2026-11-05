"""Disposable filesystem and PATH commands; reuse the existing lock registry.

Mutating commands run as production does: a real `offsite_snapshot.py create`
copies the closure from a fixture live tree, and the worker then imports only
from that snapshot while it holds job-offsite (prepare-disk: the legacy lock on
fd 9). The fake commands stand in for devices, restic and the mount helper.
"""
import atexit
from contextlib import ExitStack
from dataclasses import asdict, replace
import fcntl
import functools
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from backup_admission import HeldDescriptor
import backup_locks
from offsite_config import COMMANDS, FROZEN_IMPLEMENTATION, JOB_KIND, RESTIC_INSTALLER, Config, MARKER
from pi.tests.backup.backup_lock_fixtures import temporary_registry


REPO_SCRIPTS = Path(__file__).resolve().parents[2] / 'scripts'
# Command doubles skip site (-S): none needs it, and a test starts dozens of them.
FAKE_SHEBANG = '#!' + sys.executable + ' -S\n'
READ_ONLY = ('status', 'dashboard', 'rates')
PROLOGUE = '''
import os, sys
from contextlib import nullcontext
from unittest.mock import patch
# Production descriptor numbers: prepare-disk's legacy lock is fd 9.
for pair in filter(None, os.environ.pop('OFFSITE_FIXTURE_FDS', '').split(',')):
    source, target = map(int, pair.split(':'))
    os.dup2(source, target)
    os.close(source)
# Darwin has no /proc fdinfo. All command/record behavior remains real; the
# Linux-only tests separately exercise the unchanged kernel admission check.
context = patch('backup_locks.require_held') if sys.platform != 'linux' else nullcontext()
'''
SNAPSHOT_BOOTSTRAP = PROLOGUE + '''
import offsite_snapshot
with context:
    raise SystemExit(offsite_snapshot.main(sys.argv[1:]))
'''
BOOTSTRAP = PROLOGUE + '''
import json
from pathlib import Path
from types import SimpleNamespace
from offsite_config import Config
from offsite_backup import main
v=json.loads(Path(os.environ['OFFSITE_CONFIG']).read_text())
for k in ('state','password','mount_root','by_id','drain','ignition','borg'):
    v[k]=Path(v[k])
v['code_root']=Path(os.environ['OFFSITE_CODE_ROOT']) if os.environ.get('OFFSITE_CODE_ROOT') else None
cfg=Config(**v)
fixture=json.loads(Path(os.environ['OFFSITE_FIXTURE']).read_text())
if fixture.get('snapshot_commands') and cfg.code_root:
    # Run these helpers from the snapshot, as production does, instead of PATH fakes.
    from offsite_config import command_paths
    from offsite_remote import remote_commands
    paths={**command_paths(cfg.code_root), **remote_commands(cfg.code_root)}
    cfg.commands.update({name: paths[name] for name in fixture['snapshot_commands']})
clock=nullcontext()
if 'sample_times' in fixture:
    ticks=iter(fixture['sample_times'])
    clock=patch('upload_speed_log.time', SimpleNamespace(time=lambda: next(ticks)))
history_write=(patch('offsite_dashboard.durable_json', side_effect=OSError('fixture history write failed'))
               if fixture.get('history_write_failure') else nullcontext())
# The OOM fallback policy is a constant; tests flip it here rather than in the source.
excluded=(patch('offsite_locks.EXCLUDED_LOCAL_JOBS', tuple(fixture['excluded_local_jobs']))
          if 'excluded_local_jobs' in fixture else nullcontext())
with context:
    with clock, history_write, excluded:
        raise SystemExit(main(sys.argv[1:], config=cfg))
'''


@functools.cache
def command_double(source):
    """One read-only executable per test process for a double's source; fixtures hard-link it per name.

    macOS assesses each newly written executable on its first exec (about 70 ms
    measured, more under the parallel suite), so fresh per-fixture copies paid
    that for every command name in every test. A hard link shares the assessed
    inode yet keeps the name's own path in argv[0]. Read-only, so a test that
    rewrote a double in place would fail instead of leaking into later tests.
    """
    directory = Path(tempfile.mkdtemp(prefix='offsite-doubles-'))
    atexit.register(shutil.rmtree, directory, ignore_errors=True)
    path = directory / 'command'
    path.write_text(FAKE_SHEBANG + source)
    path.chmod(0o555)
    return path


def high_descriptor(fd):
    # Keep fixture descriptors clear of the production numbers 9 and 10.
    moved = fcntl.fcntl(fd, fcntl.F_DUPFD, 200)
    os.close(fd)
    return moved


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class OffsiteFixture:
    # An upper bound only: each mutating command also starts the snapshot CLI,
    # and the Pi runs these beside real backups.
    command_timeout = 120
    label = 'FORT-MPLS-5TB'

    def __init__(self):
        self.stack = ExitStack()
        self.registry = self.stack.enter_context(temporary_registry())
        self.root = self.registry.root.resolve()
        self.fd = self.hold(self.registry.legacy)
        self.job_path = backup_locks.lock_path(backup_locks.LockClass.JOB, JOB_KIND)
        self.job_fd = self.hold(self.job_path)
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        fake = command_double(Path(__file__).with_name('offsite_fake_command.py').read_text())
        for name in COMMANDS:
            os.link(fake, self.bin / ('fake-' + name))
        for directory in ('mnt', 'by-id', 'drain', 'state', 'password'):
            (self.root / directory).mkdir(mode=0o700)
        self.cfg = Config(state=self.root / 'state', password=self.root / 'password' / 'secret',
                          mount_root=self.root / 'mnt', by_id=self.root / 'by-id',
                          drain=self.root / 'drain' / 'mbp2tbkup', ignition=self.root / 'ignition',
                          borg=self.root / 'mnt' / 'bigboi' / 'borg' / 'vanpi-encrypted',
                          owner=os.getuid(), block_devices=False,
                          commands={name: 'fake-' + name for name in COMMANDS})
        self.live = self.root / 'live'
        self.build_live_tree(self.live)
        self.device = self.root / 'target'
        self.device.touch()
        self.link = self.cfg.by_id / 'usb-borrowed'
        self.link.symlink_to(self.device)
        self.fixture = self.root / 'fixture.json'
        self.config_file = self.root / 'config.json'
        self.write_config()
        self.state = self.initial_state()
        self.save()
        self.env = {**os.environ, **self.registry.environment, 'OFFSITE_FIXTURE': str(self.fixture),
                    'OFFSITE_CONFIG': str(self.config_file), 'PATH': str(self.bin) + os.pathsep + os.environ['PATH']}

    def hold(self, path, exclusive=True):
        fd = high_descriptor(os.open(path, os.O_RDONLY))
        self.stack.callback(os.close, fd)
        fcntl.flock(fd, fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
        return fd

    def build_live_tree(self, live):
        """The checkout's frozen closure plus a restic pin naming the fake restic."""
        for relative in FROZEN_IMPLEMENTATION:
            (live / relative).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(REPO_SCRIPTS / relative, live / relative)
        restic = self.bin / 'fake-restic'
        (live / RESTIC_INSTALLER).write_text(f'version=0.19.1\nbinary_sha={sha256(restic)}\ntarget={restic}\n')

    def write_config(self):
        self.config_file.write_text(json.dumps(asdict(self.cfg), default=str))

    def configure(self, **changes):
        self.cfg = replace(self.cfg, **changes)
        self.write_config()

    def initial_state(self):
        root = self.root / 'root-device'
        root.touch()
        disk = {'name': str(self.device), 'type': 'disk', 'model': 'Borrowed USB drive',
                'serial': 'SERIAL123', 'size': 3_000_000_000_000, 'tran': 'usb',
                'ro': False, 'label': None, 'partlabel': None, 'fstype': None,
                'mountpoints': [None], 'maj:min': '8:16', 'children': []}
        boot = {'name': str(root), 'type': 'disk', 'serial': 'ROOT', 'mountpoints': ['/']}
        return {'disks': [boot, disk], 'mounts': {'/': str(root)}, 'mount_root': str(self.cfg.mount_root),
                'policy': ['bigboi', 'movingparts', 'EXFAT512', 'mbp2tbkup', 'hdd1tb', 'hotspare-a', 'hotspare-b'],
                'offsite': [self.label]}

    def save(self):
        self.fixture.write_text(json.dumps(self.state))

    def reload(self):
        self.state = json.loads(self.fixture.read_text())

    def snapshot(self, live=None):
        """Run the real snapshot CLI from a live tree while holding job-offsite, as the launcher does."""
        live = self.live if live is None else live
        environment = {**self.env, 'PYTHONPATH': str(live / 'backup'), 'OFFSITE_STATE_ROOT': str(self.cfg.state),
                       'VANPI_BACKUP_JOB_LOCK_FD': str(self.job_fd)}
        result = subprocess.run([sys.executable, '-B', '-c', SNAPSHOT_BOOTSTRAP, 'create'], cwd=self.root,
                                env=environment, pass_fds=(self.job_fd,), text=True, capture_output=True,
                                timeout=self.command_timeout)
        if result.returncode:
            raise AssertionError('snapshot create failed: ' + result.stderr)
        return Path(result.stdout.strip())

    def start(self, *args, code=BOOTSTRAP, env=None, extra_fds=()):
        """Start one CLI command; env entries override the production-like metadata."""
        environment = dict(self.env)
        if args[0] in (*READ_ONLY, 'pause', 'resume'):
            descriptors = ()
        elif args[0] == 'prepare-disk':
            descriptors = (self.fd,)
            environment['OFFSITE_FIXTURE_FDS'] = f'{self.fd}:9'
        else:
            snapshot = self.snapshot()
            descriptors = (self.job_fd,)
            # Only the snapshot is importable, so a closure miss fails the run.
            environment.update(OFFSITE_CODE_ROOT=str(snapshot), PYTHONPATH=str(snapshot / 'backup'),
                               VANPI_BACKUP_JOB_KIND=JOB_KIND, VANPI_BACKUP_JOB_LOCK_FD=str(self.job_fd),
                               VANPI_BACKUP_JOB_LOCK_PATH=str(self.job_path))
        environment.update(env or {})
        return subprocess.Popen([sys.executable, '-B', '-c', code, *args], cwd=self.root,
                                env=environment, pass_fds=(*descriptors, *extra_fds), text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def run(self, *args, **kwargs):
        process = self.start(*args, **kwargs)
        try:
            out, err = process.communicate(timeout=self.command_timeout)
        except BaseException:
            process.terminate()
            try:
                process.communicate(timeout=45)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate()
            raise
        return subprocess.CompletedProcess(process.args, process.returncode, out, err)

    def prepare(self):
        return self.run('prepare-disk', '--device', str(self.link), '--label', self.label,
                        '--priority', '10', '--erase-confirm', 'SERIAL123')

    def sources(self):
        self.reload()
        for source in self.cfg.sources().values():
            mount = self.cfg.mount_root / source.label
            device = self.root / source.label
            device.touch()
            mount.mkdir(exist_ok=True)
            self.state['disks'].append({'name': str(device), 'type': 'disk', 'label': source.label,
                                        'mountpoints': [str(mount)]})
            self.state['mounts'][str(mount)] = str(device)
            for path in source.paths:
                path.mkdir(parents=True, exist_ok=True)
                (path / 'sample').write_text('content')
        self.save()

    def executions(self):
        path = self.root / 'executions.jsonl'
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def calls(self):
        path = self.root / 'commands.jsonl'
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def marker(self):
        return json.loads((self.cfg.mount_root / self.label / MARKER).read_text())

    def locks(self):
        """An in-process admission over the fixture's job hold, for library-level tests."""
        from offsite_locks import JOB, OffsiteLocks
        info = os.fstat(self.job_fd)
        return OffsiteLocks(JOB, HeldDescriptor(self.job_fd, self.job_path, info.st_dev, info.st_ino))

    def snapshots(self):
        code = self.cfg.state / 'code'
        return sorted(code.iterdir()) if code.exists() else []

    def close(self):
        self.stack.close()
