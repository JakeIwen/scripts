"""Manual remote-backup holds; cooperative cancellation never signals a saved PID."""
from contextlib import contextmanager
import fcntl
import os
import re
import secrets
import stat
import subprocess

from offsite_config import LABEL_PATTERN, LIVE_ROOT, REMOTE_CONFIG, REPO_ID_PATTERN, SCHEMA
from offsite_config import RemoteControlGuard, RemoteControlRecord, TargetKind
from offsite_repository import durable_json, private_directory, records
from protected_json import MISSING, read_protected_json, validate_metadata

CONTROL_PREFIX = 'remote-control-'
REVISION_PATTERN = r'[0-9a-f]{32}'
RESUME_UNIT = 'vanpi-offsite-resume'


class ControlConflict(RuntimeError):
    """The displayed intent is obsolete or another control is in progress."""


def validate_request(label, revision=None):
    if not isinstance(label, str) or not re.fullmatch(LABEL_PATTERN, label):
        raise ValueError('Invalid offsite label')
    if revision is not None and (not isinstance(revision, str) or not re.fullmatch(REVISION_PATTERN, revision)):
        raise ValueError('Invalid offsite control revision')


def control_path(cfg, label):
    validate_request(label)
    return cfg.state / (CONTROL_PREFIX + label + '.json')


def read_control(cfg, label) -> RemoteControlRecord | None:
    value = read_protected_json(control_path(cfg, label), 'Unsafe offsite control record')
    if value is MISSING:
        return None
    if (not isinstance(value, dict) or set(value) != {'schema', 'revision', 'paused', 'label', 'repo_id', 'sources'}
            or type(value['schema']) is not int or value['schema'] != SCHEMA
            or value['label'] != label or type(value['paused']) is not bool
            or not isinstance(value['repo_id'], str) or not re.fullmatch(REPO_ID_PATTERN, value['repo_id'])
            or not isinstance(value['sources'], list) or not value['sources']
            or any(not isinstance(name, str) or name not in cfg.sources() for name in value['sources'])
            or len(set(value['sources'])) != len(value['sources'])):
        raise ValueError('Invalid offsite control record')
    validate_request(label, value['revision'])
    return value


@contextmanager
def control_lock(cfg):
    private_directory(cfg.state, cfg.owner)
    path = cfg.state / 'remote-control.lock'
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, 0o600)
    try:
        info = os.fstat(fd)
        validate_metadata(info, 'Unsafe offsite control lock')
        if stat.S_IMODE(info.st_mode) != 0o600:
            raise ValueError('Unsafe offsite control lock')
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ControlConflict('Another offsite control is in progress; refresh before retrying') from None
        yield
    finally:
        os.close(fd)


def checked_plan(cfg, label, revision):
    validate_request(label, revision)
    value = read_control(cfg, label)
    if value is None or value['revision'] != revision:
        raise ControlConflict('Offsite control changed; refresh before retrying')
    marker = records(cfg)['repos'].get(label)
    if marker is None or marker['repo_id'] != value['repo_id']:
        raise ControlConflict('The recorded remote repository identity changed')
    return value


def save_control(cfg, value, **changes):
    updated = {**value, **changes, 'revision': secrets.token_hex(16)}
    durable_json(control_path(cfg, updated['label']), updated)
    return updated


def admit_backup(repo, selected):
    """Called with job-offsite held, before any source work, never for a local seed."""
    with control_lock(repo.cfg):
        previous = read_control(repo.cfg, repo.label)
        if previous is not None and previous['paused']:
            raise ControlConflict('Remote offsite backup is paused; use explicit resume')
        marker = records(repo.cfg)['repos'].get(repo.label)
        if marker is None:
            raise ValueError('Remote target is not a registered seeded repository')
        value = save_control(repo.cfg, {'schema': SCHEMA, 'label': repo.label,
                             'repo_id': marker['repo_id'], 'sources': selected, 'paused': False})
    repo.commands.remote_control = RemoteControlGuard(repo.label, value['revision'])


