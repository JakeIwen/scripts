"""A leased, one-use local turn; never owns or changes the shared backup lock."""
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
import secrets
import stat
import sys
import time
import uuid
from typing import TypedDict

import backup_priority as priority
from time_machine_store import atomic_json
from protected_json import MISSING, ProtectedStateAccessError, read_protected_json


class LocalJob(str, Enum):
    BORG = 'local-borg'
    EXFAT = 'local-exfat'


class TurnPhase(str, Enum):
    WAITING = 'waiting'
    RUNNING = 'running'
    COMPLETE = 'complete'
    BACKOFF = 'backoff'


class StateStatus(str, Enum):
    AVAILABLE = 'available'
    UNAVAILABLE = 'unavailable'


class ClockStatus(str, Enum):
    AVAILABLE = 'available'
    UNAVAILABLE = 'boot identity unavailable'


class StampAge(str, Enum):
    CURRENT = 'current'
    STALE = 'stale'
    MISSING = 'missing'
    UNKNOWN = 'unknown'


ACTIVE_PHASES = (TurnPhase.WAITING, TurnPhase.RUNNING)
TOKEN_ENV = 'VANPI_LOCAL_FAIRNESS_TOKEN'
LEASE_SECONDS = 90
BOOT_ID_FILE = Path('/proc/sys/kernel/random/boot_id')
CLOCK_FIELDS = ('started_ticks', 'admission_ticks', 'lease_ticks', 'retry_ticks')


@contextmanager
def supervisor_slot(directory=None):
    """Fence new scheduled supervisors for the complete guarded rollback."""
    with priority.locked(directory or priority.DIRECTORY, name=priority.ControlLock.LOCAL_WINDOW):
        yield


@dataclass(frozen=True)
class BootClock:
    boot_id: str
    ticks: float


def valid_boot_id(value):
    try:
        return isinstance(value, str) and str(uuid.UUID(value)) == value
    except ValueError:
        return False


def boot_clock(*, boot_id_path=BOOT_ID_FILE, elapsed=None):
    """Boot identity and suspend-inclusive elapsed time, independently injectable."""
    boot_id = boot_id_path.read_text().strip()
    if elapsed is None:
        elapsed = (lambda: time.clock_gettime(time.CLOCK_BOOTTIME)) if hasattr(time, 'CLOCK_BOOTTIME') else time.monotonic
    ticks = elapsed()
    if not valid_boot_id(boot_id) or not priority.timestamp(ticks):
        raise ValueError('Cannot verify the local fairness boot clock.')
    return BootClock(boot_id, ticks)


@dataclass(frozen=True)
class FairnessConfig:
    stale_hours: float = 24
    admission_seconds: float = 300
    backoff_hours: float = 6

    def __post_init__(self):
        for value, maximum in ((self.stale_hours, 168), (self.admission_seconds, 1800),
                               (self.backoff_hours, 168)):
            if not priority.timestamp(value) or not 0 < value <= maximum:
                raise ValueError('Invalid local fairness configuration.')


class FairnessRecord(TypedDict):
    version: int
    token: str
    mode: str
    generation: str | None
    requested_at: float
    admission_until: float
    lease_until: float
    retry_after: float
    backoff_seconds: float
    boot_id: str
    started_ticks: float
    admission_ticks: float
    lease_ticks: float
    retry_ticks: float
    phase: str
    jobs: list[str]
    admitted: list[str]
    reason: str


def stamp_age(path, now, stale_hours):
    try:
        info = path.lstat()
    except FileNotFoundError:
        return StampAge.MISSING
    if not stat.S_ISREG(info.st_mode) or info.st_mtime > now:
        return StampAge.UNKNOWN
    return StampAge.STALE if now - info.st_mtime > stale_hours * 3600 else StampAge.CURRENT


def read_record(directory=None):
    directory = directory or priority.DIRECTORY
    if not priority.private_directory(directory):
        return None
    row = read_protected_json(directory / 'local-fairness.json', 'Unsafe local fairness record.')
    if row is MISSING:
        return None
    if (not isinstance(row, dict) or type(row.get('version')) is not int or row['version'] not in (1, 2)
            or row.get('phase') not in tuple(TurnPhase)
            or not isinstance(row.get('mode'), str) or row['mode'] not in priority.JOBS
            or not isinstance(row.get('token'), str) or len(row['token']) != 64
            or any(c not in '0123456789abcdef' for c in row['token'])
            or not isinstance(row.get('reason'), str)
            or (row.get('generation') is not None and not isinstance(row['generation'], str))
            or any(not priority.timestamp(row.get(k)) for k in (
                'requested_at', 'admission_until', 'lease_until', 'retry_after', 'backoff_seconds'))
            or not 0 < row['backoff_seconds'] <= 168 * 3600
            or not isinstance(row.get('jobs'), list) or not row['jobs']
            or any(not isinstance(v, str) or v not in tuple(LocalJob) for v in row['jobs'])
            or len(row['jobs']) != len(set(row['jobs']))
            or not isinstance(row.get('admitted'), list)
            or any(not isinstance(v, str) or v not in row['jobs'] for v in row['admitted'])
            or len(row['admitted']) != len(set(row['admitted']))):
        raise ValueError('Invalid local fairness record.')
    if row['version'] == 2 and (not valid_boot_id(row.get('boot_id'))
            or any(not priority.timestamp(row.get(k)) for k in CLOCK_FIELDS)
            or not 0 <= row['admission_ticks'] - row['started_ticks'] <= 1800):
        raise ValueError('Invalid local fairness clock record.')
    return row


