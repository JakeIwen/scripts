"""SQLite storage and summaries for inferred Haveno offer activity."""

from __future__ import annotations

import sqlite3
import time
from decimal import Decimal, InvalidOperation

from .source import HavenoOffer, HavenoTrade
from .store import decimal_text, price_text


NETWORK = "reto"
CURRENCY = "USD"
PAYMENT_METHOD = "ZELLE"
WINDOW_DAYS = (7, 30)
MATCH_XMR_TOLERANCE = Decimal("0.0001")
MATCH_PRICE_TOLERANCE = Decimal("0.03")
MATCH_LOOKBACK_SECONDS = 15 * 60
WITHDRAWAL_GRACE_SECONDS = 2 * 60 * 60
RETENTION_SECONDS = 120 * 24 * 60 * 60

SCHEMA = """
CREATE TABLE IF NOT EXISTS haveno_market_trades (
    id INTEGER PRIMARY KEY,
    date_ms INTEGER NOT NULL,
    currency TEXT NOT NULL,
    payment_method TEXT NOT NULL,
    price TEXT NOT NULL,
    xmr TEXT NOT NULL,
    base_volume TEXT NOT NULL,
    UNIQUE(date_ms, payment_method, price, xmr)
);
CREATE TABLE IF NOT EXISTS haveno_market_offers (
    offer_id TEXT PRIMARY KEY,
    network TEXT NOT NULL,
    currency TEXT NOT NULL,
    payment_method TEXT NOT NULL,
    price TEXT NOT NULL,
    min_xmr TEXT NOT NULL,
    max_xmr TEXT NOT NULL,
    offered_at INTEGER NOT NULL,
    first_seen_at INTEGER NOT NULL,
    last_seen_at INTEGER NOT NULL,
    gone_at INTEGER,
    outcome TEXT CHECK (outcome IS NULL OR outcome IN ('pending', 'taken', 'withdrawn')),
    trade_id INTEGER UNIQUE REFERENCES haveno_market_trades(id) ON DELETE SET NULL,
    confidence TEXT CHECK (confidence IS NULL OR confidence IN ('high', 'low')),
    returned_count INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS haveno_activity_meta (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    tracking_since INTEGER,
    last_snapshot_at INTEGER,
    last_error TEXT
);
CREATE INDEX IF NOT EXISTS haveno_market_offers_current
    ON haveno_market_offers(payment_method, gone_at);
CREATE INDEX IF NOT EXISTS haveno_market_offers_outcome
    ON haveno_market_offers(payment_method, gone_at, outcome);
CREATE INDEX IF NOT EXISTS haveno_market_trades_time
    ON haveno_market_trades(payment_method, date_ms);
"""


class HavenoActivityStoreError(RuntimeError):
    pass


def empty_activity_summary(last_error: str | None = None) -> dict:
    return {
        "network": NETWORK,
        "currency": CURRENCY,
        "payment_method": PAYMENT_METHOD,
        "tracking_since": None,
        "last_snapshot_at": None,
        "last_error": last_error,
        "current_offers": 0,
        "windows": [
            {
                "days": days,
                "listed": 0,
                "taken": 0,
                "taken_high": 0,
                "withdrawn": 0,
                "pending": 0,
                "taken_xmr": "0",
                "median_hours_to_take": None,
                "market_trades": 0,
                "market_trades_xmr": "0",
            }
            for days in WINDOW_DAYS
        ],
        "recent": [],
    }


