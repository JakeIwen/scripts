"""Read private control records; each consumer owns its JSON schema."""
import errno
import json
import os
import stat


class ProtectedStateAccessError(ValueError):
    """A private state namespace belongs to a different effective user."""


MISSING = object()
MAX_RECORD_BYTES = 65536


def validate_metadata(info, error):
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_nlink != 1
            or info.st_mode & 0o077 or info.st_size > MAX_RECORD_BYTES):
        raise ValueError(error)


REPLACED_RETRIES = 3
_REPLACED = object()


def read_protected_json(path, error):
    # An atomic replace that lands mid-read unlinks the inode we hold (nlink 0);
    # the bytes read are a stale snapshot, so read the new record from the top.
    for attempt in range(REPLACED_RETRIES):
        result = _read_once(path, error, retry=attempt + 1 < REPLACED_RETRIES)
        if result is not _REPLACED:
            return result
    raise AssertionError('unreachable')



def _read_once(path, error, retry):
    try:
        before = path.lstat()
    except FileNotFoundError:
        return MISSING
    validate_metadata(before, error)
    try:
        # Nonblocking also prevents a raced-in FIFO from hanging this reader.
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.ENOENT, errno.ENOTDIR):
            raise ValueError(error) from None
        raise
    try:
        opened = os.fstat(fd)
        validate_metadata(opened, error)
        if (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
            raise ValueError(error)
        with os.fdopen(fd, 'rb', closefd=False) as source:
            raw = source.read(MAX_RECORD_BYTES + 1)
        after = os.fstat(fd)
        if retry and after.st_nlink == 0 and stat.S_ISREG(after.st_mode):
            return _REPLACED
        validate_metadata(after, error)
        if len(raw) > MAX_RECORD_BYTES:
            raise ValueError(error)
        return json.loads(raw.decode('utf-8'))
    finally:
        os.close(fd)