def admission_remaining(row, reading=None):
    if not row or row['version'] != 2 or row['phase'] not in ACTIVE_PHASES:
        return 0
    reading = reading or boot_clock()
    if row['boot_id'] != reading.boot_id or reading.ticks < row['started_ticks']:
        return 0
    return max(0, row['admission_ticks'] - reading.ticks)


def admission_open(row, reading=None):
    return admission_remaining(row, reading) > 0


def active(row, reading=None):
    if not row or row['version'] != 2 or row['phase'] not in ACTIVE_PHASES:
        return False
    reading = reading or boot_clock()
    return (row['boot_id'] == reading.boot_id and row['started_ticks'] <= reading.ticks
            and 0 < row['lease_ticks'] - reading.ticks <= LEASE_SECONDS
            and (row['phase'] == TurnPhase.RUNNING or admission_open(row, reading)))


def backoff_remaining(row, now, reading):
    if row['phase'] != TurnPhase.BACKOFF:
        return 0, False
    same_boot = row['version'] == 2 and row['boot_id'] == reading.boot_id
    delay = row['retry_ticks'] - reading.ticks if same_boot else row['retry_after'] - now
    invalid = delay > row['backoff_seconds'] or (same_boot and reading.ticks < row['started_ticks'])
    return (0 if invalid else max(0, delay)), invalid


def status(directory=None, now=None):
    now = time.time() if now is None else now
    directory = directory or priority.DIRECTORY
    try:
        row = read_record(directory)
    except (ProtectedStateAccessError, PermissionError):
        return {'state_status': StateStatus.UNAVAILABLE, 'active': None,
                'reason': 'Private fairness state is unavailable to this caller.'}
    if not row:
        return None
    public = {k: v for k, v in row.items() if k != 'token'} | {'state_status': StateStatus.AVAILABLE}
    try:
        reading = boot_clock()
    except (OSError, ValueError):
        unknown = row['version'] == 2 and row['phase'] in ACTIVE_PHASES
        return public | {'active': None if unknown else False,
            'clock_status': ClockStatus.UNAVAILABLE, 'admission_remaining_seconds': None,
            'lease_remaining_seconds': None, 'backoff_remaining_seconds': None}
    held = active(row, reading)
    # Wall deadlines are reports, not admission or lease authority.
    return public | {'clock_status': ClockStatus.AVAILABLE,
        'active': held, 'admission_remaining_seconds': admission_remaining(row, reading),
        'lease_remaining_seconds': row['lease_ticks'] - reading.ticks if held else 0,
        'backoff_remaining_seconds': backoff_remaining(row, now, reading)[0]}


def future_grant(row, reading):
    return bool(row and row['version'] == 2 and row['phase'] in ACTIVE_PHASES
                and row['boot_id'] == reading.boot_id and row['started_ticks'] > reading.ticks)


def retire_future_grant(row, reading, *, directory=None, now=None):
    """Called with the priority control lock held, never by status projections."""
    if future_grant(row, reading):
        now = time.time() if now is None else now
        end_record(row, False, now, 'impossible future-tick grant retired', reading, directory)
        print('Local fairness: impossible future-tick grant retired.', file=sys.stderr, flush=True)


def cloud_held(cloud_directory, now=None, *, directory=None, jobs=None):
    """The cloud target and private priority namespace are separate directories."""
    kind = priority.job_kind(cloud_directory, jobs)
    if kind is None:
        return False
    directory = directory or priority.DIRECTORY
    row = read_record(directory)
    if not row or row['version'] != 2 or row['phase'] not in ACTIVE_PHASES:
        return False
    reading = boot_clock()
    if future_grant(row, reading):
        rejected = row['token']
        with priority.locked(directory):
            row = read_record(directory)
            if row and row['token'] == rejected:
                retire_future_grant(row, reading, directory=directory, now=now)
        reading = boot_clock()
    return bool(active(row, reading) and row['mode'] == kind)


def save(row, directory=None):
    atomic_json((directory or priority.DIRECTORY) / 'local-fairness.json', row)


def end_record(row, success, now, reason, reading, directory=None):
    if (row['version'] != 2 or row['boot_id'] != reading.boot_id
            or row['started_ticks'] > reading.ticks):
        row.update(version=2, boot_id=reading.boot_id,
                   started_ticks=reading.ticks, admission_ticks=reading.ticks)
    delay = 0 if success else row['backoff_seconds']
    row.update(phase=TurnPhase.COMPLETE if success else TurnPhase.BACKOFF,
               lease_until=now, lease_ticks=reading.ticks,
               retry_after=now + delay, retry_ticks=reading.ticks + delay, reason=reason)
    save(row, directory)


