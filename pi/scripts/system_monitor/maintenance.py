"""Offline, bounded-memory event compaction; never used by read-only reports."""

import json
import os
from pathlib import Path
import shutil
import sqlite3
from contextlib import contextmanager

from .coalesce import (
    COALESCED_CATEGORIES, INDIVIDUAL_LIMIT, WALL_CLOCK, WINDOW_SECONDS, expired_state_batches,
    migrate_events, prune_legacy_fingerprints,
)
from .common import (
    EVENT_MAINTENANCE_BATCH_SIZE, EVENT_STATE_RETENTION_DAYS, event_fingerprint,
    json_dumps, normalize_boot_id, utc_timestamp,
)
from .locking import writer_lock
SPACE_RESERVE_BYTES = 64 * 1024 * 1024


@contextmanager
def maintenance_connection(path):
    path = Path(path).resolve(strict=True)
    with writer_lock(path, exclusive=True):
        connection = sqlite3.connect(path.as_uri() + "?mode=rw", uri=True, timeout=0)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA locking_mode=EXCLUSIVE")
            connection.execute("PRAGMA cache_size=-8192")
            connection.execute("PRAGMA temp_store=FILE")
            # In exclusive locking mode SQLite retains the lock between batches
            # and through VACUUM, which cannot run inside a transaction.
            connection.execute("BEGIN EXCLUSIVE")
            yield connection, path
        finally:
            connection.close()


def _current_boot(connection):
    row = connection.execute("SELECT value FROM meta WHERE key='last_boot_id'").fetchone()
    return normalize_boot_id(json.loads(row[0])) if row else None


def _prepare_plan(connection):
    columns = {row[1] for row in connection.execute("PRAGMA table_info(events)")}
    optional = {
        "repeat_count": "1", "last_seen": "timestamp", "journal_monotonic": "NULL",
        "coalesce_clock": "NULL", "coalesce_bucket": "NULL", "is_aggregate": "0",
        "last_monotonic": "NULL", "last_fingerprints": "NULL",
    }
    projection = ", ".join(
        name if name in columns else f"{fallback} AS {name}"
        for name, fallback in optional.items()
    )
    # Materialize only small metadata on disk. A stable plan allows committing
    # batches without rescanning a table while deleting the rows being scanned.
    categories = tuple(sorted(COALESCED_CATEGORIES))
    placeholders = ",".join("?" for _ in categories)
    connection.execute(
        "CREATE TEMP TABLE compact_plan AS SELECT id, timestamp, boot_id, kind, "
        "category, severity, fingerprint, length(CAST(state_json AS BLOB)) AS state_bytes, "
        "length(CAST(message AS BLOB)) + length(CAST(summary AS BLOB)) + 256 AS row_bytes, "
        f"{projection} FROM events WHERE source='kernel' AND category IN ({placeholders})",
        categories,
    )
    connection.execute(
        "UPDATE compact_plan SET coalesce_clock=COALESCE(coalesce_clock, ?), "
        "coalesce_bucket=COALESCE(coalesce_bucket, CAST(timestamp / ? AS INTEGER)), "
        "last_seen=COALESCE(last_seen, timestamp), "
        "last_monotonic=COALESCE(last_monotonic, journal_monotonic, "
        "CAST(timestamp * 1000000 AS INTEGER))",
        (WALL_CLOCK, WINDOW_SECONDS),
    )
    connection.execute("CREATE UNIQUE INDEX compact_plan_id ON compact_plan(id)")
    connection.execute(
        "CREATE TEMP TABLE compact_order AS SELECT id FROM compact_plan ORDER BY "
        "boot_id, kind, category, severity, coalesce_clock, coalesce_bucket, id"
    )


