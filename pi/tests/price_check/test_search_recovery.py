import tempfile
import sqlite3
import json
import os
import subprocess
import sys
import io
import time
from contextlib import closing
import unittest
from pathlib import Path
from unittest import mock

from pi.scripts.price_check.search_watch.browser_refresh import (
    MAX_FAILURES, RETRY_SECONDS, claim_refresh, notify_exhausted,
    record_failure, request_refresh, retry_status,
)
from pi.scripts.price_check.search_watch.refresh_install import install_headers, recheck_headers
from pi.scripts.price_check.search_watch.service import (
    SearchCookieError, SearchLoadError, SearchParserError, SearchWatchError, check_watch,
)
from pi.scripts.price_check.search_watch.store import SearchStore, SearchStoreError
from pi.scripts.price_check.search_watch.browser_results import (
    LIMITS, accept_browser_results, read_submission,
)
from pi.scripts.price_check.search_watch.parsers import EbayParseError, parse
from pi.tests.price_check.test_search_watch import EBAY_PAGE


class SearchRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.now = [1700000000]
        self.db = self.root / "prices.sqlite3"
        self.store = SearchStore(self.db, clock=lambda: self.now[0])
        self.watch = self.store.add_watch("ebay", "https://www.ebay.com/sch/i.html?_nkw=x")
        self.headers = self.root / "headers"
        self.headers.write_text("User-Agent: old\nCookie: old=value\n")
        self.headers.chmod(0o600)

    def tearDown(self):
        self.store.close()
        self.temporary.cleanup()

    def check(self, **kwargs):
        return check_watch(self.store, self.watch, fetcher=lambda _: EBAY_PAGE, **kwargs)

    def request(self, *, with_headers=True):
        request_refresh(self.store, self.watch)
        request = claim_refresh(self.store)
        return {**request, "headers": "User-Agent: new\nCookie: new=value\n"} if with_headers else request

    def browser_page(self, html=EBAY_PAGE):
        return {**self.request(with_headers=False), "final_url": self.watch["url"], "html": html}

    def test_browser_page_recovers_results_without_any_http_request_or_cookie_write(self):
        payload = self.browser_page()
        before = self.headers.read_bytes(), self.headers.stat().st_mtime_ns
        notify = mock.Mock()
        with mock.patch("subprocess.run", side_effect=AssertionError("no HTTP client may run")):
            result = accept_browser_results(self.store, payload, notify_new=notify)
        self.assertEqual(result, {"ok": True, "result_count": 2, "notification_failed": False})
        self.assertEqual(self.store.get_watch(self.watch["id"])["last_status"], "ok")
        self.assertEqual(self.store.get_watch(self.watch["id"])["result_count"], 2)
        self.assertIsNone(retry_status(self.store)["retry_after"])
        notify.assert_called_once()
        self.assertEqual((self.headers.read_bytes(), self.headers.stat().st_mtime_ns), before)
        with self.assertRaisesRegex(ValueError, "no longer current"):
            accept_browser_results(self.store, payload, notify_new=notify)
        notify.assert_called_once()

    def test_browser_page_uses_existing_dismissal_and_new_listing_rules(self):
        self.check(notify=False)
        self.store.dismiss_result(self.watch["id"], "123456789012")
        payload = self.browser_page()
        notify = mock.Mock()
        accept_browser_results(self.store, payload, notify_new=notify)
        watch = self.store.get_watch(self.watch["id"])
        self.assertEqual((watch["result_count"], watch["hidden_current_count"]), (1, 1))
        notify.assert_not_called()

    def test_browser_page_failure_preserves_previous_results(self):
        self.check(notify=False)
        payload = self.browser_page()
        before = self.store.get_watch(self.watch["id"])
        for page in ("<title>Pardon Our Interruption</title>", "unknown result layout"):
            with self.assertRaises(EbayParseError):
                accept_browser_results(self.store, {**payload, "html": page})
        self.assertEqual(self.store.get_watch(self.watch["id"]), before)

    def test_browser_page_rejects_changed_query_host_or_claim(self):
        payload = self.browser_page()
        variants = (
            {"final_url": "https://example.com/sch/i.html?_nkw=x"},
            {"final_url": "https://www.ebay.com/sch/i.html?_nkw=other"},
            {"final_url": "https://www.ebay.com/sch/i.html?_nkw=x&_udhi=20"},
            {"final_url": "https://user@www.ebay.com/sch/i.html?_nkw=x"},
            {"final_url": "https://www.ebay.com:444/sch/i.html?_nkw=x"},
            {"request_id": "0" * 32}, {"attempt": 2}, {"headers": "Cookie: private=value"},
        )
        for changed in variants:
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                accept_browser_results(self.store, {**payload, **changed})
        self.assertEqual(self.store.get_watch(self.watch["id"])["last_status"], "never")

    def test_browser_page_accepts_equivalent_url_encoding(self):
        payload = self.browser_page()
        result = accept_browser_results(self.store, {**payload,
            "final_url": "https://www.ebay.com:443/sch/i.html?_nkw=%78"})
        self.assertTrue(result["ok"])

    def test_browser_page_bounds_are_measured_in_bytes(self):
        payload = self.browser_page()
        with mock.patch.dict(LIMITS, {"max_html_bytes": 3}), self.assertRaises(ValueError):
            accept_browser_results(self.store, {**payload, "html": "éé"})
        with mock.patch.dict(LIMITS, {"max_submission_bytes": 20}):
            stream = io.BytesIO(b"x" * 100)
            with self.assertRaisesRegex(ValueError, "too large"):
                read_submission(stream)
            self.assertEqual(stream.tell(), 21)
        self.assertEqual(read_submission(io.BytesIO(b'{"html":"example"}')), {"html": "example"})

    def test_browser_page_expiring_during_parse_cannot_update_results(self):
        payload = self.browser_page()
        parsed = parse(EBAY_PAGE)

        def slow_parse(_html):
            self.now[0] += RETRY_SECONDS
            return parsed

        with mock.patch("pi.scripts.price_check.search_watch.browser_results.parse", side_effect=slow_parse):
            with self.assertRaisesRegex(ValueError, "expired during parsing"):
                accept_browser_results(self.store, payload)
        self.assertEqual(self.store.get_watch(self.watch["id"])["last_status"], "never")

    def test_browser_result_storage_survives_notification_failure(self):
        result = accept_browser_results(self.store, self.browser_page(),
                                        notify_new=mock.Mock(side_effect=RuntimeError("ntfy offline")))
        self.assertTrue(result["ok"])
        self.assertTrue(result["notification_failed"])
        self.assertEqual(self.store.get_watch(self.watch["id"])["last_status"], "ok")
        self.assertIsNone(retry_status(self.store)["retry_after"])

    def test_browser_result_storage_failure_is_not_reported_as_success(self):
        payload = self.browser_page()
        with mock.patch.object(self.store, "record_success", side_effect=SearchStoreError("storage failed")):
            with self.assertRaises(SearchStoreError):
                accept_browser_results(self.store, payload)
        self.assertEqual(self.store.get_watch(self.watch["id"])["last_status"], "never")
        self.assertIsNotNone(retry_status(self.store)["retry_after"])

    def test_results_bridge_accepts_browser_html_with_no_available_cookie_file(self):
        self.now[0] = int(time.time())
        payload = self.browser_page()
        sender = self.root / "ntfy-stub"
        sender.write_text('#!/bin/sh\nprintf "%s\\n" "$1" >> "$ALERT_LOG"\n')
        sender.chmod(0o700)
        log = self.root / "alerts"
        bridge = Path(__file__).parents[2] / "scripts/price_check/ebay_refresh.py"
        env = {**os.environ, "NTFY_SEND_BIN": str(sender), "ALERT_LOG": str(log),
               "EBAY_HEADERS_FILE": str(self.root / "does-not-exist")}
        result = subprocess.run(
            [sys.executable, str(bridge), "--db", str(self.db), "results"],
            input=json.dumps(payload), capture_output=True, text=True, env=env, check=True,
        )
        self.assertEqual(json.loads(result.stdout)["result_count"], 2)
        self.assertEqual(len(log.read_text().splitlines()), 1)
        self.assertIsNone(retry_status(self.store)["retry_after"])

    def test_recheck_recovers_results_and_cancels_renewal_without_replacing_headers(self):
        request = self.request(with_headers=False)
        self.store.record_error(self.watch["id"], "request rejected")
        before = self.headers.read_bytes(), self.headers.stat().st_mtime_ns
        notify = mock.Mock()
        self.assertFalse(recheck_headers(
            self.store, request, fetcher=lambda _: EBAY_PAGE, notify_new=notify,
        ))
        self.assertEqual(self.store.get_watch(self.watch["id"])["last_status"], "ok")
        self.assertEqual(self.store.get_watch(self.watch["id"])["result_count"], 2)
        notify.assert_called_once()
        self.now[0] += RETRY_SECONDS
        self.assertIsNone(claim_refresh(self.store))
        self.assertEqual((self.headers.read_bytes(), self.headers.stat().st_mtime_ns), before)

    def test_recheck_gate_keeps_request_and_cooldown_for_browser_renewal(self):
        request = self.request(with_headers=False)
        self.assertTrue(recheck_headers(
            self.store, request, fetcher=lambda _: "<title>Pardon Our Interruption</title>",
        ))
        self.assertIsNone(claim_refresh(self.store))
        self.now[0] += RETRY_SECONDS
        self.assertEqual(claim_refresh(self.store)["request_id"], request["request_id"])

    def test_recheck_network_or_parser_failure_defers_without_requesting_new_headers(self):
        request = self.request(with_headers=False)
        cases = (
            (mock.Mock(side_effect=SearchLoadError("network unavailable")), SearchLoadError),
            (lambda _: "unknown page markup", SearchParserError),
        )
        for fetcher, error in cases:
            with self.assertRaises(error):
                recheck_headers(self.store, request, fetcher=fetcher)
        self.assertIsNone(claim_refresh(self.store))
        self.now[0] += RETRY_SECONDS
        self.assertEqual(claim_refresh(self.store)["request_id"], request["request_id"])

    def test_obsolete_or_malformed_recheck_never_downloads_a_page(self):
        request = self.request(with_headers=False)
        self.check(notify=False)
        fetcher = mock.Mock()
        self.assertFalse(recheck_headers(self.store, request, fetcher=fetcher))
        for invalid in (None, {**request, "search_id": True}, {**request, "url": []},
                        {**request, "request_id": "short"}, {**request, "unexpected": 1}):
            with self.assertRaises(ValueError):
                recheck_headers(self.store, invalid, fetcher=fetcher)
        fetcher.assert_not_called()

    def test_cookie_failure_queues_refresh_and_rate_limits_claims(self):
        for _ in range(2):
            with self.assertRaises(SearchCookieError):
                check_watch(self.store, self.watch, fetcher=lambda _: "<title>Pardon Our Interruption</title>")
        first = claim_refresh(self.store)
        self.assertEqual(first["search_id"], self.watch["id"])
        request_refresh(self.store, self.watch)
        self.assertIsNone(claim_refresh(self.store))
        self.now[0] += RETRY_SECONDS
        self.assertEqual(claim_refresh(self.store)["request_id"], first["request_id"])

    def test_fifty_failures_stop_recovery_and_alert_once_even_after_restart(self):
        request_refresh(self.store, self.watch)
        alert = mock.Mock()
        for attempt in range(1, MAX_FAILURES + 1):
            request = claim_refresh(self.store)
            self.assertEqual(request["attempt"], attempt)
            notify_exhausted(self.store, alert)
            alert.assert_not_called()
            record_failure(self.store, request)
            record_failure(self.store, request)
            request_refresh(self.store, self.watch)
            notify_exhausted(self.store, alert)
            if attempt < MAX_FAILURES:
                alert.assert_not_called()
                self.assertEqual(retry_status(self.store)["retry_after"], RETRY_SECONDS)
            self.assertIsNone(claim_refresh(self.store))
            if attempt == 25:
                self.store.close()
                self.store = SearchStore(self.db, clock=lambda: self.now[0])
            self.now[0] += RETRY_SECONDS
        alert.assert_called_once()
        self.assertEqual(alert.call_args.args[1], 50)
        self.assertIsNone(retry_status(self.store)["retry_after"])
        request_refresh(self.store, self.watch)
        self.assertIsNone(claim_refresh(self.store))
        notify_exhausted(self.store, alert)
        alert.assert_called_once()
        self.check(notify=False)
        request_refresh(self.store, self.watch)
        self.assertEqual(claim_refresh(self.store)["attempt"], 1)

    def test_expired_attempt_counts_once_and_fences_old_submissions(self):
        first = self.request(with_headers=False)
        self.now[0] += RETRY_SECONDS * 10
        second = claim_refresh(self.store)
        self.assertEqual(second["attempt"], 2)
        record_failure(self.store, first)
        with self.assertRaisesRegex(ValueError, "no longer current"):
            install_headers(self.store, {**first, "headers": "User-Agent: new\nCookie: new=x\n"}, self.headers)
        record_failure(self.store, second)
        self.now[0] += RETRY_SECONDS
        self.assertEqual(claim_refresh(self.store)["attempt"], 3)

    def test_attempt_expiring_during_validation_cannot_replace_headers(self):
        request = self.request()
        original = self.headers.read_bytes()

        def delayed_page(*_args, **_kwargs):
            self.now[0] += RETRY_SECONDS
            return EBAY_PAGE

        with mock.patch("pi.scripts.price_check.search_watch.refresh_install.fetch_ebay", side_effect=delayed_page):
            with self.assertRaisesRegex(ValueError, "existing headers preserved"):
                install_headers(self.store, request, self.headers)
        self.assertEqual(self.headers.read_bytes(), original)
        self.assertEqual(list(self.root.glob(".ebay-refresh-*")), [])

    def test_interval_is_measured_from_attempt_start(self):
        request = self.request(with_headers=False)
        self.now[0] += 80
        record_failure(self.store, request)
        self.assertEqual(retry_status(self.store)["retry_after"], RETRY_SECONDS - 80)
        self.assertIsNone(claim_refresh(self.store))
        self.now[0] += RETRY_SECONDS - 80
        self.assertEqual(claim_refresh(self.store)["attempt"], 2)

    def test_expired_fiftieth_attempt_alerts_and_retries_failed_notification_only(self):
        request_refresh(self.store, self.watch)
        for _ in range(MAX_FAILURES - 1):
            record_failure(self.store, claim_refresh(self.store))
            self.now[0] += RETRY_SECONDS
        last = claim_refresh(self.store)
        self.assertEqual(last["attempt"], MAX_FAILURES)
        alert = mock.Mock(side_effect=[RuntimeError("offline"), None])
        notify_exhausted(self.store, alert)
        alert.assert_not_called()
        self.now[0] += RETRY_SECONDS
        self.assertIsNone(claim_refresh(self.store))
        notify_exhausted(self.store, alert)
        self.assertEqual(alert.call_count, 1)
        self.assertEqual(retry_status(self.store)["retry_after"], RETRY_SECONDS)
        notify_exhausted(self.store, alert)
        self.assertEqual(alert.call_count, 1)
        self.now[0] += RETRY_SECONDS
        notify_exhausted(self.store, alert)
        self.assertEqual(alert.call_count, 2)
        self.assertIsNone(retry_status(self.store)["retry_after"])
        self.assertIsNone(claim_refresh(self.store))

    def test_explicit_restart_invalidates_old_attempt(self):
        old = self.request(with_headers=False)
        request_refresh(self.store, self.watch, restart=True)
        new = claim_refresh(self.store)
        self.assertNotEqual(new["request_id"], old["request_id"])
        record_failure(self.store, old)
        record_failure(self.store, new)
        self.now[0] += RETRY_SECONDS
        self.assertEqual(claim_refresh(self.store)["attempt"], 2)

    def test_migrates_old_queue_without_inventing_failures(self):
        self.store.close()
        with closing(sqlite3.connect(self.db)) as connection, connection:
            connection.execute("DROP TABLE search_browser_refresh")
            connection.execute("""CREATE TABLE search_browser_refresh (
                search_id INTEGER PRIMARY KEY REFERENCES search_watches(id) ON DELETE CASCADE,
                request_id TEXT NOT NULL, requested_at INTEGER NOT NULL,
                next_attempt_at INTEGER NOT NULL DEFAULT 0)""")
            connection.execute("INSERT INTO search_browser_refresh VALUES (?, ?, ?, ?)",
                               (self.watch["id"], "c" * 32, self.now[0], self.now[0] + 1800))
        self.store = SearchStore(self.db, clock=lambda: self.now[0])
        self.assertEqual(retry_status(self.store)["retry_after"], RETRY_SECONDS)
        self.now[0] += RETRY_SECONDS
        request = claim_refresh(self.store)
        self.assertEqual((request["request_id"], request["attempt"]), ("c" * 32, 1))

    def test_bridge_reports_fiftieth_failure_through_ntfy_only_once(self):
        request_refresh(self.store, self.watch)
        for _ in range(MAX_FAILURES - 1):
            record_failure(self.store, claim_refresh(self.store))
            self.now[0] += RETRY_SECONDS
        last = claim_refresh(self.store)
        sender = self.root / "ntfy-stub"
        sender.write_text('#!/bin/sh\nprintf "%s\\n" "$1" >> "$ALERT_LOG"\n')
        sender.chmod(0o700)
        log = self.root / "alerts"
        bridge = Path(__file__).parents[2] / "scripts/price_check/ebay_refresh.py"
        env = {**os.environ, "NTFY_SEND_BIN": str(sender), "ALERT_LOG": str(log)}
        for _ in range(2):
            result = subprocess.run(
                [sys.executable, str(bridge), "--db", str(self.db), "failed"],
                input=json.dumps(last), capture_output=True, text=True, env=env, check=True,
            )
            self.assertTrue(json.loads(result.stdout)["ok"])
        self.assertEqual(len(log.read_text().splitlines()), 1)
        self.assertIn("50", log.read_text())
        status = subprocess.run(
            [sys.executable, str(bridge), "--db", str(self.db), "status"],
            capture_output=True, text=True, env=env, check=True,
        )
        self.assertIsNone(json.loads(status.stdout)["retry_after"])

    def test_parser_error_queues_a_recheck_but_dry_run_does_not(self):
        with self.assertRaises(SearchCookieError):
            check_watch(self.store, self.watch, record=False,
                        fetcher=lambda _: "<title>Pardon Our Interruption</title>")
        self.assertIsNone(claim_refresh(self.store))
        with self.assertRaises(SearchParserError):
            check_watch(self.store, self.watch, fetcher=lambda _: "unknown markup")
        self.assertEqual(claim_refresh(self.store)["attempt"], 1)

    def test_successful_check_cancels_unneeded_refresh(self):
        self.request()
        self.check(notify=False)
        self.assertIsNone(claim_refresh(self.store))

    @mock.patch("pi.scripts.price_check.search_watch.refresh_install.fetch_ebay", return_value=EBAY_PAGE)
    def test_install_validates_on_pi_then_atomically_replaces_headers(self, fetch):
        payload = self.request()
        self.assertEqual(install_headers(self.store, payload, self.headers), EBAY_PAGE)
        self.assertEqual(self.headers.read_text(), payload["headers"])
        self.assertEqual(self.headers.stat().st_mode & 0o777, 0o600)
        self.assertIsNone(claim_refresh(self.store))
        self.assertNotEqual(fetch.call_args.kwargs["headers_path"], self.headers)

    @mock.patch("pi.scripts.price_check.search_watch.refresh_install.fetch_ebay", return_value="<title>Pardon Our Interruption</title>")
    def test_rejected_candidate_keeps_existing_headers_and_pending_request(self, _fetch):
        payload = self.request()
        original = self.headers.read_text()
        with self.assertRaisesRegex(ValueError, "existing headers preserved"):
            install_headers(self.store, payload, self.headers)
        self.assertEqual(self.headers.read_text(), original)
        self.assertEqual(list(self.root.glob(".ebay-refresh-*")), [])
        self.now[0] += RETRY_SECONDS
        self.assertEqual(claim_refresh(self.store)["request_id"], payload["request_id"])

    def test_stale_request_cannot_replace_headers(self):
        payload = self.request()
        self.check(notify=False)
        with self.assertRaisesRegex(ValueError, "no longer current"):
            install_headers(self.store, payload, self.headers)
        self.assertIn("old=value", self.headers.read_text())

    def test_rejects_untrusted_payload_and_extra_header_without_fetching(self):
        payload = self.request()
        payload["headers"] += "Authorization: bad\n"
        with mock.patch("pi.scripts.price_check.search_watch.service.subprocess.run") as run:
            with self.assertRaises(ValueError):
                install_headers(self.store, payload, self.headers)
            run.assert_not_called()
        payload["url"] = "https://example.com/"
        with self.assertRaises(SearchWatchError):
            install_headers(self.store, payload, self.headers)


if __name__ == "__main__":
    unittest.main()
