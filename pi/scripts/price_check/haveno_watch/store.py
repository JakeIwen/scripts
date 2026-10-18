"""Private SQLite storage for Haveno offer watches."""

from __future__ import annotations

import os
import sqlite3
import time
from decimal import Decimal
from pathlib import Path


SCHEMA = """
CREATE TABLE IF NOT EXISTS haveno_watches (
    id INTEGER PRIMARY KEY,
    network TEXT NOT NULL DEFAULT 'reto',
    currency TEXT NOT NULL,
    payment_method TEXT NOT NULL,
    threshold_pct TEXT NOT NULL,
    fee_pct TEXT NOT NULL,
    min_xmr TEXT,
    max_xmr TEXT,
    title TEXT,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    last_checked_at INTEGER,
    last_status TEXT NOT NULL DEFAULT 'never'
        CHECK (last_status IN ('never', 'ok', 'error')),
    last_error TEXT,
    notify_muted_until INTEGER
);
CREATE UNIQUE INDEX IF NOT EXISTS haveno_watches_configuration
    ON haveno_watches(
        network, currency, payment_method, threshold_pct, fee_pct,
        IFNULL(min_xmr, ''), IFNULL(max_xmr, '')
    );
CREATE TABLE IF NOT EXISTS haveno_offers (
    watch_id INTEGER NOT NULL
        REFERENCES haveno_watches(id) ON DELETE CASCADE,
    offer_id TEXT NOT NULL,
    price TEXT NOT NULL,
    vs_market_pct TEXT NOT NULL,
    effective_pct TEXT NOT NULL,
    min_xmr TEXT NOT NULL,
    max_xmr TEXT NOT NULL,
    offered_at INTEGER NOT NULL,
    first_seen_at INTEGER NOT NULL,
    last_seen_at INTEGER NOT NULL,
    is_current INTEGER NOT NULL CHECK (is_current IN (0, 1)),
    matches INTEGER NOT NULL CHECK (matches IN (0, 1)),
    first_matched_at INTEGER,
    notified_at INTEGER,
    PRIMARY KEY (watch_id, offer_id)
);
CREATE TABLE IF NOT EXISTS haveno_checks (
    id INTEGER PRIMARY KEY,
    watch_id INTEGER NOT NULL
        REFERENCES haveno_watches(id) ON DELETE CASCADE,
    checked_at INTEGER NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('ok', 'error')),
    reference_price TEXT,
    reference_source TEXT,
    best_price TEXT,
    best_vs_market_pct TEXT,
    offer_count INTEGER,
    match_count INTEGER,
    error TEXT
);
CREATE INDEX IF NOT EXISTS haveno_offers_current
    ON haveno_offers(watch_id, is_current, price);
CREATE INDEX IF NOT EXISTS haveno_checks_watch_time
    ON haveno_checks(watch_id, checked_at DESC);
"""

HISTORY_SECONDS = 30 * 24 * 60 * 60


class HavenoStoreError(RuntimeError):
    pass


def decimal_text(value: Decimal | None) -> str | None:
    if value is None:
        return None
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def price_text(value: Decimal | None) -> str | None:
    return None if value is None else f"{value:.2f}"


def percent_text(value: Decimal | None) -> str | None:
    if value is None:
        return None
    normalized = abs(value) if value == 0 else value
    return f"{normalized:.2f}"


def method_title(value: str) -> str:
    return " ".join(part.title() for part in value.split("_"))


def signed_percent_text(value: str) -> str:
    return f"{Decimal(value):+.2f}"


def default_display_title(row: sqlite3.Row) -> str:
    fee = Decimal(row["fee_pct"])
    suffix = f" ({signed_percent_text(row['fee_pct'])}% fee)" if fee > 0 else ""
    return (
        f"XMR · {method_title(row['payment_method'])} · "
        f"≤{signed_percent_text(row['threshold_pct'])}% vs market{suffix}"
    )


