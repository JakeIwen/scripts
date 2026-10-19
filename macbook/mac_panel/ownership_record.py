"""Private ownership record, published only after the listener has bound."""
import json
import os
from pathlib import Path
import secrets
import stat
import tempfile
import time

from panel_contracts import Ownership
from panel_paths import PORT


def new_record() -> Ownership:
    return Ownership(pid=os.getpid(), instance=secrets.token_urlsafe(24),
                     token=secrets.token_urlsafe(32), port=PORT, started_at=time.time())


def atomic_write(path: Path, data: bytes, mode=0o600):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(prefix=f'.{path.name}.', dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.chmod(mode)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def write_record(path: Path, record: Ownership):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.chmod(0o700)
    atomic_write(path, json.dumps(record).encode())


def read_record(path: Path) -> Ownership | None:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                    or stat.S_IMODE(info.st_mode) != 0o600 or info.st_size > 4096):
                return None
            record = json.loads(os.read(fd, 4096))
        finally:
            os.close(fd)
        if not isinstance(record, dict):
            return None
        if type(record.get('pid')) is not int or record['pid'] <= 0 or record.get('port') != PORT:
            return None
        if not all(isinstance(record.get(key), str) and len(record[key]) >= 20
                   for key in ('instance', 'token')):
            return None
        if type(record.get('started_at')) not in (int, float):
            return None
        return record
    except (OSError, ValueError):
        return None


def remove_record(path: Path, record: Ownership):
    current = read_record(path)
    if current and current['pid'] == record['pid'] and current['instance'] == record['instance']:
        path.unlink(missing_ok=True)
