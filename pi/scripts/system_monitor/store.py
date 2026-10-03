"""EventStore and its SQLite ownership."""

import json
import os
import sqlite3

if __package__:
    from .common import (
        DEFAULT_CRASH_SAMPLE_LIMIT,
        DEFAULT_DATABASE,
        DEFAULT_RETENTION_DAYS,
        DEFAULT_SAMPLE_INTERVAL,
        DEFAULT_SAMPLE_RETENTION_HOURS,
        event_fingerprint,
        json_dumps,
        normalize_boot_id,
        utc_timestamp,
    )
else:
    from common import (
        DEFAULT_CRASH_SAMPLE_LIMIT,
        DEFAULT_DATABASE,
        DEFAULT_RETENTION_DAYS,
        DEFAULT_SAMPLE_INTERVAL,
        DEFAULT_SAMPLE_RETENTION_HOURS,
        event_fingerprint,
        json_dumps,
        normalize_boot_id,
        utc_timestamp,
    )


class EventStore:
    def __init__(self, path=DEFAULT_DATABASE, clock=utc_timestamp, read_only=False):
        self.path = path
        self.clock = clock
        self.read_only = read_only
        if read_only:
            uri_path = os.path.abspath(path).replace("?", "%3f").replace("#", "%23")
            self.connection = sqlite3.connect(
                f"file:{uri_path}?mode=ro", uri=True, timeout=10
            )
        else:
            parent = os.path.dirname(path) or "."
            os.makedirs(parent, exist_ok=True)
            self.connection = sqlite3.connect(path, timeout=10)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA busy_timeout=5000")
        if not read_only:
            self.connection.execute("PRAGMA journal_mode=WAL")
            # A flight-recorder sample that exists only in an unflushed page is
            # little help after sudden power loss. FULL asks SQLite and the OS to
            # flush every committed 5-second sample before reporting success.
            self.connection.execute("PRAGMA synchronous=FULL")
            self.connection.execute("PRAGMA wal_autocheckpoint=32")
            self._create_schema()
            try:
                os.chmod(path, 0o640)
            except OSError:
                pass

    def _create_schema(self):
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL NOT NULL,
                boot_id TEXT,
                category TEXT NOT NULL,
                kind TEXT NOT NULL,
                severity TEXT NOT NULL,
                source TEXT NOT NULL,
                summary TEXT NOT NULL,
                message TEXT NOT NULL,
                fingerprint TEXT NOT NULL UNIQUE,
                state_json TEXT
            );
            CREATE INDEX IF NOT EXISTS events_timestamp_idx ON events(timestamp DESC);
            CREATE INDEX IF NOT EXISTS events_kind_timestamp_idx ON events(kind, timestamp DESC);
            CREATE INDEX IF NOT EXISTS events_report_idx
                ON events(timestamp, kind, severity, category);
            CREATE TABLE IF NOT EXISTS resource_rollups (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                period_start REAL NOT NULL,
                period_end REAL NOT NULL,
                boot_id TEXT,
                sample_count INTEGER NOT NULL,
                cpu_peak REAL,
                memory_peak REAL,
                swap_peak REAL,
                load1_peak REAL,
                temperature_peak REAL,
                root_used_peak REAL,
                arm_mhz_min REAL,
                metrics_json TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS rollups_period_idx
                ON resource_rollups(period_end DESC);
            CREATE TABLE IF NOT EXISTS crash_analyses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                previous_boot_id TEXT NOT NULL UNIQUE,
                analyzed_at REAL NOT NULL,
                previous_boot_started_at REAL,
                previous_boot_ended_at REAL,
                level TEXT NOT NULL,
                headline TEXT NOT NULL,
                report_json TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS crash_analyses_time_idx
                ON crash_analyses(analyzed_at DESC);
            CREATE TABLE IF NOT EXISTS resource_samples (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL NOT NULL,
                monotonic_seconds REAL,
                boot_id TEXT NOT NULL,
                sample_json TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS samples_boot_monotonic_idx
                ON resource_samples(boot_id, monotonic_seconds DESC);
            CREATE INDEX IF NOT EXISTS samples_timestamp_idx
                ON resource_samples(timestamp DESC);
            CREATE TABLE IF NOT EXISTS pstore_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fingerprint TEXT NOT NULL UNIQUE,
                previous_boot_id TEXT NOT NULL,
                first_seen_at REAL NOT NULL,
                name TEXT NOT NULL,
                source TEXT NOT NULL,
                content TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS pstore_boot_idx
                ON pstore_records(previous_boot_id, id);
            PRAGMA user_version=4;
            """
        )
        self.connection.commit()

    def close(self):
        self.connection.close()

    def get_meta(self, key, default=None):
        row = self.connection.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        if row is None:
            return default
        try:
            return json.loads(row["value"])
        except (TypeError, ValueError):
            return default

    def set_meta(self, key, value, commit=True):
        self.connection.execute(
            "INSERT INTO meta(key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, json_dumps(value)),
        )
        if commit:
            self.connection.commit()

    def record_sample(self, sample):
        """Atomically persist the dashboard state and detailed flight sample."""
        boot_id = normalize_boot_id(sample.get("boot_id")) or "unknown"
        durable_sample = dict(sample)
        durable_sample["boot_id"] = boot_id
        self.connection.execute(
            """
            INSERT INTO resource_samples(
                timestamp, monotonic_seconds, boot_id, sample_json
            ) VALUES (?, ?, ?, ?)
            """,
            (
                float(sample["timestamp"]),
                sample.get("uptime_seconds"),
                boot_id,
                json_dumps(durable_sample),
            ),
        )
        self.set_meta("current", durable_sample, commit=False)
        self.connection.commit()

    def flight_samples(self, boot_id, limit=DEFAULT_CRASH_SAMPLE_LIMIT):
        boot_id = normalize_boot_id(boot_id)
        if not boot_id:
            return []
        rows = self.connection.execute(
            """
            SELECT sample_json FROM resource_samples
            WHERE boot_id = ?
            ORDER BY monotonic_seconds DESC, id DESC LIMIT ?
            """,
            (boot_id, max(1, min(int(limit), 5000))),
        ).fetchall()
        samples = []
        for row in reversed(rows):
            try:
                samples.append(json.loads(row["sample_json"]))
            except (TypeError, ValueError):
                continue
        return samples

    def claim_pstore_records(self, boot_id, records):
        boot_id = normalize_boot_id(boot_id)
        if not boot_id or self.read_only:
            return 0
        inserted = 0
        for record in records:
            content = record.get("content") or ""
            name = record.get("name") or "pstore"
            source = record.get("source") or "pstore"
            fingerprint = event_fingerprint("pstore", name, content)
            cursor = self.connection.execute(
                """
                INSERT OR IGNORE INTO pstore_records(
                    fingerprint, previous_boot_id, first_seen_at,
                    name, source, content
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (fingerprint, boot_id, self.clock(), name, source, content),
            )
            inserted += int(cursor.rowcount == 1)
        self.connection.commit()
        return inserted

    def pstore_for_boot(self, boot_id):
        boot_id = normalize_boot_id(boot_id)
        if not boot_id:
            return []
        try:
            rows = self.connection.execute(
                """
                SELECT name, source, content FROM pstore_records
                WHERE previous_boot_id = ? ORDER BY id
                """,
                (boot_id,),
            ).fetchall()
        except sqlite3.OperationalError:
            # Allows a read-only report during the brief upgrade window before
            # the writer has migrated an older database.
            return []
        return [dict(row) for row in rows]

    def insert_event(
        self,
        timestamp,
        boot_id,
        category,
        kind,
        severity,
        source,
        summary,
        message,
        fingerprint,
        state=None,
    ):
        cursor = self.connection.execute(
            """
            INSERT OR IGNORE INTO events(
                timestamp, boot_id, category, kind, severity, source,
                summary, message, fingerprint, state_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                float(timestamp),
                boot_id,
                category,
                kind,
                severity,
                source,
                summary,
                message,
                fingerprint,
                json_dumps(state) if state is not None else None,
            ),
        )
        self.connection.commit()
        return cursor.rowcount == 1

    def insert_rollup(self, rollup):
        self.connection.execute(
            """
            INSERT INTO resource_rollups(
                period_start, period_end, boot_id, sample_count, cpu_peak,
                memory_peak, swap_peak, load1_peak, temperature_peak,
                root_used_peak, arm_mhz_min, metrics_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                rollup["period_start"],
                rollup["period_end"],
                rollup.get("boot_id"),
                rollup["sample_count"],
                rollup.get("cpu_peak"),
                rollup.get("memory_peak"),
                rollup.get("swap_peak"),
                rollup.get("load1_peak"),
                rollup.get("temperature_peak"),
                rollup.get("root_used_peak"),
                rollup.get("arm_mhz_min"),
                json_dumps(rollup["metrics"]),
            ),
        )
        self.connection.commit()

    def prune(
        self,
        retention_days=DEFAULT_RETENTION_DAYS,
        sample_retention_hours=DEFAULT_SAMPLE_RETENTION_HOURS,
    ):
        cutoff = self.clock() - float(retention_days) * 86400
        deleted = self.connection.execute(
            "DELETE FROM resource_rollups WHERE period_end < ?", (cutoff,)
        ).rowcount
        # Use insertion order rather than wall time: a Pi without a battery RTC
        # can jump its clock at NTP synchronization, including backwards.
        max_samples = max(
            1,
            int(float(sample_retention_hours) * 3600 / DEFAULT_SAMPLE_INTERVAL),
        )
        deleted += self.connection.execute(
            """
            DELETE FROM resource_samples
            WHERE id < COALESCE((
                SELECT id FROM resource_samples
                ORDER BY id DESC LIMIT 1 OFFSET ?
            ), 0)
            """,
            (max_samples - 1,),
        ).rowcount
        self.connection.commit()
        return deleted

    def save_crash_analysis(self, report):
        analysis = report.get("analysis") or {}
        previous_boot = analysis.get("previous_boot") or {}
        boot_id = normalize_boot_id(previous_boot.get("boot_id"))
        if not analysis.get("available") or not boot_id:
            return False
        self.connection.execute(
            """
            INSERT INTO crash_analyses(
                previous_boot_id, analyzed_at, previous_boot_started_at,
                previous_boot_ended_at, level, headline, report_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(previous_boot_id) DO UPDATE SET
                analyzed_at = excluded.analyzed_at,
                previous_boot_started_at = excluded.previous_boot_started_at,
                previous_boot_ended_at = excluded.previous_boot_ended_at,
                level = excluded.level,
                headline = excluded.headline,
                report_json = excluded.report_json
            """,
            (
                boot_id,
                report["generated_at"],
                previous_boot.get("started_at"),
                previous_boot.get("ended_at"),
                analysis.get("level", "unknown"),
                analysis.get("headline", "Crash analysis"),
                json_dumps(report),
            ),
        )
        self.connection.commit()
        return True

    def crash_history(self, limit=20, full=False):
        rows = self.connection.execute(
            "SELECT * FROM crash_analyses ORDER BY analyzed_at DESC LIMIT ?",
            (max(1, min(int(limit), 100)),),
        ).fetchall()
        history = []
        for row in rows:
            try:
                report = json.loads(row["report_json"])
            except (TypeError, ValueError):
                report = {}
            analysis = report.get("analysis") or {}
            item = {
                "id": row["id"],
                "previous_boot_id": row["previous_boot_id"],
                "analyzed_at": row["analyzed_at"],
                "level": row["level"],
                "headline": row["headline"],
                "previous_boot": analysis.get("previous_boot"),
                "findings": analysis.get("findings", []),
                "counts": analysis.get("counts", {}),
                "pstore_records": len(analysis.get("pstore", [])),
                "resource_peaks": (
                    (analysis.get("resource_evidence") or {}).get("peaks") or {}
                ),
            }
            if full:
                item["report"] = report
            history.append(item)
        return history

    def report_event_rows(self, since, limit):
        return self.connection.execute(
            "SELECT * FROM events WHERE timestamp >= ? ORDER BY timestamp DESC LIMIT ?",
            (since, max(1, min(int(limit), 500))),
        ).fetchall()

    def report_rollup_rows(self, since):
        return self.connection.execute(
            "SELECT * FROM resource_rollups WHERE period_end >= ? ORDER BY period_end ASC",
            (since,),
        ).fetchall()

    def grouped_event_counts(self, since):
        return self.connection.execute(
            """
        SELECT kind, severity, category, COUNT(*) AS count, MAX(timestamp) AS last_seen
        FROM events WHERE timestamp >= ? GROUP BY kind, severity, category
        """, (since,),
        ).fetchall()

    def power_transition_rows(self, since):
        return self.connection.execute(
            """
        SELECT kind, timestamp, boot_id FROM events
        WHERE kind IN ('undervoltage_started', 'undervoltage_cleared') AND timestamp >= ?
        ORDER BY timestamp ASC
        """, (since,),
        ).fetchall()

    def usb_count_between(self, start, end):
        return self.connection.execute(
            """SELECT COUNT(*) FROM events
               WHERE timestamp >= ? AND timestamp <= ? AND category = 'usb'""",
            (start, end),
        ).fetchone()

    def filtered_event_rows(self, since, limit, category=None, severity=None):
        clauses = ["timestamp >= ?"]
        values = [since]
        if category:
            clauses.append("category = ?")
            values.append(category)
        if severity:
            clauses.append("severity = ?")
            values.append(severity)
        values.append(max(1, min(limit, 1000)))
        return self.connection.execute(
            f"SELECT * FROM events WHERE {' AND '.join(clauses)} ORDER BY timestamp DESC LIMIT ?",
            values,
        ).fetchall()


def decode_row_json(row, key):
    try:
        return json.loads(row[key]) if row[key] else None
    except (TypeError, ValueError):
        return None
