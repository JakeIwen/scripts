"""Privacy-preserving notifications for the BetterTouchTool guard.

The notification body is deliberately derived from coarse finding categories.
The private audit report remains the only place containing UUIDs and details.
Warnings deliberately use ``NTFY_WARNING_URL`` directly instead of the existing
agent-notification bridge, whose topic has a different purpose.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import http.client
import os
from pathlib import Path
import pwd
import subprocess
from typing import Sequence
import urllib.parse
import urllib.request

from .model import AuditStatus, Finding, FindingKind


ROOT = Path(__file__).resolve().parents[3]
WARNING_SECRETS = ROOT / "pi" / "secrets" / ".bash_variables"
SOURCE_WARNING_URL = (
    'set +x; source "$1" >/dev/null 2>&1 || exit; '
    'printf "%s" "${NTFY_WARNING_URL-}"'
)
SOURCE_TIMEOUT_SECONDS = 5
DELIVERY_TIMEOUT_SECONDS = 15

@dataclass(frozen=True)
class GuardNotification:
    """A fully redacted notification ready for delivery."""

    title: str
    message: str
    priority: str


SAFE_KIND_LABELS = {
    FindingKind.MISSING: "missing",
    FindingKind.REPARENTED: "parent",
    FindingKind.PRESET_CHANGED: "preset",
    FindingKind.DISABLED: "enablement",
    FindingKind.ENABLED_CHANGED: "enablement",
    FindingKind.TYPE_CHANGED: "type",
    FindingKind.ORDER_CHANGED: "order",
    FindingKind.ACTION_CHANGED: "action",
    FindingKind.CONFIG_CHANGED: "configuration",
    FindingKind.DEPENDENCY_CHANGED: "dependency",
    FindingKind.UNASSIGNED: "unassigned",
    FindingKind.CHECKPOINT_MISSING: "checkpoint",
    FindingKind.CHECKPOINT_INVALID: "checkpoint",
    FindingKind.DATABASE_UNAVAILABLE: "database",
    FindingKind.UNSUPPORTED_SCHEMA: "schema",
    FindingKind.MONITOR_STATE_INVALID: "monitor",
}


def _safe_kind(kind: object) -> str:
    if not isinstance(kind, str):
        return "other"
    try:
        return SAFE_KIND_LABELS[FindingKind(kind)]
    except (KeyError, ValueError):
        return "other"


def finding_counts(findings: Sequence[Finding]) -> tuple[tuple[str, int], ...]:
    """Return allowlisted type counts without retaining identifying report data."""
    counts = Counter(_safe_kind(finding.get("kind")) for finding in findings)
    return tuple(sorted(counts.items()))


def _count_summary(counts: tuple[tuple[str, int], ...]) -> str:
    total = sum(count for _, count in counts)
    noun = "finding" if total == 1 else "findings"
    if not counts:
        return "No finding categories were available."
    categories = ", ".join(f"{kind}: {count}" for kind, count in counts)
    return f"{total} {noun} ({categories})."


def alert_notification(
    status: AuditStatus, counts: tuple[tuple[str, int], ...]
) -> GuardNotification:
    """Build a generic warning containing no report identifiers or details."""
    if status == AuditStatus.DRIFT:
        title = "BetterTouchTool guard: configuration drift"
        lead = "The read-only configuration audit detected drift."
    elif status == AuditStatus.UNAVAILABLE:
        title = "BetterTouchTool guard: audit unavailable"
        lead = "The read-only configuration audit could not verify the configuration."
    else:
        raise ValueError(f"Cannot build an alert for audit status {status!r}")
    message = f"{lead} {_count_summary(counts)} Review the local private audit report."
    return GuardNotification(title=title, message=message, priority="high")


def recovery_notification() -> GuardNotification:
    """Build the single generic recovery notice for a resolved alert."""
    return GuardNotification(
        title="BetterTouchTool guard: healthy",
        message="Two consecutive read-only audits confirmed the configuration is healthy.",
        priority="default",
    )


def _warning_url() -> str | None:
    """Load the private destination without placing it in arguments or logs."""
    try:
        owner_home = pwd.getpwuid(os.getuid()).pw_dir
        result = subprocess.run(
            [
                "/bin/bash",
                "--noprofile",
                "--norc",
                "-c",
                SOURCE_WARNING_URL,
                "_",
                str(WARNING_SECRETS),
            ],
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


def send_warning_notification(notification: GuardNotification) -> bool:
    """POST a redacted notification to the dedicated private warning topic."""
    url = _warning_url()
    if url is None:
        return False
    try:
        request = urllib.request.Request(
            url,
            data=notification.message.encode("utf-8"),
            headers={
                "Title": notification.title,
                "Priority": notification.priority,
                "Tags": "warning",
            },
            method="POST",
        )
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(request, timeout=DELIVERY_TIMEOUT_SECONDS) as response:
            return 200 <= response.status < 300
    except (OSError, TypeError, UnicodeError, ValueError, http.client.HTTPException):
        return False
