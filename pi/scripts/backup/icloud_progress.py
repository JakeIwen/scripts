"""Progress signals without exposing rclone messages or downloaded contents."""
import hashlib
from enum import Enum
import json
import math
import os
from pathlib import Path
import threading
import time


class TransferActivity(str, Enum):
    CONNECTING = 'connecting'
    DOWNLOADING = 'downloading'
    NETWORK_CHECK = 'network-check'
    INVENTORY = 'inventory'
    WAITING = 'waiting'

COPY_COMPLETIONS = frozenset({
    'Copied (new)', 'Copied (replaced existing)', 'Copied (server-side copy)',
    'Copied (Rcat, new)', 'Copied (Rcat, replaced existing)',
    'Multi-thread Copied (new)', 'Multi-thread Copied (replaced existing)',
})


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
    def __init__(self, on_completed=None):
        self.offset = 0
        self.partial = b''
        self.counters = {}
        self.on_completed = on_completed

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
            if self.on_completed is not None and isinstance(value, dict):
                message, name = value.get('msg'), value.get('object')
                if (value.get('level') == 'info' and isinstance(name, str) and isinstance(message, str)
                        and message.split(' to: ', 1)[0] in COPY_COMPLETIONS):
                    self.on_completed(name)
            stats = value.get('stats') if isinstance(value, dict) else None
            if not isinstance(stats, dict):
                continue
            speed = stats.get('speed')
            self.counters['speed'] = (float(speed) if type(speed) in (int, float)
                                      and math.isfinite(speed) and speed >= 0 else None)
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


RETENTION_PROGRESS_WRITE_SECONDS = 30
RETENTION_LOG_EVERY = 50


class RetentionStep(str, Enum):
    RETIRING = 'retiring'
    LISTING = 'listing'
    DELETING = 'deleting'
    LOCAL = 'local'
    COMPLETE = 'complete'


class RetentionProgress:
    """Advisory orphan-cleanup counts in worker state (`retention_progress`).

    An episode is one generation's cleanup under one keep_generations value,
    stored when it starts; counts accumulate across interrupted runs of the same
    episode. Never used to authorize a deletion or to decide completion:
    retention_done_for stays the only completion record.
    """

    def __init__(self, state, generation, keep_generations, save, log=None, clock=None, monotonic=None):
        self.save, self.log = save, log or print
        self.clock, self.monotonic = clock or time.time, monotonic or time.monotonic
        self.last_write = None
        self.listing_started = None
        record = state.get('retention_progress')
        if not (isinstance(record, dict) and record.get('generation') == generation
                and record.get('keep_generations') == keep_generations):
            record = self.new_episode(generation, keep_generations)
        self.record = state['retention_progress'] = record
        self.update(step=RetentionStep.RETIRING.value)

    def new_episode(self, generation, keep_generations):
        return {'generation': generation, 'keep_generations': keep_generations,
                'episode_started_at': self.clock(), 'listings': 0, 'planned_objects': None,
                'planned_bytes': None, 'remaining_objects': None, 'remaining_bytes': None,
                'deleted_objects': 0, 'deleted_bytes': 0, 'completed_at': None}

    def update(self, force=True, **values):
        self.record.update(values)
        now = self.monotonic()
        if force or self.last_write is None or now - self.last_write >= RETENTION_PROGRESS_WRITE_SECONDS:
            self.record['updated_at'] = self.clock()
            self.last_write = now
            self.save()

    def listing(self):
        self.listing_started = self.monotonic()
        self.record['listings'] = self.record.get('listings', 0) + 1
        self.log('Retention: listing cloud objects', flush=True)
        self.update(step=RetentionStep.LISTING.value, listing_started_at=self.clock(),
                    listing_finished_at=None, listing_seconds=None, listed_objects=None, completed_at=None)

    def tick(self, counters=None):
        """Throttled heartbeat from a running command's progress callback."""
        listed = (counters or {}).get('listed')
        if self.record.get('step') == RetentionStep.LISTING.value and type(listed) is int:
            self.record['listed_objects'] = listed
        self.update(force=False)

    def listed(self, total, objects, size):
        record, previous = self.record, self.record.get('remaining_objects')
        if record.get('planned_objects') is None or (type(previous) is int and objects > previous):
            if type(previous) is int and objects > previous:
                # More left than last time: a different cleanup set, so restart the count.
                self.log(f'Retention: {objects:,} unreferenced objects now, more than the '
                         f'{previous:,} left before; counting a new cleanup', flush=True)
                record.update(self.new_episode(record['generation'], record['keep_generations']), listings=1)
            record.update(planned_objects=objects, planned_bytes=size)
        seconds = round(self.monotonic() - self.listing_started, 1) if self.listing_started is not None else None
        self.log(f'Retention: listed {total:,} objects in {seconds}s; {objects:,} unreferenced '
                 f'({size / 1024**3:.1f} GiB) to delete', flush=True)
        self.update(step=RetentionStep.DELETING.value, listing_finished_at=self.clock(), listing_seconds=seconds,
                    remaining_objects=objects, remaining_bytes=size,
                    deleted_objects=record['planned_objects'] - objects,
                    deleted_bytes=max(0, record['planned_bytes'] - size))

    def deleted(self, size):
        record = self.record
        values = {'remaining_objects': record['remaining_objects'] - 1,
                  'remaining_bytes': max(0, record['remaining_bytes'] - size),
                  'deleted_objects': record['deleted_objects'] + 1,
                  'deleted_bytes': record['deleted_bytes'] + size}
        if values['deleted_objects'] % RETENTION_LOG_EVERY == 0:
            self.log(f"Retention: deleted {values['deleted_objects']:,} / {record['planned_objects']:,} "
                     f"({values['remaining_bytes'] / 1024**3:.1f} GiB left)", flush=True)
        self.update(force=False, **values)

    def local(self):
        self.update(step=RetentionStep.LOCAL.value)

    def finished(self):
        record = self.record
        self.log(f"Retention: deleted {record.get('deleted_objects', 0):,} unreferenced objects "
                 f"({record.get('deleted_bytes', 0) / 1024**3:.1f} GiB); cleanup complete", flush=True)
        self.update(step=RetentionStep.COMPLETE.value, completed_at=self.clock())
