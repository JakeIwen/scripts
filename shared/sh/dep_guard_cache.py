"""Best-effort private fact cache for the flat-deployed install guard."""

import copy
import fcntl
import json
import math
import os
from pathlib import Path
import stat
import threading
import time


MAX_BYTES = 1024 * 1024
MAX_ENTRIES = 2000
LOCK_WAIT = 0.5


def _encode(value):
    return json.dumps(value, separators=(',', ':'), allow_nan=False).encode('ascii')


def _load(path):
    try:
        flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
        with os.fdopen(os.open(path, flags), 'rb') as source:
            if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                return {}
            data = source.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            return {}
        rows = json.loads(data)
        if not isinstance(rows, dict):
            return {}
        now = time.time()
        return {k: r for k, r in rows.items() if isinstance(r, list) and len(r) == 3
                and all(type(t) in (int, float) and math.isfinite(t) for t in r[:2])
                and r[1] > now}
    except (OSError, ValueError, OverflowError, RecursionError):
        return {}


def _bounded(rows):
    # Serialize each row once. Re-encoding the whole file for each eviction
    # would make the 1 MiB space bound an expensive lock-time penalty.
    fragments = []
    size = 2  # outer braces
    now = time.time()
    for key, row in sorted(rows.items(), key=lambda pair: pair[1][0], reverse=True):
        if row[1] <= now:
            continue
        field = _encode(key) + b':' + _encode(row)
        if len(field) + 2 > MAX_BYTES:
            continue  # an individually uncacheable fact must not evict the cache
        size += len(field) + bool(fragments)
        if size > MAX_BYTES or len(fragments) >= MAX_ENTRIES:
            break
        fragments.append(field)
    return b'{' + b','.join(fragments) + b'}'


def _acquire(fd, deadline):
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except BlockingIOError:
            left = deadline - time.monotonic()
            if left <= 0:
                return False
            time.sleep(min(0.01, left))


def _replace(path, data):
    # This name is owned by the lock. A killed writer can leave at most one
    # temp file on the Pi's SD card, not a new mkstemp orphan per invocation.
    temp = path.with_suffix('.tmp')
    temp.unlink(missing_ok=True)
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, 'wb') as output:
            output.write(data)
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


class Cache:
    def __init__(self, home: Path, env=None):
        configured = Path((os.environ if env is None else env).get('XDG_CACHE_HOME') or '.')
        root = configured if configured.is_absolute() else Path(home) / '.cache'
        self.path = root / 'dep-install-guard' / 'lookups.json'
        self._data = _load(self.path)
        self._pending = {}
        self._mutex = threading.Lock()
        self._last_stamp = 0

    def get(self, key):
        with self._mutex:
            row = self._pending.get(key, self._data.get(key))
            return copy.deepcopy(row[2]) if row and row[1] > time.time() else None

    def put(self, key, value, ttl_seconds):
        with self._mutex:
            # Back-to-back puts can fall in a single wall-clock tick. Preserve
            # their true observation order at the eviction boundary.
            self._last_stamp = max(time.time_ns(), self._last_stamp + 1)
            self._pending[key] = [self._last_stamp, time.time() + ttl_seconds, copy.deepcopy(value)]

    def save(self, max_wait=LOCK_WAIT):
        """Merge only our dirty facts, preferring the most recently observed fact."""
        deadline = time.monotonic() + max(0, min(max_wait, LOCK_WAIT))
        with self._mutex:
            pending = self._pending.copy()
        if not pending:
            return True
        try:
            self.path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
            flags = os.O_RDONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK
            fd = os.open(self.path.with_suffix('.json.lock'), flags, 0o600)
            try:
                if not stat.S_ISREG(os.fstat(fd).st_mode) or not _acquire(fd, deadline):
                    return False
                rows = _load(self.path)
                for key, row in pending.items():
                    if key not in rows or rows[key][0] <= row[0]:
                        rows[key] = row
                data = _bounded(rows)
                _replace(self.path, data)
                with self._mutex:
                    self._data = json.loads(data)
                    for key, row in pending.items():
                        if self._pending.get(key) is row:
                            del self._pending[key]
                return True
            finally:
                os.close(fd)
        except (OSError, ValueError, TypeError, OverflowError, RecursionError):
            return False
