import contextlib
import importlib.util
import io
import json
import sys
import tempfile
import time
import unittest
from decimal import Decimal
from pathlib import Path
from unittest import mock

from pi.scripts.price_check.haveno_watch.activity import collect_activity
from pi.scripts.price_check.haveno_watch.activity_store import (
    RETENTION_SECONDS,
    HavenoActivityStore,
)
from pi.scripts.price_check.haveno_watch.source import (
    DEPTH_URL,
    PRICE_URL,
    TRADES_URL,
    HavenoOffer,
    HavenoSourceError,
    HavenoTrade,
    fetch_trades,
)
from pi.scripts.price_check.haveno_watch.store import HavenoStore


ROOT = Path(__file__).parents[3]
SCRIPT = ROOT / "pi" / "scripts" / "price_check" / "main.py"
FIXTURES = Path(__file__).with_name("fixtures") / "haveno"
BASE = 1_800_000_000

SPEC = importlib.util.spec_from_file_location("price_check_haveno_activity_test", SCRIPT)
price_check = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
sys.modules[SPEC.name] = price_check
SPEC.loader.exec_module(price_check)


def offer(
    offer_id: str,
    *,
    offered_at: int = BASE - 3600,
    minimum: str = "1",
    maximum: str = "1",
    price: str = "100",
    payment_method: str = "ZELLE",
) -> HavenoOffer:
    return HavenoOffer(
        offer_id=offer_id,
        offered_at=offered_at,
        min_xmr=Decimal(minimum),
        max_xmr=Decimal(maximum),
        price=Decimal(price),
        payment_method=payment_method,
    )


def trade(
    date_ms: int,
    *,
    xmr: str = "1",
    price: str = "100",
    payment_method: str = "ZELLE",
) -> HavenoTrade:
    amount = Decimal(xmr)
    trade_price = Decimal(price)
    return HavenoTrade(
        currency="USD",
        date_ms=date_ms,
        payment_method=payment_method,
        price=trade_price,
        xmr=amount,
        usd_amount=amount * trade_price,
    )


class HavenoTradesSourceTests(unittest.TestCase):
    def test_parses_trade_feed_and_uses_rel_volume_as_xmr(self):
        payload = (FIXTURES / "trades.json").read_text(encoding="utf-8")
        trades = fetch_trades(lambda _url: payload, "USD")
        self.assertEqual(len(trades), 3)
        self.assertEqual(trades[0].xmr, Decimal("2.001"))
        self.assertEqual(trades[0].usd_amount, Decimal("1161.5805"))
        self.assertEqual(trades[0].payment_method, "ZELLE")

    def test_rejects_malformed_trade_feed(self):
        invalid_documents = [
            {},
            [{"currency": "USD"}],
            [{
                "currency": "USD", "price": 1, "date": 1,
                "paymentMethod": "ZELLE", "base_vol": 1, "rel_vol": 0,
            }],
            [{
                "currency": "EUR", "price": 1, "date": 1,
                "paymentMethod": "ZELLE", "base_vol": 1, "rel_vol": 1,
            }],
            [{
                "currency": "USD", "price": "NaN", "date": 1,
                "paymentMethod": "ZELLE", "base_vol": 1, "rel_vol": 1,
            }],
        ]
        for document in invalid_documents:
            with self.subTest(document=document):
                with self.assertRaises(HavenoSourceError):
                    fetch_trades(lambda _url, value=document: json.dumps(value), "USD")


class HavenoActivityStoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.db = Path(self.temporary.name) / "private" / "prices.sqlite3"
        self.haveno_store = HavenoStore(self.db, clock=lambda: BASE)
        self.store = HavenoActivityStore(self.haveno_store.connection, clock=lambda: BASE)

    def tearDown(self):
        self.haveno_store.close()
        self.temporary.cleanup()

    def test_empty_summary_shape(self):
        summary = self.store.summary(now=BASE)
        self.assertEqual(summary["network"], "reto")
        self.assertEqual(summary["currency"], "USD")
        self.assertEqual(summary["payment_method"], "ZELLE")
        self.assertIsNone(summary["tracking_since"])
        self.assertIsNone(summary["last_snapshot_at"])
        self.assertIsNone(summary["last_error"])
        self.assertEqual(summary["current_offers"], 0)
        self.assertEqual(summary["recent"], [])
        self.assertEqual([window["days"] for window in summary["windows"]], [7, 30])
        for window in summary["windows"]:
            self.assertEqual(
                window,
                {
                    "days": window["days"],
                    "listed": 0,
                    "taken": 0,
                    "taken_high": 0,
                    "withdrawn": 0,
                    "pending": 0,
                    "taken_xmr": "0",
                    "median_hours_to_take": None,
                    "market_trades": 0,
                    "market_trades_xmr": "0",
                },
            )

    def test_classifies_takes_with_confidence_claims_and_returns(self):
        first_offers = (
            offer("a-fixed", offered_at=BASE - 6 * 3600),
            offer("b-range", offered_at=BASE - 4 * 3600, maximum="3"),
            offer("c-fixed", offered_at=BASE - 3600),
            offer("d-other", payment_method="ACH"),
        )
        self.store.record_snapshot(first_offers, (), now=BASE)
        first = self.store.summary(now=BASE)
        self.assertEqual(first["tracking_since"], BASE)
        self.assertEqual(first["last_snapshot_at"], BASE)
        self.assertEqual(first["current_offers"], 3)

        gone_at = BASE + 600
        trades = (
            trade((BASE + 500) * 1000),
            trade((BASE + 590) * 1000, xmr="2", price="99"),
            trade((BASE + 580) * 1000, xmr="2", price="101"),
            trade((BASE + 570) * 1000, payment_method="ACH"),
        )
        self.store.record_snapshot((), trades, now=gone_at)
        rows = {
            row["offer_id"]: row
            for row in self.store.connection.execute(
                "SELECT * FROM haveno_market_offers"
            ).fetchall()
        }
        self.assertEqual((rows["a-fixed"]["outcome"], rows["a-fixed"]["confidence"]), ("taken", "high"))
        self.assertEqual((rows["b-range"]["outcome"], rows["b-range"]["confidence"]), ("taken", "low"))
        self.assertEqual(rows["c-fixed"]["outcome"], "pending")
        self.assertNotEqual(rows["a-fixed"]["trade_id"], rows["b-range"]["trade_id"])
        self.assertEqual(
            self.store.connection.execute(
                "SELECT COUNT(*) FROM haveno_market_offers WHERE trade_id=?",
                (rows["a-fixed"]["trade_id"],),
            ).fetchone()[0],
            1,
        )

        summary = self.store.summary(now=gone_at)
        window = summary["windows"][0]
        self.assertEqual(
            {key: window[key] for key in ("listed", "taken", "taken_high", "withdrawn", "pending")},
            {"listed": 3, "taken": 2, "taken_high": 1, "withdrawn": 0, "pending": 1},
        )
        self.assertEqual(window["taken_xmr"], "3")
        self.assertEqual(window["median_hours_to_take"], "5.2")
        self.assertEqual(window["market_trades"], 3)
        self.assertEqual(window["market_trades_xmr"], "5")
        self.assertEqual(len(summary["recent"]), 3)
        self.assertEqual(summary["recent"][0]["trade_price"], "100.00")
        self.assertEqual(summary["recent"][1]["confidence"], "low")
        self.assertIsNone(summary["recent"][2]["trade_at"])

        self.store.record_snapshot((), trades, now=gone_at + 2 * 3600)
        withdrawn = self.store.connection.execute(
            "SELECT * FROM haveno_market_offers WHERE offer_id='c-fixed'"
        ).fetchone()
        self.assertEqual(withdrawn["outcome"], "withdrawn")

        self.store.record_snapshot((first_offers[0],), trades, now=gone_at + 2 * 3600 + 60)
        returned = self.store.connection.execute(
            "SELECT * FROM haveno_market_offers WHERE offer_id='a-fixed'"
        ).fetchone()
        self.assertIsNone(returned["gone_at"])
        self.assertIsNone(returned["outcome"])
        self.assertIsNone(returned["trade_id"])
        self.assertIsNone(returned["confidence"])
        self.assertEqual(returned["returned_count"], 1)

    def test_prunes_old_resolved_offers_and_unlinked_trades(self):
        listed = offer("old")
        matched = trade((BASE + 30) * 1000)
        unmatched = trade((BASE + 20) * 1000, xmr="2")
        self.store.record_snapshot((listed,), (matched, unmatched), now=BASE)
        self.store.record_snapshot((), (matched, unmatched), now=BASE + 60)
        self.assertEqual(
            self.store.connection.execute("SELECT COUNT(*) FROM haveno_market_offers").fetchone()[0],
            1,
        )
        self.store.record_snapshot((), (), now=BASE + 61 + RETENTION_SECONDS)
        self.assertEqual(
            self.store.connection.execute("SELECT COUNT(*) FROM haveno_market_offers").fetchone()[0],
            0,
        )
        self.assertEqual(
            self.store.connection.execute("SELECT COUNT(*) FROM haveno_market_trades").fetchone()[0],
            0,
        )

    def test_failed_trade_fetch_records_error_without_changing_offers(self):
        depth = json.dumps({
            "asks": [{
                "offer_id": "kept", "offer_date": BASE * 1000,
                "amount": 1, "min_amount": 1, "price": 100,
                "payment_method": "ZELLE",
            }],
            "bids": [],
        })

        def good_fetch(url):
            if url == DEPTH_URL.format("USD", "reto"):
                return depth
            if url == TRADES_URL.format("USD", "reto"):
                return "[]"
            raise AssertionError(url)

        self.assertTrue(collect_activity(self.store, fetcher=good_fetch, now=BASE))

        def failed_fetch(url):
            if url == DEPTH_URL.format("USD", "reto"):
                return '{"asks":[],"bids":[]}'
            raise OSError("trades offline")

        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            self.assertFalse(collect_activity(self.store, fetcher=failed_fetch, now=BASE + 600))
        summary = self.store.summary(now=BASE + 600)
        self.assertEqual(summary["current_offers"], 1)
        self.assertIn("trades offline", summary["last_error"])
        self.assertEqual(summary["last_snapshot_at"], BASE)
        self.assertIn("price-check Haveno activity:", stderr.getvalue())

        self.assertTrue(collect_activity(self.store, fetcher=good_fetch, now=BASE + 700))
        self.assertIsNone(self.store.summary(now=BASE + 700)["last_error"])


class HavenoActivityCliTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.db = Path(self.temporary.name) / "prices.sqlite3"
        self.offer_document = {
            "asks": [{
                "offer_id": "live", "offer_date": int(time.time() * 1000),
                "amount": 1, "min_amount": 1, "price": 100,
                "payment_method": "ZELLE",
            }],
            "bids": [],
        }

    def tearDown(self):
        self.temporary.cleanup()

    def fetcher(self, url):
        if url == DEPTH_URL.format("USD", "reto"):
            return json.dumps(self.offer_document)
        if url == TRADES_URL.format("USD", "reto"):
            return "[]"
        if url == PRICE_URL:
            return json.dumps({
                "data": [{
                    "baseCurrencyCode": "XMR", "counterCurrencyCode": "USD",
                    "price": 100, "timestampMs": int(time.time() * 1000),
                }]
            })
        raise AssertionError(url)

    def run_cli(self, *arguments, fetcher=None):
        argv = [str(SCRIPT), "--db", str(self.db), "--json", *arguments]
        stdout = io.StringIO()
        stderr = io.StringIO()
        with mock.patch.object(sys, "argv", argv), mock.patch.object(
            price_check, "fetch", side_effect=fetcher or self.fetcher
        ), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            status = price_check.main()
        return status, json.loads(stdout.getvalue()), stderr.getvalue()

    def test_only_recording_all_checks_snapshot_and_no_notify_still_records(self):
        self.run_cli("haveno-add", "3")
        status, payload, _ = self.run_cli("haveno-check", "1", "--no-notify")
        self.assertEqual(status, 0)
        self.assertIsNone(payload["haveno_activity"]["tracking_since"])

        status, payload, _ = self.run_cli("--dry-run", "haveno-check", "all")
        self.assertEqual(status, 0)
        self.assertIsNone(payload["haveno_activity"]["tracking_since"])

        status, payload, _ = self.run_cli("haveno-check", "all", "--no-notify")
        self.assertEqual(status, 0)
        self.assertIsNotNone(payload["haveno_activity"]["tracking_since"])
        self.assertEqual(payload["haveno_activity"]["current_offers"], 1)

    def test_combined_check_all_snapshots_without_watches(self):
        status, payload, _ = self.run_cli("check", "all")
        self.assertEqual(status, 0)
        self.assertEqual(payload["haveno_watches"], [])
        self.assertEqual(payload["haveno_activity"]["current_offers"], 1)

    def test_trade_fetch_failure_does_not_change_exit_status_or_offer_state(self):
        status, payload, _ = self.run_cli("haveno-check", "all", "--no-notify")
        self.assertEqual(status, 0)
        self.assertEqual(payload["haveno_activity"]["current_offers"], 1)

        self.offer_document = {"asks": [], "bids": []}

        def failing_fetch(url):
            if url == TRADES_URL.format("USD", "reto"):
                raise OSError("trade feed down")
            return self.fetcher(url)

        status, payload, stderr = self.run_cli(
            "haveno-check", "all", "--no-notify", fetcher=failing_fetch
        )
        self.assertEqual(status, 0)
        self.assertEqual(payload["check_errors"], [])
        self.assertEqual(payload["haveno_activity"]["current_offers"], 1)
        self.assertIn("trade feed down", payload["haveno_activity"]["last_error"])
        self.assertIn("price-check Haveno activity:", stderr)


if __name__ == "__main__":
    unittest.main()
