"""Private SQLite storage for saved searches and their result identities."""

from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from .parsers import SearchResult


SCHEMA = """
CREATE TABLE IF NOT EXISTS search_watches (
    id INTEGER PRIMARY KEY,
    parser TEXT NOT NULL,
    url TEXT NOT NULL,
    normalized_url TEXT NOT NULL UNIQUE,
    title TEXT,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    last_checked_at INTEGER,
    last_status TEXT NOT NULL DEFAULT 'never'
        CHECK (last_status IN ('never', 'ok', 'error')),
    last_error TEXT
);
CREATE TABLE IF NOT EXISTS search_results (
    search_id INTEGER NOT NULL
        REFERENCES search_watches(id) ON DELETE CASCADE,
    item_id TEXT NOT NULL,
    title TEXT NOT NULL,
    url TEXT NOT NULL,
    price TEXT,
    shipping TEXT,
    image_url TEXT,
    first_seen_at INTEGER NOT NULL,
    last_seen_at INTEGER NOT NULL,
    listing_order INTEGER NOT NULL,
    is_current INTEGER NOT NULL DEFAULT 1 CHECK (is_current IN (0, 1)),
    dismissed_at INTEGER,
    PRIMARY KEY (search_id, item_id)
);
CREATE TABLE IF NOT EXISTS search_checks (
    id INTEGER PRIMARY KEY,
    search_id INTEGER NOT NULL
        REFERENCES search_watches(id) ON DELETE CASCADE,
    checked_at INTEGER NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('ok', 'error')),
    result_count INTEGER,
    new_count INTEGER,
    error TEXT
);
CREATE INDEX IF NOT EXISTS search_results_current
    ON search_results(search_id, is_current, listing_order);
CREATE INDEX IF NOT EXISTS search_checks_watch_time
    ON search_checks(search_id, checked_at DESC);
"""


class SearchStoreError(RuntimeError):
    pass


def normalized_url(url: str) -> str:
    parts = urlsplit(url.strip())
    return urlunsplit(
        (parts.scheme.lower(), parts.netloc.lower(), parts.path, parts.query, "")
    )


