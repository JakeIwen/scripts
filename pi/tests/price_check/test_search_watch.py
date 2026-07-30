import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from pi.scripts.price_check.search_watch.parsers.ebay_parser import (
    EbayGateError,
    EbayParseError,
    SearchResult,
    item_id_from_url,
    parse,
)
from pi.scripts.price_check.search_watch.service import (
    SearchCookieError,
    SearchLoadError,
    SearchParserError,
    SearchWatchError,
    check_watch,
    fetch_ebay,
    validate_watch,
)
from pi.scripts.price_check.search_watch.store import SearchStore


EBAY_PAGE = """
<html><body><ul class="srp-results">
  <li class="s-item">
    <a class="s-item__link"
       href="https://www.ebay.com/itm/MicroPod-tool/123456789012?hash=abc">
      <div class="s-item__title"><span>New Listing</span> MicroPod II Tool</div>
    </a>
    <span class="s-item__price">US $499.00</span>
    <span class="s-item__shipping">Free shipping</span>
    <img src="https://i.ebayimg.com/example.jpg">
  </li>
  <li class="s-item">
    <a href="https://www.ebay.com/itm/234567890123">
      <div class="s-item__title">Diagnostic Interface</div>
    </a>
    <span class="s-item__price">$525.00</span>
  </li>
</ul>
<h2>Results matching <span>fewer words</span></h2>
<ul class="srp-results"><li class="s-item">
  <a href="https://www.ebay.com/itm/345678901234">
    <div class="s-item__title">Wrong result</div>
  </a>
  <span class="s-item__price">$1.00</span>
</li></ul>
</body></html>
"""


class EbayParserTests(unittest.TestCase):
    def test_extracts_exact_results_and_stops_at_fewer_words(self):
        results = parse(EBAY_PAGE)
        self.assertEqual([result.item_id for result in results], [
            "123456789012",
            "234567890123",
        ])
        self.assertEqual(results[0].title, "MicroPod II Tool")
        self.assertEqual(results[0].price, "US $499.00")
        self.assertEqual(results[0].shipping, "Free shipping")
        self.assertEqual(
            results[0].url, "https://www.ebay.com/itm/123456789012"
        )

    def test_extracts_item_id_from_common_listing_urls(self):
        self.assertEqual(
            item_id_from_url("https://www.ebay.com/itm/title/123456789012?x=1"),
            "123456789012",
        )
        self.assertEqual(
            item_id_from_url("https://www.ebay.com/itm/123456789012"),
            "123456789012",
        )
        self.assertIsNone(item_id_from_url("https://www.ebay.com/sch/i.html"))

    def test_rejects_browser_challenge(self):
        with self.assertRaisesRegex(EbayGateError, "gated page"):
            parse("<title>Pardon Our Interruption</title>")

    def test_rejects_explicit_access_denied_content(self):
        with self.assertRaisesRegex(EbayGateError, "access-denied"):
            parse("<main>Access to this page has been denied</main>")


class SearchStoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.now = [1_700_000_000]
        self.db = Path(self.temporary.name) / "private" / "prices.sqlite3"
        self.store = SearchStore(self.db, clock=lambda: self.now[0])
        self.watch = self.store.add_watch(
            "ebay",
            "https://www.ebay.com/sch/i.html?_nkw=micropod",
            "MicroPod",
        )
        self.first = SearchResult(
            "123456789012",
            "First",
            "https://www.ebay.com/itm/123456789012",
            "$499.00",
        )
        self.second = SearchResult(
            "234567890123",
            "Second",
            "https://www.ebay.com/itm/234567890123",
            "$525.00",
        )

    def tearDown(self):
        self.store.close()
        self.temporary.cleanup()

    def test_database_and_parent_are_private(self):
        self.assertEqual(self.db.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.db.parent.stat().st_mode & 0o777, 0o700)

    def test_new_identity_current_state_and_permanent_dismissal(self):
        watch, new = self.store.record_success(
            self.watch["id"], [self.first, self.second]
        )
        self.assertEqual([result["item_id"] for result in new], [
            self.first.item_id,
            self.second.item_id,
        ])
        self.assertEqual(watch["result_count"], 2)

        watch, dismissed = self.store.dismiss_result(
            self.watch["id"], self.first.item_id
        )
        self.assertTrue(dismissed["dismissed"])
        self.assertEqual(watch["result_count"], 1)

        self.now[0] += 60
        self.store.record_success(self.watch["id"], [self.second])
        self.now[0] += 60
        watch, new = self.store.record_success(
            self.watch["id"], [self.first, self.second]
        )
        self.assertEqual(new, [])
        self.assertEqual(watch["result_count"], 1)
        self.assertEqual(watch["hidden_current_count"], 1)
        self.assertEqual(watch["dismissed_count"], 1)
        self.assertEqual(watch["known_count"], 2)

    def test_error_preserves_last_successful_results(self):
        self.store.record_success(self.watch["id"], [self.first])
        self.now[0] += 60
        failed = self.store.record_error(self.watch["id"], "HTML changed")
        self.assertEqual(failed["last_status"], "error")
        self.assertEqual(failed["last_error"], "HTML changed")
        self.assertEqual(failed["result_count"], 1)