def stop_cause(cfg, guard: RemoteControlGuard):
    try:
        value = read_control(cfg, guard.label)
        if value is not None and value['paused'] == guard.expected_paused and value['revision'] == guard.revision:
            return None
    except (OSError, ValueError, RuntimeError):
        return 'Remote offsite hold is unreadable; stopped safely; retry explicitly'
    return 'Remote offsite backup paused; resume explicitly to reread unfinished sources'


def resume_backup(repo, revision):
    """Only the frozen, job-admitted worker clears a hold, after fresh remote checks."""
    with control_lock(repo.cfg):
        plan = checked_plan(repo.cfg, repo.label, revision)
        if not plan['paused']:
            raise ControlConflict('Remote offsite backup is not paused')
    # No control lock across network calls: a fresh pause cancels even the
    # preflight. Only this exact held revision permits its read-only checks.
    repo.commands.remote_control = RemoteControlGuard(repo.label, revision, expected_paused=True)
    repo.verify()
    with control_lock(repo.cfg):
        plan = checked_plan(repo.cfg, repo.label, revision)
        accepted = save_control(repo.cfg, plan, paused=False)
    repo.commands.remote_control = RemoteControlGuard(repo.label, accepted['revision'])
    return plan['sources']


def request_control(cfg, action, label, revision, *, command=subprocess.run):
    """Pause is durable immediately; resume starts one guarded attempt, with no retry timer."""
    if os.geteuid() != cfg.owner:
        raise RuntimeError('Run offsite_backup.sh as root')
    if action not in ('pause', 'resume'):
        raise ValueError('Unknown offsite control')
    with control_lock(cfg):
        plan = checked_plan(cfg, label, revision)
        if action == 'pause':
            # Change the revision even if held already: invalidates a pending resume.
            save_control(cfg, plan, paused=True)
        else:
            if not plan['paused']:
                raise ControlConflict('Remote offsite backup is not paused')
            from offsite_remote import transport_config
            transport_config(cfg.state / REMOTE_CONFIG)
            if cfg.ignition.exists() or cfg.ignition.is_symlink():
                raise ControlConflict('Ignition is on; park before resuming')
    if action == 'resume':
        # Release the intent mutex before the worker can acquire it. A pause in
        # this gap changes revision, so the frozen worker refuses this request.
        argv = [cfg.commands.get('systemd-run', '/usr/bin/systemd-run'),
                '--unit=' + RESUME_UNIT, '--collect', '--service-type=exec',
                '--property=KillMode=mixed', '--property=TimeoutStopSec=120',
                str(LIVE_ROOT / 'backup/offsite_backup.sh'), 'resume-run', '--remote',
                '--label', label, '--revision', revision]
        try:
            result = command(argv, capture_output=True, text=True, timeout=10, check=False)
        except (OSError, subprocess.SubprocessError):
            raise ControlConflict('Resume launch unconfirmed; refresh before retrying') from None
        if result.returncode:
            raise ControlConflict('Resume could not start; hold retained; refresh before retrying')
    return {'ok': True, 'action': action}


def control_status(cfg, current):
    """No probes, credentials or PID signalling; only local private intent is read."""
    if current.get('target_kind') != TargetKind.REMOTE:
        return {'supported': False, 'reason': 'local-target'}
    label = current.get('label')
    try:
        validate_request(label)
        value = read_control(cfg, label)
        run_revision = current.get('control_revision')
        if value is None or not isinstance(run_revision, str) or not re.fullmatch(REVISION_PATTERN, run_revision):
            return {'supported': False, 'reason': 'older-run'}
        checked_plan(cfg, label, value['revision'])
        return {'supported': True, 'label': label, 'revision': value['revision'], 'paused': value['paused']}
    except (OSError, ValueError, RuntimeError):
        return {'supported': False, 'reason': 'unknown'}
