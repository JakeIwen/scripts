"""Validate browser headers against eBay on the Pi before replacing its secret."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import TypedDict

from .browser_refresh import RefreshRequest, clear_refresh, is_current_request
from .parsers import parse
from .service import SearchWatchError, fetch_ebay, validate_watch


class RefreshSubmission(RefreshRequest):
    headers: str


def validate_submission(payload: object) -> RefreshSubmission:
    if not isinstance(payload, dict) or set(payload) != {
        "search_id", "request_id", "url", "headers"
    }:
        raise ValueError("invalid browser refresh payload")
    if type(payload["search_id"]) is not int or payload["search_id"] < 1:
        raise ValueError("invalid search ID")
    for key in ("request_id", "url", "headers"):
        if not isinstance(payload[key], str):
            raise ValueError("invalid browser refresh fields")
    if len(payload["request_id"]) != 32 or not 0 < len(payload["headers"]) <= 65536:
        raise ValueError("invalid browser refresh size")
    validate_watch("ebay", payload["url"])
    return payload


def install_headers(store, payload: object, destination: Path) -> str:
    request = validate_submission(payload)
    if not is_current_request(store, request):
        raise ValueError("browser refresh request is no longer current")
    if destination.is_symlink() or not destination.parent.is_dir():
        raise ValueError("unsafe browser header destination")
    descriptor, temporary = tempfile.mkstemp(prefix=".ebay-refresh-", dir=destination.parent)
    candidate = Path(temporary)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as target:
            target.write(request["headers"])
            target.flush()
            os.fsync(target.fileno())
        # A browser success on the Mac is insufficient: cookies may be IP-bound.
        page = fetch_ebay(request["url"], headers_path=candidate)
        parse(page)
        os.replace(candidate, destination)
    except (OSError, SearchWatchError, ValueError) as error:
        # Do not put browser payloads, curl arguments or cookies in log messages.
        raise ValueError("browser headers failed Pi validation; existing headers preserved") from error
    finally:
        candidate.unlink(missing_ok=True)
    clear_refresh(store, request["search_id"])
    return page