class HavenoActivityStore:
    """Activity tables sharing an already secured Haveno database connection."""

    def __init__(self, connection: sqlite3.Connection, *, clock=time.time):
        self.connection = connection
        self.clock = clock
        try:
            self.connection.executescript(SCHEMA)
            self.connection.commit()
        except sqlite3.Error as error:
            raise HavenoActivityStoreError(f"could not initialize Haveno activity: {error}") from error

    def record_error(self, error: str) -> None:
        detail = error[-1000:]
        try:
            with self.connection:
                self.connection.execute(
                    """
                    INSERT INTO haveno_activity_meta(id, last_error) VALUES (1, ?)
                    ON CONFLICT(id) DO UPDATE SET last_error=excluded.last_error
                    """,
                    (detail,),
                )
        except sqlite3.Error as storage_error:
            raise HavenoActivityStoreError(
                f"could not record Haveno activity error: {storage_error}"
            ) from storage_error

    def record_snapshot(
        self,
        offers: tuple[HavenoOffer, ...],
        trades: tuple[HavenoTrade, ...],
        *,
        now: int | None = None,
    ) -> None:
        snapshot_at = int(self.clock()) if now is None else now
        try:
            with self.connection:
                for trade in trades:
                    self._insert_trade(trade)
                for offer in offers:
                    self._upsert_offer(offer, snapshot_at)
                self._mark_gone({offer.offer_id for offer in offers}, snapshot_at)
                self._classify_pending(snapshot_at)
                self._prune(snapshot_at)
                self.connection.execute(
                    """
                    INSERT INTO haveno_activity_meta
                        (id, tracking_since, last_snapshot_at, last_error)
                    VALUES (1, ?, ?, NULL)
                    ON CONFLICT(id) DO UPDATE SET
                        tracking_since=COALESCE(haveno_activity_meta.tracking_since,
                                                excluded.tracking_since),
                        last_snapshot_at=excluded.last_snapshot_at,
                        last_error=NULL
                    """,
                    (snapshot_at, snapshot_at),
                )
        except (sqlite3.Error, InvalidOperation, ValueError) as error:
            raise HavenoActivityStoreError(f"could not store Haveno activity: {error}") from error

    def _insert_trade(self, trade: HavenoTrade) -> None:
        self.connection.execute(
            """
            INSERT OR IGNORE INTO haveno_market_trades
                (date_ms, currency, payment_method, price, xmr, base_volume)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                trade.date_ms,
                trade.currency,
                trade.payment_method,
                price_text(trade.price),
                decimal_text(trade.xmr),
                decimal_text(trade.usd_amount),
            ),
        )

    def _upsert_offer(self, offer: HavenoOffer, now: int) -> None:
        self.connection.execute(
            """
            INSERT INTO haveno_market_offers
                (offer_id, network, currency, payment_method, price, min_xmr,
                 max_xmr, offered_at, first_seen_at, last_seen_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(offer_id) DO UPDATE SET
                price=excluded.price,
                min_xmr=excluded.min_xmr,
                max_xmr=excluded.max_xmr,
                last_seen_at=excluded.last_seen_at,
                returned_count=haveno_market_offers.returned_count +
                    CASE WHEN haveno_market_offers.gone_at IS NULL THEN 0 ELSE 1 END,
                gone_at=NULL,
                outcome=NULL,
                trade_id=NULL,
                confidence=NULL
            """,
            (
                offer.offer_id,
                NETWORK,
                CURRENCY,
                offer.payment_method,
                price_text(offer.price),
                decimal_text(offer.min_xmr),
                decimal_text(offer.max_xmr),
                offer.offered_at,
                now,
                now,
            ),
        )

    def _mark_gone(self, current_ids: set[str], now: int) -> None:
        parameters: list[object] = [now, NETWORK, CURRENCY]
        exclusion = ""
        if current_ids:
            placeholders = ",".join("?" for _ in current_ids)
            exclusion = f" AND offer_id NOT IN ({placeholders})"
            parameters.extend(sorted(current_ids))
        self.connection.execute(
            f"""
            UPDATE haveno_market_offers
            SET gone_at=?, outcome='pending', trade_id=NULL, confidence=NULL
            WHERE network=? AND currency=? AND gone_at IS NULL{exclusion}
            """,
            parameters,
        )

    def _classify_pending(self, now: int) -> None:
        offers = self.connection.execute(
            """
            SELECT * FROM haveno_market_offers
            WHERE outcome='pending'
            ORDER BY gone_at, offer_id
            """
        ).fetchall()
        for offer in offers:
            candidates = self._trade_candidates(offer)
            if candidates:
                chosen = candidates[0]
                confidence = (
                    "high"
                    if len(candidates) == 1 and offer["min_xmr"] == offer["max_xmr"]
                    else "low"
                )
                self.connection.execute(
                    """
                    UPDATE haveno_market_offers
                    SET outcome='taken', trade_id=?, confidence=?
                    WHERE offer_id=?
                    """,
                    (chosen["id"], confidence, offer["offer_id"]),
                )
            elif offer["gone_at"] <= now - WITHDRAWAL_GRACE_SECONDS:
                self.connection.execute(
                    """
                    UPDATE haveno_market_offers
                    SET outcome='withdrawn', trade_id=NULL, confidence=NULL
                    WHERE offer_id=?
                    """,
                    (offer["offer_id"],),
                )

    def _trade_candidates(self, offer: sqlite3.Row) -> list[sqlite3.Row]:
        rows = self.connection.execute(
            """
            SELECT trade.* FROM haveno_market_trades AS trade
            LEFT JOIN haveno_market_offers AS claimant ON claimant.trade_id=trade.id
            WHERE claimant.offer_id IS NULL
              AND trade.payment_method=?
              AND trade.date_ms>=? AND trade.date_ms<=?
            """,
            (
                offer["payment_method"],
                (offer["last_seen_at"] - MATCH_LOOKBACK_SECONDS) * 1000,
                offer["gone_at"] * 1000 + 999,
            ),
        ).fetchall()
        offer_price = Decimal(offer["price"])
        minimum = Decimal(offer["min_xmr"]) - MATCH_XMR_TOLERANCE
        maximum = Decimal(offer["max_xmr"]) + MATCH_XMR_TOLERANCE
        candidates = [
            row
            for row in rows
            if minimum <= Decimal(row["xmr"]) <= maximum
            and abs(Decimal(row["price"]) / offer_price - 1) <= MATCH_PRICE_TOLERANCE
        ]
        return sorted(
            candidates,
            key=lambda row: (
                abs(Decimal(row["price"]) / offer_price - 1),
                abs(offer["gone_at"] * 1000 - row["date_ms"]),
                row["id"],
            ),
        )

    def _prune(self, now: int) -> None:
        cutoff = now - RETENTION_SECONDS
        self.connection.execute(
            """
            DELETE FROM haveno_market_offers
            WHERE outcome IN ('taken', 'withdrawn') AND gone_at < ?
            """,
            (cutoff,),
        )
        self.connection.execute(
            """
            DELETE FROM haveno_market_trades
            WHERE date_ms < ?
              AND NOT EXISTS (
                  SELECT 1 FROM haveno_market_offers
                  WHERE haveno_market_offers.trade_id=haveno_market_trades.id
              )
            """,
            (cutoff * 1000,),
        )

    def summary(self, *, now: int | None = None) -> dict:
        current_time = int(self.clock()) if now is None else now
        try:
            meta = self.connection.execute(
                "SELECT * FROM haveno_activity_meta WHERE id=1"
            ).fetchone()
            current = self.connection.execute(
                """
                SELECT COUNT(*) FROM haveno_market_offers
                WHERE payment_method=? AND gone_at IS NULL
                """,
                (PAYMENT_METHOD,),
            ).fetchone()[0]
            windows = [self._window_summary(days, current_time) for days in WINDOW_DAYS]
            recent = self._recent()
        except (sqlite3.Error, InvalidOperation, ValueError) as error:
            raise HavenoActivityStoreError(f"could not summarize Haveno activity: {error}") from error
        return {
            "network": NETWORK,
            "currency": CURRENCY,
            "payment_method": PAYMENT_METHOD,
            "tracking_since": meta["tracking_since"] if meta else None,
            "last_snapshot_at": meta["last_snapshot_at"] if meta else None,
            "last_error": meta["last_error"] if meta else None,
            "current_offers": current,
            "windows": windows,
            "recent": recent,
        }

    def _window_summary(self, days: int, now: int) -> dict:
        cutoff = now - days * 24 * 60 * 60
        rows = self.connection.execute(
            "SELECT * FROM haveno_market_offers WHERE payment_method=?",
            (PAYMENT_METHOD,),
        ).fetchall()
        listed = sum(cutoff <= row["offered_at"] <= now for row in rows)
        resolved = [
            row
            for row in rows
            if row["gone_at"] is not None and cutoff <= row["gone_at"] <= now
        ]
        taken = [row for row in resolved if row["outcome"] == "taken"]
        trades = self.connection.execute(
            """
            SELECT * FROM haveno_market_trades
            WHERE payment_method=? AND date_ms>=? AND date_ms<=?
            """,
            (PAYMENT_METHOD, cutoff * 1000, now * 1000 + 999),
        ).fetchall()
        linked = []
        for offer in taken:
            linked.append(
                self.connection.execute(
                    "SELECT * FROM haveno_market_trades WHERE id=?", (offer["trade_id"],)
                ).fetchone()
            )
        durations = sorted(
            Decimal(offer["gone_at"] - offer["offered_at"]) / Decimal(3600)
            for offer in taken
        )
        return {
            "days": days,
            "listed": listed,
            "taken": len(taken),
            "taken_high": sum(row["confidence"] == "high" for row in taken),
            "withdrawn": sum(row["outcome"] == "withdrawn" for row in resolved),
            "pending": sum(row["outcome"] == "pending" for row in resolved),
            "taken_xmr": decimal_text(sum((Decimal(row["xmr"]) for row in linked), Decimal(0))),
            "median_hours_to_take": self._median_hours(durations),
            "market_trades": len(trades),
            "market_trades_xmr": decimal_text(
                sum((Decimal(row["xmr"]) for row in trades), Decimal(0))
            ),
        }

    @staticmethod
    def _median_hours(values: list[Decimal]) -> str | None:
        if not values:
            return None
        middle = len(values) // 2
        median = values[middle] if len(values) % 2 else (values[middle - 1] + values[middle]) / 2
        return f"{median:.1f}"

    def _recent(self) -> list[dict]:
        rows = self.connection.execute(
            """
            SELECT offer.*, trade.price AS trade_price, trade.xmr AS trade_xmr,
                   trade.date_ms AS trade_date_ms
            FROM haveno_market_offers AS offer
            LEFT JOIN haveno_market_trades AS trade ON trade.id=offer.trade_id
            WHERE offer.payment_method=? AND offer.gone_at IS NOT NULL
            ORDER BY offer.gone_at DESC, offer.offer_id
            LIMIT 15
            """,
            (PAYMENT_METHOD,),
        ).fetchall()
        return [self._public_recent(row) for row in rows]

    @staticmethod
    def _public_recent(row: sqlite3.Row) -> dict:
        taken = row["outcome"] == "taken"
        return {
            "offer_id": row["offer_id"],
            "price": price_text(Decimal(row["price"])),
            "min_xmr": decimal_text(Decimal(row["min_xmr"])),
            "max_xmr": decimal_text(Decimal(row["max_xmr"])),
            "offered_at": row["offered_at"],
            "first_seen_at": row["first_seen_at"],
            "gone_at": row["gone_at"],
            "outcome": row["outcome"],
            "confidence": row["confidence"] if taken else None,
            "trade_price": price_text(Decimal(row["trade_price"])) if taken else None,
            "trade_xmr": decimal_text(Decimal(row["trade_xmr"])) if taken else None,
            "trade_at": row["trade_date_ms"] // 1000 if taken else None,
        }
