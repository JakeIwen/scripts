#!/usr/bin/python3
"""Private stdin/stdout bridge for the Mac's anonymous eBay browser hook."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import sys
from pathlib import Path

from main import DEFAULT_DB, send_search_alert, send_search_error
from search_watch import SearchStore, check_watch
from search_watch.browser_refresh import claim_refresh, request_refresh
from search_watch.refresh_install import install_headers
from search_watch.service import DEFAULT_EBAY_HEADERS


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("command", choices=("claim", "install", "request"))
    parser.add_argument("search_id", type=int, nargs="?")
    args = parser.parse_args()
    try:
        with SearchStore(args.db) as store:
            if args.command == "claim":
                print(json.dumps(claim_refresh(store)))
                return 0
            with args.db.with_suffix(".check.lock").open("a") as lock:
                os.chmod(lock.name, 0o600)
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                if args.command == "request":
                    request_refresh(store, store.get_watch(args.search_id))
                    print(json.dumps({"ok": True}))
                    return 0
                payload = json.loads(sys.stdin.read(70000))
                path = Path(os.environ.get("EBAY_HEADERS_FILE", DEFAULT_EBAY_HEADERS))
                page = install_headers(store, payload, path)
                watch = store.get_watch(payload["search_id"])
                try:
                    check_watch(
                        store, watch, fetcher=lambda _url: page,
                        notify_new=send_search_alert, notify_error=send_search_error,
                    )
                    check_failed = False
                except Exception:
                    # Report the successful install separately from check/alert failure.
                    check_failed = True
                print(json.dumps({"ok": True, "check_failed": check_failed}))
                return 0
    except Exception as error:
        print(json.dumps({
            "ok": False, "error": type(error).__name__,
            "cause": type(error.__cause__).__name__ if error.__cause__ else None,
        }))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
