"""Fetch, parse, and record saved-search results."""

from __future__ import annotations

import os
import re
import stat
import subprocess
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .parsers import EbayGateError, EbayParseError, parse as parse_ebay
from .store import SearchStore, SearchStoreError


CURL = "/usr/bin/curl"
DEFAULT_EBAY_HEADERS = Path("/home/pi/secrets/.ebay_headers")
PARSERS = {"ebay": parse_ebay}


class SearchWatchError(RuntimeError):
    error_code = "search"


class SearchCookieError(SearchWatchError):
    error_code = "cookie"


class SearchLoadError(SearchWatchError):
    error_code = "load"


class SearchParserError(SearchWatchError):
    error_code = "parser"


def validate_watch(parser: str, url: str, title: str = "") -> tuple[str, str, str | None]:
    if parser not in PARSERS:
        raise SearchWatchError(f"unknown search parser {parser!r}")
    parsed = urlsplit(url.strip())
    if (
        parsed.scheme != "https"
        or parsed.hostname != "www.ebay.com"
        or parsed.path != "/sch/i.html"
    ):
        raise SearchWatchError(
            "eBay search URL must start with https://www.ebay.com/sch/i.html"
        )
    query = parse_qs(parsed.query)
    if not query.get("_nkw", [""])[0].strip():
        raise SearchWatchError("eBay search URL is missing its _nkw query")
    if len(url) > 4096:
        raise SearchWatchError("search URL is too long")
    if any(character in title for character in ("\t", "\r", "\n")):
        raise SearchWatchError("search title cannot contain tabs or newlines")
    if len(title) > 160:
        raise SearchWatchError("search title must be 160 characters or fewer")
    display_title = title.strip() or query["_nkw"][0].strip()
    return parser, url.strip(), display_title


def _validated_headers_file() -> Path:
    path = Path(os.environ.get("EBAY_HEADERS_FILE", DEFAULT_EBAY_HEADERS))
    try:
        details = path.lstat()
    except OSError as error:
        raise SearchCookieError(
            f"eBay browser headers are unavailable at {path}"
        ) from error
    if stat.S_ISLNK(details.st_mode) or not stat.S_ISREG(details.st_mode):
        raise SearchCookieError("eBay browser headers must be a regular file")
    if details.st_mode & 0o077:
        raise SearchCookieError("eBay browser headers must have permissions 600")
    if details.st_size <= 0 or details.st_size > 65536:
        raise SearchCookieError("eBay browser headers have an invalid size")
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        raise SearchCookieError("could not read eBay browser headers") from error
    headers = {}
    for line in lines:
        if not line.strip():
            continue
        name, separator, value = line.partition(":")
        lowered = name.strip().lower()
        if not separator or lowered not in {"user-agent", "cookie"} or lowered in headers:
            raise SearchCookieError(
                "eBay browser headers must contain only User-Agent and Cookie"
            )
        if not value.strip() or any(character in value for character in ("\r", "\n")):
            raise SearchCookieError("eBay browser headers contain an invalid value")
        headers[lowered] = value.strip()
    if set(headers) != {"user-agent", "cookie"}:
        raise SearchCookieError(
            "eBay browser headers must contain User-Agent and Cookie"
        )
    return path


def fetch_ebay(url: str) -> str:
    headers = _validated_headers_file()
    command = [
        CURL,
        "--location",
        "--max-redirs",
        "0",
        "--fail",
        "--silent",
        "--show-error",
        "--compressed",
        "--connect-timeout",
        "10",
        "--max-time",
        "45",
        "--max-filesize",
        "10000000",
        "--proto",
        "=https",
        "--proto-redir",
        "=https",
        "-H",
        f"@{headers}",
        "-H",
        "Accept: text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "-H",
        "Accept-Language: en-US,en;q=0.9",
        "-H",
        "Sec-GPC: 1",
        "-H",
        "Upgrade-Insecure-Requests: 1",
        "-H",
        "Sec-Fetch-Dest: document",
        "-H",
        "Sec-Fetch-Mode: navigate",
        "-H",
        "Sec-Fetch-Site: none",
        "-H",
        "Pragma: no-cache",
        "-H",
        "Cache-Control: no-cache",
        url,
    ]
    try:
        result = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
    except OSError as error:
        raise SearchLoadError(f"could not start eBay download: {error}") from error
    except subprocess.CalledProcessError as error:
        detail = getattr(error, "stderr", "") or str(error)
        detail = detail.strip()
        status_match = re.search(
            r"(?:error:|HTTP(?: response code)?)\s*(\d{3})", detail
        )
        status = int(status_match.group(1)) if status_match else None
        if (
            status in {401, 403, 407, 429}
            or error.returncode == 47
            or "redirect" in detail.casefold()
        ):
            raise SearchCookieError(
                f"eBay rejected or redirected the browser request: {detail}"
            ) from error
        raise SearchLoadError(f"eBay download failed: {detail}") from error
    return result.stdout


def _fetch_and_parse(watch: dict, fetcher) -> list:
    page = fetcher(watch["url"])
    try:
        return PARSERS[watch["parser"]](page)
    except EbayGateError as error:
        raise SearchCookieError(str(error)) from error
    except EbayParseError as error:
        raise SearchParserError(str(error)) from error


def check_watch(
    store: SearchStore,
    watch: dict,
    *,
    notify_new=None,
    notify_error=None,
    notify: bool = True,
    record: bool = True,
    fetcher=fetch_ebay,
) -> dict:
    try:
        results = _fetch_and_parse(watch, fetcher)
    except SearchWatchError as error:
        failed_watch = store.record_error(watch["id"], str(error)) if record else watch
        if notify and notify_error:
            try:
                notify_error(failed_watch, error)
            except Exception as notification_error:
                raise SearchWatchError(
                    f"{error}; error notification failed: {notification_error}"
                ) from notification_error
        raise

    if record:
        updated, new_results = store.record_success(watch["id"], results)
    else:
        updated = {**watch, "result_count": len(results)}
        new_results = []
    if notify and new_results and notify_new:
        try:
            notify_new(updated, new_results)
        except Exception as error:
            raise SearchWatchError(f"new-result notification failed: {error}") from error
    return {**updated, "new_results": new_results}


def check_watches(
    store: SearchStore,
    watches: list[dict],
    *,
    notify_new=None,
    notify_error=None,
    notify: bool = True,
    record: bool = True,
    fetcher=fetch_ebay,
) -> tuple[list[dict], list[dict]]:
    checked, errors = [], []
    for watch in watches:
        try:
            updated = check_watch(
                store,
                watch,
                notify_new=notify_new,
                notify_error=notify_error,
                notify=notify,
                record=record,
                fetcher=fetcher,
            )
            checked.append(updated)
            print(
                f"{updated['display_title']}: {updated['result_count']} current "
                f"eBay {'result' if updated['result_count'] == 1 else 'results'}",
                file=sys.stderr,
            )
        except (SearchWatchError, SearchStoreError) as error:
            errors.append(
                {
                    "kind": "search",
                    "id": watch["id"],
                    "error_code": getattr(error, "error_code", "storage"),
                    "message": str(error),
                }
            )
            print(f"price-check search: {watch['url']}: {error}", file=sys.stderr)
    return checked, errors
