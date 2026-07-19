"""Parse prices from Amazon product pages."""

from __future__ import annotations

import html as html_module
import re
from dataclasses import dataclass
from decimal import Decimal


class AmazonParseError(RuntimeError):
    pass


@dataclass(frozen=True)
class Product:
    title: str
    price: Decimal


def _money(text: str) -> Decimal:
    decoded = html_module.unescape(re.sub(r"<[^>]+>", "", text)).strip()
    match = re.search(r"\$?\s*([0-9][0-9,]*(?:\.[0-9]{2})?)", decoded)
    if not match:
        raise AmazonParseError(f"could not interpret price {decoded!r}")
    return Decimal(match.group(1).replace(",", ""))


def parse(page: str) -> Product:
    lowered = page.lower()
    bot_markers = (
        "robot check",
        "enter the characters you see below",
        "automated access",
    )
    if any(marker in lowered for marker in bot_markers):
        raise AmazonParseError("Amazon returned a bot-check page")

    title_match = re.search(
        r"<span[^>]+id=[\"']productTitle[\"'][^>]*>(.*?)</span>",
        page,
        flags=re.IGNORECASE | re.DOTALL,
    )
    title = (
        html_module.unescape(re.sub(r"<[^>]+>", "", title_match.group(1))).strip()
        if title_match
        else "Amazon product"
    )

    core_match = re.search(
        r"<div[^>]+id=[\"']corePrice_feature_div[\"'][^>]*>",
        page,
        flags=re.IGNORECASE,
    )
    if not core_match:
        raise AmazonParseError("Amazon primary price section was not found")
    # The primary purchase row is first in this section. Limiting the search also
    # prevents a later Subscribe & Save or recommendation price from being used.
    primary = page[core_match.end() : core_match.end() + 60_000]
    price_patterns = (
        r"class=[\"'][^\"']*\bapex-pricetopay-value\b[^\"']*[\"'][^>]*>.*?"
        r"class=[\"'][^\"']*\ba-offscreen\b[^\"']*[\"'][^>]*>(.*?)</span>",
        r"id=[\"']twister-plus-price-data-price[\"'][^>]+value=[\"']([^\"']+)",
        r"id=[\"'](?:priceblock_ourprice|priceblock_dealprice)[\"'][^>]*>(.*?)</",
    )
    for pattern in price_patterns:
        price_match = re.search(pattern, primary, flags=re.IGNORECASE | re.DOTALL)
        if price_match:
            return Product(title=title, price=_money(price_match.group(1)))
    raise AmazonParseError("Amazon primary price was not found")
