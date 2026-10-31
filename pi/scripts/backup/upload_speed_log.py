"""Per-minute iCloud upload speed samples, kept for 30 days.

Each sample is two little-endian uint32s: the epoch second the minute ended and
the bytes per second averaged over it. Only the admitted worker writes the file;
the dashboard's status helper reads it.
"""
import os
import struct
import tempfile
import time

FILENAME = 'upload-speed.bin'
RECORD = struct.Struct('<II')
RETENTION_SECONDS = 30 * 86400
SAMPLE_SECONDS = 60
# Byte counters further apart than this span a pause (network check retries,
# a new rclone command, verification), not throughput.
MAX_GAP_SECONDS = 120
# Rewriting the file every minute once it holds a month would be wasteful;
# trim old samples hourly and let readers filter the rest.
PRUNE_SLACK_SECONDS = 3600
UINT32_MAX = 2**32 - 1


def log_path(directory):
    return directory / FILENAME


def _records(data):
    usable = len(data) - len(data) % RECORD.size
    return [RECORD.unpack_from(data, offset) for offset in range(0, usable, RECORD.size)]


def read_samples(path, now=None):
    """Samples from the last 30 days, oldest first; a missing log has none."""
    now = time.time() if now is None else now
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return []
    cutoff = now - RETENTION_SECONDS
    return sorted((at, speed) for at, speed in _records(data) if cutoff <= at <= now + 86400)


def prune(path, now=None):
    """Drop samples older than 30 days (and torn tails) with an atomic rewrite."""
    now = time.time() if now is None else now
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return
    kept = [row for row in _records(data) if now - RETENTION_SECONDS <= row[0]]
    if len(kept) * RECORD.size == len(data):
        return
    fd, name = tempfile.mkstemp(prefix='.' + path.name + '.', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(b''.join(RECORD.pack(*row) for row in kept))
            f.flush()
            os.fsync(f.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def append(path, at, bytes_per_second):
    with path.open('ab') as f:
        size = f.tell()
        if size % RECORD.size:
            # A torn earlier write would misalign every later record.
            f.truncate(size - size % RECORD.size)
            f.seek(0, os.SEEK_END)
        f.write(RECORD.pack(int(at), min(UINT32_MAX, max(0, round(bytes_per_second)))))
    with path.open('rb') as f:
        first = f.read(RECORD.size)
    if len(first) == RECORD.size and RECORD.unpack(first)[0] < at - RETENTION_SECONDS - PRUNE_SLACK_SECONDS:
        prune(path, at)


class UploadSpeedSampler:
    """Turn cumulative per-command byte counters into one sample a minute."""

    def __init__(self, interval=SAMPLE_SECONDS):
        self.interval = interval
        self.last = None
        self.started = None
        self.sent = 0
        self.seconds = 0.0

    def observe(self, directory, sent_bytes, now=None):
        if type(sent_bytes) is not int or sent_bytes < 0:
            return
        now = time.time() if now is None else now
        if self.last is not None:
            at, previous = self.last
            if not 0 < now - at <= MAX_GAP_SECONDS:
                # A sample spans one stretch of uploading, never a pause.
                self.started, self.sent, self.seconds = None, 0, 0.0
            # A smaller counter means a new rclone command started from zero.
            elif sent_bytes >= previous:
                self.sent += sent_bytes - previous
                self.seconds += now - at
                if self.started is None:
                    self.started = at
        self.last = (now, sent_bytes)
        if self.started is not None and now - self.started >= self.interval and self.seconds > 0:
            speed = self.sent / self.seconds
            self.started, self.sent, self.seconds = None, 0, 0.0
            try:
                append(log_path(directory), now, speed)
            except OSError:
                pass  # The graph is optional; a lost sample must never stop an upload.
