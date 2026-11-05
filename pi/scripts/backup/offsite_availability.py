"""Bounded local network evidence, never a REST request or a disk probe."""
import json
import subprocess

from offsite_config import REMOTE_CONFIG
from offsite_remote import transport_config


def remote_availability(cfg, *, command=subprocess.run):
    path = cfg.state / REMOTE_CONFIG
    if not path.exists() and not path.is_symlink():
        return {'state': 'unavailable', 'reason': 'not-configured'}
    try:
        transport = transport_config(path)
        result = command(['/usr/bin/tailscale', 'status', '--json'], capture_output=True,
                         text=True, timeout=2, check=False)
        if result.returncode:
            raise ValueError('status failed')
        status = json.loads(result.stdout)
        if not isinstance(status, dict):
            raise ValueError('unknown Tailscale state')
        backend = status.get('BackendState')
        if backend in ('Stopped', 'NeedsLogin', 'NeedsMachineAuth'):
            return {'state': 'unavailable', 'reason': 'network-offline'}
        if not isinstance(status.get('Peer'), dict):
            raise ValueError('unknown Tailscale peers')
        # Missing/new daemon states are incomplete evidence, not a confirmed outage.
        own = status.get('Self')
        if backend != 'Running' or not isinstance(own, dict) or type(own.get('Online')) is not bool:
            raise ValueError('unknown Tailscale state')
        for row in status['Peer'].values():
            if (not isinstance(row, dict) or not isinstance(row.get('TailscaleIPs'), list)
                    or any(not isinstance(ip, str) for ip in row['TailscaleIPs'])):
                raise ValueError('unknown peer identity')
        peers = [row for row in status['Peer'].values() if transport['address'] in row['TailscaleIPs']]
        if own['Online'] is False or len(peers) == 1 and peers[0].get('Online') is False:
            return {'state': 'unavailable', 'reason': 'network-offline'}
        if len(peers) != 1 or peers[0].get('Online') is not True:
            raise ValueError('peer state unknown')
        result = command(['/usr/bin/ip', '-j', '-4', 'route', 'get', transport['address']],
                         capture_output=True, text=True, timeout=2, check=False)
        if result.returncode:
            raise ValueError('route failed')
        route = json.loads(result.stdout)
        if (not isinstance(route, list) or len(route) != 1 or not isinstance(route[0], dict)
                or not isinstance(route[0].get('dev'), str) or not route[0]['dev']):
            raise ValueError('route unknown')
        if route[0].get('dev') != 'tailscale0':
            return {'state': 'unavailable', 'reason': 'network-offline'}
        # Online peer/route are not proof the NAS has the right disk mounted.
        return {'state': 'unknown', 'reason': 'target-unverified'}
    except (OSError, ValueError, RuntimeError, TypeError, AttributeError, subprocess.SubprocessError):
        return {'state': 'unknown', 'reason': 'probe-failed'}
