"""Shared durable holds and queued admission for cloud backup workers."""
import fcntl
from contextlib import ExitStack
import json
from pathlib import Path
import subprocess
import sys
import time

from icloud_status import INDEFINITE_PAUSE, number, pause_deadline, read_json, write_json
import backup_priority as priority
import local_fairness
from protected_json import ProtectedStateAccessError

MAX_PAUSE_MINUTES = 7 * 24 * 60
CLOUD_JOBS = {job.service: job.directory for job in priority.JOBS.values()}


def is_paused(directory, now=None, *, priority_directory=None, jobs=None):
    deadline = pause_deadline(directory)
    try:
        held = local_fairness.cloud_held(directory, now, directory=priority_directory, jobs=jobs)
    except (ProtectedStateAccessError, PermissionError):
        # Unknown private fairness state cannot admit or resume a cloud worker.
        # Owner integrity/schema errors remain strict; this is not a broad fallback.
        held = True
    return (held or deadline == INDEFINITE_PAUSE
            or (deadline is not None and deadline > (time.time() if now is None else now)))


def pause_for_local_turn(*, directory, service, command=subprocess.run, priority_directory=None, jobs=None):
    """The durable fairness lease is independent of the user's pause.json."""
    with (directory / 'control.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if not local_fairness.cloud_held(directory, directory=priority_directory, jobs=jobs):
            raise ValueError('No active local fairness reservation.')
        request_service('stop', service, command)


def resume_after_local_turn(*, directory, service, now=None, command=subprocess.run,
                            priority_directory=None, jobs=None):
    with (directory / 'control.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if not is_paused(directory, now, priority_directory=priority_directory, jobs=jobs):
            # Do not rewrite even an expired manual hold. Normal priority retries
            # also recover a failed start or an interrupted window supervisor.
            request_service('start', service, command)



def admit_worker(directory, now=None, *, priority_directory=None, jobs=None):
    """Consume a queued resume only after the shell acquired the backup lock."""
    with (directory / 'control.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if is_paused(directory, now, priority_directory=priority_directory, jobs=jobs):
            return False
        (directory / 'pause.json').unlink(missing_ok=True)
        return True


def request_service(operation, service, command):
    command(['/usr/bin/systemctl', operation, '--no-block', service],
            check=True, capture_output=True, text=True, timeout=10)


def automatic_retry_due(directory, now, *, priority_directory=None, jobs=None):
    state = read_json(directory / 'state.json', {})
    kind = priority.job_kind(directory, jobs)
    if kind is not None:
        priority.complete_job(kind, state, directory=priority_directory)
    retry_at = number(state.get('retry_at'))
    due = state.get('phase') == 'deferred' and retry_at is not None and retry_at <= now
    selected = (priority.selected_directory(directory, directory=priority_directory, jobs=jobs)
                and state.get('phase') != 'authentication_required')
    return bool(due or (selected and now - state.get('last_attempt_at', 0) >= 300))


def take_turn(service, *, jobs=CLOUD_JOBS, now=None, command=subprocess.run,
              priority_directory=None, priority_jobs=None):
    """Pause the peer and queue this job, without touching the common backup lock."""
    if service not in jobs or set(jobs) != set(CLOUD_JOBS):
        raise ValueError('unsupported cloud backup selection')
    registry = priority.JOBS if priority_jobs is None else priority_jobs
    selected = priority.policy(priority_directory, registry)
    if selected != priority.PriorityMode.NORMAL and registry[selected].service != service:
        raise ValueError('Change Backup priority before switching cloud jobs.')
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


def control(action, minutes=None, *, directory, service, now=None, command=subprocess.run,
            priority_directory=None, jobs=None):
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
                if is_paused(directory, now, priority_directory=priority_directory, jobs=jobs):
                    return {'ok': True, 'changed': False}
                if deadline is None and not automatic_retry_due(directory, now,
                        priority_directory=priority_directory, jobs=jobs):
                    return {'ok': True, 'changed': False}
            # The worker consumes this only after acquiring the backup lock.
            # A busy Pi backup therefore leaves the resume queued for the timer.
            if action == 'resume':
                write_json(directory / 'pause.json', {'paused_until': now})
            if is_paused(directory, now, priority_directory=priority_directory, jobs=jobs):
                return {'ok': True, 'changed': False}
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
