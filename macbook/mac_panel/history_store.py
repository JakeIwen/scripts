"""Private, bounded SQLite time series; connections belong to each operation."""
from collections import defaultdict
from contextlib import contextmanager
import os
from pathlib import Path
import sqlite3
from statistics import median
import time

from panel_contracts import HistoryPayload

RETENTION_DAYS = 30
MAX_SAMPLES = 5000


class HistoryStore:
    def __init__(self, directory=None):
        self.directory = Path(directory or os.environ.get(
            'MAC_PANEL_STATE_DIR', Path.home() / '.local/state/mac-panel'))
        self.path = self.directory / 'history.sqlite'

    @contextmanager
    def connection(self):
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.directory.chmod(0o700)
        # Create privately before SQLite opens it, including outside server.main's umask.
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(fd)
        self.path.chmod(0o600)
        connection = sqlite3.connect(self.path, timeout=10)
        try:
            connection.execute('PRAGMA journal_mode=WAL')
            connection.execute('CREATE TABLE IF NOT EXISTS samples('
                               'section TEXT, key TEXT, ts REAL, level TEXT, '
                               'value REAL NULL, text TEXT NULL)')
            connection.execute('CREATE INDEX IF NOT EXISTS samples_series ON samples(section, key, ts)')
            with connection:
                yield connection
        finally:
            connection.close()

    def record(self, samples):
        with self.connection() as connection:
            connection.executemany('INSERT INTO samples VALUES (:section, :key, :ts, :level, :value, :text)',
                                   samples)

    def series(self, section, since, until, key=None, limit=MAX_SAMPLES) -> HistoryPayload:
        if not 1 <= limit <= MAX_SAMPLES:
            raise ValueError('History limit must be between 1 and 5000')
        clause = 'section = ? AND ts >= ? AND ts <= ?'
        args = [section, since, until]
        if key is not None:
            clause += ' AND key = ?'
            args.append(key)
        result = defaultdict(list)
        with self.connection() as connection:
            # COUNT and iteration share a snapshot even while the sampler commits.
            connection.execute('BEGIN')
            counts = {name: (count, first, last) for name, count, first, last in
                      connection.execute('SELECT key, count(*), min(ts), max(ts) FROM samples WHERE '
                                         + clause + ' GROUP BY key', args)}
            # Every series keeps both endpoints, even if that exceeds the total budget.
            budget = min(limit, max(2, MAX_SAMPLES // max(1, len(counts))))
            selected = {name for name, (count, _, _) in counts.items() if count > budget}
            slots, changes, previous, indexes = defaultdict(dict), defaultdict(list), {}, defaultdict(int)
            rows = connection.execute('SELECT key, ts, level, value, text FROM samples WHERE '
                                      + clause + ' ORDER BY key, ts, rowid', args)
            for name, ts, level, value, text in rows:
                index = indexes[name]
                indexes[name] += 1
                point = dict(ts=ts, level=level, value=value, text=text)
                if name not in selected:
                    result[name].append(point)
                    continue
                count, first, last = counts[name]
                if name in previous and previous[name] != level:
                    changes[name].append((index, point))
                previous[name] = level
                if budget == 1:
                    slot = 0
                    distance = count - 1 - index
                else:
                    fraction = (ts - first) / (last - first) if last > first else index / (count - 1)
                    slot = round(fraction * (budget - 1))
                    distance = abs(fraction * (budget - 1) - slot)
                    if index == 0 or index == count - 1:
                        distance = -1
                if slot not in slots[name] or distance < slots[name][slot][0]:
                    slots[name][slot] = (distance, index, point)
        for name in selected:
            chosen = {index: point for _, index, point in slots[name].values()}
            protected = {0, counts[name][0] - 1}
            if budget > 2:
                for index, point in changes[name]:
                    if index in chosen:
                        protected.add(index)
                        continue
                    candidates = chosen.keys() - protected
                    if not candidates:
                        break
                    replace = min(candidates, key=lambda item: abs(chosen[item]['ts'] - point['ts']))
                    del chosen[replace]
                    chosen[index] = point
                    protected.add(index)
            result[name] = [chosen[index] for index in sorted(chosen)]
        # Report spacing only for thinned series: sparse raw samples must not turn an outage into cadence.
        spacing = {name: median(deltas) if deltas else 0 for name, points in result.items()
                   for deltas in [[b['ts'] - a['ts'] for a, b in zip(points, points[1:])
                                   if b['ts'] > a['ts']]] if name in selected}
        return {'series': dict(result), 'spacing': spacing}

    def prune(self, days=RETENTION_DAYS):
        with self.connection() as connection:
            connection.execute('DELETE FROM samples WHERE ts < ?', (time.time() - days * 86400,))
