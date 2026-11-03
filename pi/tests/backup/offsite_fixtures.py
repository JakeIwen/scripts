"""Disposable filesystem and PATH commands; reuse the existing lock registry."""
from contextlib import ExitStack
from dataclasses import asdict
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys

from offsite_config import COMMANDS, Config, MARKER, REPO_PATH
from pi.tests.backup.backup_lock_fixtures import temporary_registry


BOOTSTRAP = '''
from dataclasses import replace
import json, os, sys
from pathlib import Path
from unittest.mock import patch
from contextlib import nullcontext
from offsite_config import Config
from offsite_backup import main
v=json.loads(Path(os.environ['OFFSITE_CONFIG']).read_text())
for k in ('state','password','mount_root','by_id','drain','ignition','borg'):
    v[k]=Path(v[k])
cfg=Config(**v)
# Darwin has no /proc fdinfo. All command/record behavior remains real; the
# Linux-only test separately exercises the unchanged kernel admission check.
context=patch('backup_locks.require_held') if sys.platform != 'linux' else nullcontext()
with context:
    raise SystemExit(main(sys.argv[1:], config=cfg))
'''


class OffsiteFixture:
    command_timeout = 30

    def __init__(self):
        self.stack = ExitStack()
        self.registry = self.stack.enter_context(temporary_registry())
        self.root = self.registry.root.resolve()
        self.fd = os.open(self.registry.legacy, os.O_RDONLY)
        self.stack.callback(os.close, self.fd)
        fcntl.flock(self.fd, fcntl.LOCK_EX)
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        fake = Path(__file__).with_name('offsite_fake_command.py').read_text()
        for name in COMMANDS:
            path = self.bin / ('fake-' + name)
            path.write_text('#!' + sys.executable + '\n' + fake)
            path.chmod(0o755)
        for directory in ('mnt', 'by-id', 'drain', 'state', 'password'):
            (self.root / directory).mkdir(mode=0o700)
        self.cfg = Config(state=self.root / 'state', password=self.root / 'password' / 'secret',
                          mount_root=self.root / 'mnt', by_id=self.root / 'by-id',
                          drain=self.root / 'drain' / 'mbp2tbkup', ignition=self.root / 'ignition',
                          borg=self.root / 'mnt' / 'bigboi' / 'borg' / 'vanpi-encrypted',
                          owner=os.getuid(), block_devices=False, lock_fd=self.fd,
                          commands={name: 'fake-' + name for name in COMMANDS})
        self.label = 'offsite-3tb'
        self.device = self.root / 'target'
        self.device.touch()
        self.link = self.cfg.by_id / 'usb-borrowed'
        self.link.symlink_to(self.device)
        self.fixture = self.root / 'fixture.json'
        self.config_file = self.root / 'config.json'
        self.config_file.write_text(json.dumps(asdict(self.cfg), default=str))
        self.state = self.initial_state()
        self.save()
        self.env = {**os.environ, **self.registry.environment, 'OFFSITE_FIXTURE': str(self.fixture),
                    'OFFSITE_CONFIG': str(self.config_file), 'PATH': str(self.bin) + os.pathsep + os.environ['PATH']}

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

    def start(self, *args):
        return subprocess.Popen([sys.executable, '-B', '-c', BOOTSTRAP, *args], cwd=self.root,
                                env=self.env, pass_fds=(self.fd,), text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def run(self, *args):
        process = self.start(*args)
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

    def calls(self):
        path = self.root / 'commands.jsonl'
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def marker(self):
        return json.loads((self.cfg.mount_root / self.label / MARKER).read_text())

    def close(self):
        self.stack.close()
