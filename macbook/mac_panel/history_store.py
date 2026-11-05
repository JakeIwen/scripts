"""Private, bounded SQLite time series; connections belong to each operation."""
from collections import defaultdict
from contextlib import contextmanager
import os
from pathlib import Path
import sqlite3
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
            count = connection.execute('SELECT count(*) FROM samples WHERE ' + clause, args).fetchone()[0]
            # Select evenly spaced row positions, retaining both endpoints. Iterating the
            # cursor bounds Python memory even for a long unfiltered history request.
            selected = None
            if count > limit:
                selected = ({round(i * (count - 1) / (limit - 1)) for i in range(limit)}
                            if limit > 1 else {count - 1})
            rows = connection.execute('SELECT key, ts, level, value, text FROM samples WHERE '
                                      + clause + ' ORDER BY ts, key, rowid', args)
            for index, (name, ts, level, value, text) in enumerate(rows):
                if selected is None or index in selected:
                    result[name].append(dict(ts=ts, level=level, value=value, text=text))
        return {'series': dict(result)}

    def prune(self, days=RETENTION_DAYS):
        with self.connection() as connection:
            connection.execute('DELETE FROM samples WHERE ts < ?', (time.time() - days * 86400,))
