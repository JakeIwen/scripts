"""Durable one-copy requests without changing verification history or schedules."""
from typing import TypedDict
import math
import time

from protected_json import MISSING, read_protected_json
from time_machine_store import atomic_json, read_json


class BackupNowRequest(TypedDict):
    version: int
    requested_at: float
    previous_success_at: float
    previous_generation: str | None


def pending(directory, state=None):
    record = read_protected_json(directory / 'backup-now.json', 'Invalid backup-now request.')
    if record is MISSING:
        return False
    if (not isinstance(record, dict) or type(record.get('version')) is not int or record['version'] != 1
            or (record.get('previous_generation') is not None and not isinstance(record['previous_generation'], str))
            or any(type(record.get(key)) not in (int, float) or not math.isfinite(record[key])
                   or record[key] < 0 for key in ('requested_at', 'previous_success_at'))):
        raise ValueError('Invalid backup-now request.')
    state = read_json(directory / 'state.json', {}) if state is None else state
    return (state.get('last_generation') == record.get('previous_generation')
            and (state.get('last_success_at') or 0) <= record['previous_success_at'])


def request(directory, now=None):
    """Caller holds control.lock; repeated clicks coalesce into one recovery copy."""
    state = read_json(directory / 'state.json', {})
    if pending(directory, state):
        return
    record: BackupNowRequest = {'version': 1, 'requested_at': time.time() if now is None else now,
                               'previous_success_at': state.get('last_success_at') or 0,
                               'previous_generation': state.get('last_generation')}
    atomic_json(directory / 'backup-now.json', record)


def due(cfg, state, directory, now=None):
    return bool(state.get('pending')
                or (time.time() if now is None else now) - (state.get('last_success_at') or 0)
                >= cfg['interval_days'] * 86400 or pending(directory, state))
