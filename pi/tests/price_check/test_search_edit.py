"""Saved-search edits use the CLI and retain identity/history in a real database."""

import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

from pi.scripts.price_check.search_watch.browser_refresh import claim_refresh, request_refresh
from pi.scripts.price_check.search_watch.parsers import SearchResult
from pi.scripts.price_check.search_watch.service import SearchLoadError, check_watch
from pi.scripts.price_check.search_watch.store import SearchStore, SearchStoreError
from pi.tests.price_check.test_search_watch import EBAY_PAGE


TOOL = Path(__file__).resolve().parents[2] / "scripts/price_check/main.py"
URL = "https://www.ebay.com/sch/i.html?_nkw=micropod"
NEW_URL = "https://www.ebay.com/sch/i.html?_nkw=diagnostic&_udhi=500"


class SearchEditTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.db = Path(self.temporary.name) / "private/prices.sqlite3"
        self.run_cli("search-add", "ebay", URL, "MicroPod")
        self.store = SearchStore(self.db)
        self.addCleanup(self.store.close)
        self.watch = self.store.list_watches()[0]
        self.watch_id = str(self.watch["id"])
        self.first = SearchResult("123456789012", "First", "https://www.ebay.com/itm/123456789012", "$499")
        self.second = SearchResult("234567890123", "Second", "https://www.ebay.com/itm/234567890123", "$525")
        self.store.record_success(self.watch["id"], [self.first, self.second])
        self.store.dismiss_result(self.watch["id"], self.first.item_id)

    def run_cli(self, *args, ok=True):
        result = subprocess.run(
            [sys.executable, str(TOOL), "--db", str(self.db), "--json", *args],
            text=True, capture_output=True, timeout=10,
        )
        payload = json.loads(result.stdout)
        self.assertEqual(payload["ok"], ok, payload)
        self.assertEqual(result.returncode, 0 if ok else 1, result.stderr)
        return payload

    def test_title_and_query_edits_preserve_history_dismissals_and_schedule(self):
        schedule = self.db.with_suffix(".schedule-description.json")
        schedule.write_text('{"expression":"0 10,15,20 * * *","description":"saved"}')
        schedule_before = schedule.read_bytes()
        before = self.store.get_watch(self.watch["id"])
        renamed = self.run_cli("search-edit", self.watch_id, "ebay", URL, "Updated title")["search"]
        self.assertEqual(renamed["id"], before["id"])
        for field in ("created_at", "last_checked_at", "results", "dismissed_count", "known_count"):
            self.assertEqual(renamed[field], before[field])
        self.assertEqual(renamed["title"], "Updated title")
        request_refresh(self.store, renamed)
        self.assertIsNotNone(claim_refresh(self.store))
        changed = self.run_cli("search-edit", self.watch_id, "ebay", NEW_URL, "New query")["search"]
        self.assertEqual(changed["id"], before["id"])
        self.assertEqual(changed["results"], [])
        self.assertEqual(changed["last_status"], "never")
        self.assertEqual((changed["known_count"], changed["dismissed_count"]), (2, 1))
        self.assertIsNone(claim_refresh(self.store))
        with sqlite3.connect(self.db) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM search_checks").fetchone()[0], 1)
        refreshed, new = self.store.record_success(changed["id"], [self.first, self.second])
        self.assertEqual(new, [])
        self.assertEqual([item["item_id"] for item in refreshed["results"]], [self.second.item_id])
        self.assertEqual(schedule.read_bytes(), schedule_before)

    def test_duplicate_invalid_and_missing_edits_leave_configuration_intact(self):
        self.run_cli("search-add", "ebay", NEW_URL, "Other")
        before = self.store.get_watch(self.watch["id"])
        for url in (NEW_URL, "https://example.com/search", "https://www.ebay.com/sch/i.html"):
            with self.subTest(url=url):
                self.run_cli("search-edit", self.watch_id, "ebay", url, "Bad change", ok=False)
                self.assertEqual(self.store.get_watch(self.watch["id"]), before)
        self.run_cli("search-edit", "999", "ebay", URL, "Missing", ok=False)
        self.assertEqual(len(self.store.list_watches()), 2)

    def test_inflight_old_query_cannot_publish_results_or_errors_after_edit(self):
        for failed in (False, True):
            with self.subTest(failed=failed):
                self.run_cli("search-edit", self.watch_id, "ebay", URL, "Original")
                watch = self.store.get_watch(self.watch["id"])

                def fetcher(_url):
                    self.run_cli("search-edit", self.watch_id, "ebay", NEW_URL, "Edited")
                    if failed:
                        raise SearchLoadError("Old request failed")
                    return EBAY_PAGE

                with self.assertRaisesRegex(SearchStoreError, "changed or was removed"):
                    check_watch(self.store, watch, fetcher=fetcher, notify=False)
                current = self.store.get_watch(self.watch["id"])
                self.assertEqual(current["url"], NEW_URL)
                self.assertEqual(current["last_status"], "never")
                self.assertEqual(current["results"], [])
                self.assertIsNone(claim_refresh(self.store))


if __name__ == "__main__":
    unittest.main()
