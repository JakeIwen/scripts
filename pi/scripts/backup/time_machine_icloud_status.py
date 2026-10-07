#!/usr/bin/env python3
"""Read-only, credential-free Time Machine cloud status for the dashboard."""
import json
from pathlib import Path
import subprocess
import sys

from icloud_status import apply_manual_control, build_status, number, pause_deadline, read_json

STATE = Path('/var/lib/vanpi-time-machine-icloud')
CONFIG = Path('/etc/vanpi-time-machine-icloud.json')
AUTH_ERROR = Path('/var/lib/vanpi-icloud-backup/authentication-error.json')


def status():
    def capture(args):
        return subprocess.run(args, check=True, capture_output=True, text=True, timeout=2).stdout.strip()
    unit = 'vanpi-time-machine-icloud'
    service = dict(line.split('=', 1) for line in capture([
        '/usr/bin/systemctl', 'show', unit + '.service', '-p', 'ActiveState', '-p', 'MainPID']).splitlines())
    deadline = pause_deadline(STATE)
    timer_unit = unit + ('-resume' if deadline is not None else '')
    next_check = None
    try:
        timer = capture(['/usr/bin/systemctl', 'show', timer_unit + '.timer', '-p', 'NextElapseUSecRealtime', '--value'])
        if timer and timer != 'n/a':
            next_check = float(capture(['/usr/bin/date', '--date=' + timer, '+%s']))
    except (ValueError, OSError, subprocess.SubprocessError):
        pass
    state = read_json(STATE / 'state.json', {})
    result = build_status(state, read_json(STATE / 'history.json', {}), read_json(CONFIG, {}),
                          service, next_check, auth_error=AUTH_ERROR.exists())
    apply_manual_control(result, deadline)
    # Numeric capture counters are public; paths, credentials and raw errors are not.
    if state.get('worker_pid') == int(service.get('MainPID') or 0) or not result['running']:
        for key in ('capture_bytes', 'capture_total_bytes'):
            result['progress'][key] = number(state.get('progress', {}).get(key))
    return result


if __name__ == '__main__':
    if sys.argv[1:]:
        sys.exit(2)
    try:
        print(json.dumps(status(), allow_nan=False))
    except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError):
        print(json.dumps({'available': False, 'running': False, 'attention': True}))
