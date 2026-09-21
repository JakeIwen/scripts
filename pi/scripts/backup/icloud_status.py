#!/usr/bin/env python3
"""Private attempt bookkeeping and a fixed, read-only dashboard projection.

The dashboard CLI reads local metadata only: no credentials, disks, mounts,
router probes or cloud requests. Never export raw exception/server messages.
"""
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import uuid

STATE_DIR = Path('/var/lib/vanpi-icloud-backup')
CONFIG = Path('/etc/vanpi-icloud-backup.json')
PHASES = {'checking', 'preparing', 'uploading', 'verifying', 'publishing',
          'retention', 'complete', 'not due', 'deferred', 'error',
          'authentication_required', 'interrupted', 'unavailable'}
WORK_PHASES = {'checking', 'preparing', 'uploading', 'verifying', 'publishing', 'retention'}
COUNTERS = ('upload_estimated_bytes', 'upload_total_bytes', 'command_bytes',
            'verified_bytes', 'verification_total_bytes', 'verified_files',
            'verification_total_files', 'current_file_bytes')


def read_json(path, default):
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return default


def write_json(path, value):
    fd, name = tempfile.mkstemp(prefix='.' + path.name, dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream, sort_keys=True)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def number(value):
    return value if type(value) in (int, float) and math.isfinite(value) and value >= 0 else None


def generation(value):
    return value if isinstance(value, str) and re.fullmatch(r'vanpi-\d{8}T\d{6}Z-[0-9a-f]{8}', value) else None


def phase(value):
    return value if value in PHASES else 'unavailable'


def reason(state):
    """Translate known conditions without exposing paths, SSIDs or server bodies."""
    value = str(state.get('last_error') or '').casefold()
    if state.get('phase') == 'authentication_required' or 'login' in value or 'authentication' in value:
        return 'iCloud sign-in needs attention.'
    if 'local-backup window' in value:
        return 'Waiting for the daily local backups.'
    if 'ignition' in value:
        return 'Paused while ignition is on.'
    if 'policy' in value and 'hdd' in value:
        return 'Waiting for the backup disk policy.'
    if any(word in value for word in ('starlink', 'blocked ssid', 'blocked uplink')):
        return 'Paused to avoid the capped Starlink connection.'
    if any(word in value for word in ('uplink', 'route', 'ssid', 'network path')):
        return 'Waiting for a verified, permitted internet route.'
    if 'borg backup is missing or stale' in value:
        return 'Waiting for a fresh local Borg backup.'
    if 'headroom' in value:
        return 'Not enough free staging space.'
    if 'shutdown' in value or 'disk lifecycle' in value:
        return 'Stopped safely; saved work will be reused.'
    if 'progress' in value and ('timeout' in value or 'no ' in value):
        return 'No-progress watchdog stopped the attempt; it will retry.'
    if state.get('phase') == 'deferred':
        return 'Paused by a safety check; saved work will be reused.'
    if state.get('phase') == 'error':
        return 'Attempt failed; see the private service log for details.'
    return {'checking': 'Checking backup prerequisites.',
            'preparing': 'Freezing and checking the encrypted Borg recovery copy.',
            'uploading': 'Uploading the frozen recovery copy.',
            'verifying': 'Downloading and SHA-256 checking every file.',
            'publishing': 'Publishing and checking the completion marker.',
            'retention': 'Recovery copy verified; applying retention.',
            'complete': 'Recovery copy uploaded and download-verified.',
            'not due': 'Waiting for the next weekly recovery copy.',
            'interrupted': 'Worker is no longer running; saved work can be resumed.'
            }.get(state.get('phase'), 'Local iCloud status is unavailable.')


def progress(state):
    raw = state.get('progress') or {}
    return {key: number(raw.get(key)) for key in COUNTERS}


def attempt_row(state, ended_at=None):
    return {'id': state['attempt_id'], 'started_at': state['attempt_started_at'],
            'ended_at': ended_at, 'phase': phase(state.get('phase')),
            'work_phase': phase(state.get('work_phase', 'checking')),
            'generation': generation(state.get('pending') or state.get('last_generation')),
            'message': reason(state), 'progress': progress(state),
            'verified_at': number(state.get('attempt_verified_at'))}