def reconcile_backoff(row, now, reading, log, directory=None):
    if not row or row['phase'] != TurnPhase.BACKOFF:
        return False
    delay, invalid = backoff_remaining(row, now, reading)
    if invalid:
        log('Local fairness: invalid clock-dependent backoff cleared.')
    if row['version'] != 2 or row['boot_id'] != reading.boot_id:
        row.update(version=2, boot_id=reading.boot_id, started_ticks=reading.ticks,
                   admission_ticks=reading.ticks, lease_ticks=reading.ticks)
    # Rebase the reporting deadline and cross-boot backoff onto this boot clock.
    row.update(retry_after=now + delay, retry_ticks=reading.ticks + delay)
    save(row, directory)
    return delay > 0


def reserve(stamps, config, *, now=None, log=print, directory=None, jobs=None):
    now = time.time() if now is None else now
    directory = directory or priority.DIRECTORY
    with priority.locked(directory):
        previous = read_record(directory)
        reading = boot_clock()
        if active(previous, reading):
            log('Local fairness: another scheduled turn is active.')
            return None
        if previous and previous['phase'] in ACTIVE_PHASES:
            end_record(previous, False, now, 'abandoned or expired turn', reading, directory)
        if reconcile_backoff(previous, now, reading, log, directory):
            log('Local fairness: failed-turn backoff is active.')
            return None
        mode = priority.policy(directory, jobs)
        if mode == priority.PriorityMode.NORMAL:
            return None
        due = []
        for kind, path in stamps.items():
            age = stamp_age(path, now, config.stale_hours)
            if age == StampAge.UNKNOWN:
                log(f'Local fairness: {kind.value} success stamp is future-dated or unsafe; age unknown, skipping.')
            elif age in (StampAge.STALE, StampAge.MISSING):
                due.append(kind.value)
        if not due:
            return None
        selected = priority.read_record(directory)
        row: FairnessRecord = dict(version=2, token=secrets.token_hex(32), mode=mode,
            generation=selected['generation'], requested_at=now,
            admission_until=now + config.admission_seconds, lease_until=now + LEASE_SECONDS,
            retry_after=now, backoff_seconds=config.backoff_hours * 3600,
            boot_id=reading.boot_id, started_ticks=reading.ticks,
            admission_ticks=reading.ticks + config.admission_seconds,
            lease_ticks=reading.ticks + LEASE_SECONDS, retry_ticks=reading.ticks,
            phase=TurnPhase.WAITING, jobs=due, admitted=[], reason='waiting for cloud to yield')
        save(row, directory)
        log('Local fairness: reserved one scheduled Borg/EXFAT turn.')
        return row


def heartbeat(token, now=None, *, directory=None):
    now = time.time() if now is None else now
    directory = directory or priority.DIRECTORY
    with priority.locked(directory):
        row = read_record(directory)
        reading = boot_clock()
        retire_future_grant(row, reading, directory=directory, now=now)
        if not active(row, reading) or row['token'] != token:
            return False
        row.update(lease_until=now + LEASE_SECONDS, lease_ticks=reading.ticks + LEASE_SECONDS)
        save(row, directory)
        return True


def next_admission(token, kind, config, *, now=None, directory=None):
    """After a child exits, give the next job its own bounded start window."""
    now = time.time() if now is None else now
    directory = directory or priority.DIRECTORY
    with priority.locked(directory):
        row = read_record(directory)
        reading = boot_clock()
        if (not active(row, reading) or row['token'] != token
                or row['phase'] != TurnPhase.RUNNING
                or kind not in row['jobs'] or kind in row['admitted']):
            return None
        # WAITING prevents repeated renewal before this next job is admitted.
        row.update(phase=TurnPhase.WAITING, started_ticks=reading.ticks,
                   admission_ticks=reading.ticks + config.admission_seconds,
                   admission_until=now + config.admission_seconds,
                   reason='waiting for the next scheduled local backup')
        save(row, directory)
        return row


def admit(kind, token, *, now=None, directory=None, jobs=None):
    """Called only after normal shared-lock acquisition, never for generic disk jobs."""
    now = time.time() if now is None else now
    directory = directory or priority.DIRECTORY
    if kind not in tuple(LocalJob) or not token:
        return False
    with priority.locked(directory):
        row = read_record(directory)
        reading = boot_clock()
        retire_future_grant(row, reading, directory=directory, now=now)
        selected = priority.read_record(directory)
        if (not active(row, reading) or not admission_open(row, reading)
                or not secrets.compare_digest(row['token'], token)
                or kind not in row['jobs'] or kind in row['admitted']
                or priority.policy(directory, jobs) != row['mode']
                or selected['generation'] != row['generation']):
            return False
        row['admitted'].append(kind)
        row.update(phase=TurnPhase.RUNNING, reason='scheduled local backup running')
        save(row, directory)
        return True


def finish(token, success, reason, *, now=None, directory=None):
    now = time.time() if now is None else now
    directory = directory or priority.DIRECTORY
    reading = boot_clock()
    with priority.locked(directory):
        row = read_record(directory)
        if row and row['token'] == token:
            end_record(row, success, now, reason, reading, directory)
