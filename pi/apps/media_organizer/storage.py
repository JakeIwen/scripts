"""Private, durable JSON records for organizer state (not payload files)."""
import json
import os
import re
import secrets
import tempfile
from datetime import datetime, timezone
from pathlib import Path


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id() -> str:
    return datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ-') + secrets.token_hex(2)


def record_path(directory: Path, record_id: str) -> Path:
    if not isinstance(record_id, str) or not re.fullmatch(r'\d{8}T\d{6}Z-[0-9a-f]{4}', record_id):
        raise ValueError('invalid record id')
    return directory / (record_id + '.json')


def read_json(path: Path):
    with path.open(encoding='utf-8') as stream:
        return json.load(stream)


def write_json(path: Path, data) -> None:
    # Separate from tool_settings: these journals live in the package release
    # boundary and need directory fsync, not that tool's preference schema.
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name, dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(data, stream, ensure_ascii=False, allow_nan=False)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
