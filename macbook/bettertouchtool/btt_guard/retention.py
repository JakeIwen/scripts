"""Weekly pruning of Guard history older than 90 days.

Checkpoint generations and rejected-capture diagnostics past RETENTION_SECONDS are
deleted. The active checkpoint is always kept, however old: audits and repair compare
against it. Without a readable active pointer no checkpoint is pruned. The scheduled
monitor runs this at most once per PRUNE_INTERVAL_SECONDS under guard.lock; `guard
prune` previews and `guard prune --apply` deletes.
"""
from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
import re
import shutil
import stat
from typing import List, Optional

from .checkpoint import ACTIVE_FILE
from .model import PruneResult
from .storage import load_json, write_json

DAY = 86400
RETENTION_SECONDS = 90 * DAY
PRUNE_INTERVAL_SECONDS = 7 * DAY
STATE_NAME = 'retention.json'
# The suffix comes from tempfile.mkdtemp, whose names use lower-case letters, digits and '_'.
CHECKPOINT_NAME = re.compile(r'^cp-(\d{8}T\d{6}Z)-[a-z0-9_]+$')
REJECTED_NAME = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')


def _owned_directory(path: Path) -> bool:
    # lstat, so a symlink is never followed or counted as a directory.
    info = path.lstat()
    return stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid()


def _active_checkpoint(state: Path) -> Optional[str]:
    try:
        value = load_json(state/ACTIVE_FILE).get('checkpoint_id')
    except (OSError, ValueError, AttributeError):
        return None
    return value if isinstance(value, str) else None


def _expired_checkpoints(state: Path, now: float) -> List[Path]:
    active = _active_checkpoint(state)
    if active is None:
        return []
    expired = []
    for path in sorted((state/'checkpoints').glob('cp-*')):
        match = CHECKPOINT_NAME.fullmatch(path.name)
        if not match or path.name == active or not _owned_directory(path):
            continue
        created = datetime.strptime(match[1], '%Y%m%dT%H%M%SZ').replace(tzinfo=timezone.utc).timestamp()
        if now - created > RETENTION_SECONDS:
            expired.append(path)
    return expired


def _expired_rejections(state: Path, now: float) -> List[Path]:
    return [path for path in sorted((state/'rejected-captures').glob('*'))
            if REJECTED_NAME.fullmatch(path.name) and _owned_directory(path)
            and now - path.lstat().st_mtime > RETENTION_SECONDS]


def expired(state_dir, now: float) -> List[Path]:
    state = Path(state_dir)
    return _expired_checkpoints(state, now) + _expired_rejections(state, now)


def prune(state_dir, now: float, *, apply: bool = False) -> PruneResult:
    """List (or, with apply, delete) expired history; the caller holds guard.lock to apply."""
    paths = expired(state_dir, now)
    if apply:
        for path in paths:
            # Re-check right before deleting, in case the entry changed since it was listed.
            if not _owned_directory(path):
                raise ValueError(f'Refusing to prune {path.name}: no longer an owned directory.')
            shutil.rmtree(path)
    return PruneResult(applied=apply, retention_days=RETENTION_SECONDS // DAY,
                       entries=[f'{path.parent.name}/{path.name}' for path in paths])


def prune_if_due(state_dir, now: float) -> Optional[PruneResult]:
    """The scheduled monitor's weekly pass; a failure is recorded and retried next week."""
    path = Path(state_dir)/STATE_NAME
    try:
        last = load_json(path).get('last_run')
    except (OSError, ValueError, AttributeError):
        last = None
    if isinstance(last, (int, float)) and 0 <= now - last < PRUNE_INTERVAL_SECONDS:
        return None
    try:
        result = prune(state_dir, now, apply=True)
        write_json(path, {'last_run': now, 'entries': result['entries'], 'error': None})
        return result
    except (OSError, ValueError) as error:
        write_json(path, {'last_run': now, 'entries': [], 'error': str(error)[:200]})
        return None
