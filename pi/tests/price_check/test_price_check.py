import importlib.util
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest import mock


SCRIPT_DIR = Path(__file__).parents[2] / "scripts" / "price_check"
SCRIPT = SCRIPT_DIR / "main.py"
sys.path.insert(0, str(SCRIPT_DIR))
SPEC = importlib.util.spec_from_file_location("price_check", SCRIPT)
price_check = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
sys.modules[SPEC.name] = price_check
SPEC.loader.exec_module(price_check)


AMAZON_PAGE = """
  <span id="productTitle">Example &amp; Product</span>
  <div id="corePrice_feature_div">
    <span class="a-price apex-pricetopay-value">
      <span class="a-offscreen">$63.15</span>
    </span>
    <span class="a-price apex-pricetopay-value">
      <span class="a-offscreen">$53.68</span>
    </span>
  </div>
"""


class AmazonParserTests(unittest.TestCase):
    def test_uses_primary_price_not_subscription_price(self):
        product = price_check.parse_amazon(AMAZON_PAGE)
        self.assertEqual(product.title, "Example & Product")
        self.assertEqual(product.price, Decimal("63.15"))

    def test_rejects_bot_check(self):
        with self.assertRaisesRegex(price_check.AmazonParseError, "bot-check"):
            price_check.parse_amazon("<title>Robot Check</title>")


class PriceStoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.db = Path(self.temporary.name) / "private" / "prices.sqlite3"
        self.store = price_check.PriceStore(self.db, clock=lambda: 1_700_000_000)

    def tearDown(self):
        self.store.close()
        self.temporary.cleanup()

    def add(self, **overrides):
        values = {
            "parser": "amazon",
            "threshold": Decimal("55"),
            "url": "https://example.com/item?variant=red",
            "title": "Red item",
            **overrides,
        }
        return self.store.add_item(**values)

    def test_database_and_parent_are_private(self):
        self.assertEqual(self.db.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.db.parent.stat().st_mode & 0o777, 0o700)

    def test_add_list_and_duplicate_url(self):
        item = self.add()
        self.assertEqual(item["threshold"], "55.00")
        self.assertEqual(item["last_status"], "never")
        self.assertEqual(self.store.list_items()[0]["display_title"], "Red item")
        with self.assertRaisesRegex(price_check.StoreError, "already configured"):
            self.add(url="https://EXAMPLE.com/item?variant=red#details")

    def test_remove_by_url_preserves_query_and_ignores_fragment(self):
        red = self.add()
        blue = self.add(
            url="https://example.com/item?variant=blue", title="Blue item"
        )
        removed = self.store.remove_item(
            "https://example.com/item?variant=red#details"
        )
        self.assertEqual(removed["id"], red["id"])
        self.assertEqual([item["id"] for item in self.store.list_items()], [blue["id"]])

    def test_update_preserves_check_state_and_history(self):
        item = self.add()
        self.store.record_success(item["id"], "Scraped title", Decimal("54.99"))
        updated = self.store.update_item(
            item["id"],
            "amazon",
            Decimal("50"),
            "https://example.com/item?variant=green",
            "Green item",
        )
        self.assertEqual(updated["threshold"], "50.00")
        self.assertEqual(updated["url"], "https://example.com/item?variant=green")
        self.assertEqual(updated["display_title"], "Green item")
        self.assertEqual(updated["last_price"], "54.99")
        self.assertEqual(updated["last_status"], "ok")
        count = self.store.connection.execute("SELECT count(*) FROM checks").fetchone()[0]
        self.assertEqual(count, 1)

    def test_update_rejects_duplicate_url(self):
        first = self.add()
        self.add(url="https://example.com/other", title="Other")
        with self.assertRaisesRegex(price_check.StoreError, "already configured"):
            self.store.update_item(
                first["id"],
                "amazon",
                Decimal("45"),
                "https://example.com/other#details",
                "Duplicate",
            )

    def test_records_success_error_and_last_scraped_title(self):
        item = self.add(title=None)
        updated = self.store.record_success(
            item["id"], "Scraped title", Decimal("54.99")
        )
        self.assertEqual(updated["last_price"], "54.99")
        self.assertEqual(updated["last_price_checked_at"], 1_700_000_000)
        self.assertTrue(updated["below_threshold"])
        self.assertEqual(updated["display_title"], "Scraped title")
        failed = self.store.record_error(item["id"], "HTML changed")
        self.assertEqual(failed["last_status"], "error")
        self.assertEqual(failed["last_price"], "54.99")
        self.assertEqual(failed["last_price_checked_at"], 1_700_000_000)
        count = self.store.connection.execute("SELECT count(*) FROM checks").fetchone()[0]
        self.assertEqual(count, 2)

    def test_mutes_notifications_until_deadline_and_can_unmute(self):
        now = [1_700_000_000]
        self.store.clock = lambda: now[0]
        item = self.add()
        muted = self.store.set_notification_mute(item["id"], 3)
        self.assertTrue(muted["notifications_muted"])
        self.assertEqual(muted["notify_muted_until"], now[0] + 3 * 86400)
        now[0] += 3 * 86400
        self.assertFalse(self.store.get_item(item["id"])["notifications_muted"])
        unmuted = self.store.set_notification_mute(item["id"], 0)
        self.assertIsNone(unmuted["notify_muted_until"])

    def test_adds_mute_and_price_timestamp_columns_to_existing_database(self):
        legacy_db = Path(self.temporary.name) / "legacy.sqlite3"
        connection = sqlite3.connect(legacy_db)
        connection.executescript(
            """
            CREATE TABLE items (
                id INTEGER PRIMARY KEY, parser TEXT, threshold_cents INTEGER,
                url TEXT, normalized_url TEXT UNIQUE, title TEXT,
                created_at INTEGER, updated_at INTEGER, last_checked_at INTEGER,
                last_price_cents INTEGER, last_status TEXT, last_error TEXT,
                last_title TEXT
            );
            CREATE TABLE checks (
                id INTEGER PRIMARY KEY, item_id INTEGER, checked_at INTEGER,
                status TEXT, price_cents INTEGER, title TEXT, error TEXT
            );
            INSERT INTO items VALUES (
                1, 'amazon', 5500, 'https://example.com/item',
                'https://example.com/item', 'Example', 10, 30, 30, 5499,
                'error', 'changed', 'Example'
            );
            INSERT INTO checks(item_id, checked_at, status, price_cents, title)
            VALUES (1, 20, 'ok', 5499, 'Example');
            INSERT INTO checks(item_id, checked_at, status, error)
            VALUES (1, 30, 'error', 'changed');
            """
        )
        connection.close()
        with price_check.PriceStore(legacy_db, clock=lambda: 40) as migrated:
            item = migrated.get_item(1)
            self.assertEqual(item["last_price_checked_at"], 20)
            self.assertFalse(item["notifications_muted"])
            self.assertIsNone(item["notify_muted_until"])

    @mock.patch.object(price_check, "send_ntfy")
    @mock.patch.object(price_check, "fetch", return_value=AMAZON_PAGE)
    def test_check_updates_database_and_notifies_below_threshold(self, _fetch, send):
        item = self.add(threshold=Decimal("64"))
        updated = price_check.check_item(self.store, item)
        self.assertEqual(updated["last_price"], "63.15")
        send.assert_called_once()

    @mock.patch.object(price_check, "send_ntfy")
    @mock.patch.object(price_check, "fetch", return_value=AMAZON_PAGE)
    def test_muted_item_records_price_without_notification(self, _fetch, send):
        item = self.add(threshold=Decimal("64"))
        muted = self.store.set_notification_mute(item["id"], 2)
        updated = price_check.check_item(self.store, muted)
        self.assertEqual(updated["last_price"], "63.15")
        self.assertTrue(updated["notifications_muted"])
        send.assert_not_called()

    @mock.patch.object(price_check, "send_parser_error")
    @mock.patch.object(price_check, "fetch", return_value="<html>changed</html>")
    def test_parser_failure_is_recorded_and_notified(self, _fetch, send_error):
        item = self.add()
        with self.assertRaisesRegex(price_check.PriceCheckError, "section was not found"):
            price_check.check_item(self.store, item)
        self.assertEqual(self.store.get_item(item["id"])["last_status"], "error")
        send_error.assert_called_once()

    @mock.patch.object(price_check, "send_parser_error")
    @mock.patch.object(price_check, "fetch", return_value="<html>changed</html>")
    def test_muted_parser_failure_is_recorded_without_notification(
        self, _fetch, send_error
    ):
        item = self.add()
        muted = self.store.set_notification_mute(item["id"], 2)
        with self.assertRaisesRegex(price_check.PriceCheckError, "section was not found"):
            price_check.check_item(self.store, muted)
        self.assertEqual(self.store.get_item(item["id"])["last_status"], "error")
        send_error.assert_not_called()

    @mock.patch.object(price_check, "fetch", return_value=AMAZON_PAGE)
    def test_dry_check_does_not_change_database(self, _fetch):
        item = self.add()
        checked, errors = price_check.check_items(
            self.store, [item], notify=False, record=False
        )
        self.assertEqual(errors, [])
        self.assertEqual(checked[0]["last_price"], "63.15")
        self.assertEqual(self.store.get_item(item["id"])["last_status"], "never")

    def test_migrates_old_tsv_without_disabled_rows(self):
        path = Path(self.temporary.name) / "price_checks.tsv"
        path.write_text(
            "amazon\t55\thttps://example.com/base\tBase item\n"
            "amazon\t40\thttps://example.com/disabled\tDisabled item\n",
            encoding="utf-8",
        )
        path.with_name("price_checks.local.tsv").write_text(
            "amazon\t35\thttps://example.com/local\tLocal item\n",
            encoding="utf-8",
        )
        path.with_name("price_checks.disabled").write_text(
            "https://example.com/disabled\n", encoding="utf-8"
        )
        count = price_check.migrate_tsv(self.store, path)
        self.assertEqual(count, 2)
        self.assertEqual(
            [item["display_title"] for item in self.store.list_items()],
            ["Base item", "Local item"],
        )


