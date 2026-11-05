"""Private durable files for the separately installed bootstrap runtime.

This runtime has no shared/python import root. Like the independently installed
NAS receiver, it keeps the small atomic writer local; directory fsync is part
of this deployment decision contract, not only file-data publication.
"""
import json
import os
from pathlib import Path
import stat
import tempfile

MAX_RECORD_BYTES = 4 * 1024 * 1024


def sync(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def directory(path, mode=0o700, create=True):
    path = Path(path)
    if path.is_symlink():
        raise ValueError('Symlinked bootstrap directory: ' + str(path))
    if not path.exists() and create:
        path.mkdir(mode=mode)
        sync(path.parent)
    info = path.stat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o022:
        raise ValueError('Unsafe bootstrap directory: ' + str(path))
    return path


def read(path, private=False):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_RECORD_BYTES:
            raise ValueError('Unsafe bootstrap file: ' + str(path))
        if private and (info.st_uid != os.geteuid() or info.st_mode & 0o022):
            raise ValueError('Untrusted bootstrap record: ' + str(path))
        return stream.read(MAX_RECORD_BYTES + 1)


def write(path, data, mode=0o600, owner=None):
    path = Path(path)
    if path.is_symlink():
        raise ValueError('Refusing symlink: ' + str(path))
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name + '.', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fchmod(stream.fileno(), mode)
            if owner is not None:
                os.fchown(stream.fileno(), *owner)
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        sync(path.parent)
    finally:
        if os.path.lexists(temporary):
            os.unlink(temporary)


def read_json(path):
    return json.loads(read(path, private=True))


def write_json(path, value):
    write(path, (json.dumps(value, indent=2, allow_nan=False) + '\n').encode())


def remove(path):
    path = Path(path)
    read(path, private=True)
    path.unlink()
    sync(path.parent)
