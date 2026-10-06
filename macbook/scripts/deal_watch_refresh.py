#!/usr/bin/env python3
"""Poll the Pi's refresh hook and renew headers through the managed clean browser."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
from pathlib import Path
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[2]
REMOTE = "/usr/bin/python3 /home/pi/scripts/price_check/ebay_refresh.py"
NODE = "/Users/jacobr/.nvm/versions/node/v22.22.0/bin/node"


class RefreshFailure(RuntimeError):
    """A deliberately credential-free diagnostic suitable for the worker log."""


def remote(host: str, command: str, payload=None):
    try:
        result = subprocess.run(
            ["/usr/bin/ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8",
             "-o", "StrictHostKeyChecking=yes", host, f"{REMOTE} {command}"],
            input=json.dumps(payload) if payload is not None else None,
            text=True, capture_output=True, timeout=90, check=True,
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


def sync_local_headers(headers: str) -> None:
    destination = ROOT / "pi/secrets/.ebay_headers"
    descriptor, temporary = tempfile.mkstemp(prefix=".ebay-refresh-", dir=destination.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as target:
            target.write(headers)
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary, destination)
    finally:
        Path(temporary).unlink(missing_ok=True)


def refresh(host: str) -> bool:
    request = remote(host, "claim")
    if request is None:
        return False
    if not isinstance(request, dict) or set(request) != {"search_id", "request_id", "url"}:
        raise ValueError("invalid refresh request")
    try:
        browser = subprocess.run(
            [NODE, str(Path(__file__).with_name("ebay_browser_headers.mjs")), request["url"]],
            capture_output=True, text=True, timeout=120, check=True,
        )
    except subprocess.CalledProcessError as error:
        reason = "anonymous browser capture failed"
        detail = (error.stderr or "").strip()
        if re.fullmatch(
            r"eBay anonymous browser refresh failed at [A-Za-z0-9 (),.-]{1,180}; "
            r"existing headers retained\.", detail
        ):
            reason = detail
        raise RefreshFailure(reason) from error
    headers = json.loads(browser.stdout)["headers"]
    result = remote(host, "install", {**request, "headers": headers})
    if not result.get("ok"):
        raise ValueError("Pi rejected the browser headers")
    # Broad sync publishes this ignored secret, so keep it in step with the Pi.
    sync_local_headers(headers)
    print("eBay browser headers renewed and validated on vanpi.", flush=True)
    if result.get("check_failed"):
        print("Headers renewed, but the follow-up result check or alert failed.", flush=True)
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="pi@vanpi.lan")
    args = parser.parse_args()
    state = ROOT / "tmp/deal-watch-refresh"
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (state / "worker.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0
        try:
            refresh(args.host)
            return 0
        except RefreshFailure as error:
            print(f"Deal Watch refresh deferred: {error}; will retry.", flush=True)
            return 1
        except Exception as error:
            # Exceptions can include captured secret payloads: report only the type.
            print(f"Deal Watch refresh deferred ({type(error).__name__}); will retry.", flush=True)
            return 1


if __name__ == "__main__":
    raise SystemExit(main())
