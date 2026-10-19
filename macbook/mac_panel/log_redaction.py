"""Redact display text and read only bounded tails of regular log files."""
import os
from pathlib import Path
import re
import stat

from panel_contracts import LogRecord

URL = re.compile(r'https?://[^\s<>\"\']+', re.I)
AUTH = re.compile(r'(?i)\b(authorization\s*[:=]\s*)([^\r\n]+)')
BEARER = re.compile(r'(?i)\bbearer\s+[a-z0-9._~+/=-]+')
PAIR = re.compile(r'''(?ix)(["']?\b(?:[a-z0-9_]*(?:token|password|passwd|secret|api_key|ntfy_warning_url))["']?\s*[:=]\s*)(?:"[^"\r\n]*"|'[^'\r\n]*'|[^\s,;}]+)''')


def redact(text: str) -> str:
    # Arbitrary self-hosted ntfy topics cannot be identified by hostname alone.
    text = URL.sub('[URL hidden]', text)
    text = AUTH.sub(r'\1[hidden]', text)
    text = BEARER.sub('Bearer [hidden]', text)
    return PAIR.sub(r'\1[hidden]', text)


def redact_data(value):
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, list):
        return [redact_data(item) for item in value]
    if isinstance(value, dict):
        return {key: redact_data(item) for key, item in value.items()}
    return value


def safe_display_path(path: Path) -> bool:
    parts = path.resolve().parts
    return not any(parts[i:i + 2] == ('pi', 'secrets') for i in range(len(parts) - 1))


def log_tail(path: str, lines: int = 30, limit: int = 65536) -> LogRecord:
    record = LogRecord(path=redact(path), text='', modified=None, error=None)
    try:
        target = Path(path).expanduser()
        if not target.is_absolute() or not safe_display_path(target):
            raise ValueError('Log path is not permitted')
        fd = os.open(target, os.O_RDONLY | os.O_NONBLOCK)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode):
                raise ValueError('Log is not a regular file')
            os.lseek(fd, max(0, info.st_size - limit), os.SEEK_SET)
            content = os.read(fd, limit).decode('utf-8', errors='replace')
            record.update(text=redact('\n'.join(content.splitlines()[-lines:])),
                          modified=info.st_mtime)
        finally:
            os.close(fd)
    except (OSError, ValueError) as exc:
        record['error'] = redact(str(exc))
    return record
