"""Shared durable holds and queued admission for cloud backup workers."""
import fcntl
import json
import subprocess
import sys
import time

from icloud_status import pause_deadline, write_json

MAX_PAUSE_MINUTES = 7 * 24 * 60


def is_paused(directory, now=None):
    deadline = pause_deadline(directory)
    return deadline is not None and deadline > (time.time() if now is None else now)


def admit_worker(directory, now=None):
    """Consume a queued resume only after the shell acquired the backup lock."""
    with (directory / 'control.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if is_paused(directory, now):
            return False
        (directory / 'pause.json').unlink(missing_ok=True)
        return True


def control(action, minutes=None, *, directory, service, now=None, command=subprocess.run):
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
        command(['/usr/bin/systemctl', operation, '--no-block', service],
                check=True, capture_output=True, text=True, timeout=10)
        return {'ok': True, 'changed': True}


def run_cli(directory, service):
    args = sys.argv[1:]
    if args in (['--resume'], ['--resume-if-due']):
        result = control(args[0][2:], directory=directory, service=service)
    elif len(args) == 2 and args[0] == '--pause':
        result = control('pause', args[1], directory=directory, service=service)
    else:
        raise ValueError('expected --pause MINUTES, --resume or --resume-if-due')
    print(json.dumps(result))