def _plan_rows(connection):
    position = 0
    while True:
        batch = connection.execute(
            "SELECT plan.*, ordered.rowid AS plan_position FROM compact_order ordered "
            "JOIN compact_plan plan ON plan.id=ordered.id WHERE ordered.rowid > ? "
            "ORDER BY ordered.rowid LIMIT ?", (position, EVENT_MAINTENANCE_BATCH_SIZE),
        ).fetchall()
        if not batch:
            break
        position = batch[-1]["plan_position"]
        yield batch


def _key(row):
    return (
        normalize_boot_id(row["boot_id"]) or "unknown", row["kind"], row["category"],
        row["severity"], row["coalesce_clock"], row["coalesce_bucket"],
    )


def _estimate(connection, cutoff, progress):
    old = connection.execute(
        "SELECT COUNT(*), COALESCE(SUM(length(CAST(state_json AS BLOB))), 0) "
        "FROM events WHERE timestamp < ? AND state_json IS NOT NULL", (cutoff,),
    ).fetchone()
    result = {"rows_removed": 0, "snapshots_cleared": old[0], "estimated_bytes_reclaimed": old[1]}
    previous, count, scanned = None, 0, 0
    for batch in _plan_rows(connection):
        for row in batch:
            key = _key(row)
            count = count + 1 if key == previous else 1
            previous = key
            if count > 1 and row["state_bytes"] and row["timestamp"] >= cutoff:
                result["snapshots_cleared"] += 1
                result["estimated_bytes_reclaimed"] += row["state_bytes"]
            if count > INDIVIDUAL_LIMIT + 1:
                result["rows_removed"] += 1
                result["estimated_bytes_reclaimed"] += row["row_bytes"]
        scanned += len(batch)
        if scanned % 10000 == 0:
            progress(f"Scanned {scanned:,} journal rows")
    result["journal_rows_scanned"] = scanned
    page_size = connection.execute("PRAGMA page_size").fetchone()[0]
    free_pages = connection.execute("PRAGMA freelist_count").fetchone()[0]
    result["estimated_bytes_reclaimed"] += free_pages * page_size
    return result


def _require_space(path, estimated_size):
    # WAL-mode VACUUM may need both the temporary rebuilt DB and its WAL.
    required = 2 * max(estimated_size, 0) + SPACE_RESERVE_BYTES
    free = shutil.disk_usage(path.parent).free
    if free < required:
        raise OSError(f"insufficient free space: {free:,} bytes available; {required:,} required")
    return required


def _remember_legacy(connection, row, current_boot):
    if row["coalesce_clock"] == WALL_CLOCK and not row["is_aggregate"] and (
        normalize_boot_id(row["boot_id"]) == current_boot
    ):
        connection.execute(
            "INSERT OR IGNORE INTO legacy_event_fingerprints VALUES (?, ?)",
            (row["fingerprint"], current_boot),
        )


def _fold_row(connection, row, aggregate_id):
    aggregate = connection.execute(
        "SELECT last_monotonic, last_fingerprints FROM events WHERE id=?", (aggregate_id,),
    ).fetchone()
    last = max(row["last_monotonic"], aggregate["last_monotonic"])
    fingerprints = []
    if aggregate["last_monotonic"] == last:
        fingerprints.extend(json.loads(aggregate["last_fingerprints"] or "[]"))
    if row["last_monotonic"] == last:
        fingerprints.extend(json.loads(row["last_fingerprints"]) if row["is_aggregate"]
                            else [row["fingerprint"]])
    connection.execute(
        "UPDATE events SET repeat_count=repeat_count+?, last_seen=MAX(last_seen, ?), "
        "last_monotonic=?, last_fingerprints=? WHERE id=?",
        (row["repeat_count"], row["last_seen"], last, json_dumps(sorted(set(fingerprints))),
         aggregate_id),
    )
    connection.execute("DELETE FROM events WHERE id=?", (row["id"],))


