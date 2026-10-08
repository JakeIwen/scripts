"""Temporary cloud-first scheduling, independent of manual pause records."""
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
import fcntl
import math
import os
from pathlib import Path
import stat
import time
from typing import TypedDict

from time_machine_store import atomic_json, read_json


class PriorityMode(str, Enum):
    NORMAL = 'normal'
    PI = 'pi'
    TIME_MACHINE = 'time-machine'


MODE_VALUES = tuple(mode.value for mode in PriorityMode)


@dataclass(frozen=True)
class CloudJob:
    label: str
    service: str
    directory: Path
    config: Path


JOBS = {
    PriorityMode.PI: CloudJob('Pi iCloud', 'vanpi-icloud-backup.service',
        Path('/var/lib/vanpi-icloud-backup'), Path('/etc/vanpi-icloud-backup.json')),
    PriorityMode.TIME_MACHINE: CloudJob('Mac iCloud', 'vanpi-time-machine-icloud.service',
        Path('/var/lib/vanpi-time-machine-icloud'), Path('/etc/vanpi-time-machine-icloud.json')),
}
DIRECTORY = Path('/var/lib/vanpi-backup-priority')
BORG_STAMP = Path('/home/pi/backups/stamps/borg_ok')


class PriorityRecord(TypedDict):
    version: int
    mode: str
    requested_at: float
    generation: str | None
    previous_success_at: float


def timestamp(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def private_directory(directory, create=False):
    if create:
        directory.mkdir(mode=0o700, exist_ok=True)
    try:
        s = directory.lstat()
    except FileNotFoundError:
        return False
    if not stat.S_ISDIR(s.st_mode) or s.st_uid != os.geteuid() or s.st_mode & 0o077:
        raise ValueError('Backup priority storage failed its ownership checks.')
    return True


@contextmanager
def locked(directory):
    private_directory(directory, create=True)
    fd = os.open(directory / 'control.lock', os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        s = os.fstat(fd)
        if not stat.S_ISREG(s.st_mode) or s.st_uid != os.geteuid() or s.st_nlink != 1 or s.st_mode & 0o022:
            raise ValueError('Backup priority lock failed its ownership checks.')
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)


def read_record(directory):
    if not private_directory(directory):
        return None
    path = directory / 'priority.json'
    try:
        s = path.lstat()
    except FileNotFoundError:
        return None
    if (not stat.S_ISREG(s.st_mode) or s.st_uid != os.geteuid() or s.st_nlink != 1
            or s.st_mode & 0o077 or s.st_size > 65536):
        raise ValueError('Backup priority record failed its ownership checks.')
    row = read_json(path)
    if (not isinstance(row, dict) or type(row.get('version')) is not int or row['version'] != 1
            or row.get('mode') not in MODE_VALUES
            or not timestamp(row.get('requested_at')) or not timestamp(row.get('previous_success_at'))
            or ('completed_at' in row and not timestamp(row['completed_at']))
            or (row.get('generation') is not None and not isinstance(row['generation'], str))):
        raise ValueError('Backup priority record is invalid.')
    return row


def policy(directory=None, jobs=None):
    directory, jobs = directory or DIRECTORY, jobs or JOBS
    record = read_record(directory)
    if not record or record['mode'] == PriorityMode.NORMAL or record.get('completed_at') is not None:
        return PriorityMode.NORMAL
    mode = PriorityMode(record['mode'])
    state = read_json(jobs[mode].directory / 'state.json', {})
    verified = state.get('last_success_at', 0)
    if (timestamp(verified) and verified > max(record['previous_success_at'], record['requested_at'])
            and (record['generation'] is None or state.get('last_generation') == record['generation'])):
        return PriorityMode.NORMAL
    return mode


def complete_job(kind, state, directory=None):
    directory = directory or DIRECTORY
    record = read_record(directory)
    if not record or record['mode'] != kind or record.get('completed_at') is not None:
        return
    with locked(directory):
        record = read_record(directory)
        verified = state.get('last_success_at', 0)
        if (record and record['mode'] == kind and timestamp(verified)
                and verified > max(record['requested_at'], record['previous_success_at'])
                and record['generation'] in (None, state.get('last_generation'))):
            # Keep completion latched after last_generation advances again.
            # Read-only dashboard projections never mutate this record.
            atomic_json(directory / 'priority.json', {**record, 'completed_at': verified})


def allows(kind, directory=None, jobs=None):
    if kind not in ('disk', PriorityMode.PI, PriorityMode.TIME_MACHINE):
        raise ValueError('Unknown backup job.')
    selected = policy(directory, jobs)
    return selected == PriorityMode.NORMAL or kind == selected


def selected_directory(directory):
    selected = policy()
    return selected != PriorityMode.NORMAL and JOBS[selected].directory == directory


def eligibility(mode, jobs=None, now=None, borg_stamp=None):
    jobs, now = jobs or JOBS, time.time() if now is None else now
    job = jobs[mode]
    if not job.config.is_file() or not job.directory.is_dir():
        return False, 'This cloud backup is not installed.'
    state = read_json(job.directory / 'state.json', {})
    cfg = read_json(job.config, {})
    if not state.get('pending') and now - (state.get('last_success_at') or 0) < cfg.get('interval_days', 7) * 86400:
        return False, 'This cloud backup is already current.'
    if mode == PriorityMode.PI and not state.get('progress', {}).get('upload_total_bytes'):
        try:
            age = now - (borg_stamp or BORG_STAMP).stat().st_mtime
        except FileNotFoundError:
            age = float('inf')
        if not 0 <= age <= cfg.get('max_source_age_hours', 48) * 3600:
            return False, 'A fresh Pi Borg backup is needed before Pi cloud priority can start.'
    return True, None


def set_policy(value, directory=None, jobs=None, now=None, borg_stamp=None):
    mode = PriorityMode(value)
    directory, jobs, now = directory or DIRECTORY, jobs or JOBS, time.time() if now is None else now
    with locked(directory):
        if mode == PriorityMode.NORMAL:
            (directory / 'priority.json').unlink(missing_ok=True)
        else:
            allowed, reason = eligibility(mode, jobs, now, borg_stamp)
            if not allowed:
                raise ValueError(reason)
            state = read_json(jobs[mode].directory / 'state.json', {})
            if not state.get('pending') and (state.get('last_success_at') or 0) > now:
                raise ValueError('This cloud backup just completed; refresh its status.')
            record: PriorityRecord = {'version': 1, 'mode': mode, 'requested_at': now,
                'generation': state.get('pending'), 'previous_success_at': state.get('last_success_at') or 0}
            atomic_json(directory / 'priority.json', record)
    return mode


def status(directory=None, jobs=None, now=None):
    directory, jobs, now = directory or DIRECTORY, jobs or JOBS, time.time() if now is None else now
    record = read_record(directory)
    selected = policy(directory, jobs)
    entries = []
    for mode, job in jobs.items():
        state = read_json(job.directory / 'state.json', {})
        eligible, reason = eligibility(mode, jobs, now)
        entries.append({'kind': mode, 'label': job.label, 'eligible': eligible, 'reason': reason,
                        'last_success_at': state.get('last_success_at'),
                        'waiting': selected != PriorityMode.NORMAL and selected != mode})
    return {'mode': selected, 'requested_at': record['requested_at'] if record else None,
            'completed': bool(record and selected == PriorityMode.NORMAL), 'jobs': entries}
