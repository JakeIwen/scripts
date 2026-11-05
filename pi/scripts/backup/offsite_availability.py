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
        if not isinstance(status, dict) or not isinstance(status.get('Peer'), dict):
            raise ValueError('unknown Tailscale state')
        peers = [row for row in status['Peer'].values()
                 if isinstance(row, dict) and transport['address'] in row.get('TailscaleIPs', [])]
        if (status.get('BackendState') != 'Running' or status.get('Self', {}).get('Online') is False
                or len(peers) == 1 and peers[0].get('Online') is False):
            return {'state': 'unavailable', 'reason': 'network-offline'}
        if len(peers) != 1 or peers[0].get('Online') is not True or status.get('Self', {}).get('Online') is not True:
            raise ValueError('peer state unknown')
        result = command(['/usr/bin/ip', '-j', '-4', 'route', 'get', transport['address']],
                         capture_output=True, text=True, timeout=2, check=False)
        if result.returncode:
            raise ValueError('route failed')
        route = json.loads(result.stdout)
        if not isinstance(route, list) or len(route) != 1 or not isinstance(route[0], dict):
            raise ValueError('route unknown')
        if route[0].get('dev') != 'tailscale0':
            return {'state': 'unavailable', 'reason': 'network-offline'}
        # Online peer/route are not proof the NAS has the right disk mounted.
        return {'state': 'unknown', 'reason': 'target-unverified'}
    except (OSError, ValueError, RuntimeError, TypeError, AttributeError, subprocess.SubprocessError):
        return {'state': 'unknown', 'reason': 'probe-failed'}
