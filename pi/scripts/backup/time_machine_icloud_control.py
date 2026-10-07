#!/usr/bin/env python3
"""Durable, bounded manual holds, independent of worker-owned backup state."""
import fcntl
import json
from pathlib import Path
import subprocess
import sys
import time

from icloud_status import number, read_json, write_json

STATE = Path('/var/lib/vanpi-time-machine-icloud')
SERVICE = 'vanpi-time-machine-icloud.service'
MAX_PAUSE_MINUTES = 7 * 24 * 60


def pause_deadline(directory=STATE):
    value = read_json(directory / 'pause.json', {})
    if not isinstance(value, dict):
        raise ValueError('invalid manual pause state')
    deadline = value.get('paused_until')
    if deadline is not None and number(deadline) is None:
        raise ValueError('invalid manual pause deadline')
    return deadline


def is_paused(directory=STATE, now=None):
    deadline = pause_deadline(directory)
    return deadline is not None and deadline > (time.time() if now is None else now)


def admit_worker(directory=STATE, now=None):
    """Consume a queued resume only after the shell acquired the backup lock."""
    with (directory / 'control.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if is_paused(directory, now):
            return False
        (directory / 'pause.json').unlink(missing_ok=True)
        return True


def control(action, minutes=None, directory=STATE, now=None, command=subprocess.run):
    if action not in ('pause', 'resume', 'resume-if-due'):
        raise ValueError('unsupported Time Machine control')
    if action == 'pause':
        if not isinstance(minutes, str) or not minutes.isascii() or not minutes.isdecimal():
            raise ValueError('pause duration must be whole minutes')
        minutes = int(minutes)
        if not 1 <= minutes <= MAX_PAUSE_MINUTES:
            raise ValueError('pause duration must be between 1 minute and 7 days')
    elif minutes is not None:
        raise ValueError('resume does not accept a duration')
    now = time.time() if now is None else now
    # Serializes dashboard requests and expiry ticks without the worker's
    # lifetime backup lock, so a running capture can always be paused.
    with (directory / 'control.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if action == 'pause':
            write_json(directory / 'pause.json', {'paused_until': now + minutes * 60})
            operation = 'stop'
        else:
            deadline = pause_deadline(directory)
            if action == 'resume-if-due' and (deadline is None or deadline > now):
                return {'ok': True, 'changed': False}
            # The worker consumes this only after acquiring the backup lock.
            # A busy Pi backup therefore leaves the resume queued for the timer.
            if action == 'resume':
                write_json(directory / 'pause.json', {'paused_until': now})
            operation = 'start'
        command(['/usr/bin/systemctl', operation, '--no-block', SERVICE],
                check=True, capture_output=True, text=True, timeout=10)
        return {'ok': True, 'changed': True}


def main():
    args = sys.argv[1:]
    if args in (['--resume'], ['--resume-if-due']):
        result = control(args[0][2:])
    elif len(args) == 2 and args[0] == '--pause':
        result = control('pause', args[1])
    else:
        raise ValueError('expected --pause MINUTES, --resume or --resume-if-due')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
