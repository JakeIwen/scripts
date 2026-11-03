"""Remote CLI fixture: real subprocess boundary, synthetic admission evidence only."""
from dataclasses import asdict
import json
import os
import subprocess
import sys

from offsite_remote import REMOTE_COMMANDS
from pi.tests.backup.offsite_fixtures import BOOTSTRAP, OffsiteFixture

SECRET = 'fixture-transport-secret-' + 'x' * 40
GUARD_COMMAND = '''
import json,os,sys
from pathlib import Path
name=Path(sys.argv[0]).name
root=Path(os.environ['OFFSITE_FIXTURE']).parent
if name == 'metered':
    if sys.argv[1:] != ['admit','pi.offsite_backup']:
        raise SystemExit(2)
    print(json.dumps({'allowed': not (root/'denied').exists(), 'reason':'fixture'}))
elif name == 'route':
    print(json.dumps([{'dev':'eth0' if (root/'wrong-route').exists() else 'tailscale0'}]))
elif name == 'tailscale':
    online=not (root/'offline').exists()
    print(json.dumps({'BackendState':'Running','Self':{'Online':online},'Peer':{'nas':{'Online':online,'TailscaleIPs':['100.64.0.10']}}}))
else:
    print('{}')
'''


class RemoteFixture(OffsiteFixture):
    # Real receipt/readback commands perform several key derivations on the Pi.
    command_timeout = 180

    def __init__(self):
        super().__init__()
        for name in REMOTE_COMMANDS:
            path = self.bin / name
            path.write_text('#!' + sys.executable + '\n' + GUARD_COMMAND)
            path.chmod(0o700)
            self.cfg.commands[name] = str(path)
        self.config_file.write_text(json.dumps(asdict(self.cfg), default=str))
        self.remote_config = self.cfg.state / 'remote.json'
        self.remote_config.write_text(json.dumps({'schema': 1, 'address': '100.64.0.10', 'port': 8088,
                                                  'transport_password': SECRET}))
        self.remote_config.chmod(0o600)
        self.loopback = None

    def start(self, *args):
        code = BOOTSTRAP
        if self.loopback:
            # The production parser still validates the Tailscale-only endpoint.
            # This fixture alone redirects transport to a random loopback port.
            setup = '''
from offsite_remote import RemoteRepository
class LoopbackRepository(RemoteRepository):
    def configure(self):
        super().configure()
        self.path=os.environ['OFFSITE_LOOPBACK']
'''
            code = code.replace('with context:', setup + "\nwith context, patch('offsite_remote.RemoteRepository',LoopbackRepository):")
        environment = {**self.env, 'OFFSITE_LOOPBACK': self.loopback or ''}
        descriptors = () if args[0] in ('status', 'dashboard', 'rates') else (self.fd,)
        return subprocess.Popen([sys.executable, '-B', '-c', code, *args], cwd=self.root,
                                env=environment, pass_fds=descriptors, text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
