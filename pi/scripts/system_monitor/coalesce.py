"""Bound journal event rows and snapshots while retaining replay watermarks."""

import json

from .common import (
    EVENT_MAINTENANCE_BATCH_SIZE, event_fingerprint, json_dumps, normalize_boot_id,
)

WINDOW_SECONDS = 60
INDIVIDUAL_LIMIT = 20
# The network recorder imports other categories append-only by event id.
COALESCED_CATEGORIES = frozenset(("usb", "storage"))
MONOTONIC_CLOCK = "monotonic"
WALL_CLOCK = "wall"
EVENT_COLUMNS = {
    "repeat_count": "INTEGER NOT NULL DEFAULT 1",
    "last_seen": "REAL",
    "journal_monotonic": "INTEGER",
    "coalesce_clock": "TEXT",
    "coalesce_bucket": "INTEGER",
    "is_aggregate": "INTEGER NOT NULL DEFAULT 0",
    "last_monotonic": "INTEGER",
    "last_fingerprints": "TEXT",
    "state_event_id": "INTEGER",
}


def migrate_events(connection):
    """Only collector initialization and explicit maintenance call this."""
    columns = {row[1] for row in connection.execute("PRAGMA table_info(events)")}
    for name, declaration in EVENT_COLUMNS.items():
        if name not in columns:
            connection.execute(f"ALTER TABLE events ADD COLUMN {name} {declaration}")
    connection.execute(
        "CREATE INDEX IF NOT EXISTS events_coalesce_idx ON events("
        "boot_id, kind, category, severity, coalesce_clock, coalesce_bucket, id) "
        "WHERE coalesce_bucket IS NOT NULL"
    )
    # Keep the v4 index during the upgrade; reports use this covering v5 index.
    connection.execute(
        "CREATE INDEX IF NOT EXISTS events_report_v5_idx ON events("
        "timestamp, kind, severity, category, repeat_count, last_seen)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS events_state_expiry_idx ON events(timestamp) "
        "WHERE state_json IS NOT NULL"
    )
    connection.execute(
        "CREATE TABLE IF NOT EXISTS legacy_event_fingerprints ("
        "fingerprint TEXT PRIMARY KEY, boot_id TEXT NOT NULL) WITHOUT ROWID"
    )
    connection.execute("PRAGMA user_version=5")


def window_key(event, position, clock):
    return (
        normalize_boot_id(event["boot_id"]) or "unknown",
        event["kind"], event["category"], event["severity"], clock,
        int(position) // (WINDOW_SECONDS * 1_000_000),
    )


def window_rows(connection, key):
    return connection.execute(
        "SELECT id, is_aggregate, last_monotonic, last_fingerprints "
        "FROM events WHERE boot_id=? AND kind=? AND category=? AND severity=? "
        "AND coalesce_clock=? AND coalesce_bucket=? ORDER BY id", key,
    ).fetchall()


def insert_row(connection, event, state, position=None, clock=None, bucket=None,
               state_event_id=None, aggregate=False):
    values = [event[name] for name in (
        "timestamp", "boot_id", "category", "kind", "severity", "source",
        "summary", "message", "fingerprint",
    )]
    cursor = connection.execute(
        "INSERT OR IGNORE INTO events(timestamp, boot_id, category, kind, severity, "
        "source, summary, message, fingerprint, state_json, journal_monotonic, "
        "coalesce_clock, coalesce_bucket, state_event_id, is_aggregate, last_seen, "
        "last_monotonic, last_fingerprints) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (*values, json_dumps(state) if state is not None else None, position, clock,
         bucket, state_event_id, int(aggregate), event["timestamp"], position,
         json_dumps([event["fingerprint"]]) if aggregate else None),
    )
    return cursor.rowcount == 1


def insert_journal_event(connection, event, state, position, clock):
    """Caller owns the transaction; journalctl supplies monotonic-order records.

    A watermark discards the already-folded prefix on boot replay. Fingerprints
    at the watermark distinguish different messages sharing a journal timestamp.
    """
    if event["category"] not in COALESCED_CATEGORIES:
        return insert_row(connection, event, state, position, clock)
    fingerprint = event["fingerprint"]
    if connection.execute(
        "SELECT 1 FROM events WHERE fingerprint=? UNION ALL "
        "SELECT 1 FROM legacy_event_fingerprints WHERE fingerprint=? LIMIT 1",
        (fingerprint, fingerprint),
    ).fetchone():
        return False
    key = window_key(event, position, clock)
    event = {**event, "boot_id": key[0]}
    rows = window_rows(connection, key)
    aggregate = next((row for row in rows if row["is_aggregate"]), None)
    if aggregate is not None:
        last = aggregate["last_monotonic"]
        fingerprints = json.loads(aggregate["last_fingerprints"] or "[]")
        if position < last or (position == last and fingerprint in fingerprints):
            return False
        fingerprints = fingerprints + [fingerprint] if position == last else [fingerprint]
        connection.execute(
            "UPDATE events SET repeat_count=repeat_count+1, "
            "last_seen=MAX(last_seen, ?), last_monotonic=?, last_fingerprints=? "
            "WHERE id=?",
            (event["timestamp"], position, json_dumps(fingerprints), aggregate["id"]),
        )
        return True
    first_id = rows[0]["id"] if rows else None
    aggregate = len(rows) >= INDIVIDUAL_LIMIT
    if aggregate:
        event = {**event, "fingerprint": event_fingerprint("journal-window", *key)}
    inserted = insert_row(
        connection, event, state if not rows else None, position, clock, key[-1],
        first_id, aggregate,
    )
    if inserted and aggregate:
        # The aggregate's stable identity is not a journal-record fingerprint.
        connection.execute(
            "UPDATE events SET last_fingerprints=? WHERE fingerprint=?",
            (json_dumps([fingerprint]), event["fingerprint"]),
        )
    return inserted


def expired_state_batches(connection, cutoff):
    """Yield bounded updates so callers can commit/checkpoint before continuing."""
    while True:
        ids = [row[0] for row in connection.execute(
            "SELECT id FROM events WHERE timestamp < ? "
            "AND state_json IS NOT NULL ORDER BY timestamp LIMIT ?",
            (cutoff, EVENT_MAINTENANCE_BATCH_SIZE),
        ).fetchall()]
        if not ids:
            return
        placeholders = ",".join("?" for _ in ids)
        changed = connection.execute(
            f"UPDATE events SET state_json=NULL WHERE id IN ({placeholders})", ids,
        ).rowcount
        yield changed


def prune_legacy_fingerprints(connection, current_boot_id):
    boot_id = normalize_boot_id(current_boot_id)
    if boot_id:
        connection.execute(
            "DELETE FROM legacy_event_fingerprints WHERE boot_id != ?", (boot_id,)
        )
