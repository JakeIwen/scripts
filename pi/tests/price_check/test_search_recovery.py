import tempfile
import unittest
from pathlib import Path
from unittest import mock

from pi.scripts.price_check.search_watch.browser_refresh import (
    RETRY_SECONDS, claim_refresh, request_refresh,
)
from pi.scripts.price_check.search_watch.refresh_install import install_headers
from pi.scripts.price_check.search_watch.service import (
    SearchCookieError, SearchParserError, SearchWatchError, check_watch,
)
from pi.scripts.price_check.search_watch.store import SearchStore
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

    def request(self):
        request_refresh(self.store, self.watch)
        return {**claim_refresh(self.store), "headers": "User-Agent: new\nCookie: new=value\n"}

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

    def test_parser_error_and_dry_run_do_not_queue_refresh(self):
        with self.assertRaises(SearchParserError):
            check_watch(self.store, self.watch, fetcher=lambda _: "unknown markup")
        self.assertIsNone(claim_refresh(self.store))
        with self.assertRaises(SearchCookieError):
            check_watch(self.store, self.watch, record=False,
                        fetcher=lambda _: "<title>Pardon Our Interruption</title>")
        self.assertIsNone(claim_refresh(self.store))

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