class CliTests(unittest.TestCase):
    def run_cli(self, *arguments):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "prices.sqlite3"
            with mock.patch.object(sys, "argv", [str(SCRIPT), "--db", str(db), *arguments]):
                with mock.patch("builtins.print") as output:
                    status = price_check.main()
            return status, output

    def test_json_add_outputs_dashboard_schema(self):
        status, output = self.run_cli(
            "--json", "add", "amazon", "55", "https://example.com/item", "Example"
        )
        self.assertEqual(status, 0)
        payload = json.loads(output.call_args.args[0])
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["item"]["display_title"], "Example")

    def test_json_edit_updates_existing_item(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "prices.sqlite3"
            with price_check.PriceStore(db) as store:
                item = store.add_item(
                    "amazon", Decimal("55"), "https://example.com/item", "Example"
                )
            arguments = [
                str(SCRIPT),
                "--db",
                str(db),
                "--json",
                "edit",
                str(item["id"]),
                "amazon",
                "45",
                "https://example.com/updated",
                "Updated",
            ]
            with mock.patch.object(sys, "argv", arguments):
                with mock.patch("builtins.print") as output:
                    status = price_check.main()
            payload = json.loads(output.call_args.args[0])
            self.assertEqual(status, 0)
            self.assertEqual(payload["item"]["display_title"], "Updated")
            self.assertEqual(payload["item"]["threshold"], "45.00")

    def test_json_mute_updates_item(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "prices.sqlite3"
            with price_check.PriceStore(db, clock=lambda: 1_700_000_000) as store:
                item = store.add_item(
                    "amazon", Decimal("55"), "https://example.com/item", "Example"
                )
            arguments = [
                str(SCRIPT),
                "--db",
                str(db),
                "--json",
                "mute",
                str(item["id"]),
                "7",
            ]
            with mock.patch.object(sys, "argv", arguments):
                with mock.patch("builtins.print") as output:
                    status = price_check.main()
            payload = json.loads(output.call_args.args[0])
            self.assertEqual(status, 0)
            self.assertTrue(payload["item"]["notifications_muted"])
            self.assertIsNotNone(payload["item"]["notify_muted_until"])


if __name__ == "__main__":
    unittest.main()
