"""Read bounded journal evidence for an existing cloud backup attempt.

Only fixed worker diagnoses are public. Never return arbitrary journal text,
tracebacks, command arguments, paths or provider responses to the dashboard.
"""
import json
import math
import re
import subprocess
import time
from typing import TypedDict

from .van_dashboard_common import run_command


JOBS = {
    'pi': ('icloud', 'vanpi-icloud-backup.service'),
    'time-machine': ('time_machine_icloud', 'vanpi-time-machine-icloud.service'),
}
ATTEMPT_ID = re.compile(r'[0-9a-f]{32}\Z')
MAX_JOURNAL_ROWS = 200

# Match complete authored messages; an appended secret must not pass through.
KNOWN_REASONS = {
    'unsafe staging object; cleanup refused':
        'Cleanup found an unexpected or unsafe staging file and stopped to protect backup data.',
    'insufficient staging disk headroom':
        'The staging disk lacked the required free-space reserve.',
    'unsafe staging root': 'The staging directory failed its safety checks.',
    'archive directory is unsafe or absent': 'The archive directory was unavailable or failed its safety checks.',
    'ambiguous or unsafe backup mount': 'The backup filesystem mount could not be verified safely.',
    'backup filesystem label does not match its mount': 'The mounted filesystem did not match the expected backup disk.',
    'cannot determine backup mount state': 'The worker could not determine whether the backup disk was mounted.',
    'image changed during capture': 'The source image changed while it was being copied. No frozen copy was published.',
    'image changed across capture; no generation published':
        'The final source inventory differed from the starting inventory. No frozen copy was published.',
    'Time Machine capture lost exclusive source access':
        'The capture gate or SMB connection state changed, so capture stopped to preserve consistency.',
    'local store owner mismatch': 'The local staging store did not have the expected ownership marker.',
    'cloud store owner mismatch': 'The cloud store did not have the expected ownership marker.',
    'cloud object missing or wrong size': 'An expected cloud object was absent or had the wrong size.',
    'cloud object failed downloaded SHA-256 check': 'A downloaded cloud object failed its checksum verification.',
    'cloud manifest read-back mismatch': 'The uploaded manifest did not match its downloaded copy.',
    'cloud completion marker read-back mismatch': 'The cloud completion marker did not match its downloaded copy.',
    'completion marker verification failed': 'The cloud completion marker failed verification.',
    'local manifest changed during verification': 'The local manifest changed during verification.',
    'copied repository is incomplete': 'The frozen Borg repository was incomplete.',
    'OSError': 'The older worker recorded an operating-system error without the operation or error number.',
    'CalledProcessError': 'A required command failed. This older entry did not record which command failed.',
    'TimeoutExpired': 'A required command exceeded its time limit.',
    'FileNotFoundError': 'A required disk, file or command was unavailable.',
    'PermissionError': 'A required file or operation was denied by permissions.',
}


class JournalDiagnosis(TypedDict):
    at: float
    message: str
    explanation: str


class AttemptDetails(TypedDict):
    attempt: dict
    entries: list[JournalDiagnosis]
    note: str | None


def public_diagnosis(message):
    if not isinstance(message, str):
        return None
    prefix, separator, reason = message.partition(': ')
    if separator and prefix in ('Failed', 'Deferred', 'Retrying', 'Authentication required'):
        if reason in KNOWN_REASONS:
            return message, KNOWN_REASONS[reason]
        failure = re.fullmatch(r'(rclone|rsync|borg|findmnt) (copy|copyto|cat|lsjson|check|command|with-lock|deletefile|purge) failed \(exit ([0-9]{1,3})\)', reason)
        if failure:
            return message, 'The named command returned a failure status; saved completed files remain available for retry.'
        # Authentication messages can contain account/provider details; project
        # their category rather than attempting to redact arbitrary free text.
        if prefix == 'Authentication required':
            return 'Authentication required', 'iCloud sign-in or trusted-device approval needs attention.'
    return None


def journal_diagnoses(unit, started, ended, command):
    try:
        result = command([
            '/usr/bin/journalctl', '--unit=' + unit, '--since=@' + f'{started:.6f}',
            '--until=@' + f'{ended:.6f}', '--no-pager', '--quiet', '--output=json',
            '--output-fields=__REALTIME_TIMESTAMP,MESSAGE', '--lines=' + str(MAX_JOURNAL_ROWS),
        ], timeout=5)
        if result.returncode:
            return [], 'The service journal could not be read. The saved attempt information is shown below.'
        entries: list[JournalDiagnosis] = []
        for line in result.stdout.splitlines()[-MAX_JOURNAL_ROWS:]:
            row = json.loads(line)
            at = int(row.get('__REALTIME_TIMESTAMP', '0')) / 1_000_000
            diagnosis = public_diagnosis(row.get('MESSAGE'))
            if started <= at <= ended and diagnosis:
                entries.append({'at': at, 'message': diagnosis[0], 'explanation': diagnosis[1]})
        note = None if entries else (
            'No detailed failure reason is available in the retained service journal for this attempt. '
            'Older logs may have rotated, or the worker may not have recorded a supported diagnosis.')
        return entries, note
    except (OSError, ValueError, TypeError, AttributeError, subprocess.SubprocessError):
        return [], 'The service journal is unavailable. The saved attempt information is shown below.'


def attempt_details(kind, attempt_id, status_loader, command=run_command) -> AttemptDetails:
    if kind not in JOBS or (attempt_id != 'latest' and not ATTEMPT_ID.fullmatch(attempt_id)):
        raise ValueError('invalid cloud backup attempt')
    key, unit = JOBS[kind]
    status = status_loader().get(key)
    if not status or not status.get('available'):
        raise RuntimeError('Cloud backup status is unavailable.')
    attempts = status.get('attempts', [])
    attempt = next((row for row in attempts if attempt_id == 'latest' or row.get('id') == attempt_id), None)
    if attempt is None:
        raise LookupError('This attempt is no longer in the retained backup history.')
    started = attempt.get('started_at')
    ended = attempt.get('ended_at')
    if type(started) not in (int, float) or not math.isfinite(started) or started < 0:
        raise RuntimeError('This attempt has invalid timing information.')
    if ended is None:
        ended = min([time.time()] + [row['started_at'] for row in attempts if row['started_at'] > started])
    if any(type(value) not in (int, float) or not math.isfinite(value) or value < 0
           for value in (started, ended)) or ended < started:
        raise RuntimeError('This attempt has invalid timing information.')
    entries, note = journal_diagnoses(unit, started, ended, command)
    return {'attempt': attempt, 'entries': entries, 'note': note}
