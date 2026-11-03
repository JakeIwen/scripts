"""Identity-bound REST target; every network operation uses fresh cost admission."""
import json

from offsite_config import REMOTE_CONFIG, REMOTE_CONSUMER, validate_transport
from offsite_repository import Interrupted, LocalRepository, password, records
from protected_json import read_protected_json


REMOTE_COMMANDS = {
    'tailscale': '/usr/bin/tailscale',
    'uplink': '/home/pi/scripts/backup/icloud_uplink.py',
    'metered': '/home/pi/scripts/vanpi-metered',
    'route': '/usr/bin/ip',
}


def transport_config(path):
    return validate_transport(read_protected_json(path, 'Unsafe offsite remote configuration'))


class RemoteRepository(LocalRepository):
    remote = True

    def __init__(self, cfg, commands, label):
        super().__init__(cfg, commands, label)
        # Optional binaries are not consulted for any local v1 operation.
        for name, path in REMOTE_COMMANDS.items():
            commands.cfg.commands.setdefault(name, path)
        self.marker = self.transport = self.auth = None

    def configure(self):
        cfg = self.cfg
        self.marker = records(cfg)['repos'].get(self.label)
        if self.marker is None:
            raise ValueError('Remote target is not a registered seeded repository')
        transport = transport_config(cfg.state / REMOTE_CONFIG)
        password(cfg)
        if transport['transport_password'] == cfg.password.read_text().strip():
            raise ValueError('Transport password must not be the restic encryption password')
        self.path = 'rest:http://{}:{}/'.format(transport['address'], transport['port'])
        self.auth = (self.marker['repo_id'], transport['transport_password'])
        self.transport = transport

    def network_guard(self):
        self.commands.allowed()
        if self.transport is None:
            self.configure()
        try:
            if (transport_config(self.cfg.state / REMOTE_CONFIG) != self.transport
                    or records(self.cfg)['repos'].get(self.label) != self.marker):
                raise ValueError('Remote configuration or registration changed')
            password(self.cfg)
            # Reuse the existing evidence collector via its deployed CLI, not its
            # historical import bootstrap or cached dashboard connectivity state.
            evidence = self.commands.run('uplink', timeout=35)
            verdict = json.loads(self.commands.run('metered', 'admit', REMOTE_CONSUMER,
                                                  input_text=evidence, timeout=6))
            if verdict.get('allowed') is not True:
                raise ValueError('denied')
            status = json.loads(self.commands.run('tailscale', 'status', '--json', timeout=5))
            peer = [row for row in status.get('Peer', {}).values()
                    if self.transport['address'] in row.get('TailscaleIPs', [])]
            if (status.get('BackendState') != 'Running' or status.get('Self', {}).get('Online') is not True
                    or len(peer) != 1 or peer[0].get('Online') is not True):
                raise ValueError('Tailscale peer unavailable')
            route = json.loads(self.commands.run('route', '-j', '-4', 'route', 'get',
                                                  self.transport['address'], timeout=5))
            if not isinstance(route, list) or len(route) != 1 or route[0].get('dev') != 'tailscale0':
                raise ValueError('Remote route is not Tailscale')
        except Interrupted:
            raise
        except (OSError, ValueError, RuntimeError, TypeError, AttributeError) as exc:
            raise Interrupted('Remote offsite admission denied or unknown; retry explicitly after checking uplink and Tailscale') from None

    def exact_mount(self, label=None, *, cleanup=False):
        if label is not None:
            return super().exact_mount(label, cleanup=cleanup)
        self.network_guard()
        return self.path

    def mount_source(self, label, on_mount=None):
        if label == self.label:
            return False
        return super().mount_source(label, on_mount=on_mount)

    def unmount(self):
        # This is a network repository, never a local offsite mount.
        return None

    def restic(self, *args, **kwargs):
        source_guard = kwargs.pop('guard', None)
        def guard():
            self.network_guard()
            if source_guard:
                source_guard()
        guard()
        result = self.commands.run('restic', '--repo', self.path, '--password-file',
                                   self.cfg.password, '--no-cache', *args,
                                   rest_auth=self.auth, guard=guard, **kwargs)
        guard()
        return result

    def verify(self):
        password(self.cfg)
        actual = json.loads(self.restic('cat', 'config')).get('id')
        if actual != self.marker['repo_id']:
            raise RuntimeError('Unknown repository ID: remote decrypted config does not match registration')
        return self.marker

    def check(self, subset=None):
        from offsite_receipts import evidence_operation
        def operation():
            self.verify()
            options = ['--read-data-subset', subset] if subset else []
            return self.restic('check', *options, timeout=None)
        return evidence_operation(self, 'check', operation, subset=subset)
