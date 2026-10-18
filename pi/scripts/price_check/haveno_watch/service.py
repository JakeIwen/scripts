"""Validate, match, record, and notify for Haveno offer watches."""

from __future__ import annotations

import re
import sys
import time
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from .source import HavenoMarket, HavenoSourceError, fetch_market
from .store import HavenoStore, HavenoStoreError, decimal_text, percent_text, price_text, method_title


PAYMENT_METHOD = re.compile(r"^[A-Z][A-Z0-9_]{1,39}$")
CURRENCY = re.compile(r"^[A-Z]{3}$")


class HavenoWatchError(RuntimeError):
    pass


class HavenoNotificationError(HavenoWatchError):
    pass


def _parse_decimal(
    text: str | None,
    label: str,
    *,
    minimum: Decimal = Decimal("0"),
    minimum_inclusive: bool = False,
    maximum: Decimal | None = None,
    places: int,
) -> Decimal | None:
    if text is None or text == "":
        return None
    try:
        value = Decimal(text)
    except (InvalidOperation, ValueError) as error:
        raise HavenoWatchError(f"{label} must be a decimal number") from error
    if not value.is_finite():
        raise HavenoWatchError(f"{label} must be finite")
    below_minimum = value < minimum if minimum_inclusive else value <= minimum
    if below_minimum:
        comparison = "at least" if minimum_inclusive else "greater than"
        raise HavenoWatchError(f"{label} must be {comparison} {minimum}")
    if maximum is not None and value > maximum:
        raise HavenoWatchError(f"{label} must be at most {maximum}")
    if value.as_tuple().exponent < -places:
        raise HavenoWatchError(f"{label} may have at most {places} decimal places")
    return value


def validate_watch(
    threshold_pct: str,
    *,
    fee_pct: str = "0",
    payment_method: str = "ZELLE",
    currency: str = "USD",
    min_xmr: str | None = None,
    max_xmr: str | None = None,
    title: str = "",
) -> dict:
    threshold = _parse_decimal(
        threshold_pct,
        "threshold percent",
        minimum=Decimal("-50"),
        minimum_inclusive=True,
        maximum=Decimal("50"),
        places=2,
    )
    fee = _parse_decimal(
        fee_pct,
        "fee percent",
        minimum=Decimal("0"),
        minimum_inclusive=True,
        maximum=Decimal("20"),
        places=2,
    )
    assert threshold is not None and fee is not None
    minimum = _parse_decimal(min_xmr, "minimum XMR", places=12)
    maximum = _parse_decimal(max_xmr, "maximum XMR", places=12)
    if minimum is not None and maximum is not None and minimum > maximum:
        raise HavenoWatchError("minimum XMR cannot exceed maximum XMR")
    if not PAYMENT_METHOD.fullmatch(payment_method):
        raise HavenoWatchError("payment method must be 2-40 uppercase letters, digits, or underscores")
    if not CURRENCY.fullmatch(currency):
        raise HavenoWatchError("currency must be exactly three uppercase letters")
    if any(character in title for character in ("\t", "\r", "\n")):
        raise HavenoWatchError("title cannot contain tabs or newlines")
    if len(title) > 160:
        raise HavenoWatchError("title must be 160 characters or fewer")
    return {
        "network": "reto",
        "currency": currency,
        "payment_method": payment_method,
        "threshold_pct": percent_text(threshold),
        "fee_pct": percent_text(fee),
        "min_xmr": decimal_text(minimum),
        "max_xmr": decimal_text(maximum),
        "title": title.strip() or None,
    }


