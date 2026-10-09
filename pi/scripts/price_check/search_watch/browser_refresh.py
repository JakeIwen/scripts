"""Durable requests for the Mac's signed-out browser renewal hook."""

from __future__ import annotations

import uuid
from typing import TypedDict


RETRY_SECONDS = 1800
SCHEMA = """
CREATE TABLE IF NOT EXISTS search_browser_refresh (
    search_id INTEGER PRIMARY KEY REFERENCES search_watches(id) ON DELETE CASCADE,
    request_id TEXT NOT NULL,
    requested_at INTEGER NOT NULL,
    next_attempt_at INTEGER NOT NULL DEFAULT 0
);
"""


class RefreshRequest(TypedDict):
    search_id: int
    request_id: str
    url: str


def validate_request(payload: object) -> RefreshRequest:
    if not isinstance(payload, dict) or set(payload) != set(RefreshRequest.__annotations__):
        raise ValueError("invalid browser refresh request")
    if type(payload["search_id"]) is not int or payload["search_id"] < 1:
        raise ValueError("invalid search ID")
    if (not isinstance(payload["request_id"], str) or len(payload["request_id"]) != 32
            or not isinstance(payload["url"], str) or not 0 < len(payload["url"]) <= 4096):
        raise ValueError("invalid browser refresh fields")
    return payload


def request_refresh(store, watch: dict) -> None:
    with store.connection:
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
        # One browser attempt per half hour, including failed/interrupted workers.
        row = store.connection.execute(
            """SELECT r.search_id, r.request_id, w.url
               FROM search_browser_refresh r
               JOIN search_watches w ON w.id=r.search_id
               WHERE NOT EXISTS (
                   SELECT 1 FROM search_browser_refresh WHERE next_attempt_at>?
               ) ORDER BY r.requested_at LIMIT 1""",
            (now,),
        ).fetchone()
        if row is None:
            return None
        store.connection.execute(
            "UPDATE search_browser_refresh SET next_attempt_at=? WHERE search_id=?",
            (now + RETRY_SECONDS, row["search_id"]),
        )
        return dict(row)


def is_current_request(store, request: RefreshRequest) -> bool:
    row = store.connection.execute(
        """SELECT 1 FROM search_browser_refresh r
           JOIN search_watches w ON w.id=r.search_id
           WHERE r.search_id=? AND r.request_id=? AND w.url=?""",
        (request["search_id"], request["request_id"], request["url"]),
    ).fetchone()
    return row is not None
