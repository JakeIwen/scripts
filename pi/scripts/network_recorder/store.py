"""Collector-owned schema, durable checkpoints and bounded incident materialization."""

import hashlib
import errno
import json
import math
import os
import sqlite3
import time
from pathlib import Path

from .parsers import identity, redact, safe_object

SCHEMA_VERSION = 1
CONTINUITY_SECONDS = 180
DEFAULT_DATABASE = os.environ.get(
    "VANPI_NETWORK_DATABASE", "/var/lib/vanpi-network/events.sqlite3"
)
COLUMNS = "time source_time received_at imported_at source device uplink kind severity message time_quality domain state stream boot_id monotonic session backfill".split()


def connect_readonly(path):
    connection = sqlite3.connect(
        Path(path).absolute().as_uri() + "?mode=ro", uri=True, timeout=3
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    if connection.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION:
        connection.close()
        raise ValueError("unsupported recorder schema; run collector setup")
    return connection


class Store:
    def __init__(
        self,
        path=DEFAULT_DATABASE,
        max_events=150000,
        retention_days=30,
        max_bytes=256 * 1024 * 1024,
        create_parent=True,
        existing_only=False,
        write_guard=None,
        portable_permissions=False,
    ):
        if (
            max_events < 1
            or not 0 < retention_days <= 365
            or max_bytes < 4 * 1024 * 1024
        ):
            raise ValueError("invalid retention limits (minimum database budget 4 MiB)")
        self.path = str(path)
        self.write_guard = write_guard
        self._guard()
        self.max_events, self.retention_days, self.max_bytes = (
            max_events,
            retention_days,
            max_bytes,
        )
        if create_parent:
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self.db = sqlite3.connect(
            Path(path).absolute().as_uri() + "?mode=rw" if existing_only else path,
            uri=existing_only,
            timeout=5,
        )
        try:
            self._initialize(portable_permissions)
        except BaseException:
            self.db.close()
            self.db = None
            raise

    def _initialize(self, portable_permissions):
        self._guard()
        self.db.row_factory = sqlite3.Row
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, SCHEMA_VERSION):
            raise ValueError("unsupported recorder schema")
        self.db.execute("PRAGMA auto_vacuum=INCREMENTAL")
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("PRAGMA busy_timeout=3000")
        self.db.execute("PRAGMA wal_autocheckpoint=128")
        # Reserve room for WAL and its index even during a maintenance transaction.
        self.page_limit = int(self.max_bytes // (4096 * 3))
        self.db.execute(f"PRAGMA max_page_count={self.page_limit}")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT, fingerprint TEXT NOT NULL UNIQUE,
                time REAL NOT NULL, source_time REAL, received_at REAL, imported_at REAL NOT NULL,
                source TEXT NOT NULL, device TEXT NOT NULL, uplink TEXT, kind TEXT NOT NULL,
                severity TEXT NOT NULL, message TEXT NOT NULL, time_quality TEXT NOT NULL,
                domain TEXT NOT NULL, state TEXT NOT NULL, stream TEXT NOT NULL,
                boot_id TEXT, monotonic REAL, session TEXT NOT NULL, backfill INTEGER NOT NULL,
                provenance TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS event_range ON events(time,source,device,uplink,kind,state);
            CREATE INDEX IF NOT EXISTS event_stream ON events(stream,session,time,id);
            CREATE INDEX IF NOT EXISTS event_source ON events(source,received_at);
            CREATE INDEX IF NOT EXISTS event_kind_time ON events(kind,time);
            CREATE TABLE IF NOT EXISTS incidents (
                id TEXT PRIMARY KEY, stream TEXT NOT NULL, session TEXT NOT NULL, day INTEGER NOT NULL,
                onset REAL NOT NULL, latest REAL NOT NULL, end REAL, domain TEXT NOT NULL,
                device TEXT NOT NULL, uplink TEXT, first_id INTEGER NOT NULL, last_id INTEGER NOT NULL,
                evidence_count INTEGER NOT NULL, closed INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS incident_range ON incidents(onset,latest,end,device,uplink);
            CREATE INDEX IF NOT EXISTS incident_partition ON incidents(stream,session,day);
            CREATE TABLE IF NOT EXISTS incident_aliases (
                id TEXT PRIMARY KEY, incident_id TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS checkpoints (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS dirty_partitions (stream TEXT, session TEXT, day INTEGER,
                PRIMARY KEY(stream,session,day));
            CREATE TABLE IF NOT EXISTS coverage (
                source TEXT PRIMARY KEY, device TEXT NOT NULL, checked_at REAL NOT NULL,
                last_received_at REAL, status TEXT NOT NULL, detail TEXT NOT NULL
            );
            PRAGMA user_version=1;
        """)
        try:
            os.chmod(self.path, 0o640)
        except OSError as exc:
            if not portable_permissions or exc.errno not in (
                errno.EPERM,
                errno.EOPNOTSUPP,
            ):
                raise
        self.set_checkpoint(
            "limits",
            dict(
                max_events=self.max_events,
                retention_days=self.retention_days,
                max_bytes=self.max_bytes,
            ),
        )
        self.db.commit()
        if self.checkpoint("identity-version") != 2:
            self.db.execute(
                "INSERT OR IGNORE INTO dirty_partitions SELECT DISTINCT stream,session,CAST(time/86400 AS INTEGER) FROM events"
            )
            self.set_checkpoint("identity-version", 2)
        if self.checkpoint("correlation-version") != 2:
            for boundary in self.db.execute(
                "SELECT time FROM events WHERE kind='router-kernel' AND message LIKE '%Linux version%'"
            ).fetchall():
                self.db.execute(
                    "INSERT OR IGNORE INTO dirty_partitions SELECT DISTINCT stream,session,CAST(time/86400 AS INTEGER) FROM events WHERE source='openwrt' AND time BETWEEN ? AND ?",
                    (boundary[0] - 180, boundary[0] + 180),
                )
            self.set_checkpoint("correlation-version", 2)
        self._rebuild(self.db.execute("SELECT * FROM dirty_partitions").fetchall())
        self.db.commit()

    def close(self):
        if self.db:
            self.db.close()
            self.db = None

    def _guard(self):
        if self.write_guard:
            self.write_guard()

    def checkpoint(self, key, default=None):
        row = self.db.execute(
            "SELECT value FROM checkpoints WHERE key=?", (key,)
        ).fetchone()
        return json.loads(row[0]) if row else default

    def set_checkpoint(self, key, value):
        self._guard()
        self.db.execute(
            "INSERT OR REPLACE INTO checkpoints VALUES (?,?)",
            (key, json.dumps(safe_object(value))),
        )

    def coverage(self, source, device, status, detail, last_received_at=None, now=None):
        self._guard()
        self.db.execute(
            "INSERT OR REPLACE INTO coverage VALUES (?,?,?,?,?,?)",
            (
                source,
                device,
                time.time() if now is None else now,
                last_received_at,
                status,
                redact(detail),
            ),
        )
        self.db.commit()

    def ingest(self, events, checkpoints=None):
        """Atomic checkpoint with each bounded source batch; stable record IDs dedupe replay."""
        self._guard()
        count = 0
        dirty = set()
        # Callers bound source batches; chunking also bounds WAL during direct bulk import.
        for event in events:
            event = dict(event)
            if event.get("time") is None or not math.isfinite(float(event["time"])):
                continue
            event.setdefault("session", event.get("boot_id") or "unverified")
            event.setdefault("backfill", False)
            event.setdefault("imported_at", time.time())
            event.setdefault("source_time", None)
            event.setdefault("received_at", None)
            event.setdefault("monotonic", None)
            event.setdefault("boot_id", None)
            provenance = safe_object(event.get("provenance", {}))
            # Imported_at/backfill and rotated filename do not change physical identity.
            key = {
                k: v
                for k, v in provenance.items()
                if k
                not in (
                    "file",
                    "format",
                    "source_ip",
                    "source_timezone",
                    "reported_at_raw",
                    "receipt_time_raw",
                    "uncertainty_seconds",
                )
            }
            if not key:
                key = {
                    "record": event.get("provenance"),
                    "time": event["time"],
                    "stream": event["stream"],
                    "message": event["message"],
                }
            fingerprint = identity(event["source"], key)
            values = [
                redact(event.get(k)) if isinstance(event.get(k), str) else event.get(k)
                for k in COLUMNS
            ]
            row = self.db.execute(
                "INSERT OR IGNORE INTO events(fingerprint,"
                + ",".join(COLUMNS)
                + ",provenance) VALUES ("
                + ",".join("?" for _ in range(len(COLUMNS) + 2))
                + ")",
                [fingerprint]
                + values
                + [json.dumps(provenance, separators=(",", ":"))],
            )
            if row.rowcount:
                count += 1
                partition = (
                    event["stream"],
                    event["session"],
                    int(event["time"] // 86400),
                )
                affected = {partition}
                if (
                    event["source"] == "openwrt"
                    and event["kind"] == "router-kernel"
                    and "Linux version" in event["message"]
                ):
                    # Backfilled boundaries can invalidate already-materialized
                    # recovery on another stream, including around midnight.
                    affected.update(
                        tuple(r)
                        for r in self.db.execute(
                            "SELECT DISTINCT stream,session,CAST(time/86400 AS INTEGER) FROM events WHERE source='openwrt' AND time BETWEEN ? AND ?",
                            (
                                event["time"] - CONTINUITY_SECONDS,
                                event["time"] + CONTINUITY_SECONDS,
                            ),
                        )
                    )
                for candidate in affected - dirty:
                    self.db.execute(
                        "INSERT OR IGNORE INTO dirty_partitions VALUES (?,?,?)",
                        candidate,
                    )
                dirty.update(affected)
            if count and count % 250 == 0:
                self._guard()
                self.db.commit()
                self._bound_storage()
        self._rebuild(dirty)
        for key, value in (checkpoints or {}).items():
            self.set_checkpoint(key, value)
        self.db.commit()
        self._bound_storage()
        return count

    def _rebuild(self, partitions):
        for stream, session, day in partitions:
            previous = self.db.execute(
                "SELECT id,first_id FROM incidents WHERE stream=? AND session=? AND day=?",
                (stream, session, day),
            ).fetchall()
            self.db.execute(
                "DELETE FROM incidents WHERE stream=? AND session=? AND day=?",
                (stream, session, day),
            )
            rows = self.db.execute(
                "SELECT id,time,state,domain,device,uplink,source,fingerprint FROM events WHERE stream=? AND session=? AND time>=? AND time<? ORDER BY time,id",
                (stream, session, day * 86400, (day + 1) * 86400),
            )
            active = None

            def save(end=None, closed=0):
                if active is None:
                    return
                incident_id = hashlib.sha256(
                    f"{stream}|{session}|{active['fingerprint']}".encode()
                ).hexdigest()[:24]
                self.db.execute(
                    "INSERT OR REPLACE INTO incidents VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        incident_id,
                        stream,
                        session,
                        day,
                        active["onset"],
                        active["latest"],
                        end,
                        active["domain"],
                        active["device"],
                        active["uplink"],
                        active["first_id"],
                        active["last_id"],
                        active["count"] + int(end is not None),
                        closed,
                    ),
                )

            for row in rows:
                if row["state"] not in ("failure", "success", "unknown"):
                    continue
                if (
                    active
                    and active["source"] == "openwrt"
                    and self.db.execute(
                        "SELECT 1 FROM events WHERE source='openwrt' AND kind='router-kernel' AND time BETWEEN ? AND ? AND message LIKE '%Linux version%' LIMIT 1",
                        (active["latest"], row["time"]),
                    ).fetchone()
                    is not None
                ):
                    save(closed=1)
                    active = None
                if active and (
                    row["time"] - active["latest"] > CONTINUITY_SECONDS
                    or (row["state"] == "failure" and row["domain"] != active["domain"])
                ):
                    save(closed=1)
                    active = None
                if row["state"] == "failure":
                    if active is None:
                        active = dict(
                            onset=row["time"],
                            latest=row["time"],
                            domain=row["domain"],
                            device=row["device"],
                            uplink=row["uplink"],
                            first_id=row["id"],
                            last_id=row["id"],
                            count=1,
                            source=row["source"],
                            fingerprint=row["fingerprint"],
                        )
                    else:
                        active.update(
                            latest=row["time"],
                            last_id=row["id"],
                            count=active["count"] + 1,
                        )
                elif active:
                    active["last_id"] = row["id"]
                    save(
                        end=row["time"] if row["state"] == "success" else None,
                        closed=1,
                    )
                    active = None
            save()
            for old in previous:
                event = self.db.execute(
                    "SELECT fingerprint FROM events WHERE id=?", (old["first_id"],)
                ).fetchone()
                if event:
                    self.alias_incident(old["id"], event[0])
            self.db.execute(
                "UPDATE incidents SET closed=1 WHERE stream=? AND end IS NULL AND EXISTS (SELECT 1 FROM events first WHERE first.id=incidents.first_id AND first.source='openwrt') AND EXISTS (SELECT 1 FROM events e WHERE e.source='openwrt' AND e.kind='router-kernel' AND e.time>=incidents.latest AND e.message LIKE '%Linux version%')",
                (stream,),
            )
            # A later observation in another session cannot recover this one,
            # but does establish that its earlier failure is no longer current.
            self.db.execute(
                "UPDATE incidents SET closed=1 WHERE stream=? AND end IS NULL AND EXISTS (SELECT 1 FROM events e WHERE e.stream=incidents.stream AND e.session<>incidents.session AND e.time>incidents.latest)",
                (stream,),
            )
            self.db.execute(
                "UPDATE incidents SET closed=1 WHERE stream=? AND end IS NULL AND EXISTS (SELECT 1 FROM events e WHERE e.kind='receiver-clock-boundary' AND json_extract(e.provenance,'$.previous_session')=incidents.session AND incidents.latest<=json_extract(e.provenance,'$.previous_time'))",
                (stream,),
            )
            self.db.execute(
                "DELETE FROM dirty_partitions WHERE stream=? AND session=? AND day=?",
                (stream, session, day),
            )

    def alias_incident(self, previous_id, first_fingerprint):
        """Resolve an old or volatile episode into its retained containing episode."""
        row = self.db.execute(
            "SELECT i.id FROM incidents i JOIN events e ON e.stream=i.stream AND e.session=i.session AND e.domain=i.domain WHERE e.fingerprint=? AND e.time BETWEEN i.onset AND COALESCE(i.end,i.latest) ORDER BY i.onset DESC LIMIT 1",
            (first_fingerprint,),
        ).fetchone()
        if row and row[0] != previous_id:
            self.db.execute(
                "UPDATE incident_aliases SET incident_id=? WHERE incident_id=?",
                (row[0], previous_id),
            )
            self.db.execute(
                "INSERT OR REPLACE INTO incident_aliases VALUES (?,?)",
                (previous_id, row[0]),
            )

    def _bound_storage(self):
        self._guard()
        count = self.db.execute("SELECT count(*) FROM events").fetchone()[0]
        pages = self.db.execute("PRAGMA page_count").fetchone()[0]
        free = self.db.execute("PRAGMA freelist_count").fetchone()[0]
        excess = max(0, count - self.max_events)
        if pages - free > self.page_limit * 0.72:
            excess = max(excess, max(250, count // 5))
        if excess:
            self._delete(
                "id IN (SELECT id FROM events ORDER BY time,id LIMIT ?)", (excess,)
            )
            prior = self.checkpoint("retention", {})
            prior.update(
                evicted_events=prior.get("evicted_events", 0) + excess,
                last_eviction_at=time.time(),
            )
            self.set_checkpoint("retention", prior)
            self.db.commit()
        self.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    def _delete(self, where, args):
        partitions = self.db.execute(
            "SELECT DISTINCT stream,session,CAST(time/86400 AS INTEGER) FROM events WHERE "
            + where,
            args,
        ).fetchall()
        self.db.execute("DELETE FROM events WHERE " + where, args)
        self._rebuild(partitions)

    def prune(self, now=None):
        self._guard()
        now = time.time() if now is None else now
        self._delete("time<?", (now - self.retention_days * 86400,))
        self.db.execute(
            "DELETE FROM incident_aliases WHERE incident_id NOT IN (SELECT id FROM incidents)"
        )
        self.db.commit()
        self._bound_storage()
        self.db.execute("PRAGMA incremental_vacuum(128)")
        self.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