def matching_offers(watch: dict, market: HavenoMarket) -> list[dict]:
    minimum = Decimal(watch["min_xmr"]) if watch["min_xmr"] is not None else None
    maximum = Decimal(watch["max_xmr"]) if watch["max_xmr"] is not None else None
    threshold = Decimal(watch["threshold_pct"])
    fee = Decimal(watch["fee_pct"])
    filtered = []
    for offer in market.offers:
        if offer.payment_method != watch["payment_method"]:
            continue
        if minimum is not None and offer.max_xmr < minimum:
            continue
        if maximum is not None and offer.min_xmr > maximum:
            continue
        # Match on the same two-decimal figure the dashboard and alert display.
        vs_market = (
            Decimal(100) * (offer.price / market.reference_price - Decimal(1))
        ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        effective = vs_market + fee
        filtered.append(
            {
                "offer_id": offer.offer_id,
                "offered_at": offer.offered_at,
                "min_xmr": offer.min_xmr,
                "max_xmr": offer.max_xmr,
                "price": offer.price,
                "vs_market_pct": vs_market,
                "effective_pct": effective,
                "matches": effective <= threshold,
            }
        )
    return sorted(filtered, key=lambda offer: (offer["price"], offer["offer_id"]))


def _short_decimal(text: str) -> str:
    return text.rstrip("0").rstrip(".") if "." in text else text


def _signed_short_percent(text: str) -> str:
    value = Decimal(text)
    sign = "-" if value < 0 else "+"
    return f"{sign}{_short_decimal(format(abs(value), 'f'))}"


def _age_text(offered_at: int, now: int) -> str:
    seconds = max(0, now - offered_at)
    if seconds < 60 * 60:
        return f"{seconds // 60}m"
    if seconds < 24 * 60 * 60:
        return f"{seconds // (60 * 60)}h"
    return f"{seconds // (24 * 60 * 60)}d"


def notification(watch: dict, offers: list[dict], *, now: int) -> tuple[str, str]:
    best = offers[0]
    vs_market = _signed_short_percent(best["vs_market_pct"])
    currency_prefix = "$" if watch["currency"] == "USD" else f"{watch['currency']} "
    title = (
        f"XMR {vs_market}% · {currency_prefix}{best['price']} "
        f"{method_title(watch['payment_method'])} ({len(offers)} new)"
    )
    lines = []
    include_fee = Decimal(watch["fee_pct"]) > 0
    for offer in offers[:5]:
        offer_vs_market = _signed_short_percent(offer["vs_market_pct"])
        xmr_range = (
            offer["min_xmr"]
            if offer["min_xmr"] == offer["max_xmr"]
            else f"{offer['min_xmr']}–{offer['max_xmr']}"
        )
        line = (
            f"{currency_prefix}{offer['price']} ({offer_vs_market}% vs "
            f"{currency_prefix}{watch['reference_price']}) · {xmr_range} XMR · "
            f"listed {_age_text(offer['offered_at'], now)} ago"
        )
        if include_fee:
            line += f" · {_signed_short_percent(offer['effective_pct'])}% with fee"
        lines.append(line)
    lines.append(watch["market_url"])
    return title, "\n".join(lines)


def check_watch(
    store: HavenoStore,
    watch: dict,
    market: HavenoMarket,
    *,
    notify_sender=None,
    notify: bool = True,
    record: bool = True,
) -> dict:
    offers = matching_offers(watch, market)
    if not record:
        return {
            **watch,
            "reference_price": price_text(market.reference_price),
            "reference_source": market.reference_source,
            "offer_count": len(offers),
            "match_count": sum(offer["matches"] for offer in offers),
        }
    updated, candidates = store.record_success(
        watch["id"], market.reference_price, market.reference_source, offers
    )
    if not notify or updated["notifications_muted"] or not candidates or notify_sender is None:
        return updated
    title, message = notification(updated, candidates, now=int(store.clock()))
    try:
        notify_sender(title, message, "high", "moneybag")
    except Exception as error:
        raise HavenoNotificationError(f"Haveno notification failed: {error}") from error
    store.mark_notified(updated["id"], [offer["offer_id"] for offer in candidates])
    return store.get_watch(updated["id"])


def _market_results(watches: list[dict], fetcher, now: float) -> dict[str, HavenoMarket | Exception]:
    results: dict[str, HavenoMarket | Exception] = {}
    for watch in watches:
        currency = watch["currency"]
        if currency in results:
            continue
        try:
            results[currency] = fetch_market(
                fetcher, currency, watch["network"], now=now
            )
        except Exception as error:
            results[currency] = error
    return results


def check_watches(
    store: HavenoStore,
    watches: list[dict],
    *,
    fetcher,
    notify_sender=None,
    notify: bool = True,
    record: bool = True,
) -> tuple[list[dict], list[dict]]:
    checked, errors = [], []
    markets = _market_results(watches, fetcher, store.clock())
    for watch in watches:
        market = markets[watch["currency"]]
        try:
            if isinstance(market, Exception):
                raise market
            updated = check_watch(
                store,
                watch,
                market,
                notify_sender=notify_sender,
                notify=notify,
                record=record,
            )
            checked.append(updated)
            print(
                f"{updated['display_title']}: {updated['match_count']} matching of "
                f"{updated['offer_count']} current offers",
                file=sys.stderr,
            )
        except (HavenoSourceError, HavenoWatchError, HavenoStoreError) as error:
            if record and not isinstance(error, HavenoNotificationError):
                try:
                    store.record_error(watch["id"], str(error))
                except HavenoStoreError as storage_error:
                    error = storage_error
            errors.append({"id": watch["id"], "message": str(error)})
            print(f"price-check Haveno: {watch['display_title']}: {error}", file=sys.stderr)
    return checked, errors