class SearchStore:
    def __init__(self, path: Path, *, clock=time.time):
        self.path = Path(path)
        self.clock = clock
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.path.parent, 0o700)
        self.connection = sqlite3.connect(self.path, timeout=15)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.execute("PRAGMA busy_timeout=15000")
        self.connection.execute("PRAGMA journal_mode=WAL")
        try:
            self.connection.executescript(SCHEMA)
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        os.chmod(self.path, 0o600)

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "SearchStore":
        return self

    def __exit__(self, *_args) -> None:
        self.close()

    @staticmethod
    def _public_result(row: sqlite3.Row) -> dict:
        return {
            "item_id": row["item_id"],
            "title": row["title"],
            "url": row["url"],
            "price": row["price"],
            "shipping": row["shipping"],
            "image_url": row["image_url"],
            "first_seen_at": row["first_seen_at"],
            "last_seen_at": row["last_seen_at"],
            "dismissed": row["dismissed_at"] is not None,
            "dismissed_at": row["dismissed_at"],
        }

    def _public_watch(self, row: sqlite3.Row) -> dict:
        counts = self.connection.execute(
            """
            SELECT
                SUM(CASE WHEN is_current=1 AND dismissed_at IS NULL THEN 1 ELSE 0 END)
                    AS visible_count,
                SUM(CASE WHEN is_current=1 AND dismissed_at IS NOT NULL THEN 1 ELSE 0 END)
                    AS hidden_current_count,
                SUM(CASE WHEN dismissed_at IS NOT NULL THEN 1 ELSE 0 END)
                    AS dismissed_count,
                COUNT(*) AS known_count
            FROM search_results
            WHERE search_id=?
            """,
            (row["id"],),
        ).fetchone()
        results = self.connection.execute(
            """
            SELECT * FROM search_results
            WHERE search_id=? AND is_current=1 AND dismissed_at IS NULL
            ORDER BY listing_order, item_id
            """,
            (row["id"],),
        ).fetchall()
        return {
            "id": row["id"],
            "parser": row["parser"],
            "url": row["url"],
            "title": row["title"],
            "display_title": row["title"] or row["url"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "last_checked_at": row["last_checked_at"],
            "last_status": row["last_status"],
            "last_error": row["last_error"],
            "result_count": counts["visible_count"] or 0,
            "hidden_current_count": counts["hidden_current_count"] or 0,
            "dismissed_count": counts["dismissed_count"] or 0,
            "known_count": counts["known_count"] or 0,
            "results": [self._public_result(result) for result in results],
        }

    def list_watches(self) -> list[dict]:
        rows = self.connection.execute(
            "SELECT * FROM search_watches ORDER BY created_at, id"
        ).fetchall()
        return [self._public_watch(row) for row in rows]

    def get_watch(self, watch_id: int) -> dict:
        row = self.connection.execute(
            "SELECT * FROM search_watches WHERE id=?", (watch_id,)
        ).fetchone()
        if row is None:
            raise SearchStoreError(f"search watch {watch_id} was not found")
        return self._public_watch(row)

    def add_watch(
        self, parser: str, url: str, title: str | None = None
    ) -> dict:
        now = int(self.clock())
        try:
            cursor = self.connection.execute(
                """
                INSERT INTO search_watches
                    (parser, url, normalized_url, title, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (parser, url, normalized_url(url), title or None, now, now),
            )
            self.connection.commit()
        except sqlite3.IntegrityError as error:
            raise SearchStoreError(f"search URL is already configured: {url}") from error
        return self.get_watch(cursor.lastrowid)

    def remove_watch(self, watch_id: int) -> dict:
        watch = self.get_watch(watch_id)
        self.connection.execute("DELETE FROM search_watches WHERE id=?", (watch_id,))
        self.connection.commit()
        return watch

    def dismiss_result(self, watch_id: int, item_id: str) -> tuple[dict, dict]:
        now = int(self.clock())
        row = self.connection.execute(
            "SELECT * FROM search_results WHERE search_id=? AND item_id=?",
            (watch_id, item_id),
        ).fetchone()
        if row is None:
            raise SearchStoreError(
                f"eBay result {item_id} was not found in search watch {watch_id}"
            )
        self.connection.execute(
            """
            UPDATE search_results
            SET dismissed_at=COALESCE(dismissed_at, ?)
            WHERE search_id=? AND item_id=?
            """,
            (now, watch_id, item_id),
        )
        self.connection.commit()
        row = self.connection.execute(
            "SELECT * FROM search_results WHERE search_id=? AND item_id=?",
            (watch_id, item_id),
        ).fetchone()
        dismissed = self._public_result(row)
        return self.get_watch(watch_id), dismissed

    def record_success(
        self, watch_id: int, results: list[SearchResult]
    ) -> tuple[dict, list[dict]]:
        now = int(self.clock())
        known_ids = {
            row["item_id"]
            for row in self.connection.execute(
                "SELECT item_id FROM search_results WHERE search_id=?", (watch_id,)
            ).fetchall()
        }
        with self.connection:
            cursor = self.connection.execute(
                """
                UPDATE search_watches
                SET last_checked_at=?, last_status='ok', last_error=NULL, updated_at=?
                WHERE id=?
                """,
                (now, now, watch_id),
            )
            if cursor.rowcount != 1:
                raise SearchStoreError(
                    f"search watch {watch_id} was removed during its check"
                )
            self.connection.execute(
                "UPDATE search_results SET is_current=0 WHERE search_id=?",
                (watch_id,),
            )
            for position, result in enumerate(results):
                self.connection.execute(
                    """
                    INSERT INTO search_results
                        (search_id, item_id, title, url, price, shipping, image_url,
                         first_seen_at, last_seen_at, listing_order, is_current)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                    ON CONFLICT(search_id, item_id) DO UPDATE SET
                        title=excluded.title,
                        url=excluded.url,
                        price=excluded.price,
                        shipping=excluded.shipping,
                        image_url=excluded.image_url,
                        last_seen_at=excluded.last_seen_at,
                        listing_order=excluded.listing_order,
                        is_current=1
                    """,
                    (
                        watch_id,
                        result.item_id,
                        result.title,
                        result.url,
                        result.price,
                        result.shipping,
                        result.image_url,
                        now,
                        now,
                        position,
                    ),
                )
            new_ids = [result.item_id for result in results if result.item_id not in known_ids]
            self.connection.execute(
                """
                INSERT INTO search_checks
                    (search_id, checked_at, status, result_count, new_count)
                VALUES (?, ?, 'ok', ?, ?)
                """,
                (watch_id, now, len(results), len(new_ids)),
            )
            self._prune_history(watch_id)
        new_rows = []
        for item_id in new_ids:
            row = self.connection.execute(
                "SELECT * FROM search_results WHERE search_id=? AND item_id=?",
                (watch_id, item_id),
            ).fetchone()
            new_rows.append(self._public_result(row))
        return self.get_watch(watch_id), new_rows

    def record_error(self, watch_id: int, error: str) -> dict:
        now = int(self.clock())
        detail = error[-1000:]
        with self.connection:
            cursor = self.connection.execute(
                """
                UPDATE search_watches
                SET last_checked_at=?, last_status='error', last_error=?, updated_at=?
                WHERE id=?
                """,
                (now, detail, now, watch_id),
            )
            if cursor.rowcount != 1:
                raise SearchStoreError(
                    f"search watch {watch_id} was removed during its check"
                )
            self.connection.execute(
                """
                INSERT INTO search_checks(search_id, checked_at, status, error)
                VALUES (?, ?, 'error', ?)
                """,
                (watch_id, now, detail),
            )
            self._prune_history(watch_id)
        return self.get_watch(watch_id)

    def _prune_history(self, watch_id: int) -> None:
        self.connection.execute(
            """
            DELETE FROM search_checks
            WHERE search_id=? AND id NOT IN (
                SELECT id FROM search_checks
                WHERE search_id=?
                ORDER BY checked_at DESC, id DESC
                LIMIT 100
            )
            """,
            (watch_id, watch_id),
        )

    def summary(self) -> dict:
        watches = self.list_watches()
        return {
            "count": len(watches),
            "checked": sum(watch["last_checked_at"] is not None for watch in watches),
            "results": sum(watch["result_count"] for watch in watches),
            "errors": sum(watch["last_status"] == "error" for watch in watches),
        }
