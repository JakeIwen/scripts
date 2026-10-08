#!/usr/bin/env python3
"""Private attempt bookkeeping and a fixed, read-only dashboard projection.

The dashboard CLI reads local metadata only: no credentials, disks, mounts,
router probes or cloud requests. Never export raw exception/server messages.
"""
import json
import errno
from enum import Enum
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import uuid
import backup_priority

STATE_DIR = Path('/var/lib/vanpi-icloud-backup')
CONFIG = Path('/etc/vanpi-icloud-backup.json')
INDEFINITE_PAUSE = 'indefinite'
TRANSFER_STALL_SECONDS = 120
PHASES = {'checking', 'preparing', 'uploading', 'verifying', 'publishing',
          'retention', 'complete', 'not due', 'deferred', 'error',
          'authentication_required', 'interrupted', 'unavailable'}
WORK_PHASES = {'checking', 'preparing', 'uploading', 'verifying', 'publishing', 'retention'}


class BackupFailure(str, Enum):
    DISK_IO = 'disk_io'
    DISK_UNAVAILABLE = 'disk_unavailable'
    DISK_READ_ONLY = 'disk_read_only'
    DISK_FULL = 'disk_full'
    PERMISSION = 'permission'
    HELPER_TIMEOUT = 'helper_timeout'
    PREREQUISITE = 'prerequisite'
    TRANSFER_STALLED = 'transfer_stalled'


FAILURE_MESSAGES = {
    BackupFailure.DISK_IO: 'Backup disk I/O was interrupted; saved chunks will be reused on retry.',
    BackupFailure.DISK_UNAVAILABLE: 'The backup disk or a required file is unavailable; waiting to retry safely.',
    BackupFailure.DISK_READ_ONLY: 'The backup filesystem is read-only; waiting for writable storage before retrying.',
    BackupFailure.DISK_FULL: 'The backup disk is full; more staging space is needed before capture can continue.',
    BackupFailure.PERMISSION: 'A required backup file is not accessible; its permissions need attention.',
    BackupFailure.HELPER_TIMEOUT: 'A backup prerequisite check timed out; it will retry.',
    BackupFailure.PREREQUISITE: 'A backup disk or prerequisite check failed; it will retry safely.',
    BackupFailure.TRANSFER_STALLED: 'The transfer stopped making progress; saved work will be reused on retry.',
}
RETRYABLE_FAILURES = frozenset(FAILURE_MESSAGES) - {BackupFailure.PERMISSION, BackupFailure.DISK_FULL}


def classify_failure(exc):
    if isinstance(exc, OSError):
        return {errno.EIO: BackupFailure.DISK_IO, errno.ENODEV: BackupFailure.DISK_UNAVAILABLE,
                errno.ENXIO: BackupFailure.DISK_UNAVAILABLE, errno.ENOENT: BackupFailure.DISK_UNAVAILABLE,
                errno.ESTALE: BackupFailure.DISK_UNAVAILABLE, errno.EROFS: BackupFailure.DISK_READ_ONLY,
                errno.ENOSPC: BackupFailure.DISK_FULL,
                errno.EACCES: BackupFailure.PERMISSION, errno.EPERM: BackupFailure.PERMISSION}.get(exc.errno)
    if isinstance(exc, subprocess.TimeoutExpired):
        return BackupFailure.HELPER_TIMEOUT
    if isinstance(exc, subprocess.CalledProcessError):
        return BackupFailure.PREREQUISITE
    if isinstance(exc, RuntimeError) and 'made no progress' in str(exc):
        return BackupFailure.TRANSFER_STALLED
    return None
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


def pause_deadline(directory):
    value = read_json(directory / 'pause.json', {})
    if not isinstance(value, dict):
        raise ValueError('invalid manual pause state')
    deadline = value.get('paused_until')
    if deadline not in (None, INDEFINITE_PAUSE) and number(deadline) is None:
        raise ValueError('invalid manual pause deadline')
    return deadline