class SearchServiceTests(unittest.TestCase):
    @staticmethod
    def headers_file(directory):
        headers = Path(directory) / ".ebay_headers"
        headers.write_text(
            "User-Agent: Test Browser\nCookie: anonymous=value\n",
            encoding="utf-8",
        )
        headers.chmod(0o600)
        return headers

    def test_validates_ebay_search_and_derives_title(self):
        parser, url, title = validate_watch(
            "ebay", "https://www.ebay.com/sch/i.html?_nkw=micro%20pod"
        )
        self.assertEqual(parser, "ebay")
        self.assertEqual(title, "micro pod")
        self.assertIn("_nkw=", url)

    def test_rejects_non_ebay_host(self):
        with self.assertRaisesRegex(SearchWatchError, "eBay search URL"):
            validate_watch(
                "ebay", "https://example.com/sch/i.html?_nkw=micro%20pod"
            )

    def test_fetch_uses_private_header_file_reference_not_cookie_argv(self):
        with tempfile.TemporaryDirectory() as directory:
            headers = self.headers_file(directory)
            completed = SimpleNamespace(stdout=EBAY_PAGE)
            with mock.patch.dict(
                os.environ, {"EBAY_HEADERS_FILE": str(headers)}, clear=False
            ), mock.patch(
                "pi.scripts.price_check.search_watch.service.subprocess.run",
                return_value=completed,
            ) as run:
                self.assertEqual(fetch_ebay("https://www.ebay.com/sch/i.html?_nkw=x"), EBAY_PAGE)
            argv = run.call_args.args[0]
            self.assertIn(f"@{headers}", argv)
            self.assertNotIn("anonymous=value", " ".join(argv))

    def test_rejects_permissive_header_file(self):
        with tempfile.TemporaryDirectory() as directory:
            headers = self.headers_file(directory)
            headers.chmod(0o644)
            with mock.patch.dict(
                os.environ, {"EBAY_HEADERS_FILE": str(headers)}, clear=False
            ), self.assertRaisesRegex(SearchCookieError, "permissions 600"):
                fetch_ebay("https://www.ebay.com/sch/i.html?_nkw=x")

    def test_http_rejection_requests_cookie_refresh(self):
        with tempfile.TemporaryDirectory() as directory:
            headers = self.headers_file(directory)
            rejected = subprocess.CalledProcessError(
                22,
                ["/usr/bin/curl"],
                stderr="curl: (22) The requested URL returned error: 403",
            )
            with mock.patch.dict(
                os.environ, {"EBAY_HEADERS_FILE": str(headers)}, clear=False
            ), mock.patch(
                "pi.scripts.price_check.search_watch.service.subprocess.run",
                side_effect=rejected,
            ), self.assertRaisesRegex(SearchCookieError, "rejected"):
                fetch_ebay("https://www.ebay.com/sch/i.html?_nkw=x")

    def test_redirect_trap_requests_cookie_refresh(self):
        with tempfile.TemporaryDirectory() as directory:
            headers = self.headers_file(directory)
            rejected = subprocess.CalledProcessError(
                47,
                ["/usr/bin/curl"],
                stderr="curl: (47) Maximum (0) redirects followed",
            )
            with mock.patch.dict(
                os.environ, {"EBAY_HEADERS_FILE": str(headers)}, clear=False
            ), mock.patch(
                "pi.scripts.price_check.search_watch.service.subprocess.run",
                side_effect=rejected,
            ), self.assertRaisesRegex(SearchCookieError, "redirected"):
                fetch_ebay("https://www.ebay.com/sch/i.html?_nkw=x")

    def test_network_failure_is_a_load_error(self):
        with tempfile.TemporaryDirectory() as directory:
            headers = self.headers_file(directory)
            failed = subprocess.CalledProcessError(
                28,
                ["/usr/bin/curl"],
                stderr="curl: (28) Operation timed out",
            )
            with mock.patch.dict(
                os.environ, {"EBAY_HEADERS_FILE": str(headers)}, clear=False
            ), mock.patch(
                "pi.scripts.price_check.search_watch.service.subprocess.run",
                side_effect=failed,
            ), self.assertRaisesRegex(SearchLoadError, "download failed"):
                fetch_ebay("https://www.ebay.com/sch/i.html?_nkw=x")

    def test_gated_and_changed_pages_reach_distinct_notification_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "prices.sqlite3"
            with SearchStore(db) as store:
                watch = store.add_watch(
                    "ebay",
                    "https://www.ebay.com/sch/i.html?_nkw=x",
                    "Example",
                )
                notified = []
                with self.assertRaises(SearchCookieError):
                    check_watch(
                        store,
                        watch,
                        fetcher=lambda _url: "<h1>Verify you are a human</h1>",
                        notify_error=lambda _watch, error: notified.append(error),
                    )
                self.assertIsInstance(notified[-1], SearchCookieError)

                with self.assertRaises(SearchParserError):
                    check_watch(
                        store,
                        store.get_watch(watch["id"]),
                        fetcher=lambda _url: "<html>new result layout</html>",
                        notify_error=lambda _watch, error: notified.append(error),
                    )
                self.assertIsInstance(notified[-1], SearchParserError)
                self.assertEqual(store.get_watch(watch["id"])["last_status"], "error")


if __name__ == "__main__":
    unittest.main()
