import contextlib
import fcntl
import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from unittest import mock

from pi.scripts.price_check.cron_schedule import CronScheduleManager
from pi.scripts.price_check.haveno_watch.service import (
    HavenoNotificationError,
    HavenoWatchError,
    check_watch,
    check_watches,
    matching_offers,
    validate_watch,
)
from pi.scripts.price_check.haveno_watch.source import (
    DEPTH_URL,
    KRAKEN_URL,
    PRICE_URL,
    TRADES_URL,
    HavenoMarket,
    HavenoSourceError,
    fetch_market,
)
from pi.scripts.price_check.haveno_watch.store import HavenoStore


ROOT = Path(__file__).parents[3]
SCRIPT_DIR = ROOT / "pi" / "scripts" / "price_check"
SCRIPT = SCRIPT_DIR / "main.py"
FIXTURES = Path(__file__).with_name("fixtures") / "haveno"
NOW = 1_792_322_505

SPEC = importlib.util.spec_from_file_location("price_check_haveno_test", SCRIPT)
price_check = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
sys.modules[SPEC.name] = price_check
SPEC.loader.exec_module(price_check)


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def fixture_fetch(url: str) -> str:
    if url == PRICE_URL:
        return fixture("price.json")
    if url == KRAKEN_URL:
        return fixture("kraken.json")
    if url == DEPTH_URL.format("USD", "reto"):
        return fixture("depth.json")
    if url == TRADES_URL.format("USD", "reto"):
        return fixture("trades.json")
    raise AssertionError(f"unexpected URL {url}")


def market() -> HavenoMarket:
    return fetch_market(fixture_fetch, "USD", now=NOW)


class HavenoSourceTests(unittest.TestCase):
    def test_parses_real_trimmed_samples(self):
        result = market()
        self.assertEqual(result.reference_price, Decimal("581.555"))
        self.assertEqual(result.reference_source, "haveno-pricenode")
        self.assertEqual(len(result.offers), 5)
        self.assertEqual(result.offers[0].payment_method, "ZELLE")

    def test_stale_pricenode_falls_back_to_kraken_midpoint(self):
        stale = json.loads(fixture("price.json"))
        stale["data"][1]["timestampMs"] = (NOW - 901) * 1000
        calls = []

        def fetch(url):
            calls.append(url)
            if url == PRICE_URL:
                return json.dumps(stale)
            return fixture_fetch(url)

        result = fetch_market(fetch, "USD", now=NOW)
        self.assertEqual(result.reference_price, Decimal("581.72500000"))
        self.assertEqual(result.reference_source, "kraken")
        self.assertEqual(calls.count(PRICE_URL), 1)
        self.assertEqual(calls.count(KRAKEN_URL), 1)

    def test_both_reference_sources_failing_is_an_error(self):
        def fetch(url):
            if url == PRICE_URL:
                raise OSError("offline")
            if url == KRAKEN_URL:
                return '{"error":["unavailable"],"result":{}}'
            return fixture_fetch(url)

        with self.assertRaisesRegex(HavenoSourceError, "reference price unavailable"):
            fetch_market(fetch, "USD", now=NOW)

    def test_non_usd_does_not_use_kraken_fallback(self):
        calls = []

        def fetch(url):
            calls.append(url)
            raise OSError("offline")

        with self.assertRaises(HavenoSourceError):
            fetch_market(fetch, "EUR", now=NOW)
        self.assertNotIn(KRAKEN_URL, calls)

    def test_malformed_book_is_an_error(self):
        def fetch(url):
            if url.startswith("https://haveno.markets/api/v1/depth/"):
                return '{"bids":[]}'
            return fixture_fetch(url)

        with self.assertRaisesRegex(HavenoSourceError, "asks list"):
            fetch_market(fetch, "USD", now=NOW)