def queued_backup_message(lock_file=Path('/run/lock/vanpi_backup.lock'), process_root=Path('/proc')):
    owners = {
        'icloud_backup.py': 'Pi iCloud backup',
        'time_machine_icloud.py': 'Mac Time Machine iCloud backup',
        'pi_backup.sh': 'local Pi backup',
        'exfat_snapshot.sh': 'EXFAT512 snapshot',
        'clone_to_sd.sh': 'hotspare clone',
        'clone_now.sh': 'hotspare clone',
    }
    try:
        pid = lock_file.read_text().strip()
        if re.fullmatch(r'[1-9][0-9]{0,9}', pid):
            proc = process_root / pid
            # The PID record alone can be stale; require the live process's
            # inherited backup-lock descriptor and an exact known entry point.
            if (proc / 'fd/9').samefile(lock_file):
                args = (proc / 'cmdline').read_bytes().split(b'\0')
                for filename, label in owners.items():
                    if ('/home/pi/scripts/backup/' + filename).encode() in args:
                        return f'Resume queued: waiting for {label} to finish. Checks every minute.'
    except (OSError, ValueError):
        pass
    return 'Resume queued: another backup may be using the disks. Checks every minute.'


def apply_manual_control(result, deadline, now=None):
    now = time.time() if now is None else now
    indefinite = deadline == INDEFINITE_PAUSE
    paused = indefinite or (deadline is not None and deadline > now)
    result['manual_pause_indefinite'] = indefinite
    result['manual_pause_until'] = deadline if paused and not indefinite else None
    result['resume_pending'] = deadline is not None and not paused
    result['controls_available'] = True
    if paused:
        result.update(phase='paused', message=('Stopping safely for the manual pause.' if result['running']
                                             else 'Manually paused until you resume it.' if indefinite
                                             else 'Manually paused; automatic retries resume when the pause expires.'))
        result['next_check_at'] = None if indefinite else deadline
        result['stalled'] = False
    elif result['resume_pending'] and not result['running']:
        result.update(phase='deferred', message=queued_backup_message())
    return result


def apply_priority_control(result, directory):
    selected = backup_priority.policy()
    if (selected != backup_priority.PriorityMode.NORMAL and not result['running']
            and result['phase'] not in ('paused', 'not due', 'complete')
            and backup_priority.JOBS[selected].directory != directory):
        result.update(phase='deferred', message='Waiting for ' + backup_priority.JOBS[selected].label
                      + ' to finish; selected in Backup priority.')
    return result


def number(value):
    return value if type(value) in (int, float) and math.isfinite(value) and value >= 0 else None


def generation(value):
    return value if isinstance(value, str) and re.fullmatch(r'(?:vanpi|tm)-\d{8}T\d{6}Z-[0-9a-f]{8}', value) else None


def phase(value):
    return value if value in PHASES else 'unavailable'


def reason(state):
    """Translate known conditions without exposing paths, SSIDs or server bodies."""
    value = str(state.get('last_error') or '').casefold()
    if state.get('phase') in ('error', 'deferred') and state.get('failure_code') in FAILURE_MESSAGES:
        return FAILURE_MESSAGES[state['failure_code']]
    if state.get('kind') == 'time-machine' and state.get('phase') == 'preparing':
        if state.get('progress', {}).get('capture_cleaning'):
            return 'Reclaiming unused local staging chunks before Time Machine capture.'
        if state.get('progress', {}).get('capture_draining'):
            return 'Mac image detached; waiting for its SMB file handles to close before capture.'
        return ('Waiting for the Mac to cleanly detach its idle Time Machine image.'
                if state.get('progress', {}).get('capture_waiting') else
                'Capturing a frozen encrypted Time Machine image; normal backups resume afterward.')
    if 'coordinator unavailable' in value:
        return 'Waiting for the Mac capture helper; install it or wake the Mac. Normal local backups remain enabled.'
    if 'capture lost exclusive source access' in value:
        return 'Capture stopped because its exclusive Time Machine access was lost; it will retry safely.'
    if 'mac clean detach' in value or 'image handles remain' in value:
        return 'Waiting for a clean Mac image detach; check the capture coordinator.'
    if state.get('phase') == 'authentication_required' or 'login' in value or 'authentication' in value:
        return 'iCloud sign-in needs attention.'
    if 'local-backup window' in value:
        return 'Waiting for the daily local backups.'
    if value.startswith('yielding to selected '):
        return 'Waiting for the cloud backup selected in Backup priority.'
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
    if 'unsafe staging object' in value:
        return 'Local staging contains an unrecognized or unsafe file; cleanup stopped to protect backup data.'
    if 'image changed' in value:
        return 'The Time Machine image changed during capture; a consistent frozen copy has not been completed.'
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
                 phase='checking', work_phase='checking', last_error=None, failure_code=None)
    save_attempt(directory, state)


