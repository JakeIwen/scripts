"""Read the encrypted offsite restic backups' status for the Backups sheet.

The Pi's `offsite_backup.sh` keeps its state root-private, so the dashboard asks
it through fixed read-only commands (`dashboard`, `rates <source>`) and never
opens the state directory. The deployed v1 script has only `status`; the
dashboard falls back to it and reports history as unsupported until the
v2 script is installed. Wire contract: pi/docs/backup/OFFSITE.md.
"""
import json
import os
import re
import subprocess

from .van_dashboard_common import SUDO, run_command

SCRIPT = '/home/pi/scripts/backup/offsite_backup.sh'
TIMEOUT_SECONDS = 8
UINT32_MAX = 2**32 - 1
# Mirrors Result in pi/scripts/backup/offsite_config.py (a separate process on
# the Pi, so the vocabulary cannot be imported here).
RESULTS = ('running', 'ok', 'failed', 'interrupted', 'not-run')
TARGET_KINDS = ('local', 'remote')
SOURCE_RE = re.compile(r'[a-z0-9][a-z0-9-]{0,31}')
LABEL_RE = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,15}')
MOUNT_ROOT = '/mnt'
TEXT_LIMIT = 500


class OffsiteError(RuntimeError):
    """The offsite status could not be read or did not match the contract."""


def _fail(what):
    raise OffsiteError(f'invalid offsite {what}')


def _object(value, what):
    if not isinstance(value, dict):
        _fail(what)
    return value


def _number(value, what, *, integer=False, nullable=False):
    if value is None and nullable:
        return None
    ok = type(value) is int if integer else type(value) in (int, float)
    if not ok or value < 0 or (isinstance(value, float) and value != value):
        _fail(what)
    return value


def _text(value, what, limit=TEXT_LIMIT):
    if not isinstance(value, str) or len(value) > limit:
        _fail(what)
    return value


def _choice(value, choices, what):
    if value not in choices:
        _fail(what)
    return value


def _copy(raw, fields, what):
    """Pick the contract fields present in `raw`, each checked by its validator."""
    return {name: check(raw[name], f'{what}.{name}') for name, check in fields.items() if name in raw}


def _num(nullable=False, integer=False):
    return lambda value, what: _number(value, what, integer=integer, nullable=nullable)


def _txt(limit=TEXT_LIMIT):
    return lambda value, what: _text(value, what, limit)


_result = lambda value, what: _choice(value, RESULTS, what)  # noqa: E731
_target = lambda value, what: _choice(value, TARGET_KINDS, what)  # noqa: E731

OUTCOME_FIELDS = {
    'result': _result,
    'cause': _txt(),
    'snapshot': _txt(64),
    'receipt': _txt(128),
    'logical_bytes_processed': _num(integer=True),
    'newly_added_packed_bytes': _num(integer=True, nullable=True),
    'duration_seconds': _num(),
}
HISTORY_FIELDS = {**OUTCOME_FIELDS, 'finished': _num(), 'target_kind': _target}
EVIDENCE_FIELDS = {
    'operation': lambda value, what: _choice(value, ('check', 'restore-test'), what),
    'label': _txt(16),
    'started': _num(),
    'finished': _num(),
    'duration_seconds': _num(),
    'result': lambda value, what: _choice(value, ('ok', 'failed'), what),
    'cause': _txt(),
}


def _outcome(raw, what, fields=OUTCOME_FIELDS):
    record = _copy(_object(raw, what), fields, what)
    if 'result' not in record:
        _fail(f'{what}.result')
    return record


def _status(raw):
    raw = _object(raw, 'status')
    record = _copy(raw, {
        'result': _result,
        'label': lambda value, what: _label(value, what),
        'source': lambda value, what: None if value is None else _source(value, what),
        'bytes_done': _num(integer=True),
        'bytes_total': _num(integer=True, nullable=True),
        'percent': _num(nullable=True),
        'rate': _num(nullable=True),
        'eta': _num(nullable=True),
        'started': _num(),
        'updated': _num(),
        'finished': _num(),
        'cause': _txt(),
        'notification_error': _txt(),
        'duration_seconds': _num(),
        'target_kind': _target,
        'progress_measure': _txt(64),
    }, 'status')
    if 'result' not in record:
        _fail('status.result')
    if 'last_result' in raw:
        record['last_result'] = None if raw['last_result'] is None else _outcome(raw['last_result'], 'status.last_result')
    if 'sources' in raw:
        sources = _object(raw['sources'], 'status.sources')
        record['sources'] = {
            _source(name, 'status.sources key'): _outcome(value, f'status.sources.{name}')
            for name, value in sources.items()
        }
    errors = raw.get('cleanup_errors', [])
    if not isinstance(errors, list) or len(errors) > 20:
        _fail('status.cleanup_errors')
    record['cleanup_errors'] = [_text(item, 'status.cleanup_errors') for item in errors]
    # v1 wrote no target_kind; every v1 target was a local drive.
    record.setdefault('target_kind', 'local')
    return record


