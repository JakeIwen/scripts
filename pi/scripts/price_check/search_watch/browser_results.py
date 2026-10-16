"""Accept a leased browser page using the normal parser, without fetching it again."""

from __future__ import annotations

import json
from pathlib import Path
from typing import BinaryIO, TypedDict
from urllib.parse import parse_qs, urlsplit

from .browser_refresh import RefreshRequest, is_current_request, validate_request
from .parsers import parse
from .service import SearchNotificationError, apply_search_results, validate_watch


LIMITS = json.loads(Path(__file__).with_name("browser_capture_limits.json").read_text())


class BrowserPage(TypedDict):
    final_url: str
    html: str


class BrowserResultSubmission(RefreshRequest, BrowserPage):
    pass


class BrowserResultReceipt(TypedDict):
    ok: bool
    result_count: int
    notification_failed: bool


def read_submission(source: BinaryIO) -> object:
    data = source.read(LIMITS["max_submission_bytes"] + 1)
    if len(data) > LIMITS["max_submission_bytes"]:
        raise ValueError("browser result submission is too large")
    return json.loads(data.decode("utf-8"))


def validate_submission(payload: object) -> BrowserResultSubmission:
    if not isinstance(payload, dict) or set(payload) != set(BrowserResultSubmission.__annotations__):
        raise ValueError("invalid browser result submission")
    validate_request({key: payload[key] for key in RefreshRequest.__annotations__})
    if not isinstance(payload["html"], str) or not payload["html"]:
        raise ValueError("browser results require HTML")
    if len(payload["html"].encode("utf-8")) > LIMITS["max_html_bytes"]:
        raise ValueError("browser page is too large")
    final_url = payload["final_url"]
    if not isinstance(final_url, str) or not 0 < len(final_url) <= LIMITS["max_url_characters"]:
        raise ValueError("invalid browser result URL")
    validate_watch("ebay", payload["url"])
    final, expected = urlsplit(final_url), urlsplit(payload["url"])
    if (final.scheme != "https" or final.hostname != "www.ebay.com"
            or final.username or final.password or final.port not in (None, 443)
            or final.path != expected.path
            or parse_qs(final.query, keep_blank_values=True) != parse_qs(expected.query, keep_blank_values=True)):
        raise ValueError("browser result URL does not match the saved search")
    return payload


def accept_browser_results(store, payload: object, *, notify_new=None) -> BrowserResultReceipt:
    submission = validate_submission(payload)
    if not is_current_request(store, submission):
        raise ValueError("browser recovery attempt is no longer current")
    results = parse(submission["html"])
    notification_failed = False
    # Pin the watch and attempt before the existing result writer opens its
    # transaction; a removed/recreated watch must never receive this old page.
    with store.connection:
        store.connection.execute("BEGIN IMMEDIATE")
        if not is_current_request(store, submission):
            raise ValueError("browser recovery attempt expired during parsing")
        watch = store.get_watch(submission["search_id"])
        try:
            apply_search_results(store, watch, results, notify_new=notify_new)
        except SearchNotificationError:
            # A failed alert must not restart an already completed recovery.
            notification_failed = True
    return {"ok": True, "result_count": len(results), "notification_failed": notification_failed}
