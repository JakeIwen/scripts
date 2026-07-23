"""Private SQLite storage for configured products and price-check results."""

from __future__ import annotations

import os
import sqlite3
import time
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY,
    parser TEXT NOT NULL,
    threshold_cents INTEGER NOT NULL CHECK (threshold_cents > 0),
    url TEXT NOT NULL,
    normalized_url TEXT NOT NULL UNIQUE,
    title TEXT,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    last_checked_at INTEGER,
    last_price_cents INTEGER,
    last_price_checked_at INTEGER,
    last_status TEXT NOT NULL DEFAULT 'never'
        CHECK (last_status IN ('never', 'ok', 'error')),
    last_error TEXT,
    last_title TEXT,
    notify_muted_until INTEGER
);
CREATE TABLE IF NOT EXISTS checks (
    id INTEGER PRIMARY KEY,
    item_id INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    checked_at INTEGER NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('ok', 'error')),
    price_cents INTEGER,
    title TEXT,
    error TEXT
);
CREATE INDEX IF NOT EXISTS checks_item_time ON checks(item_id, checked_at DESC);
"""


class StoreError(RuntimeError):
    pass


def normalized_url(url: str) -> str:
    parts = urlsplit(url.strip())
    return urlunsplit(
        (parts.scheme.lower(), parts.netloc.lower(), parts.path, parts.query, "")
    )


def cents(value: Decimal) -> int:
    exact = value * 100
    if exact != exact.to_integral_value():
        raise StoreError("price values may have at most two decimal places")
    result = int(exact)
    if result <= 0:
        raise StoreError("price values must be greater than zero")
    return result


def money(value: int | None) -> str | None:
    return None if value is None else f"{Decimal(value) / 100:.2f}"


class PriceStore:
    def __init__(self, path: Path, *, clock=time.time):
        self.path = Path(path)
        self.clock = clock
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            os.chmod(self.path.parent, 0o700)
        except OSError as error:
            raise StoreError(f"could not secure database directory: {error}") from error
        self.connection = sqlite3.connect(self.path, timeout=15)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.execute("PRAGMA busy_timeout=15000")
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.executescript(SCHEMA)
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            self._migrate_schema()
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        os.chmod(self.path, 0o600)

    def _migrate_schema(self) -> None:
        columns = {
            row["name"]
            for row in self.connection.execute("PRAGMA table_info(items)").fetchall()
        }
        if "last_price_checked_at" not in columns:
            self.connection.execute(
                "ALTER TABLE items ADD COLUMN last_price_checked_at INTEGER"
            )
        if "notify_muted_until" not in columns:
            self.connection.execute(
                "ALTER TABLE items ADD COLUMN notify_muted_until INTEGER"
            )
        self.connection.execute(
            """
            UPDATE items
            SET last_price_checked_at = (
                SELECT MAX(checked_at)
                FROM checks
                WHERE checks.item_id=items.id AND checks.status='ok'
            )
            WHERE last_price_checked_at IS NULL AND last_price_cents IS NOT NULL
            """
        )

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "PriceStore":
        return self

    def __exit__(self, *_args) -> None:
        self.close()

    def _public(self, row: sqlite3.Row) -> dict:
        price_cents = row["last_price_cents"]
        threshold_cents = row["threshold_cents"]
        notify_muted_until = row["notify_muted_until"]
        display_title = row["title"] or row["last_title"] or row["url"]
        return {
            "id": row["id"],
            "parser": row["parser"],
            "threshold": money(threshold_cents),
            "threshold_cents": threshold_cents,
            "url": row["url"],
            "title": row["title"],
            "display_title": display_title,
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "last_checked_at": row["last_checked_at"],
            "last_price": money(price_cents),
            "last_price_cents": price_cents,
            "last_price_checked_at": row["last_price_checked_at"],
            "last_status": row["last_status"],
            "last_error": row["last_error"],
            "last_title": row["last_title"],
            "notify_muted_until": notify_muted_until,
            "notifications_muted": (
                notify_muted_until is not None
                and notify_muted_until > int(self.clock())
            ),
            "below_threshold": (
                price_cents < threshold_cents if price_cents is not None else None
            ),
        }

    def list_items(self) -> list[dict]:
        rows = self.connection.execute(
            "SELECT * FROM items ORDER BY created_at, id"
        ).fetchall()
        return [self._public(row) for row in rows]

    def get_item(self, item_id: int) -> dict:
        row = self.connection.execute(
            "SELECT * FROM items WHERE id=?", (item_id,)
        ).fetchone()
        if row is None:
            raise StoreError(f"price check {item_id} was not found")
        return self._public(row)

    def add_item(
        self, parser: str, threshold: Decimal, url: str, title: str | None = None
    ) -> dict:
        now = int(self.clock())
        normalized = normalized_url(url)
        try:
            cursor = self.connection.execute(
                """
                INSERT INTO items
                    (parser, threshold_cents, url, normalized_url, title,
                     created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (parser, cents(threshold), url, normalized, title or None, now, now),
            )
            self.connection.commit()
        except sqlite3.IntegrityError as error:
            raise StoreError(f"URL is already configured: {url}") from error
        return self.get_item(cursor.lastrowid)

    def remove_item(self, match: str | int) -> dict:
        rows = self.connection.execute("SELECT * FROM items ORDER BY id").fetchall()
        if isinstance(match, int) or (isinstance(match, str) and match.isdigit()):
            wanted_id = int(match)
            matches = [row for row in rows if row["id"] == wanted_id]
        elif str(match).startswith(("http://", "https://")):
            wanted_url = normalized_url(str(match))
            matches = [row for row in rows if row["normalized_url"] == wanted_url]
        else:
            wanted_title = str(match).strip().casefold()
            matches = [
                row
                for row in rows
                if (row["title"] or row["last_title"] or "").strip().casefold()
                == wanted_title
            ]
        if not matches:
            raise StoreError(f"no price check matches {match!r}")
        if len(matches) > 1:
            raise StoreError(f"multiple price checks match {match!r}; remove by ID or URL")
        item = self._public(matches[0])
        self.connection.execute("DELETE FROM items WHERE id=?", (item["id"],))
        self.connection.commit()
        return item

    def update_item(
        self,
        item_id: int,
        parser: str,
        threshold: Decimal,
        url: str,
        title: str | None = None,
    ) -> dict:
        now = int(self.clock())
        try:
            cursor = self.connection.execute(
                """
                UPDATE items
                SET parser=?, threshold_cents=?, url=?, normalized_url=?,
                    title=?, updated_at=?
                WHERE id=?
                """,
                (
                    parser,
                    cents(threshold),
                    url,
                    normalized_url(url),
                    title or None,
                    now,
                    item_id,
                ),
            )
            self.connection.commit()
        except sqlite3.IntegrityError as error:
            raise StoreError(f"URL is already configured: {url}") from error
        if cursor.rowcount != 1:
            raise StoreError(f"price check {item_id} was not found")
        return self.get_item(item_id)

    def set_notification_mute(self, item_id: int, days: int) -> dict:
        if days < 0:
            raise StoreError("notification mute days cannot be negative")
        now = int(self.clock())
        muted_until = None if days == 0 else now + days * 24 * 60 * 60
        cursor = self.connection.execute(
            """
            UPDATE items
            SET notify_muted_until=?, updated_at=?
            WHERE id=?
            """,
            (muted_until, now, item_id),
        )
        self.connection.commit()
        if cursor.rowcount != 1:
            raise StoreError(f"price check {item_id} was not found")
        return self.get_item(item_id)

    def record_success(self, item_id: int, title: str, price: Decimal) -> dict:
        now = int(self.clock())
        price_cents = cents(price)
        with self.connection:
            cursor = self.connection.execute(
                """
                UPDATE items
                SET last_checked_at=?, last_price_cents=?,
                    last_price_checked_at=?, last_status='ok',
                    last_error=NULL, last_title=?, updated_at=?
                WHERE id=?
                """,
                (now, price_cents, now, title, now, item_id),
            )
            if cursor.rowcount != 1:
                raise StoreError(f"price check {item_id} was removed during its check")
            self.connection.execute(
                """
                INSERT INTO checks(item_id, checked_at, status, price_cents, title)
                VALUES (?, ?, 'ok', ?, ?)
                """,
                (item_id, now, price_cents, title),
            )
            self._prune_history(item_id)
        return self.get_item(item_id)

    def record_error(self, item_id: int, error: str) -> dict:
        now = int(self.clock())
        detail = error[-1000:]
        with self.connection:
            cursor = self.connection.execute(
                """
                UPDATE items
                SET last_checked_at=?, last_status='error', last_error=?, updated_at=?
                WHERE id=?
                """,
                (now, detail, now, item_id),
            )
            if cursor.rowcount != 1:
                raise StoreError(f"price check {item_id} was removed during its check")
            self.connection.execute(
                """
                INSERT INTO checks(item_id, checked_at, status, error)
                VALUES (?, ?, 'error', ?)
                """,
                (item_id, now, detail),
            )
            self._prune_history(item_id)
        return self.get_item(item_id)

    def _prune_history(self, item_id: int) -> None:
        self.connection.execute(
            """
            DELETE FROM checks
            WHERE item_id=? AND id NOT IN (
                SELECT id FROM checks WHERE item_id=? ORDER BY checked_at DESC, id DESC
                LIMIT 100
            )
            """,
            (item_id, item_id),
        )
