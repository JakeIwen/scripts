"""BTT's bundled CLI over its user-private local socket, without Apple Events.

The Python twin of btt_socket.js. Trigger payloads can hold private text, so a
failure never echoes BTT's response.
"""
from __future__ import annotations

import json
from pathlib import Path
import subprocess

BTTCLI = Path('/Applications/BetterTouchTool.app/Contents/SharedSupport/bin/bttcli')


def call(command, **parameters):
    result = subprocess.run([str(BTTCLI), command, *(f'{key}={value}' for key, value in parameters.items())],
                            capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise RuntimeError(f'BTT socket command {command} failed; no success confirmed.')
    return result.stdout


def exported_trigger(uuid):
    """BTT's runtime export of one trigger, or None when it has no such UUID."""
    value = json.loads(call('get_trigger', uuid=uuid) or '{}')
    if not isinstance(value, (dict, list)):
        raise RuntimeError('Invalid BTT runtime response; no success confirmed.')
    hits = [item for item in (value if isinstance(value, list) else [value])
            if isinstance(item, dict) and item.get('BTTUUID') == uuid]
    if len(hits) > 1:
        raise RuntimeError(f'BTT exported more than one trigger {uuid}; no success confirmed.')
    return hits[0] if hits else None