def save_attempt(directory, state, finished=False):
    """Only the lock-owning worker writes history; readers never mutate it."""
    if not state.get('attempt_id'):
        return
    path = directory / 'history.json'
    history = read_json(path, {'started_at': time.time(), 'attempts': [], 'verified': []})
    row = attempt_row(state, time.time() if finished else None)
    history['attempts'] = ([r for r in history['attempts'] if r['id'] != row['id']] + [row])[-100:]
    if row['verified_at'] is not None:
        history['verified'] = ([r for r in history['verified'] if r['generation'] != row['generation']] +
                               [{'generation': row['generation'], 'completed_at': row['verified_at']}])[-52:]
    write_json(path, history)


def begin_attempt(directory, state):
    state.update(attempt_id=uuid.uuid4().hex, attempt_started_at=time.time(),
                 worker_pid=os.getpid(), attempt_verified_at=None,
                 phase='checking', work_phase='checking', last_error=None)
    save_attempt(directory, state)


def build_status(state, history, cfg, service, next_check_at, now=None, auth_error=False):
    now = time.time() if now is None else now
    live = service.get('ActiveState') in ('active', 'activating', 'deactivating') and int(service.get('MainPID') or 0) > 0
    matching = live and int(service['MainPID']) == state.get('worker_pid')
    current = dict(state)
    if live and not matching:
        current.update(phase='checking', last_error=None, progress={})
    elif not live and current.get('phase') in WORK_PHASES:
        current.update(phase='interrupted', last_error=None)
    if auth_error and not live:
        current.update(phase='authentication_required')
    current_phase = phase(current.get('phase'))
    last_success = number(state.get('last_success_at'))
    interval = cfg.get('interval_days', 7)
    updated = number(state.get('progress_updated_at'))
    attempts = []
    for raw in reversed(history.get('attempts', [])[-100:]):
        row = dict(raw)
        if row.get('id') == state.get('attempt_id') and row.get('ended_at') is None:
            row = attempt_row(current)
        elif row.get('ended_at') is None:
            row.update(phase='interrupted', message='Worker stopped without a final status.')
        attempts.append(row)
    return {'available': True, 'running': live, 'phase': current_phase,
            'message': reason(current), 'last_work_phase': phase(state.get('last_work_phase', 'checking')),
            'generation': generation(state.get('pending') or state.get('last_generation')),
            'last_success_at': last_success, 'next_check_at': next_check_at,
            'next_due_at': last_success + interval * 86400 if last_success else None,
            'updated_at': updated, 'progress_stale': bool(live and (not matching or updated is None or now - updated > 60)),
            'attention': last_success is None or now - last_success > (interval + 1) * 86400 or current_phase in ('error', 'authentication_required', 'interrupted', 'unavailable'),
            'interval_days': interval, 'keep_generations': cfg.get('keep_generations', 8),
            'progress': progress(current), 'history_started_at': number(history.get('started_at')),
            'attempts': attempts, 'verified_generations': list(reversed(history.get('verified', [])))}


def dashboard_status():
    def capture(args):
        return subprocess.run(args, check=True, capture_output=True, text=True, timeout=2).stdout.strip()
    raw = capture(['/usr/bin/systemctl', 'show', 'vanpi-icloud-backup.service',
                   '-p', 'ActiveState', '-p', 'MainPID'])
    service = dict(line.split('=', 1) for line in raw.splitlines() if '=' in line)
    next_check = None
    try:
        timer = capture(['/usr/bin/systemctl', 'show', 'vanpi-icloud-backup.timer',
                         '-p', 'NextElapseUSecRealtime', '--value'])
        if timer and timer != 'n/a':
            next_check = float(capture(['/usr/bin/date', '--date=' + timer, '+%s']))
    except (ValueError, OSError, subprocess.SubprocessError):
        pass
    return build_status(read_json(STATE_DIR / 'state.json', {}),
                        read_json(STATE_DIR / 'history.json', {}), read_json(CONFIG, {}),
                        service, next_check, auth_error=(STATE_DIR / 'authentication-error.json').exists())


if __name__ == '__main__':
    # This is deliberately not a general-purpose root file reader.
    if len(sys.argv) != 1:
        sys.exit(2)
    try:
        print(json.dumps(dashboard_status(), allow_nan=False))
    except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError):
        print(json.dumps({'available': False, 'running': False, 'attention': True}))
