#!/usr/bin/env python3
"""Send a warning to the private ntfy topic in ``NTFY_WARNING_URL``.

The URL lives in ``pi/secrets/.bash_variables``. It is read by sourcing that
file in a clean bash, so it never appears in process arguments, logs or
error text. Warnings go to this topic directly rather than through the agent
notification bridge, whose topic has a different purpose. Everything in the
title and message reaches the phone, so callers pass only redacted text.

btt_guard imports ``send_warning``; deferred.zsh runs this file with the
message on stdin and gets exit 0 once the warning is delivered, 1 otherwise.
"""
from __future__ import annotations

import argparse
import http.client
import os
from pathlib import Path
import pwd
import subprocess
import sys
import urllib.parse
import urllib.request

REPO = Path(__file__).resolve().parents[2]
SECRETS = REPO / "pi" / "secrets" / ".bash_variables"
SOURCE_WARNING_URL = (
    'set +x; source "$1" >/dev/null 2>&1 || exit; '
    'printf "%s" "${NTFY_WARNING_URL-}"'
)
SOURCE_TIMEOUT_SECONDS = 5
DELIVERY_TIMEOUT_SECONDS = 15
PRIORITIES = ("min", "low", "default", "high", "max")
DELIVERY_ERRORS = (OSError, TypeError, UnicodeError, ValueError, http.client.HTTPException)


def warning_url(secrets: Path = SECRETS) -> str | None:
    """Load the destination without placing it in arguments or logs."""
    try:
        owner_home = pwd.getpwuid(os.getuid()).pw_dir
        result = subprocess.run(
            ["/bin/bash", "--noprofile", "--norc", "-c", SOURCE_WARNING_URL, "_", str(secrets)],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=SOURCE_TIMEOUT_SECONDS,
            check=False,
            env={"HOME": owner_home, "PATH": "/usr/bin:/bin"},
        )
    except (KeyError, OSError, UnicodeError, ValueError, subprocess.TimeoutExpired):
        return None
    url = result.stdout.strip()
    if result.returncode != 0 or not url or len(url) > 4096:
        return None
    try:
        parsed = urllib.parse.urlsplit(url)
        valid_host = parsed.hostname is not None
    except (UnicodeError, ValueError):
        return None
    if parsed.scheme not in ("http", "https") or not valid_host:
        return None
    if parsed.username is not None or parsed.password is not None:
        return None
    if any(ord(character) <= 32 or ord(character) == 127 for character in url):
        return None
    return url


def send_warning(title: str, message: str, priority: str = "high", *,
                 secrets: Path = SECRETS) -> str | None:
    """POST one warning; returns None once delivered, otherwise why not (never the URL)."""
    url = warning_url(secrets)
    if url is None:
        return "no usable NTFY_WARNING_URL"
    request = urllib.request.Request(
        url,
        data=message.encode("utf-8"),
        headers={"Title": title, "Priority": priority, "Tags": "warning"},
        method="POST",
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=DELIVERY_TIMEOUT_SECONDS) as response:
            status = response.status
    except DELIVERY_ERRORS as error:
        return f"delivery failed ({type(error).__name__})"
    return None if 200 <= status < 300 else f"delivery failed (HTTP {status})"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Send stdin as a warning to NTFY_WARNING_URL.")
    parser.add_argument("--title", required=True)
    parser.add_argument("--priority", choices=PRIORITIES, default="high")
    parser.add_argument("--secrets", type=Path, default=SECRETS,
                        help="bash file that sets NTFY_WARNING_URL (default: %(default)s)")
    args = parser.parse_args(argv)
    failure = send_warning(args.title, sys.stdin.read(), args.priority, secrets=args.secrets)
    if failure:
        print(f"ntfy_warning: {failure}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
