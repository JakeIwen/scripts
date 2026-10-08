"""Expiring, single-use permission to stop Time Machine for a live capture."""
import time
import uuid

from backup_priority import JOBS, PriorityMode, locked, timestamp
from time_machine_store import atomic_json, read_json

REQUEST_SECONDS = 1800


def capability(directory=None, now=None):
    directory = directory or JOBS[PriorityMode.TIME_MACHINE].directory
    now = time.time() if now is None else now
    row = read_json(directory / 'mac-coordinator.json', {})
    return row.get('stop_for_capture') is True and 0 <= now - row.get('seen_at', 0) <= 300


def request_stop(directory=None, now=None):
    directory = directory or JOBS[PriorityMode.TIME_MACHINE].directory
    now = time.time() if now is None else now
    with locked(directory):
        if not capability(directory, now):
            raise ValueError('The Mac capture coordinator needs an update or a fresh connection.')
        state = read_json(directory / 'state.json', {})
        if state.get('pending') and state.get('progress', {}).get('capture_complete'):
            raise ValueError('The frozen copy is complete; stopping Time Machine is unnecessary.')
        row = {'id': uuid.uuid4().hex, 'requested_at': now, 'expires_at': now + REQUEST_SECONDS,
               'generation': state.get('pending'), 'phase': 'pending'}
        atomic_json(directory / 'capture-stop.json', row)
    return row


def cancel_stop(directory=None):
    directory = directory or JOBS[PriorityMode.TIME_MACHINE].directory
    with locked(directory):
        row = read_json(directory / 'capture-stop.json', {})
        if row.get('phase') == 'claimed':
            raise ValueError('The stop request has already been delivered to the Mac.')
        (directory / 'capture-stop.json').unlink(missing_ok=True)


def authorization(request, directory, now=None):
    now = time.time() if now is None else now
    row = read_json(directory / 'capture-stop.json', {})
    state = read_json(directory / 'state.json', {})
    deadline = read_json(directory / 'pause.json', {}).get('paused_until')
    if deadline == 'indefinite' or (deadline is not None and (not timestamp(deadline) or deadline > now)):
        return None
    if (request.get('requested') and row.get('phase') == 'pending'
            and now < row.get('expires_at', 0) <= now + REQUEST_SECONDS
            and row.get('generation') in (None, request['generation'])
            and state.get('progress', {}).get('capture_waiting') is True):
        return {'id': row['id'], 'expires_at': row['expires_at']}
    return None


def claim_stop(generation, capture_id, request_id, directory, capture_request, now=None):
    now = time.time() if now is None else now
    with locked(directory):
        current = capture_request()
        grant = authorization(current, directory, now)
        if (not grant or current.get('generation') != generation or current.get('capture_id') != capture_id
                or grant['id'] != request_id):
            return {'authorized': False}
        row = read_json(directory / 'capture-stop.json')
        row.update(phase='claimed', claimed_at=now, generation=generation, capture_id=capture_id)
        atomic_json(directory / 'capture-stop.json', row)
        return {'authorized': True, 'expires_at': min(now + 30, current['expires_at'], row['expires_at'])}


def status(directory=None, now=None):
    directory = directory or JOBS[PriorityMode.TIME_MACHINE].directory
    now = time.time() if now is None else now
    row = read_json(directory / 'capture-stop.json', {})
    state = read_json(directory / 'state.json', {})
    complete = state.get('progress', {}).get('capture_complete') is True and bool(state.get('pending'))
    captured = complete or (row.get('phase') == 'claimed' and state.get('progress', {}).get('capture_complete') is True
                and row.get('generation') in (state.get('pending'), state.get('last_generation')))
    phase = ('complete' if row and captured else 'expired' if row and row.get('expires_at', 0) <= now
             else row.get('phase', 'none'))
    return {'coordinator_ready': capability(directory, now), 'capture_complete': complete,
            'phase': phase, 'expires_at': row.get('expires_at')}
