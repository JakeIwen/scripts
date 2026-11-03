"""Durable per-source dashboard history and read-only local metric exports."""
import re
import time

from offsite_config import HistoryEntry, HistoryRecord, LABEL_PATTERN, Result, SCHEMA, TargetKind
from offsite_repository import durable_json, private_directory, read_record
import upload_speed_log

HISTORY_FILE = 'history.json'
RATES_DIRECTORY = 'rates'


def optional_record(path, default=None):
    try:
        path.lstat()
    except FileNotFoundError:
        return default
    return read_record(path)


def status(cfg):
    path = cfg.state / 'status.json'
    return read_record(path) if path.exists() or path.is_symlink() else {'result': Result.NOT_RUN}


def validate_entry(entry, *, successful=False):
    if (not isinstance(entry, dict) or entry.get('result') not in
            ((Result.OK,) if successful else (Result.OK, Result.FAILED, Result.INTERRUPTED)) or
            entry.get('target_kind') not in (TargetKind.LOCAL, TargetKind.REMOTE)):
        raise ValueError('Invalid offsite history entry')
    for field in ('finished', 'duration_seconds'):
        if field not in entry and field != 'finished':
            continue
        value = entry.get(field)
        if type(value) not in (int, float) or not 0 <= value < float('inf'):
            raise ValueError('Invalid offsite history timestamp or duration')
    for field in ('cause', 'snapshot', 'receipt'):
        if field in entry and not isinstance(entry[field], str):
            raise ValueError('Invalid offsite history text field')
    for field in ('logical_bytes_processed', 'newly_added_packed_bytes'):
        if field not in entry or (field == 'newly_added_packed_bytes' and entry[field] is None):
            continue
        if type(entry[field]) is not int or entry[field] < 0:
            raise ValueError('Invalid offsite history byte count')


def history(cfg) -> HistoryRecord:
    value = optional_record(cfg.state / HISTORY_FILE, {})
    if not isinstance(value, dict):
        raise ValueError('Invalid offsite history')
    for label, sources in value.items():
        if not re.fullmatch(LABEL_PATTERN, label) or not isinstance(sources, dict):
            raise ValueError('Invalid offsite history label or sources')
        for source, row in sources.items():
            if source not in cfg.sources() or not isinstance(row, dict) or not {'latest', 'last_ok'} <= row.keys():
                raise ValueError('Invalid offsite source history')
            validate_entry(row['latest'])
            if row['last_ok'] is not None:
                validate_entry(row['last_ok'], successful=True)
    return value


def record_outcome(cfg, label, source, outcome, target_kind):
    # The caller already holds the exclusive backup lock. Refuse to replace any
    # unreadable/malformed history rather than erase another drive's recovery point.
    value = history(cfg)
    entry: HistoryEntry = {**outcome, 'finished': time.time(), 'target_kind': target_kind}
    validate_entry(entry)
    sources = value.setdefault(label, {})
    previous = sources.get(source, {}).get('last_ok')
    sources[source] = {'latest': entry, 'last_ok': entry if outcome['result'] == Result.OK else previous}
    durable_json(cfg.state / HISTORY_FILE, value)


def rate_path(cfg, source):
    if source not in cfg.sources():
        raise ValueError('Unknown offsite source: ' + source)
    return cfg.state / RATES_DIRECTORY / (source + '.bin')


def rate_sampler(cfg, source):
    try:
        path = rate_path(cfg, source)
        private_directory(path.parent, cfg.owner)
        upload_speed_log.prune(path)
        return upload_speed_log.UploadSpeedSampler(path=path)
    except (OSError, ValueError, RuntimeError):
        return None  # Missing graph data must never prevent a source backup.


def dashboard(cfg):
    return {'schema': SCHEMA, 'status': status(cfg), 'history': history(cfg),
            'evidence': {'check': optional_record(cfg.state / 'remote-check.json'),
                         'restore_test': optional_record(cfg.state / 'remote-restore-test.json')}}


def rates(cfg, source):
    return {'schema': SCHEMA, 'source': source,
            'samples': upload_speed_log.read_samples(rate_path(cfg, source))}