class HavenoValidationAndMatchingTests(unittest.TestCase):
    def test_signed_thresholds_match_effective_percentage(self):
        positive = matching_offers(validate_watch("7"), market())
        self.assertEqual(len(positive), 4)
        self.assertEqual(sum(offer["matches"] for offer in positive), 3)
        self.assertGreater(positive[2]["vs_market_pct"], 0)
        self.assertTrue(positive[2]["matches"])

        negative = matching_offers(validate_watch("-1"), market())
        self.assertEqual(sum(offer["matches"] for offer in negative), 1)
        self.assertLess(negative[0]["vs_market_pct"], Decimal("-1"))

        zero = matching_offers(validate_watch("0"), market())
        self.assertEqual(sum(offer["matches"] for offer in zero), 2)

    def test_fee_can_shift_an_offer_out_of_match(self):
        without_fee = matching_offers(validate_watch("0"), market())
        with_fee = matching_offers(validate_watch("0", fee_pct="0.50"), market())
        self.assertTrue(without_fee[1]["matches"])
        self.assertFalse(with_fee[1]["matches"])
        self.assertEqual(
            with_fee[1]["effective_pct"],
            with_fee[1]["vs_market_pct"] + Decimal("0.50"),
        )

    def test_matches_on_the_displayed_two_decimal_percentage(self):
        base = market()
        # 100.004 vs 100 is +0.004%, displayed and matched as +0.00%.
        offer = replace(base.offers[0], price=Decimal("100.004"), payment_method="ZELLE")
        priced = replace(base, reference_price=Decimal("100"), offers=(offer,))
        [result] = matching_offers(validate_watch("0"), priced)
        self.assertEqual(result["vs_market_pct"], Decimal("0.00"))
        self.assertTrue(result["matches"])

    def test_size_filters_use_max_for_minimum_and_min_for_maximum(self):
        base = validate_watch("3", min_xmr="3", max_xmr="8")
        offers = matching_offers(base, market())
        self.assertEqual([offer["max_xmr"] for offer in offers], [Decimal("10"), Decimal("11.65")])

        too_small = validate_watch("3", min_xmr="10.1")
        self.assertEqual(
            [offer["max_xmr"] for offer in matching_offers(too_small, market())],
            [Decimal("11.65")],
        )
        too_large_minimum = validate_watch("3", max_xmr="5")
        self.assertNotIn(
            Decimal("10"),
            [offer["max_xmr"] for offer in matching_offers(too_large_minimum, market())],
        )

    def test_validation_rejects_bad_values(self):
        invalid = (
            (("-50.01",), {}),
            (("50.01",), {}),
            (("1.001",), {}),
            (("3",), {"fee_pct": "-0.01"}),
            (("3",), {"fee_pct": "20.01"}),
            (("3",), {"fee_pct": "0.001"}),
            (("3",), {"payment_method": "zelle"}),
            (("3",), {"currency": "US"}),
            (("3",), {"min_xmr": "2", "max_xmr": "1"}),
            (("3",), {"min_xmr": "0.0000000000001"}),
            (("3",), {"title": "bad\ntitle"}),
        )
        for arguments, keywords in invalid:
            with self.subTest(arguments=arguments, keywords=keywords):
                with self.assertRaises(HavenoWatchError):
                    validate_watch(*arguments, **keywords)

    def test_signed_bounds_and_zero_fee_are_valid(self):
        self.assertEqual(validate_watch("-50")["threshold_pct"], "-50.00")
        self.assertEqual(validate_watch("50")["threshold_pct"], "50.00")
        self.assertEqual(validate_watch("-0")["threshold_pct"], "0.00")
        self.assertEqual(validate_watch("0", fee_pct="0")["fee_pct"], "0.00")


class HavenoStoreAndServiceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.db = Path(self.temporary.name) / "private" / "prices.sqlite3"
        self.now = [NOW]
        self.store = HavenoStore(self.db, clock=lambda: self.now[0])

    def tearDown(self):
        self.store.close()
        self.temporary.cleanup()

    def add(self, threshold="3", **options):
        return self.store.add_watch(validate_watch(threshold, **options))

    def test_private_database_duplicate_and_json_shape(self):
        watch = self.add(max_xmr="10.000")
        self.assertEqual(self.db.parent.stat().st_mode & 0o777, 0o700)
        self.assertEqual(self.db.stat().st_mode & 0o777, 0o600)
        self.assertEqual(watch["threshold_pct"], "3.00")
        self.assertEqual(watch["fee_pct"], "0.00")
        self.assertEqual(watch["max_xmr"], "10")
        self.assertEqual(watch["display_title"], "XMR · Zelle · ≤+3.00% vs market")
        self.assertIsNone(watch["reference_price"])
        with self.assertRaisesRegex(Exception, "already configured"):
            self.add(max_xmr="10")

    def test_default_display_title_formats_sign_and_fee(self):
        negative = self.add(threshold="-2", fee_pct="0.5")
        zero = self.add(threshold="0", max_xmr="9")
        self.assertEqual(
            negative["display_title"],
            "XMR · Zelle · ≤-2.00% vs market (+0.50% fee)",
        )
        self.assertEqual(zero["display_title"], "XMR · Zelle · ≤+0.00% vs market")

    def test_records_fields_and_notifies_each_offer_only_once_after_return(self):
        watch = self.add(fee_pct="0.50")
        notifications = []
        updated = check_watch(
            self.store, watch, market(), notify_sender=lambda *args: notifications.append(args)
        )
        self.assertEqual(updated["reference_price"], "581.56")
        self.assertEqual(updated["best_price"], "563.08")
        self.assertEqual(updated["best_vs_market_pct"], "-3.18")
        self.assertEqual(updated["best_vs_market_24h_pct"], "-3.18")
        self.assertEqual(updated["offer_count"], 4)
        self.assertEqual(updated["match_count"], 2)
        self.assertEqual(len(notifications), 1)
        self.assertIn("XMR -3.18% · $563.08 Zelle (2 new)", notifications[0][0])
        self.assertIn(
            "$563.08 (-3.18% vs $581.56) · 6–10 XMR",
            notifications[0][1],
        )
        self.assertIn("· -2.68% with fee", notifications[0][1])

        check_watch(self.store, updated, market(), notify_sender=lambda *args: notifications.append(args))
        empty = replace(market(), offers=())
        check_watch(self.store, self.store.get_watch(watch["id"]), empty, notify_sender=lambda *args: notifications.append(args))
        check_watch(self.store, self.store.get_watch(watch["id"]), market(), notify_sender=lambda *args: notifications.append(args))
        self.assertEqual(len(notifications), 1)

    def test_notification_formats_positive_listed_percentage(self):
        watch = self.add(threshold="7")
        source_market = market()
        premium_market = replace(source_market, offers=(source_market.offers[3],))
        notifications = []
        check_watch(
            self.store,
            watch,
            premium_market,
            notify_sender=lambda *args: notifications.append(args),
        )
        self.assertEqual(len(notifications), 1)
        self.assertIn("XMR +6.8%", notifications[0][0])
        self.assertIn("(+6.8% vs $581.56)", notifications[0][1])

    def test_best_24h_uses_minimum_listed_percentage(self):
        watch = self.add()
        original = market()
        check_watch(self.store, watch, original, notify=False)
        self.now[0] += 60
        lower = replace(original.offers[0], price=Decimal("550"))
        changed = replace(original, offers=(lower, *original.offers[1:]))
        updated = check_watch(
            self.store, self.store.get_watch(watch["id"]), changed, notify=False
        )
        self.assertEqual(updated["best_vs_market_pct"], "-5.43")
        self.assertEqual(updated["best_vs_market_24h_pct"], "-5.43")

    def test_mute_and_no_notify_preserve_future_notification(self):
        muted_watch = self.store.set_notification_mute(self.add()["id"], 1)
        sent = []
        check_watch(self.store, muted_watch, market(), notify_sender=lambda *args: sent.append(args))
        self.assertEqual(sent, [])
        self.store.set_notification_mute(muted_watch["id"], 0)
        check_watch(
            self.store,
            self.store.get_watch(muted_watch["id"]),
            market(),
            notify_sender=lambda *args: sent.append(args),
        )
        self.assertEqual(len(sent), 1)

        second = self.add(threshold="3", max_xmr="9")
        check_watch(self.store, second, market(), notify=False, notify_sender=lambda *args: sent.append(args))
        self.assertEqual(len(sent), 1)
        check_watch(
            self.store,
            self.store.get_watch(second["id"]),
            market(),
            notify_sender=lambda *args: sent.append(args),
        )
        self.assertEqual(len(sent), 2)

    def test_notification_failure_keeps_success_and_retries(self):
        watch = self.add()
        with self.assertRaises(HavenoNotificationError):
            check_watch(
                self.store,
                watch,
                market(),
                notify_sender=lambda *_args: (_ for _ in ()).throw(OSError("ntfy down")),
            )
        self.assertEqual(self.store.get_watch(watch["id"])["last_status"], "ok")
        sent = []
        check_watch(
            self.store,
            self.store.get_watch(watch["id"]),
            market(),
            notify_sender=lambda *args: sent.append(args),
        )
        self.assertEqual(len(sent), 1)

    def test_fetch_failure_records_error_and_preserves_offers(self):
        watch = self.add()
        check_watch(self.store, watch, market(), notify=False)
        checked, errors = check_watches(
            self.store,
            [self.store.get_watch(watch["id"])],
            fetcher=lambda _url: (_ for _ in ()).throw(OSError("offline")),
            notify=False,
        )
        self.assertEqual(checked, [])
        self.assertEqual(len(errors), 1)
        failed = self.store.get_watch(watch["id"])
        self.assertEqual(failed["last_status"], "error")
        self.assertEqual(failed["offer_count"], 4)
        self.assertEqual(failed["reference_price"], "581.56")

    def test_market_data_is_fetched_once_per_currency(self):
        watches = [self.add(), self.add(threshold="4")]
        calls = []

        def fetch(url):
            calls.append(url)
            return fixture_fetch(url)

        checked, errors = check_watches(
            self.store, watches, fetcher=fetch, notify=False
        )
        self.assertEqual(errors, [])
        self.assertEqual(len(checked), 2)
        self.assertEqual(calls.count(PRICE_URL), 1)
        self.assertEqual(calls.count(DEPTH_URL.format("USD", "reto")), 1)

    def test_dry_run_does_not_record_or_notify(self):
        watch = self.add()
        sent = []
        checked, errors = check_watches(
            self.store,
            [watch],
            fetcher=fixture_fetch,
            notify_sender=lambda *args: sent.append(args),
            notify=False,
            record=False,
        )
        self.assertEqual(errors, [])
        self.assertEqual(checked[0]["match_count"], 2)
        self.assertEqual(self.store.get_watch(watch["id"])["last_status"], "never")
        self.assertEqual(sent, [])

    def test_prunes_old_checks_and_noncurrent_offers(self):
        watch = self.add()
        check_watch(self.store, watch, market(), notify=False)
        cutoff = NOW - 31 * 24 * 60 * 60
        self.store.connection.execute(
            "UPDATE haveno_checks SET checked_at=? WHERE watch_id=?", (cutoff, watch["id"])
        )
        self.store.connection.execute(
            "UPDATE haveno_offers SET is_current=0, last_seen_at=? WHERE watch_id=?",
            (cutoff, watch["id"]),
        )
        self.store.connection.commit()
        check_watch(self.store, self.store.get_watch(watch["id"]), replace(market(), offers=()), notify=False)
        self.assertEqual(
            self.store.connection.execute(
                "SELECT count(*) FROM haveno_checks WHERE watch_id=?", (watch["id"],)
            ).fetchone()[0],
            1,
        )
        self.assertEqual(
            self.store.connection.execute(
                "SELECT count(*) FROM haveno_offers WHERE watch_id=?", (watch["id"],)
            ).fetchone()[0],
            0,
        )


class HavenoCliTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.db = Path(self.temporary.name) / "prices.sqlite3"

    def tearDown(self):
        self.temporary.cleanup()

    def run_cli(self, *arguments, fetcher=fixture_fetch):
        argv = [str(SCRIPT), "--db", str(self.db), "--json", *arguments]
        stdout = io.StringIO()
        stderr = io.StringIO()
        with mock.patch.object(sys, "argv", argv), mock.patch.object(
            price_check, "fetch", side_effect=fetcher
        ), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            status = price_check.main()
        return status, json.loads(stdout.getvalue()), stderr.getvalue()

    def test_add_list_check_and_remove_json_contract(self):
        status, payload, _ = self.run_cli("haveno-add", "3", "--max-xmr", "10")
        self.assertEqual(status, 0)
        self.assertIn("haveno_watch", payload)
        self.assertEqual(payload["haveno_summary"]["count"], 1)

        status, payload, _ = self.run_cli("haveno-check", "all", "--no-notify")
        self.assertEqual(status, 0)
        self.assertEqual(len(payload["haveno_checked"]), 1)
        watch = payload["haveno_watches"][0]
        expected = {
            "id", "network", "currency", "payment_method", "threshold_pct",
            "fee_pct", "min_xmr", "max_xmr", "title", "display_title", "market_url",
            "created_at", "updated_at", "last_checked_at", "last_status",
            "last_error", "notify_muted_until", "notifications_muted",
            "reference_price", "reference_source", "reference_at", "best_price",
            "best_vs_market_pct", "best_vs_market_24h_pct", "offer_count",
            "match_count", "offers",
        }
        self.assertEqual(set(watch), expected)
        self.assertEqual(watch["offers"][0]["price"], "563.08")
        self.assertRegex(watch["offers"][0]["vs_market_pct"], r"^-?\d+\.\d{2}$")
        self.assertEqual(
            watch["offers"][0]["effective_pct"],
            watch["offers"][0]["vs_market_pct"],
        )

        status, payload, _ = self.run_cli("haveno-remove", str(watch["id"]))
        self.assertEqual(status, 0)
        self.assertEqual(payload["removed_haveno_watch"]["id"], watch["id"])

    def test_negative_threshold_is_positional_for_add_and_edit(self):
        status, payload, _ = self.run_cli("haveno-add", "-2.5", "--fee-pct", "0.5")
        self.assertEqual(status, 0)
        watch = payload["haveno_watch"]
        self.assertEqual(watch["threshold_pct"], "-2.50")
        self.assertEqual(watch["fee_pct"], "0.50")

        status, payload, _ = self.run_cli(
            "haveno-edit", str(watch["id"]), "-1.25", "--fee-pct", "1"
        )
        self.assertEqual(status, 0)
        self.assertEqual(payload["haveno_watch"]["threshold_pct"], "-1.25")
        self.assertEqual(payload["haveno_watch"]["fee_pct"], "1.00")

    def test_validation_error_is_json_error(self):
        status, payload, _ = self.run_cli("haveno-add", "-50.01")
        self.assertEqual(status, 1)
        self.assertFalse(payload["ok"])

    def test_check_all_skips_held_haveno_lock(self):
        self.run_cli("haveno-add", "3")
        lock_path = self.db.with_suffix(".haveno.lock")
        with lock_path.open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            status, payload, stderr = self.run_cli("check", "all")
        self.assertEqual(status, 0)
        self.assertEqual(payload["haveno_checked"], [])
        self.assertIn("skipped", stderr)

    def test_dedicated_check_fails_when_lock_is_held(self):
        self.run_cli("haveno-add", "3")
        lock_path = self.db.with_suffix(".haveno.lock")
        with lock_path.open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            status, payload, _ = self.run_cli("haveno-check", "all")
        self.assertEqual(status, 1)
        self.assertIn("another Haveno check", payload["message"])

    def test_crontab_keeps_one_exact_managed_price_entry(self):
        crontab = (ROOT / "pi" / "crontab_pi").read_text(encoding="utf-8")
        _, expression = CronScheduleManager()._find_entry(crontab)
        self.assertEqual(expression, "0 10,15,20 * * *")
        self.assertIn("main.py haveno-check 2>&1", crontab)


if __name__ == "__main__":
    unittest.main()
