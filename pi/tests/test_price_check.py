import importlib.util
import sys
import tempfile
import unittest
from unittest import mock
from decimal import Decimal
from pathlib import Path


SCRIPT_DIR = Path(__file__).parents[1] / "scripts" / "price_check"
SCRIPT = SCRIPT_DIR / "main.py"
sys.path.insert(0, str(SCRIPT_DIR))
SPEC = importlib.util.spec_from_file_location("price_check", SCRIPT)
price_check = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
sys.modules[SPEC.name] = price_check
SPEC.loader.exec_module(price_check)


class PriceCheckTests(unittest.TestCase):
    def test_amazon_uses_primary_price_not_subscription_price(self):
        page = """
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
        product = price_check.parse_amazon(page)
        self.assertEqual(product.title, "Example & Product")
        self.assertEqual(product.price, Decimal("63.15"))

    def test_amazon_rejects_bot_check(self):
        with self.assertRaisesRegex(price_check.AmazonParseError, "bot-check"):
            price_check.parse_amazon("<title>Robot Check</title>")

    @mock.patch.object(price_check, "send_parser_error")
    @mock.patch.object(price_check, "fetch", return_value="<html>changed</html>")
    def test_parse_failure_sends_maintenance_alert(self, _fetch, send_error):
        listing = price_check.Listing(
            "amazon", Decimal("55"), "https://www.amazon.com/dp/example"
        )
        with self.assertRaisesRegex(price_check.PriceCheckError, "section was not found"):
            price_check.check_listing(listing)
        send_error.assert_called_once()

    @mock.patch.object(price_check, "send_parser_error")
    @mock.patch.object(price_check, "fetch", return_value="<html>changed</html>")
    def test_dry_run_parse_failure_does_not_notify(self, _fetch, send_error):
        listing = price_check.Listing(
            "amazon", Decimal("55"), "https://www.amazon.com/dp/example"
        )
        with self.assertRaises(price_check.PriceCheckError):
            price_check.check_listing(listing, dry_run=True)
        send_error.assert_not_called()

    def test_reads_three_column_config(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "prices.tsv"
            path.write_text(
                "# parser threshold URL\n"
                "amazon\t55\thttps://www.amazon.com/dp/example\n",
                encoding="utf-8",
            )
            listings = price_check.read_config(path)
        self.assertEqual(
            listings,
            [
                price_check.Listing(
                    "amazon", Decimal("55"), "https://www.amazon.com/dp/example"
                )
            ],
        )

    def test_reads_optional_fourth_column_title(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "prices.tsv"
            path.write_text(
                "amazon\t55\thttps://www.amazon.com/dp/example\tProtein shakes\n",
                encoding="utf-8",
            )
            listings = price_check.read_config(path)
        self.assertEqual(listings[0].title, "Protein shakes")

    def test_title_cache_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state" / "titles.json"
            expected = {"https://example.com/item": "Example item"}
            price_check.save_title_cache(path, expected)
            self.assertEqual(price_check.load_title_cache(path), expected)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_reads_local_config_overlay(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory) / "price_checks.tsv"
            local = Path(directory) / "price_checks.local.tsv"
            base.write_text(
                "amazon\t55\thttps://example.com/base\tBase item\n",
                encoding="utf-8",
            )
            local.write_text(
                "amazon\t40\thttps://example.com/local\tLocal item\n",
                encoding="utf-8",
            )
            listings = price_check.read_configs(base)
        self.assertEqual([listing.title for listing in listings], ["Base item", "Local item"])

    def test_remove_base_by_url_preserves_query_and_ignores_fragment(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory) / "price_checks.tsv"
            base.write_text(
                "amazon\t55\thttps://example.com/item?variant=red\tRed item\n"
                "amazon\t55\thttps://example.com/item?variant=blue\tBlue item\n",
                encoding="utf-8",
            )
            removed = price_check.remove_listing(
                base, {}, "https://example.com/item?variant=red#details"
            )
            remaining = price_check.read_configs(base)
        self.assertEqual(removed, "Red item")
        self.assertEqual([item.title for item in remaining], ["Blue item"])

    def test_remove_local_by_cached_title(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory) / "price_checks.tsv"
            local = Path(directory) / "price_checks.local.tsv"
            base.write_text(
                "amazon\t55\thttps://example.com/base\tBase item\n",
                encoding="utf-8",
            )
            local.write_text(
                "amazon\t40\thttps://example.com/local\n",
                encoding="utf-8",
            )
            removed = price_check.remove_listing(
                base, {"https://example.com/local": "Cached item"}, "Cached item"
            )
        self.assertEqual(removed, "Cached item")
        self.assertFalse(local.exists())


if __name__ == "__main__":
    unittest.main()
