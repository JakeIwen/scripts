"""Bounded descriptor locks; never replace a lock inode."""
import fcntl
import os
import time
from contextlib import contextmanager
from . import config

LOCK_WAIT_SECONDS = 600
BACKUP_POLL_SECONDS = 5


@contextmanager
def file_lock(path, timeout=LOCK_WAIT_SECONDS):
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise TimeoutError('timed out waiting for organizer lock') from None
                time.sleep(0.1)
        yield
    finally:
        os.close(fd)


@contextmanager
def media_lock():
    with file_lock(config.LOCK_PATH):
        yield


class BackupSyncRunning(RuntimeError):
    pass


@contextmanager
def backup_sync_excluded(wait=False, on_wait=None):
    """Shared hold on the lock the backup's media rsync takes exclusively.

    A title moved mid-rsync is deleted from the mirror or copied twice, so no
    organizer move overlaps that sync. Opened read-only: the backup (root) may
    have created the file, and a shared flock needs no write access.
    """
    fd = os.open(config.MIRROR_LOCK_PATH, os.O_CREAT | os.O_RDONLY | os.O_NOFOLLOW, 0o644)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError:
            if not wait:
                raise BackupSyncRunning('the nightly backup is syncing the media drive; '
                                        'apply or undo after it finishes') from None
            if on_wait:
                on_wait()
            deadline = time.monotonic() + config.MIRROR_LOCK_WAIT_SECONDS
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise BackupSyncRunning('the backup sync is still running') from None
                    time.sleep(BACKUP_POLL_SECONDS)
        yield
    finally:
        os.close(fd)
