"""Private, atomic state storage shared by checkpoint, monitor and repair paths."""
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile


def private_dir(path):
    path = Path(path)
    if path.is_symlink():
        raise ValueError('Refusing a symlinked guard directory.')
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not path.is_dir() or path.stat().st_uid != os.getuid():
        raise ValueError('Guard directory belongs to another user.')
    path.chmod(0o700)
    return path


def load_json(path):
    path = Path(path)
    flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0)
    descriptor = os.open(path, flags)
    with os.fdopen(descriptor, 'r', encoding='utf-8') as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError('Guard data must be a private, owner-controlled regular file.')
        return json.load(source)


def write_bytes(path, content):
    path = Path(path)
    directory = private_dir(path.parent)
    if path.is_symlink():
        raise ValueError('Refusing a symlinked guard state file.')
    if path.exists() and (not path.is_file() or path.stat().st_uid != os.getuid()):
        raise ValueError('Guard state file belongs to another owner.')
    descriptor, temporary = tempfile.mkstemp(prefix='.'+path.name+'-', dir=directory)
    try:
        with os.fdopen(descriptor, 'wb') as target:
            target.write(content)
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)  # Only the temporary file created by this call.


def write_json(path, value):
    content = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2)+'\n'
    write_bytes(path, content.encode('utf-8'))


def file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as source:
        for chunk in iter(lambda: source.read(1024*1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


@contextmanager
def state_lock(directory):
    path = private_dir(directory)/'guard.lock'
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR | getattr(os,'O_NOFOLLOW',0), 0o600)
    try:
        if os.fstat(descriptor).st_uid != os.getuid():
            raise ValueError('Guard lock belongs to another user.')
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(descriptor)