class HavenoStore:
    def __init__(self, path: Path, *, clock=time.time):
        self.path = Path(path)
        self.clock = clock
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            os.chmod(self.path.parent, 0o700)
        except OSError as error:
            raise HavenoStoreError(f"could not secure database directory: {error}") from error
        self.connection = sqlite3.connect(self.path, timeout=15)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.execute("PRAGMA busy_timeout=15000")
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.executescript(SCHEMA)
        self.connection.commit()
        os.chmod(self.path, 0o600)

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "HavenoStore":
        return self

    def __exit__(self, *_args) -> None:
        self.close()

    def _latest_success(self, watch_id: int):
        return self.connection.execute(
            """
            SELECT * FROM haveno_checks
            WHERE watch_id=? AND status='ok'
            ORDER BY checked_at DESC, id DESC LIMIT 1
            """,
            (watch_id,),
        ).fetchone()

    @staticmethod
    def _public_offer(row: sqlite3.Row) -> dict:
        return {
            "offer_id": row["offer_id"],
            "price": row["price"],
            "vs_market_pct": row["vs_market_pct"],
            "effective_pct": row["effective_pct"],
            "min_xmr": row["min_xmr"],
            "max_xmr": row["max_xmr"],
            "offered_at": row["offered_at"],
            "first_seen_at": row["first_seen_at"],
            "last_seen_at": row["last_seen_at"],
            "matches": bool(row["matches"]),
            "notified_at": row["notified_at"],
        }

    def _current_offers(self, watch_id: int) -> tuple[list[dict], int, int]:
        rows = self.connection.execute(
            """
            SELECT * FROM haveno_offers
            WHERE watch_id=? AND is_current=1
            ORDER BY CAST(price AS REAL), offer_id
            """,
            (watch_id,),
        ).fetchall()
        matching = [row for row in rows if row["matches"]]
        if len(matching) <= 20:
            nonmatching = [row for row in rows if not row["matches"]]
            visible = matching + nonmatching[: 20 - len(matching)]
            visible.sort(key=lambda row: (Decimal(row["price"]), row["offer_id"]))
        else:
            visible = matching[:20]
        return (
            [self._public_offer(row) for row in visible],
            len(rows),
            sum(bool(row["matches"]) for row in rows),
        )

    def _public_watch(self, row: sqlite3.Row) -> dict:
        successful = self._latest_success(row["id"])
        offers, offer_count, match_count = self._current_offers(row["id"])
        since = int(self.clock()) - 24 * 60 * 60
        best_24h = self.connection.execute(
            """
            SELECT MIN(CAST(best_vs_market_pct AS REAL)) AS value
            FROM haveno_checks
            WHERE watch_id=? AND status='ok' AND checked_at>=?
              AND best_vs_market_pct IS NOT NULL
            """,
            (row["id"], since),
        ).fetchone()["value"]
        muted_until = row["notify_muted_until"]
        return {
            "id": row["id"],
            "network": row["network"],
            "currency": row["currency"],
            "payment_method": row["payment_method"],
            "threshold_pct": row["threshold_pct"],
            "fee_pct": row["fee_pct"],
            "min_xmr": row["min_xmr"],
            "max_xmr": row["max_xmr"],
            "title": row["title"],
            "display_title": row["title"] or default_display_title(row),
            "market_url": f"https://haveno.markets/market/{row['currency']}",
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "last_checked_at": row["last_checked_at"],
            "last_status": row["last_status"],
            "last_error": row["last_error"],
            "notify_muted_until": muted_until,
            "notifications_muted": muted_until is not None and muted_until > int(self.clock()),
            "reference_price": successful["reference_price"] if successful else None,
            "reference_source": successful["reference_source"] if successful else None,
            "reference_at": successful["checked_at"] if successful else None,
            "best_price": successful["best_price"] if successful else None,
            "best_vs_market_pct": successful["best_vs_market_pct"] if successful else None,
            "best_vs_market_24h_pct": percent_text(Decimal(str(best_24h))) if best_24h is not None else None,
            "offer_count": offer_count,
            "match_count": match_count,
            "offers": offers,
        }

    def list_watches(self) -> list[dict]:
        rows = self.connection.execute(
            "SELECT * FROM haveno_watches ORDER BY created_at, id"
        ).fetchall()
        return [self._public_watch(row) for row in rows]

    def get_watch(self, watch_id: int) -> dict:
        row = self.connection.execute(
            "SELECT * FROM haveno_watches WHERE id=?", (watch_id,)
        ).fetchone()
        if row is None:
            raise HavenoStoreError(f"Haveno watch {watch_id} was not found")
        return self._public_watch(row)

    @staticmethod
    def _values(configuration: dict) -> tuple:
        return (
            configuration["network"],
            configuration["currency"],
            configuration["payment_method"],
            configuration["threshold_pct"],
            configuration["fee_pct"],
            configuration["min_xmr"],
            configuration["max_xmr"],
            configuration["title"],
        )

    def add_watch(self, configuration: dict) -> dict:
        now = int(self.clock())
        try:
            cursor = self.connection.execute(
                """
                INSERT INTO haveno_watches
                    (network, currency, payment_method, threshold_pct, fee_pct,
                     min_xmr, max_xmr, title, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (*self._values(configuration), now, now),
            )
            self.connection.commit()
        except sqlite3.IntegrityError as error:
            raise HavenoStoreError("this Haveno watch is already configured") from error
        return self.get_watch(cursor.lastrowid)

    def update_watch(self, watch_id: int, configuration: dict) -> dict:
        existing = self.connection.execute(
            "SELECT * FROM haveno_watches WHERE id=?", (watch_id,)
        ).fetchone()
        if existing is None:
            raise HavenoStoreError(f"Haveno watch {watch_id} was not found")
        values = self._values(configuration)
        old_criteria = tuple(
            existing[name]
            for name in (
                "network",
                "currency",
                "payment_method",
                "threshold_pct",
                "fee_pct",
                "min_xmr",
                "max_xmr",
            )
        )
        now = int(self.clock())
        try:
            with self.connection:
                self.connection.execute(
                    """
                    UPDATE haveno_watches
                    SET network=?, currency=?, payment_method=?, threshold_pct=?,
                        fee_pct=?, min_xmr=?, max_xmr=?, title=?, updated_at=?
                    WHERE id=?
                    """,
                    (*values, now, watch_id),
                )
                if old_criteria != values[:7]:
                    self.connection.execute(
                        """UPDATE haveno_watches
                           SET last_checked_at=NULL, last_status='never', last_error=NULL
                           WHERE id=?""",
                        (watch_id,),
                    )
                    self.connection.execute(
                        "UPDATE haveno_offers SET is_current=0 WHERE watch_id=?",
                        (watch_id,),
                    )
                    self.connection.execute(
                        "DELETE FROM haveno_checks WHERE watch_id=?", (watch_id,)
                    )
        except sqlite3.IntegrityError as error:
            raise HavenoStoreError("this Haveno watch is already configured") from error
        return self.get_watch(watch_id)

    def remove_watch(self, watch_id: int) -> dict:
        watch = self.get_watch(watch_id)
        self.connection.execute("DELETE FROM haveno_watches WHERE id=?", (watch_id,))
        self.connection.commit()
        return watch

    def set_notification_mute(self, watch_id: int, days: int) -> dict:
        if days < 0:
            raise HavenoStoreError("notification mute days cannot be negative")
        now = int(self.clock())
        muted_until = None if days == 0 else now + days * 24 * 60 * 60
        cursor = self.connection.execute(
            "UPDATE haveno_watches SET notify_muted_until=?, updated_at=? WHERE id=?",
            (muted_until, now, watch_id),
        )
        self.connection.commit()
        if cursor.rowcount != 1:
            raise HavenoStoreError(f"Haveno watch {watch_id} was not found")
        return self.get_watch(watch_id)

    def record_success(
        self,
        watch_id: int,
        reference_price: Decimal,
        reference_source: str,
        offers: list[dict],
    ) -> tuple[dict, list[dict]]:
        now = int(self.clock())
        matching = [offer for offer in offers if offer["matches"]]
        best = min(offers, key=lambda offer: offer["price"], default=None)
        with self.connection:
            self._update_success(watch_id, now)
            self.connection.execute(
                "UPDATE haveno_offers SET is_current=0 WHERE watch_id=?", (watch_id,)
            )
            for offer in offers:
                self._upsert_offer(watch_id, offer, now)
            self.connection.execute(
                """
                INSERT INTO haveno_checks
                    (watch_id, checked_at, status, reference_price, reference_source,
                     best_price, best_vs_market_pct, offer_count, match_count)
                VALUES (?, ?, 'ok', ?, ?, ?, ?, ?, ?)
                """,
                (
                    watch_id,
                    now,
                    price_text(reference_price),
                    reference_source,
                    price_text(best["price"]) if best else None,
                    percent_text(best["vs_market_pct"]) if best else None,
                    len(offers),
                    len(matching),
                ),
            )
            self._prune(now)
        candidates = self._notification_candidates(watch_id)
        return self.get_watch(watch_id), candidates

    def _update_success(self, watch_id: int, now: int) -> None:
        cursor = self.connection.execute(
            """
            UPDATE haveno_watches
            SET last_checked_at=?, last_status='ok', last_error=NULL, updated_at=?
            WHERE id=?
            """,
            (now, now, watch_id),
        )
        if cursor.rowcount != 1:
            raise HavenoStoreError(f"Haveno watch {watch_id} was removed during its check")

    def _upsert_offer(self, watch_id: int, offer: dict, now: int) -> None:
        matched_at = now if offer["matches"] else None
        self.connection.execute(
            """
            INSERT INTO haveno_offers
                (watch_id, offer_id, price, vs_market_pct, effective_pct,
                 min_xmr, max_xmr, offered_at, first_seen_at, last_seen_at,
                 is_current, matches, first_matched_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
            ON CONFLICT(watch_id, offer_id) DO UPDATE SET
                price=excluded.price,
                vs_market_pct=excluded.vs_market_pct,
                effective_pct=excluded.effective_pct,
                min_xmr=excluded.min_xmr,
                max_xmr=excluded.max_xmr,
                offered_at=excluded.offered_at,
                last_seen_at=excluded.last_seen_at,
                is_current=1,
                matches=excluded.matches,
                first_matched_at=COALESCE(haveno_offers.first_matched_at,
                    excluded.first_matched_at)
            """,
            (
                watch_id,
                offer["offer_id"],
                price_text(offer["price"]),
                percent_text(offer["vs_market_pct"]),
                percent_text(offer["effective_pct"]),
                decimal_text(offer["min_xmr"]),
                decimal_text(offer["max_xmr"]),
                offer["offered_at"],
                now,
                now,
                int(offer["matches"]),
                matched_at,
            ),
        )

    def _notification_candidates(self, watch_id: int) -> list[dict]:
        rows = self.connection.execute(
            """
            SELECT * FROM haveno_offers
            WHERE watch_id=? AND is_current=1 AND matches=1 AND notified_at IS NULL
            ORDER BY CAST(price AS REAL), offer_id
            """,
            (watch_id,),
        ).fetchall()
        return [self._public_offer(row) for row in rows]

    def mark_notified(self, watch_id: int, offer_ids: list[str]) -> None:
        if not offer_ids:
            return
        now = int(self.clock())
        placeholders = ",".join("?" for _ in offer_ids)
        with self.connection:
            self.connection.execute(
                f"""UPDATE haveno_offers SET notified_at=?
                    WHERE watch_id=? AND offer_id IN ({placeholders})
                      AND notified_at IS NULL""",
                (now, watch_id, *offer_ids),
            )

    def record_error(self, watch_id: int, error: str) -> dict:
        now = int(self.clock())
        detail = error[-1000:]
        with self.connection:
            cursor = self.connection.execute(
                """
                UPDATE haveno_watches
                SET last_checked_at=?, last_status='error', last_error=?, updated_at=?
                WHERE id=?
                """,
                (now, detail, now, watch_id),
            )
            if cursor.rowcount != 1:
                raise HavenoStoreError(f"Haveno watch {watch_id} was removed during its check")
            self.connection.execute(
                """INSERT INTO haveno_checks(watch_id, checked_at, status, error)
                   VALUES (?, ?, 'error', ?)""",
                (watch_id, now, detail),
            )
            self._prune(now)
        return self.get_watch(watch_id)

    def _prune(self, now: int) -> None:
        cutoff = now - HISTORY_SECONDS
        self.connection.execute(
            "DELETE FROM haveno_checks WHERE checked_at < ?", (cutoff,)
        )
        self.connection.execute(
            "DELETE FROM haveno_offers WHERE is_current=0 AND last_seen_at < ?", (cutoff,)
        )

    def summary(self) -> dict:
        watches = self.list_watches()
        return {
            "count": len(watches),
            "checked": sum(watch["last_checked_at"] is not None for watch in watches),
            "matching": sum(watch["match_count"] > 0 for watch in watches),
            "errors": sum(watch["last_status"] == "error" for watch in watches),
        }
