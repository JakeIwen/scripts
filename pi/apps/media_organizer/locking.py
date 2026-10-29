"""Bounded descriptor locks; never replace a lock inode."""
import fcntl
import os
import time
from contextlib import contextmanager
from . import config

LOCK_WAIT_SECONDS = 600


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
