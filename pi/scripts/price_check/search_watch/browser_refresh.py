"""Durable requests for the Mac's signed-out browser renewal hook."""

from __future__ import annotations

import uuid
from typing import TypedDict


RETRY_SECONDS = 360
MAX_FAILURES = 50
RETRY_COLUMNS = {
    "failures": "INTEGER NOT NULL DEFAULT 0",
    "in_flight": "INTEGER NOT NULL DEFAULT 0",
    "alerted_at": "INTEGER",
    "alert_next_attempt_at": "INTEGER NOT NULL DEFAULT 0",
}
SCHEMA = """
CREATE TABLE IF NOT EXISTS search_browser_refresh (
    search_id INTEGER PRIMARY KEY REFERENCES search_watches(id) ON DELETE CASCADE,
    request_id TEXT NOT NULL,
    requested_at INTEGER NOT NULL,
    next_attempt_at INTEGER NOT NULL DEFAULT 0,
    failures INTEGER NOT NULL DEFAULT 0,
    in_flight INTEGER NOT NULL DEFAULT 0,
    alerted_at INTEGER,
    alert_next_attempt_at INTEGER NOT NULL DEFAULT 0
);
"""


class RefreshRequest(TypedDict):
    search_id: int
    request_id: str
    url: str
    attempt: int


class RetryStatus(TypedDict):
    retry_after: int | None


def migrate_retry_state(connection, now: int) -> None:
    columns = {row[1] for row in connection.execute("PRAGMA table_info(search_browser_refresh)")}
    if set(RETRY_COLUMNS) <= columns:
        return
    with connection:
        connection.execute("BEGIN IMMEDIATE")
        columns = {row[1] for row in connection.execute("PRAGMA table_info(search_browser_refresh)")}
        for name, definition in RETRY_COLUMNS.items():
            if name not in columns:
                connection.execute(f"ALTER TABLE search_browser_refresh ADD COLUMN {name} {definition}")
        connection.execute(
            "UPDATE search_browser_refresh SET next_attempt_at=MIN(next_attempt_at, ?)",
            (now + RETRY_SECONDS,),
        )


def validate_request(payload: object) -> RefreshRequest:
    if not isinstance(payload, dict) or set(payload) != set(RefreshRequest.__annotations__):
        raise ValueError("invalid browser refresh request")
    if type(payload["search_id"]) is not int or payload["search_id"] < 1:
        raise ValueError("invalid search ID")
    if type(payload["attempt"]) is not int or not 1 <= payload["attempt"] <= MAX_FAILURES:
        raise ValueError("invalid recovery attempt")
    if (not isinstance(payload["request_id"], str) or len(payload["request_id"]) != 32
            or not isinstance(payload["url"], str) or not 0 < len(payload["url"]) <= 4096):
        raise ValueError("invalid browser refresh fields")
    return payload


def request_refresh(store, watch: dict, *, restart=False) -> None:
    with store.connection:
        if restart:
            store.connection.execute(
                "DELETE FROM search_browser_refresh WHERE search_id=?", (watch["id"],)
            )
        store.connection.execute(
            """INSERT INTO search_browser_refresh(search_id, request_id, requested_at)
               VALUES (?, ?, ?) ON CONFLICT(search_id) DO NOTHING""",
            (watch["id"], uuid.uuid4().hex, int(store.clock())),
        )


def clear_refresh(store, watch_id: int) -> None:
    with store.connection:
        store.connection.execute(
            "DELETE FROM search_browser_refresh WHERE search_id=?", (watch_id,)
        )


def claim_refresh(store) -> RefreshRequest | None:
    now = int(store.clock())
    with store.connection:
        store.connection.execute("BEGIN IMMEDIATE")
        # An abandoned lease is one failure, even if several poll intervals passed.
        store.connection.execute(
            """UPDATE search_browser_refresh SET failures=failures+1, in_flight=0
               WHERE in_flight=1 AND next_attempt_at<=?""", (now,)
        )
        row = store.connection.execute(
            """SELECT r.search_id, r.request_id, w.url, r.failures+1 AS attempt
               FROM search_browser_refresh r
               JOIN search_watches w ON w.id=r.search_id
               WHERE r.failures<? AND r.in_flight=0 AND NOT EXISTS (
                   SELECT 1 FROM search_browser_refresh WHERE next_attempt_at>?
               ) ORDER BY r.next_attempt_at, r.requested_at, r.search_id LIMIT 1""",
            (MAX_FAILURES, now),
        ).fetchone()
        if row is None:
            return None
        store.connection.execute(
            "UPDATE search_browser_refresh SET next_attempt_at=?, in_flight=1 WHERE search_id=?",
            (now + RETRY_SECONDS, row["search_id"]),
        )
        return dict(row)


def is_current_request(store, request: RefreshRequest) -> bool:
    row = store.connection.execute(
        """SELECT 1 FROM search_browser_refresh r
           JOIN search_watches w ON w.id=r.search_id
           WHERE r.search_id=? AND r.request_id=? AND w.url=?
             AND r.in_flight=1 AND r.failures+1=? AND r.next_attempt_at>?""",
        (request["search_id"], request["request_id"], request["url"],
         request["attempt"], int(store.clock())),
    ).fetchone()
    return row is not None


def record_failure(store, payload: object) -> None:
    request = validate_request(payload)
    with store.connection:
        store.connection.execute(
            """UPDATE search_browser_refresh SET failures=failures+1, in_flight=0
               WHERE search_id=? AND request_id=? AND in_flight=1 AND failures+1=?
                 AND EXISTS (SELECT 1 FROM search_watches WHERE id=? AND url=?)""",
            (request["search_id"], request["request_id"], request["attempt"],
             request["search_id"], request["url"]),
        )


def notify_exhausted(store, notifier) -> None:
    now = int(store.clock())
    rows = store.connection.execute(
        """SELECT search_id, request_id, failures FROM search_browser_refresh
           WHERE failures>=? AND alerted_at IS NULL AND alert_next_attempt_at<=?""",
        (MAX_FAILURES, now),
    ).fetchall()
    for row in rows:
        with store.connection:
            cursor = store.connection.execute(
                """UPDATE search_browser_refresh SET alert_next_attempt_at=?
                   WHERE search_id=? AND request_id=? AND alerted_at IS NULL
                     AND alert_next_attempt_at<=?""",
                (now + RETRY_SECONDS, row["search_id"], row["request_id"], now),
            )
        if cursor.rowcount != 1:
            continue
        try:
            notifier(store.get_watch(row["search_id"]), row["failures"])
        except Exception:
            # Failed delivery stays pending without restarting browser attempts.
            continue
        with store.connection:
            store.connection.execute(
                """UPDATE search_browser_refresh SET alerted_at=?
                   WHERE search_id=? AND request_id=?""",
                (now, row["search_id"], row["request_id"]),
            )


def retry_status(store) -> RetryStatus:
    now = int(store.clock())
    rows = store.connection.execute("SELECT * FROM search_browser_refresh").fetchall()
    global_due = max((row["next_attempt_at"] for row in rows), default=now)
    due = [global_due for row in rows if row["failures"] < MAX_FAILURES]
    due.extend(row["alert_next_attempt_at"] for row in rows
               if row["failures"] >= MAX_FAILURES and row["alerted_at"] is None)
    return {"retry_after": max(0, min(due) - now) if due else None}
