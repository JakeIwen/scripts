"""Fetch and validate Haveno order books and XMR reference prices."""

from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation


PRICE_URL = "https://price.haveno.network/getAllMarketPrices"
KRAKEN_URL = "https://api.kraken.com/0/public/Ticker?pair=XXMRZUSD"
DEPTH_URL = "https://haveno.markets/api/v1/depth/XMR_{}?level=2&network={}"
MAX_PRICE_AGE_SECONDS = 15 * 60


class HavenoSourceError(RuntimeError):
    pass


@dataclass(frozen=True)
class HavenoOffer:
    offer_id: str
    offered_at: int
    max_xmr: Decimal
    min_xmr: Decimal
    price: Decimal
    payment_method: str


@dataclass(frozen=True)
class HavenoMarket:
    currency: str
    network: str
    reference_price: Decimal
    reference_source: str
    offers: tuple[HavenoOffer, ...]


def _loads(payload: str, label: str):
    try:
        return json.loads(
            payload,
            parse_float=Decimal,
            parse_int=Decimal,
            parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)),
        )
    except (json.JSONDecodeError, ValueError) as error:
        raise HavenoSourceError(f"{label} returned invalid JSON") from error


def _decimal(value, label: str) -> Decimal:
    if isinstance(value, bool):
        raise HavenoSourceError(f"{label} is invalid")
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as error:
        raise HavenoSourceError(f"{label} is invalid") from error
    if not result.is_finite() or result <= 0:
        raise HavenoSourceError(f"{label} must be finite and positive")
    return result


def _download(fetcher, url: str, label: str) -> str:
    try:
        return fetcher(url)
    except Exception as error:
        raise HavenoSourceError(f"{label} download failed: {error}") from error


def _primary_reference(fetcher, currency: str, now: float) -> Decimal:
    document = _loads(_download(fetcher, PRICE_URL, "Haveno price node"), "Haveno price node")
    if not isinstance(document, dict) or not isinstance(document.get("data"), list):
        raise HavenoSourceError("Haveno price node response is missing data")
    for entry in document["data"]:
        if not isinstance(entry, dict):
            continue
        if entry.get("baseCurrencyCode") != "XMR" or entry.get("counterCurrencyCode") != currency:
            continue
        price = _decimal(entry.get("price"), f"XMR/{currency} reference price")
        timestamp_ms = _decimal(entry.get("timestampMs"), "reference timestamp")
        timestamp = float(timestamp_ms / 1000)
        if not math.isfinite(timestamp) or now - timestamp > MAX_PRICE_AGE_SECONDS:
            raise HavenoSourceError(f"XMR/{currency} reference price is stale")
        return price
    raise HavenoSourceError(f"Haveno price node has no XMR/{currency} reference price")


def _kraken_reference(fetcher) -> Decimal:
    document = _loads(_download(fetcher, KRAKEN_URL, "Kraken"), "Kraken")
    if not isinstance(document, dict) or document.get("error") != []:
        raise HavenoSourceError("Kraken returned an error")
    try:
        ticker = document["result"]["XXMRZUSD"]
        ask = _decimal(ticker["a"][0], "Kraken ask")
        bid = _decimal(ticker["b"][0], "Kraken bid")
    except (KeyError, IndexError, TypeError) as error:
        raise HavenoSourceError("Kraken response is missing XMR/USD quotes") from error
    return (ask + bid) / 2


def fetch_reference_price(fetcher, currency: str, *, now: float | None = None) -> tuple[Decimal, str]:
    checked_at = time.time() if now is None else now
    try:
        return _primary_reference(fetcher, currency, checked_at), "haveno-pricenode"
    except HavenoSourceError as primary_error:
        if currency != "USD":
            raise primary_error
        try:
            return _kraken_reference(fetcher), "kraken"
        except HavenoSourceError as fallback_error:
            raise HavenoSourceError(
                f"reference price unavailable: {primary_error}; {fallback_error}"
            ) from fallback_error


def _offer(entry, position: int) -> HavenoOffer:
    if not isinstance(entry, dict):
        raise HavenoSourceError(f"Haveno ask {position} is not an object")
    offer_id = entry.get("offer_id")
    payment_method = entry.get("payment_method")
    offered_ms = _decimal(entry.get("offer_date"), f"Haveno ask {position} offer_date")
    if not isinstance(offer_id, str) or not offer_id:
        raise HavenoSourceError(f"Haveno ask {position} has an invalid offer_id")
    if not isinstance(payment_method, str) or not payment_method:
        raise HavenoSourceError(f"Haveno ask {position} has an invalid payment_method")
    minimum = _decimal(entry.get("min_amount"), f"Haveno ask {position} min_amount")
    maximum = _decimal(entry.get("amount"), f"Haveno ask {position} amount")
    if minimum > maximum:
        raise HavenoSourceError(f"Haveno ask {position} has min_amount above amount")
    return HavenoOffer(
        offer_id=offer_id,
        offered_at=int(offered_ms / 1000),
        max_xmr=maximum,
        min_xmr=minimum,
        price=_decimal(entry.get("price"), f"Haveno ask {position} price"),
        payment_method=payment_method,
    )


def fetch_order_book(fetcher, currency: str, network: str = "reto") -> tuple[HavenoOffer, ...]:
    url = DEPTH_URL.format(currency, network)
    document = _loads(_download(fetcher, url, "Haveno order book"), "Haveno order book")
    if not isinstance(document, dict) or not isinstance(document.get("asks"), list):
        raise HavenoSourceError("Haveno order book is missing an asks list")
    offers = tuple(_offer(entry, position) for position, entry in enumerate(document["asks"], 1))
    if len({offer.offer_id for offer in offers}) != len(offers):
        raise HavenoSourceError("Haveno order book contains duplicate offer IDs")
    return offers


def fetch_market(fetcher, currency: str, network: str = "reto", *, now: float | None = None) -> HavenoMarket:
    reference, source = fetch_reference_price(fetcher, currency, now=now)
    return HavenoMarket(
        currency=currency,
        network=network,
        reference_price=reference,
        reference_source=source,
        offers=fetch_order_book(fetcher, currency, network),
    )