def _retain_row(connection, row, key, first_id, aggregate):
    fingerprint = event_fingerprint("journal-window", *key) if aggregate else row["fingerprint"]
    fingerprints = row["last_fingerprints"] or json_dumps([row["fingerprint"]])
    connection.execute(
        "UPDATE events SET boot_id=?, coalesce_clock=?, coalesce_bucket=?, "
        "state_json=CASE WHEN id=? THEN state_json ELSE NULL END, "
        "state_event_id=?, is_aggregate=?, fingerprint=?, last_seen=?, "
        "last_monotonic=?, last_fingerprints=? WHERE id=?",
        (key[0], key[-2], key[-1], first_id,
         None if row["id"] == first_id else first_id, int(aggregate), fingerprint,
         row["last_seen"], row["last_monotonic"], fingerprints if aggregate else None, row["id"]),
    )


def _checkpoint(connection):
    connection.commit()
    busy, _pages, _done = connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
    if busy:
        raise OSError("could not checkpoint exclusively; leave service stopped and retry")


def _apply_plan(connection, cutoff, current_boot, progress):
    previous, count, first_id, aggregate_id, processed = None, 0, None, None, 0
    for batch in _plan_rows(connection):
        for row in batch:
            key = _key(row)
            if key != previous:
                count, first_id, aggregate_id = 0, row["id"], None
            count += 1
            previous = key
            if count >= INDIVIDUAL_LIMIT + 1:
                _remember_legacy(connection, row, current_boot)
            if count <= INDIVIDUAL_LIMIT + 1:
                is_aggregate = count == INDIVIDUAL_LIMIT + 1
                _retain_row(connection, row, key, first_id, is_aggregate)
                if is_aggregate:
                    aggregate_id = row["id"]
            else:
                _fold_row(connection, row, aggregate_id)
        processed += len(batch)
        _checkpoint(connection)
        progress(f"Compacted {processed:,} journal rows")
    for changed in expired_state_batches(connection, cutoff):
        _checkpoint(connection)
        progress(f"Cleared {changed:,} expired state snapshots")
    prune_legacy_fingerprints(connection, current_boot)
    _checkpoint(connection)


def compact_database(path, apply=False, now=None, progress=print):
    """Dry-run by default; partial apply is resumable after disk or power failure."""
    cutoff = (utc_timestamp() if now is None else now) - EVENT_STATE_RETENTION_DAYS * 86400
    with maintenance_connection(path) as (connection, resolved):
        progress("Exclusive database lock acquired; preparing disk-backed plan")
        current_boot = _current_boot(connection)
        if not current_boot:
            raise ValueError("last_boot_id is missing; cannot bound legacy replay protection")
        _prepare_plan(connection)
        result = _estimate(connection, cutoff, progress)
        result["applied"] = False
        result["bytes_before"] = os.path.getsize(resolved)
        progress(
            f"{'Apply' if apply else 'Dry run'}: remove {result['rows_removed']:,} rows; "
            f"clear {result['snapshots_cleared']:,} snapshots; approximately "
            f"{result['estimated_bytes_reclaimed']:,} bytes reclaimable"
        )
        if not apply:
            return result
        _require_space(resolved, result["bytes_before"] - result["estimated_bytes_reclaimed"])
        migrate_events(connection)
        _checkpoint(connection)
        _apply_plan(connection, cutoff, current_boot, progress)
        page_size = connection.execute("PRAGMA page_size").fetchone()[0]
        pages = connection.execute("PRAGMA page_count").fetchone()[0]
        free_pages = connection.execute("PRAGMA freelist_count").fetchone()[0]
        required = _require_space(resolved, (pages - free_pages) * page_size)
        progress(f"VACUUM starting (reserved free-space requirement: {required:,} bytes)")
        connection.execute("PRAGMA auto_vacuum=INCREMENTAL")
        connection.execute("VACUUM")
        _checkpoint(connection)
        result.update(applied=True, bytes_after=os.path.getsize(resolved))
        progress(f"Complete: {result['bytes_before']:,} -> {result['bytes_after']:,} bytes")
        return result
