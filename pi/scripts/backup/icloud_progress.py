"""Progress signals without exposing rclone messages or downloaded contents."""
import hashlib
import json
import os
from pathlib import Path
import threading
import time


class ProgressWatch:
    def __init__(self, timeout, clock=time.monotonic):
        self.timeout = timeout
        self.clock = clock
        self.last_progress = clock()
        self.high_water = {}

    def observe(self, counters):
        changed = False
        for key, value in counters.items():
            if type(value) is int and value >= 0 and value > self.high_water.get(key, 0):
                self.high_water[key] = value
                changed = True
        if changed:
            self.last_progress = self.clock()
        return changed

    def idle_seconds(self):
        return max(0, self.clock() - self.last_progress)

    def expired(self):
        return self.idle_seconds() >= self.timeout


class RcloneStats:
    def __init__(self):
        self.offset = 0
        self.partial = b''
        self.counters = {}

    def read(self, fd):
        # pread does not move the file offset shared with the child's stderr.
        data = os.pread(fd, 1024 * 1024, self.offset)
        self.offset += len(data)
        lines = (self.partial + data).split(b'\n')
        self.partial = lines.pop()
        if len(self.partial) > 1024 * 1024:
            self.partial = b''
        for line in lines:
            try:
                value = json.loads(line)
            except (ValueError, UnicodeError):
                continue
            stats = value.get('stats') if isinstance(value, dict) else None
            if not isinstance(stats, dict):
                continue
            for key in ('bytes', 'checks', 'transfers', 'listed', 'deletes', 'renames'):
                value = stats.get(key)
                if type(value) is int and value >= 0:
                    self.counters[key] = value
        return dict(self.counters)


class ProcessIO:
    """Track local Borg/rsync reads and writes, including their descendants."""
    def __init__(self):
        self.high_water = {}

    def sample(self, pid):
        pending, seen = [pid], set()
        while pending:
            current = pending.pop()
            if current in seen:
                continue
            seen.add(current)
            base = Path('/proc') / str(current)
            try:
                stat_fields = (base / 'stat').read_text().rsplit(')', 1)[1].split()
                identity = (current, stat_fields[19])  # pid + process start ticks
                values = dict(line.split(':', 1) for line in (base / 'io').read_text().splitlines())
                amount = int(values['rchar']) + int(values['wchar'])
                self.high_water[identity] = max(amount, self.high_water.get(identity, 0))
                children = base / 'task' / str(current) / 'children'
                pending.extend(int(value) for value in children.read_text().split())
            except (OSError, ValueError, KeyError, IndexError):
                continue
        return {'local_io_bytes': sum(self.high_water.values())}


class StreamDigest:
    def __init__(self, stream):
        self.stream = stream
        self.bytes_read = 0
        self.sha = hashlib.sha256()
        self.error = None
        self.lock = threading.Lock()
        self.thread = threading.Thread(target=self._read, daemon=True)
        self.thread.start()

    def _read(self):
        try:
            while True:
                data = self.stream.read1(64 * 1024)
                if not data:
                    break
                self.sha.update(data)
                with self.lock:
                    self.bytes_read += len(data)
        except Exception as exc:
            self.error = type(exc).__name__

    def counters(self):
        with self.lock:
            return {'download_bytes': self.bytes_read}

    def result(self):
        self.thread.join(timeout=5)
        if self.thread.is_alive() or self.error:
            raise RuntimeError('could not finish hashing the downloaded file')
        return {'bytes': self.bytes_read, 'sha256': self.sha.hexdigest()}
