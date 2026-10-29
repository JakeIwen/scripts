"""Private watcher records; atomic JSON publication is shared with the organizer."""
import os
from contextlib import contextmanager
from pi.apps.media_organizer.locking import file_lock
from pi.apps.media_organizer.storage import now, read_json, write_json
from . import config
from .records import Candidate


def read(path, default):
    try:
        return read_json(path)
    except FileNotFoundError:
        return default


def candidates():
    return [Candidate.from_dict(row) for row in read(config.CANDIDATES_PATH, [])]


def save_candidates(rows):
    write_json(config.CANDIDATES_PATH, [row.to_dict() for row in rows])


def metadata(**changes):
    data = read(config.STATUS_PATH, {})
    if changes:
        data.update(changes)
        write_json(config.STATUS_PATH, data)
    return data


def log(message):
    config.LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with config.LOG_PATH.open('a', encoding='utf-8') as stream:
        stream.write(f'{now()} {message.replace(chr(10), " ")}\n')


@contextmanager
def operation(checking=False):
    config.STATE_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        with file_lock(config.STATE_DIR / 'operation.lock', timeout=0):
            marker = config.STATE_DIR / 'busy.json'
            try:
                if checking:
                    write_json(marker, {'pid': os.getpid(), 'started_at': now()})
                yield
            finally:
                if checking:
                    marker.unlink(missing_ok=True)
    except TimeoutError:
        raise RuntimeError('IPTorrents watcher is busy; try again later') from None


def checking():
    marker = read(config.STATE_DIR / 'busy.json', {})
    pid = marker.get('pid')
    if type(pid) is not int or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        pass
    # A stale marker cannot make a reused PID look like an active check.
    try:
        with file_lock(config.STATE_DIR / 'operation.lock', timeout=0):
            return False
    except TimeoutError:
        return True
