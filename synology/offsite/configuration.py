"""Small bounded private state namespace for the DSM controller."""
from enum import Enum
import json
import os
from pathlib import Path
import tempfile
from typing import TypedDict

DEFAULT_ROOT = Path('/volume1/vanpi-offsite')
POLL_SECONDS = 1
STOP_SECONDS = 15


class State(str, Enum):
    NOT_RUN = 'not-run'
    WAITING = 'waiting'
    SERVING = 'serving'
    ERROR = 'error'
    STOPPED = 'stopped'


class ReceiverStatus(TypedDict, total=False):
    pid: int
    identity: str
    updated: float
    state: State
    repo_id: str
    label: str
    server_pid: int
    server_identity: str
    cause: str
    enabled: bool
    owned_watcher: bool
    owned_server: bool


def private_root(root):
    root = Path(root)
    if not root.is_absolute() or root.resolve() != root:
        raise ValueError('Receiver root must be an absolute real directory')
    info = root.stat()
    if not root.is_dir() or info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise ValueError('Receiver root must be owned by this user and mode 0700')
    return root


def publish(path, value):
    # The NAS is independently installed: reuse the shared protected reader but
    # keep this tiny durable writer here rather than importing Pi runtime modules.
    fd, name = tempfile.mkstemp(dir=path.parent, prefix='.' + path.name)
    try:
        with os.fdopen(fd, 'w') as output:
            json.dump(value, output, sort_keys=True)
            output.write('\n')
            output.flush()
            os.fsync(output.fileno())
        os.replace(name, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def read(path):
    from protected_json import MISSING, read_protected_json
    value = read_protected_json(path, 'Unsafe receiver private record')
    return None if value is MISSING else value


def config(root):
    from offsite_config import validate_transport
    value = validate_transport(read(root / 'config.json'), extra_fields=('tailscale',))
    path = Path(value['tailscale'])
    if not path.is_absolute() or path.resolve() != path or not path.is_file() or not os.access(path, os.X_OK):
        raise ValueError('Configure the actual installed Tailscale executable')
    return value
