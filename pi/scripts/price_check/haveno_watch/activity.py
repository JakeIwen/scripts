"""Collect and classify public Haveno offer activity snapshots."""

from __future__ import annotations

import sys
import time

from .activity_store import (
    CURRENCY,
    NETWORK,
    HavenoActivityStore,
    HavenoActivityStoreError,
    empty_activity_summary,
)
from .source import HavenoSourceError, fetch_order_book, fetch_trades


def collect_activity(
    store: HavenoActivityStore,
    *,
    fetcher,
    now: int | None = None,
) -> bool:
    """Record one snapshot, retaining activity failures outside watch status."""
    snapshot_at = int(store.clock()) if now is None else now
    try:
        offers = fetch_order_book(fetcher, CURRENCY, NETWORK)
        trades = fetch_trades(fetcher, CURRENCY, NETWORK)
        store.record_snapshot(offers, trades, now=snapshot_at)
    except (HavenoSourceError, HavenoActivityStoreError) as error:
        detail = str(error)
        try:
            store.record_error(detail)
        except HavenoActivityStoreError as storage_error:
            detail = f"{detail}; {storage_error}"
        print(f"price-check Haveno activity: {detail}", file=sys.stderr)
        return False
    return True


def activity_summary(connection, *, clock=time.time) -> dict:
    """Return the public summary without allowing activity storage to fail a command."""
    try:
        return HavenoActivityStore(connection, clock=clock).summary()
    except HavenoActivityStoreError as error:
        detail = str(error)
        print(f"price-check Haveno activity: {detail}", file=sys.stderr)
        return empty_activity_summary(detail)


def run_activity_snapshot(connection, *, fetcher, clock=time.time) -> bool:
    """Create the activity store on a secured shared connection and collect once."""
    try:
        store = HavenoActivityStore(connection, clock=clock)
    except HavenoActivityStoreError as error:
        print(f"price-check Haveno activity: {error}", file=sys.stderr)
        return False
    return collect_activity(store, fetcher=fetcher)
