"""Shared durable holds and queued admission for cloud backup workers."""
import fcntl
from contextlib import ExitStack
import json
from pathlib import Path
import subprocess
import sys
import time

from icloud_status import INDEFINITE_PAUSE, number, pause_deadline, read_json, write_json

MAX_PAUSE_MINUTES = 7 * 24 * 60
CLOUD_JOBS = {
    'vanpi-icloud-backup.service': Path('/var/lib/vanpi-icloud-backup'),
    'vanpi-time-machine-icloud.service': Path('/var/lib/vanpi-time-machine-icloud'),
}


def is_paused(directory, now=None):
    deadline = pause_deadline(directory)
    return deadline == INDEFINITE_PAUSE or (deadline is not None and deadline > (time.time() if now is None else now))


def admit_worker(directory, now=None):
    """Consume a queued resume only after the shell acquired the backup lock."""
    with (directory / 'control.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if is_paused(directory, now):
            return False
        (directory / 'pause.json').unlink(missing_ok=True)
        return True


def request_service(operation, service, command):
    command(['/usr/bin/systemctl', operation, '--no-block', service],
            check=True, capture_output=True, text=True, timeout=10)


def automatic_retry_due(directory, now):
    state = read_json(directory / 'state.json', {})
    retry_at = number(state.get('retry_at'))
    return bool(state.get('phase') == 'deferred' and retry_at is not None and retry_at <= now)


def take_turn(service, *, jobs=CLOUD_JOBS, now=None, command=subprocess.run):
    """Pause the peer and queue this job, without touching the common backup lock."""
    if service not in jobs or set(jobs) != set(CLOUD_JOBS):
        raise ValueError('unsupported cloud backup selection')
    now = time.time() if now is None else now
    other = next(name for name in jobs if name != service)
    # Both intents are serialized against worker admission and ordinary
    # controls. A concurrent control fails before any state is changed.
    with ExitStack() as stack:
        for directory in sorted(jobs.values()):
            lock = stack.enter_context((directory / 'control.lock').open('a'))
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        write_json(jobs[other] / 'pause.json', {'paused_until': INDEFINITE_PAUSE})
        write_json(jobs[service] / 'pause.json', {'paused_until': now})
        request_service('stop', other, command)
        request_service('start', service, command)
    return {'ok': True, 'changed': True}


def control(action, minutes=None, *, directory, service, now=None, command=subprocess.run):
    if action not in ('pause', 'resume', 'resume-if-due'):
        raise ValueError('unsupported cloud backup control')
    if action == 'pause' and minutes != INDEFINITE_PAUSE:
        if not isinstance(minutes, str) or not minutes.isascii() or not minutes.isdecimal():
            raise ValueError('pause duration must be whole minutes')
        minutes = int(minutes)
        if not 1 <= minutes <= MAX_PAUSE_MINUTES:
            raise ValueError('pause duration must be between 1 minute and 7 days')
    elif action != 'pause' and minutes is not None:
        raise ValueError('resume does not accept a duration')
    now = time.time() if now is None else now
    # Serializes dashboard requests and expiry ticks without the worker's
    # lifetime backup lock, so a running capture can always be paused.
    with (directory / 'control.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if action == 'pause':
            deadline = INDEFINITE_PAUSE if minutes == INDEFINITE_PAUSE else now + minutes * 60
            write_json(directory / 'pause.json', {'paused_until': deadline})
            operation = 'stop'
        else:
            deadline = pause_deadline(directory)
            if action == 'resume-if-due':
                if deadline == INDEFINITE_PAUSE or (deadline is not None and deadline > now):
                    return {'ok': True, 'changed': False}
                if deadline is None and not automatic_retry_due(directory, now):
                    return {'ok': True, 'changed': False}
            # The worker consumes this only after acquiring the backup lock.
            # A busy Pi backup therefore leaves the resume queued for the timer.
            if action == 'resume':
                write_json(directory / 'pause.json', {'paused_until': now})
            operation = 'start'
        request_service(operation, service, command)
        return {'ok': True, 'changed': True}


def run_cli(directory, service):
    args = sys.argv[1:]
    if args == ['--take-turn']:
        result = take_turn(service)
    elif args in (['--resume'], ['--resume-if-due']):
        result = control(args[0][2:], directory=directory, service=service)
    elif len(args) == 2 and args[0] == '--pause':
        result = control('pause', args[1], directory=directory, service=service)
    else:
        raise ValueError('expected --pause MINUTES|indefinite, --resume, --resume-if-due or --take-turn')
    print(json.dumps(result))
