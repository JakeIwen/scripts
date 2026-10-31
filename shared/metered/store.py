"""Private serialized records, with durable atomic publication and bounded reads."""
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import stat

from shared.python.tool_settings import write_private_json


MAX_RECORD_BYTES = 1024 * 1024


class RevisionConflict(ValueError):
    """The caller must reload before applying this edit."""


class RecordStore:
    def __init__(self, path, record_type, default):
        self.path = Path(path)
        self.record_type = record_type
        self.default = default

    @contextmanager
    def locked(self):
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        lock_path = self.path.with_suffix(self.path.suffix + '.lock')
        descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, 'r+b') as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
                raise ValueError('unsafe metering lock')
            fcntl.flock(handle, fcntl.LOCK_EX)
            yield

    def load(self):
        try:
            descriptor = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW)
        except FileNotFoundError:
            return self.default()
        with os.fdopen(descriptor, 'rb') as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_RECORD_BYTES:
                raise ValueError('unsafe or oversized metering record')
            data = handle.read(MAX_RECORD_BYTES + 1)
        if len(data) > MAX_RECORD_BYTES:
            raise ValueError('oversized metering record')
        return self.record_type.from_dict(json.loads(data))

    def save_locked(self, value):
        # Validate round trips too: internal callers must obey the same wire contract.
        payload = self.record_type.from_dict(value.as_dict()).as_dict()
        if len((json.dumps(payload, indent=2, allow_nan=False) + '\n').encode()) > MAX_RECORD_BYTES:
            raise ValueError('oversized metering record')
        if self.path.is_symlink():
            raise ValueError('refusing a symlink metering record')
        write_private_json(self.path, payload)
        directory = os.open(self.path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)

    def change(self, revision, transform):
        with self.locked():
            current = self.load()
            if type(revision) is not int or revision != current.revision:
                raise RevisionConflict('metering record changed; reload and retry')
            updated = transform(current)
            self.save_locked(updated)
            return updated