def _label(value, what):
    if not isinstance(value, str) or not LABEL_RE.fullmatch(value):
        _fail(what)
    return value


def _source(value, what):
    if not isinstance(value, str) or not SOURCE_RE.fullmatch(value):
        _fail(what)
    return value


def _history(raw):
    history = {}
    for label, sources in _object(raw, 'history').items():
        by_source = {}
        for source, entry in _object(sources, 'history label').items():
            entry = _object(entry, 'history source')
            last_ok = entry.get('last_ok')
            by_source[_source(source, 'history source')] = {
                'latest': _outcome(entry.get('latest'), 'history.latest', HISTORY_FIELDS),
                'last_ok': None if last_ok is None else _outcome(last_ok, 'history.last_ok', HISTORY_FIELDS),
            }
        history[_label(label, 'history label')] = by_source
    return history


def _evidence(raw):
    raw = _object(raw, 'evidence')
    return {
        name: None if raw.get(name) is None else _outcome(raw[name], f'evidence.{name}', EVIDENCE_FIELDS)
        for name in ('check', 'restore_test')
    }


def _run(args, command):
    try:
        return command([SUDO, '-n', SCRIPT, *args], timeout=TIMEOUT_SECONDS)
    except (OSError, subprocess.SubprocessError) as exc:
        raise OffsiteError('offsite status helper failed') from exc


def _json(result, what):
    try:
        return json.loads(result.stdout)
    except (TypeError, ValueError) as exc:
        raise OffsiteError(f'offsite {what} was not JSON') from exc


def read_offsite(command=run_command):
    """Return status plus history/evidence, or status alone when v1 is installed."""
    result = _run(['dashboard'], command)
    if result.returncode:
        # v1's argparse rejects the unknown command with exit 2; `status` still works.
        fallback = _run(['status'], command)
        if fallback.returncode:
            raise OffsiteError('offsite status helper failed')
        return {
            'supported': {'history': False},
            'status': _status(_json(fallback, 'status')),
            'history': {},
            'evidence': {'check': None, 'restore_test': None},
        }
    payload = _object(_json(result, 'dashboard'), 'dashboard')
    return {
        'supported': {'history': True},
        'status': _status(payload.get('status')),
        'history': _history(payload.get('history', {})),
        'evidence': _evidence(payload.get('evidence', {})),
    }


def offsite_rates(source, command=run_command):
    """Return {supported, samples}; the CLI decides whether `source` exists."""
    if not isinstance(source, str) or not SOURCE_RE.fullmatch(source):
        raise ValueError('invalid offsite source')
    result = _run(['rates', source], command)
    if result.returncode:
        # Unknown source and a v1 script (no `rates`) both exit nonzero; v1 also
        # lacks `dashboard`, which is what tells them apart without parsing stderr.
        if _run(['dashboard'], command).returncode:
            return {'supported': False, 'samples': []}
        raise ValueError('unknown offsite source')
    samples = _object(_json(result, 'rates'), 'rates').get('samples')
    if not isinstance(samples, list) or not all(
            isinstance(row, list) and len(row) == 2
            and all(type(value) is int and 0 <= value <= UINT32_MAX for value in row)
            for row in samples):
        _fail('rate history')
    return {'supported': True, 'samples': samples}


def target_drive(label, known_labels, *, ismount=os.path.ismount, statvfs=os.statvfs):
    """Usage of a local target drive, read only when it is already mounted.

    Never mounts anything: the offsite run owns mounting, and a probe of an
    unmounted /mnt/<label> would only measure the SD card underneath.
    """
    if label is None or label not in known_labels:
        return None
    mountpoint = os.path.join(MOUNT_ROOT, label)
    try:
        mounted = ismount(mountpoint)
        stats = statvfs(mountpoint) if mounted else None
    except OSError:
        return {'label': label, 'mounted': False, 'used_bytes': None, 'total_bytes': None}
    if stats is None:
        return {'label': label, 'mounted': False, 'used_bytes': None, 'total_bytes': None}
    return {
        'label': label,
        'mounted': True,
        'used_bytes': (stats.f_blocks - stats.f_bfree) * stats.f_frsize,
        'total_bytes': stats.f_blocks * stats.f_frsize,
    }
