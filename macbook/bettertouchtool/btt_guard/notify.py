"""Privacy-preserving notifications for the BetterTouchTool guard.

The notification body is deliberately derived from coarse finding categories.
The private audit report remains the only place containing UUIDs and details.
Delivery goes through the shared ``NTFY_WARNING_URL`` sender.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Sequence

from macbook.scripts.ntfy_warning import send_warning

from .model import AuditStatus, Finding, FindingKind


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
    message = f"{lead} {_count_summary(counts)} Review the private audit report in Mac Panel (macpanel)."
    return GuardNotification(title=title, message=message, priority="high")


def recovery_notification() -> GuardNotification:
    """Build the single generic recovery notice for a resolved alert."""
    return GuardNotification(
        title="BetterTouchTool guard: healthy",
        message="Two consecutive read-only audits confirmed the configuration is healthy.",
        priority="default",
    )


def send_warning_notification(notification: GuardNotification) -> bool:
    """POST a redacted notification to the dedicated private warning topic."""
    return send_warning(notification.title, notification.message, notification.priority) is None
