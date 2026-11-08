"""Remote CLI fixture: real subprocess boundary, synthetic admission evidence only."""
import base64
from dataclasses import asdict
import json
import os
from pathlib import Path

from offsite_remote import remote_commands
from pi.tests.backup.offsite_fixtures import BOOTSTRAP, FAKE_SHEBANG, OffsiteFixture, command_double

SECRET = 'fixture-transport-secret-' + 'x' * 40
# A DSM password with characters JSON (\\ and ") and Go's encoder (<, >, &) escape.
SMB_PASSWORD = 'dsm-Pw<&>"\\' + 'q' * 12
SMB_TRANSPORT = {'schema': 1, 'kind': 'smb', 'address': '100.64.0.10', 'port': 445, 'share': 'Jake_Share',
                 'path': 'vanpi-restic', 'user': 'jake', 'password': SMB_PASSWORD}
GUARD_COMMAND = '''
import json,os,sys
from pathlib import Path
name=Path(sys.argv[0]).name
root=Path(os.environ['OFFSITE_FIXTURE']).parent
if name == 'metered':
    if sys.argv[1:] != ['admit','pi.offsite_backup']:
        raise SystemExit(2)
    # The real admit CLI reads evidence to EOF; exiting first races the parent's pipe write.
    json.load(sys.stdin)
    print(json.dumps({'allowed': not (root/'denied').exists(), 'reason':'fixture'}))
elif name == 'route':
    print(json.dumps([{'dev':'eth0' if (root/'wrong-route').exists() else 'tailscale0'}]))
elif name == 'tailscale':
    online=not (root/'offline').exists()
    print(json.dumps({'BackendState':'Running','Self':{'Online':online},'Peer':{'nas':{'Online':online,'TailscaleIPs':['100.64.0.10']}}}))
else:
    print('{}')
'''
# Helpers a snapshot run executes from its own copy; the isolation tests install
# the command double over them in the fixture live tree.
SNAPSHOT_HELPERS = ('backup/offsite_mounts.sh', 'samba_share_control.sh', 'ntfy_send.sh')
LOG_EXECUTION = """
import json, os, sys
from pathlib import Path
with (Path(os.environ['OFFSITE_FIXTURE']).parent / 'executions.jsonl').open('a') as stream:
    stream.write(json.dumps({'name': 'uplink', 'args': sys.argv[1:], 'path': sys.argv[0], 'env': {}}) + '\\n')
"""


class RemoteFixture(OffsiteFixture):
    # Real receipt/readback commands perform several key derivations on the Pi.
    command_timeout = 180

    def __init__(self):
        super().__init__()
        guard = command_double(GUARD_COMMAND)
        for name in remote_commands():
            path = self.bin / name
            os.link(guard, path)
            self.cfg.commands[name] = str(path)
        self.config_file.write_text(json.dumps(asdict(self.cfg), default=str))
        self.remote_config = self.cfg.state / 'remote.json'
        self.remote_config.write_text(json.dumps({'schema': 1, 'address': '100.64.0.10', 'port': 8088,
                                                  'transport_password': SECRET}))
        self.remote_config.chmod(0o600)
        self.loopback = None

    def install_stub_helpers(self):
        """Replace the live tree's device-facing helpers with the command double before a snapshot."""
        fake = Path(__file__).with_name('offsite_fake_command.py').read_text()
        for relative in SNAPSHOT_HELPERS:
            (self.live / relative).write_text(FAKE_SHEBANG + fake)
        (self.live / 'backup' / 'icloud_uplink.py').write_text(FAKE_SHEBANG + LOG_EXECUTION
                                                                + GUARD_COMMAND)
        for relative in (*SNAPSHOT_HELPERS, 'backup/icloud_uplink.py'):
            (self.live / relative).chmod(0o755)

    def start(self, *args, **kwargs):
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
        return super().start(*args, code=code, env={'OFFSITE_LOOPBACK': self.loopback or ''}, **kwargs)


def fake_obscured(password):
    """What the rclone double's `obscure -` prints for password."""
    return base64.urlsafe_b64encode(bytes(16) + password.encode()).decode().rstrip('=')


class SmbFixture(RemoteFixture):
    """remote.json of kind smb; runs freeze the rclone double and restic receives its RCLONE_* values."""

    def __init__(self, password=SMB_PASSWORD):
        super().__init__()
        self.rclone = self.bin / 'fake-rclone'
        # In-process repositories run the double directly; CLI runs use the snapshot's copy.
        self.cfg.commands['rclone'] = str(self.rclone)
        self.write_config()
        self.transport = {**SMB_TRANSPORT, 'password': password}
        self.write_transport(self.transport)
        self.state.update(installed_rclone=str(self.rclone), snapshot_commands=['rclone'])
        self.save()

    def write_transport(self, value):
        self.remote_config.write_text(json.dumps(value))
        self.remote_config.chmod(0o600)
