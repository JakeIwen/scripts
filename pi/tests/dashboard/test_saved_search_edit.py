"""Exercise saved-search editing through Flask, the controller and the real CLI."""

from pathlib import Path
import subprocess
import tempfile
import unittest

from pi.apps.van_dashboard import create_app, runtime
from pi.apps.van_dashboard.van_dashboard_integrations import PriceCheckController
from pi.scripts.price_check.search_watch.store import SearchStore


class SavedSearchEditApiTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.db = Path(temporary.name) / "private/prices.sqlite3"
        self.original = runtime.price_checks
        self.addCleanup(setattr, runtime, "price_checks", self.original)
        root = Path(__file__).resolve().parents[3]
        runtime.price_checks = PriceCheckController(
            tool=str(root / "pi/scripts/price_check/main.py"),
            database=str(self.db),
            command=lambda argv, timeout: subprocess.run(
                argv, capture_output=True, text=True, timeout=timeout,
            ),
        )
        with SearchStore(self.db) as store:
            watch = store.add_watch("ebay", "https://www.ebay.com/sch/i.html?_nkw=old", "Old")
        self.form = {
            "id": str(watch["id"]), "parser": "ebay",
            "url": "https://www.ebay.com/sch/i.html?_nkw=new", "title": "New",
        }
        self.client = create_app().test_client()

    def test_edit_preserves_id_and_reports_saved_title(self):
        response = self.client.post(
            "/api/price-checks/searches/edit", data=self.form,
            headers={"X-Van-Dashboard": "1"},
        )
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(response.json["message"], "Updated New")
        self.assertEqual(response.json["search"]["id"], int(self.form["id"]))
        with SearchStore(self.db) as store:
            self.assertEqual(store.get_watch(int(self.form["id"]))["url"], self.form["url"])

    def test_edit_requires_dashboard_header_and_valid_exact_form(self):
        denied = self.client.post(
            "/api/price-checks/searches/edit", data=self.form,
            headers={"Origin": "http://localhost"},
        )
        self.assertEqual(denied.status_code, 403)
        for data in ({**self.form, "id": "0"}, {**self.form, "extra": "unexpected"},
                     {**self.form, "url": "https://example.com/"}):
            response = self.client.post(
                "/api/price-checks/searches/edit", data=data,
                headers={"X-Van-Dashboard": "1"},
            )
            self.assertEqual(response.status_code, 400, response.json)
        with SearchStore(self.db) as store:
            self.assertEqual(store.get_watch(int(self.form["id"]))["title"], "Old")


if __name__ == "__main__":
    unittest.main()
