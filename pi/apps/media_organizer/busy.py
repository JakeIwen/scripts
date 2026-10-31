"""Serialized organizer operations with a live-process status marker."""
import os
from contextlib import contextmanager
from . import config
from .locking import file_lock
from .storage import now, read_json, write_json

_depth = 0


def current():
    try:
        data = read_json(config.STATE_DIR / 'busy.json')
        pid = data['pid']
        if type(pid) is not int or pid <= 0:
            return None
        os.kill(pid, 0)
        return data
    except (FileNotFoundError, ProcessLookupError, ValueError, KeyError):
        return None
    except PermissionError:
        return data


def note(detail=None, done=None, total=None):
    """Progress for status readers; a no-op outside this process's staged operation."""
    path = config.STATE_DIR / 'busy.json'
    try:
        data = read_json(path)
    except (FileNotFoundError, ValueError):
        return
    if data.get('pid') != os.getpid():
        return
    fields = {'detail': detail, 'done': done, 'total': total}
    data.update({key: value for key, value in fields.items() if value is not None})
    write_json(path, data)


def _restage(stage):
    """Show a nested stage (the completion hook's apply inside its propose);
    returns the marker to restore afterwards, or None when nothing changed."""
    path = config.STATE_DIR / 'busy.json'
    if stage is None:
        return None
    try:
        previous = read_json(path)
    except (FileNotFoundError, ValueError):
        return None
    if previous.get('pid') != os.getpid() or previous.get('stage') == stage:
        return None
    write_json(path, {'stage': stage, 'started_at': now(), 'pid': os.getpid()})
    return previous


@contextmanager
def operation(stage=None):
    global _depth
    if _depth:
        _depth += 1
        previous = _restage(stage)
        try:
            yield
        finally:
            _depth -= 1
            if previous is not None:
                write_json(config.STATE_DIR / 'busy.json', previous)
        return
    config.STATE_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    with file_lock(config.STATE_DIR / 'operation.lock'):
        _depth = 1
        try:
            if stage is not None:
                write_json(config.STATE_DIR / 'busy.json',
                           {'stage': stage, 'started_at': now(), 'pid': os.getpid()})
            yield
        finally:
            _depth = 0
            if stage is not None:
                (config.STATE_DIR / 'busy.json').unlink(missing_ok=True)
