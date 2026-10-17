#!/usr/bin/env python3
"""Recover Pi saved searches using pages loaded in the managed clean browser."""

from __future__ import annotations

import argparse
import fcntl
import json
import re
from pathlib import Path
import subprocess
import time


ROOT = Path(__file__).resolve().parents[2]
REMOTE = "/usr/bin/python3 /home/pi/scripts/price_check/ebay_refresh.py"
NODE = "/Users/jacobr/.nvm/versions/node/v22.22.0/bin/node"
LIMITS = json.loads((ROOT / "pi/scripts/price_check/search_watch/browser_capture_limits.json").read_text())


class RefreshFailure(RuntimeError):
    """A deliberately credential-free diagnostic suitable for the worker log."""


def remote(host: str, command: str, payload=None):
    encoded = json.dumps(payload, ensure_ascii=False) if payload is not None else None
    if encoded is not None and len(encoded.encode("utf-8")) > LIMITS["max_submission_bytes"]:
        raise ValueError("browser submission is too large")
    try:
        result = subprocess.run(
            ["/usr/bin/ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8",
             "-o", "StrictHostKeyChecking=yes", host, f"{REMOTE} {command}"],
            input=encoded,
            text=True, encoding="utf-8", capture_output=True, timeout=90, check=True,
        )
    except subprocess.CalledProcessError as error:
        reason = f"Pi {command} failed"
        try:
            result = json.loads(error.stdout)
            cause = result.get("cause") or result.get("error")
            if isinstance(cause, str) and re.fullmatch(r"[A-Za-z_]{1,40}", cause):
                reason += f" ({cause})"
        except (ValueError, TypeError, AttributeError):
            pass
        raise RefreshFailure(reason) from error
    return json.loads(result.stdout)


def refresh(host: str) -> bool:
    request = remote(host, "claim")
    if request is None:
        return False
    if (not isinstance(request, dict)
            or set(request) != {"search_id", "request_id", "url", "attempt"}
            or type(request["attempt"]) is not int or request["attempt"] < 1):
        raise ValueError("invalid refresh request")
    try:
        recheck = remote(host, "recheck", request)
        if recheck.get("ok") is not True or type(recheck.get("renewal_needed")) is not bool:
            raise ValueError("invalid browser refresh recheck response")
        if not recheck["renewal_needed"]:
            print("eBay renewal no longer needed; existing browser headers retained.", flush=True)
            return False
        try:
            browser = subprocess.run(
                [NODE, str(Path(__file__).with_name("ebay_browser_headers.mjs")), request["url"]],
                capture_output=True, text=True, encoding="utf-8", timeout=120, check=True,
            )
        except subprocess.CalledProcessError as error:
            reason = "anonymous browser capture failed"
            detail = (error.stderr or "").strip()
            if re.fullmatch(
                r"eBay anonymous browser recovery failed at [A-Za-z0-9 (),.-]{1,180}; "
                r"existing results retained\.", detail
            ):
                reason = detail
            raise RefreshFailure(reason) from error
        page = json.loads(browser.stdout)
        if (not isinstance(page, dict) or set(page) != {"final_url", "html"}
                or not isinstance(page["final_url"], str) or not isinstance(page["html"], str)):
            raise ValueError("invalid browser page response")
        result = remote(host, "results", {**request, **page})
        if (not isinstance(result, dict) or set(result) != {"ok", "result_count", "notification_failed"}
                or result["ok"] is not True or type(result["result_count"]) is not int
                or result["result_count"] < 0 or type(result["notification_failed"]) is not bool):
            raise ValueError("Pi rejected the browser results")
        print("Browser search results recorded on vanpi without another eBay request.", flush=True)
        if result.get("notification_failed"):
            print("Search results recovered, but the new-listing alert failed.", flush=True)
        return True
    except Exception:
        try:
            remote(host, "failed", request)
        except Exception:
            print("Deal Watch outcome acknowledgement deferred; the attempt lease will expire.", flush=True)
        raise


def run_worker(host: str, *, once=False, sleep=time.sleep) -> int:
    while True:
        failed = False
        try:
            refresh(host)
        except RefreshFailure as error:
            print(f"Deal Watch refresh deferred: {error}.", flush=True)
            failed = True
        except Exception as error:
            # Exceptions can include captured secret payloads: report only the type.
            print(f"Deal Watch refresh deferred ({type(error).__name__}).", flush=True)
            failed = True
        if once:
            return int(failed)
        try:
            status = remote(host, "status")
            if not isinstance(status, dict) or set(status) != {"retry_after"}:
                raise ValueError("invalid retry status")
            delay = status["retry_after"]
            if delay is None:
                return int(failed)
            if type(delay) is not int or delay < 0:
                raise ValueError("invalid retry delay")
        except Exception as error:
            print(f"Deal Watch retry scheduling deferred ({type(error).__name__}).", flush=True)
            return 1
        # Re-read Pi time after sleep/wake without bursting missed attempts.
        sleep(max(1, min(delay, 60)))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="pi@vanpi.lan")
    parser.add_argument("--once", action="store_true", help="process at most one due attempt")
    args = parser.parse_args()
    state = ROOT / "tmp/deal-watch-refresh"
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (state / "worker.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0
        return run_worker(args.host, once=args.once)


if __name__ == "__main__":
    raise SystemExit(main())
