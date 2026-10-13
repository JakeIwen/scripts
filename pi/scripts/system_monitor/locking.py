"""Lifetime writer admission, shared by collectors and exclusive maintenance."""

import fcntl
import os
from contextlib import contextmanager


@contextmanager
def writer_lock(path, exclusive=False):
    with open(os.path.realpath(path) + ".writer.lock", "a+b") as handle:
        mode = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
        try:
            fcntl.flock(handle, mode | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise OSError("monitor database writer is active; stop the service first") from error
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)
