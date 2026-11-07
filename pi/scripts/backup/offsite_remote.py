"""Identity-bound remote target; every network operation uses fresh cost admission.

remote.json's kind selects the transport: the REST receiver (auth bound to the
registered repository ID) or a plain shared folder through restic's rclone
backend (offsite_smb.py). SMB credentials name no repository, so an smb target
runs restic for nothing but `cat config` until verify() has matched the decrypted
config ID to the registration (plan review R5). The share could still be swapped
after that check; the docs record that residual race.
"""
import json
from pathlib import Path

from offsite_config import LIVE_ROOT, METERED_CLI, REMOTE_CONFIG, REMOTE_CONSUMER, TRANSPORT_KINDS, TransportKind
from offsite_config import validate_transport
from offsite_repository import Interrupted, LocalRepository, no_core_dumps, password, records, redacted
import offsite_smb
from protected_json import read_protected_json

# The only restic commands a repository being registered (init-remote) may run.
REGISTERING_COMMANDS = (('init',), ('cat', 'config'))
VERIFY_COMMAND = ('cat', 'config')


def remote_commands(code_root=None):
    # The uplink collector is frozen with the run; metering admission stays the live CLI.
    root = LIVE_ROOT if code_root is None else Path(code_root)
    return {
        'tailscale': '/usr/bin/tailscale',
        'uplink': str(root / 'backup' / 'icloud_uplink.py'),
        'metered': str(LIVE_ROOT / METERED_CLI),
        'route': '/usr/bin/ip',
    }


def transport_config(path):
    # The one reader of remote.json, which holds the DSM password for kind smb. Callers outside the
    # protected worker (the snapshot's needs_rclone, the dashboard's availability and control) reach
    # it with core dumps still enabled, so the guard lives here rather than at each caller.
    no_core_dumps()
    return validate_transport(read_protected_json(path, 'Unsafe offsite remote configuration'), kinds=TRANSPORT_KINDS)


class RemoteRepository(LocalRepository):
    remote = True

    def __init__(self, cfg, commands, label, *, registering=False):
        """registering=True: the label has no registration yet (init-remote), so only
        REGISTERING_COMMANDS run, with no marker check and no verify()."""
        super().__init__(cfg, commands, label)
        # Optional binaries are not consulted for any local v1 operation.
        for name, path in remote_commands(cfg.code_root).items():
            commands.cfg.commands.setdefault(name, path)
        self.registering = registering
        self.marker = self.transport = self.auth = self.rclone_env = self.transport_kind = None
        self.options, self.secrets, self.verified = (), (), False

    def configure(self):
        no_core_dumps()  # before remote.json's password (SMB or transport) is read
        cfg = self.cfg
        if self.registering:
            self.marker = None
        else:
            self.marker = records(cfg)['repos'].get(self.label)
            if self.marker is None:
                raise ValueError('Remote target is not a registered seeded repository')
        transport = transport_config(cfg.state / REMOTE_CONFIG)
        kind = TransportKind(transport.get('kind', TransportKind.REST))
        secret = transport['password' if kind == TransportKind.SMB else 'transport_password']
        password(cfg)
        if secret == cfg.password.read_text().strip():
            raise ValueError('Transport password must not be the restic encryption password')
        if kind == TransportKind.SMB:
            self.configure_smb(transport)
        elif self.registering:
            # A REST receiver authenticates as an already known repository ID.
            raise ValueError('Only a kind smb remote can be initialized from the Pi')
        else:
            self.path = 'rest:http://{}:{}/'.format(transport['address'], transport['port'])
            self.auth = (self.marker['repo_id'], transport['transport_password'])
            self.secrets = (transport['transport_password'],)
        self.transport_kind = kind
        self.transport = transport

    def configure_smb(self, transport):
        rclone = self.commands.cfg.commands['rclone']
        offsite_smb.check_rclone_version(rclone)
        obscured = offsite_smb.obscure(rclone, transport['password'])
        self.path = offsite_smb.restic_repository(transport)
        self.options = tuple(offsite_smb.restic_options(rclone))
        self.rclone_env = offsite_smb.rclone_environment(transport, obscured, {})
        self.secrets = (transport['password'], obscured)

    def network_guard(self):
        self.commands.allowed()
        if self.transport is None:
            self.configure()
        try:
            if transport_config(self.cfg.state / REMOTE_CONFIG) != self.transport:
                raise ValueError('Remote configuration changed')
            # A repository being registered has no record to compare yet.
            if not self.registering and records(self.cfg)['repos'].get(self.label) != self.marker:
                raise ValueError('Remote registration changed')
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

    def hold_target(self):
        # This is a network repository, never a local offsite disk.
        return None

    def unmount(self):
        return None

    def restic(self, *args, **kwargs):
        command = tuple(map(str, args))
        if self.registering and command not in REGISTERING_COMMANDS:
            raise RuntimeError('Registering a remote repository runs only restic init and cat config')
        source_guard = kwargs.pop('guard', None)
        def guard():
            self.network_guard()
            if source_guard:
                source_guard()
        guard()
        if (self.transport_kind == TransportKind.SMB and not self.registering and not self.verified
                and command != VERIFY_COMMAND):
            raise RuntimeError('Shared-folder repository identity must be verified before any other restic command')
        result = self.commands.run('restic', '--repo', self.path, '--password-file',
                                   self.cfg.password, '--no-cache', *self.options, *args,
                                   rest_auth=self.auth, rclone_env=self.rclone_env, redact=self.secrets,
                                   guard=guard, **kwargs)
        guard()
        return result

    def redact(self, text):
        return redacted(text, self.secrets)

    def verify(self):
        if self.registering:
            raise RuntimeError('A repository being registered has no recorded identity to verify')
        password(self.cfg)
        actual = json.loads(self.restic(*VERIFY_COMMAND)).get('id')
        if actual != self.marker['repo_id']:
            raise RuntimeError('Unknown repository ID: remote decrypted config does not match registration')
        self.verified = True
        return self.marker

    def check(self, subset=None):
        from offsite_receipts import evidence_operation
        def operation():
            self.verify()
            options = ['--read-data-subset', subset] if subset else []
            return self.redact(self.restic('check', *options, timeout=None))
        return evidence_operation(self, 'check', operation, subset=subset)