def transfer_stalled(state, matching, updated, now):
    idle = number(state.get('progress', {}).get('command_idle_seconds'))
    return bool(matching and state.get('phase') in ('uploading', 'verifying')
                and updated is not None and now - updated <= 60
                and idle is not None and idle >= TRANSFER_STALL_SECONDS)


def build_status(state, history, cfg, service, next_check_at, now=None, auth_error=False):
    now = time.time() if now is None else now
    live = service.get('ActiveState') in ('active', 'activating', 'deactivating') and int(service.get('MainPID') or 0) > 0
    matching = live and int(service['MainPID']) == state.get('worker_pid')
    current = dict(state)
    if live and not matching:
        current.update(phase='checking', last_error=None, failure_code=None, progress={})
    elif not live and current.get('phase') in WORK_PHASES:
        current.update(phase='interrupted', last_error=None)
    if auth_error and not live:
        current.update(phase='authentication_required')
    current_phase = phase(current.get('phase'))
    last_success = number(state.get('last_success_at'))
    interval = cfg.get('interval_days', 7)
    updated = number(state.get('progress_updated_at'))
    stalled = transfer_stalled(current, matching, updated, now)
    current_progress = progress(current)
    current_progress['upload_bytes_per_second'] = (
        number(current.get('progress', {}).get('upload_bytes_per_second'))
        if matching and current_phase == 'uploading' and updated is not None and now - updated <= 60 else None)
    attempts = []
    for raw in reversed(history.get('attempts', [])[-100:]):
        row = dict(raw)
        if row.get('id') == state.get('attempt_id') and row.get('ended_at') is None:
            row = attempt_row(current)
        elif row.get('ended_at') is None:
            row.update(phase='interrupted', message='Worker stopped without a final status.')
        attempts.append(row)
    return {'available': True, 'running': live, 'phase': current_phase, 'stalled': stalled,
            'message': reason(current), 'last_work_phase': phase(state.get('last_work_phase', 'checking')),
            'generation': generation(state.get('pending') or state.get('last_generation')),
            'last_success_at': last_success, 'next_check_at': next_check_at,
            'next_due_at': last_success + interval * 86400 if last_success else None,
            'updated_at': updated, 'progress_stale': bool(live and (not matching or updated is None or now - updated > 60)),
            'attention': stalled or last_success is None or now - last_success > (interval + 1) * 86400 or current_phase in ('error', 'authentication_required', 'interrupted', 'unavailable'),
            'interval_days': interval, 'keep_generations': cfg.get('keep_generations', 8),
            'progress': current_progress, 'history_started_at': number(history.get('started_at')),
            'attempts': attempts, 'verified_generations': list(reversed(history.get('verified', [])))}


def dashboard_status():
    def capture(args):
        return subprocess.run(args, check=True, capture_output=True, text=True, timeout=2).stdout.strip()
    raw = capture(['/usr/bin/systemctl', 'show', 'vanpi-icloud-backup.service',
                   '-p', 'ActiveState', '-p', 'MainPID'])
    service = dict(line.split('=', 1) for line in raw.splitlines() if '=' in line)
    deadline = pause_deadline(STATE_DIR)
    timer_unit = 'vanpi-icloud-backup' + ('-resume' if deadline is not None else '')
    next_check = None
    try:
        timer = capture(['/usr/bin/systemctl', 'show', timer_unit + '.timer',
                         '-p', 'NextElapseUSecRealtime', '--value'])
        if timer and timer != 'n/a':
            next_check = float(capture(['/usr/bin/date', '--date=' + timer, '+%s']))
    except (ValueError, OSError, subprocess.SubprocessError):
        pass
    result = build_status(read_json(STATE_DIR / 'state.json', {}),
                        read_json(STATE_DIR / 'history.json', {}), read_json(CONFIG, {}),
                        service, next_check, auth_error=(STATE_DIR / 'authentication-error.json').exists())
    return apply_priority_control(apply_manual_control(result, deadline), STATE_DIR)


if __name__ == '__main__':
    # This is deliberately not a general-purpose root file reader.
    if len(sys.argv) != 1:
        sys.exit(2)
    try:
        print(json.dumps(dashboard_status(), allow_nan=False))
    except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError):
        print(json.dumps({'available': False, 'running': False, 'attention': True}))
