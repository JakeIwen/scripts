"""Debounced notification state for read-only BetterTouchTool audits."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import re
from typing import Callable, Final, Literal, Mapping, Sequence, TypedDict, cast

from .model import AuditReport, AuditStatus, Finding
from .notify import (
    GuardNotification,
    alert_notification,
    finding_counts,
    recovery_notification,
)


MonitorAction = Literal[
    "not-needed", "pending", "deduplicated", "cooldown", "sent", "delivery-failed"
]
StateIssue = Literal["primary-invalid", "recovery-invalid", "both-invalid"]
StateSource = Literal["primary", "recovery", "initial"]
StateTarget = Literal["primary", "recovery"]
Sender = Callable[[GuardNotification], bool]
DEBOUNCE_SECONDS = 60.0
DELIVERY_RETRY_SECONDS = 300.0
STATE_VERSION = 1
INVALID_STATE: Final = object()


class MonitorState(TypedDict):
    version: int
    alert_active: bool
    last_alert_key: str | None
    candidate_key: str | None
    candidate_since: float | None
    candidate_observations: int
    candidate_confirmed: bool
    retry_not_before: float | None


@dataclass(frozen=True)
class MonitorResult:
    """New state plus audit health, kept separate from delivery outcome."""

    state: MonitorState
    audit_status: AuditStatus
    action: MonitorAction
    notification_kind: Literal["alert", "recovery"] | None = None


@dataclass(frozen=True)
class StateSelection:
    """Safe state choice; filesystem quarantine and adoption remain caller-owned."""

    state: MonitorState
    source: StateSource
    write_target: StateTarget
    reason: StateIssue | None = None
    adopt_recovery: bool = False


class MonitorStateError(RuntimeError):
    """The private monitor state could not be safely interpreted."""


def initial_state() -> MonitorState:
    return {
        "version": STATE_VERSION,
        "alert_active": False,
        "last_alert_key": None,
        "candidate_key": None,
        "candidate_since": None,
        "candidate_observations": 0,
        "candidate_confirmed": False,
        "retry_not_before": None,
    }


def _clear_candidate(state: MonitorState) -> None:
    state["candidate_key"] = None
    state["candidate_since"] = None
    state["candidate_observations"] = 0
    state["candidate_confirmed"] = False
    state["retry_not_before"] = None


def _finite_time(value: object, *, optional: bool = False) -> bool:
    if value is None:
        return optional
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _valid_state_keys(last_alert_key: object, candidate_key: object) -> bool:
    digest = r"[0-9a-f]{64}"
    if last_alert_key is not None and not re.fullmatch(digest, last_alert_key):
        return False
    if candidate_key is None or candidate_key == "recovery":
        return True
    return re.fullmatch(rf"alert:(drift|unavailable):{digest}", candidate_key) is not None


def validate_state(value: object) -> MonitorState:
    """Validate persisted JSON and return an isolated mutable state value."""
    if not isinstance(value, dict) or value.get("version") != STATE_VERSION:
        raise MonitorStateError("Unsupported or malformed monitor state")
    if set(value) != set(initial_state()):
        raise MonitorStateError("Monitor state has unexpected or missing fields")
    string_fields = ("last_alert_key", "candidate_key")
    if any(value.get(field) is not None and not isinstance(value.get(field), str) for field in string_fields):
        raise MonitorStateError("Malformed monitor state key")
    if not _valid_state_keys(value.get("last_alert_key"), value.get("candidate_key")):
        raise MonitorStateError("Malformed monitor state fingerprint")
    if not isinstance(value.get("alert_active"), bool) or not isinstance(
        value.get("candidate_confirmed"), bool
    ):
        raise MonitorStateError("Malformed monitor state flag")
    observations = value.get("candidate_observations")
    if not isinstance(observations, int) or isinstance(observations, bool) or observations < 0:
        raise MonitorStateError("Malformed monitor observation count")
    for field in ("candidate_since", "retry_not_before"):
        if not _finite_time(value.get(field), optional=True):
            raise MonitorStateError("Malformed monitor timestamp")
    candidate_key = value.get("candidate_key")
    if candidate_key is None and any(
        (value.get("candidate_since") is not None, observations, value.get("candidate_confirmed"))
    ):
        raise MonitorStateError("Inconsistent monitor candidate")
    if candidate_key is not None and value.get("candidate_since") is None:
        raise MonitorStateError("Incomplete monitor candidate")
    if candidate_key is not None and observations < 1:
        raise MonitorStateError("Candidate has no observations")
    if candidate_key == "recovery" and not value.get("alert_active"):
        raise MonitorStateError("Recovery candidate has no delivered alert")
    if value.get("alert_active") != (value.get("last_alert_key") is not None):
        raise MonitorStateError("Inconsistent delivered-alert state")
    if value.get("candidate_confirmed") and observations < 2:
        raise MonitorStateError("Inconsistent confirmed candidate")
    if value.get("retry_not_before") is not None and not value.get("candidate_confirmed"):
        raise MonitorStateError("Inconsistent delivery cooldown")
    return cast(MonitorState, dict(value))


def _state_slot(value: object | None) -> tuple[MonitorState | None, Literal["missing", "invalid", "valid"]]:
    if value is None:
        return None, "missing"
    if value is INVALID_STATE:
        return None, "invalid"
    try:
        return validate_state(value), "valid"
    except MonitorStateError:
        return None, "invalid"


def select_monitor_state(primary: object | None, recovery: object | None) -> StateSelection:
    """Choose normal/fallback state without treating corrupt state as healthy."""
    primary_state, primary_status = _state_slot(primary)
    recovery_state, recovery_status = _state_slot(recovery)
    if primary_status == "valid" and recovery_status == "valid":
        return StateSelection(recovery_state, "recovery", "primary", adopt_recovery=True)
    if primary_status == "missing" and recovery_status == "valid":
        return StateSelection(recovery_state, "recovery", "primary", adopt_recovery=True)
    if primary_status == "invalid":
        reason: StateIssue = "both-invalid" if recovery_status == "invalid" else "primary-invalid"
        state = recovery_state if recovery_state is not None else initial_state()
        source: StateSource = "recovery" if recovery_state is not None else "initial"
        return StateSelection(state, source, "recovery", reason)
    if recovery_status == "invalid":
        state = primary_state if primary_state is not None else initial_state()
        source = "primary" if primary_state is not None else "initial"
        return StateSelection(state, source, "recovery", "recovery-invalid")
    if primary_state is not None:
        return StateSelection(primary_state, "primary", "primary")
    return StateSelection(initial_state(), "initial", "primary")


def _validated_report(
    report: AuditReport,
) -> tuple[AuditStatus, Sequence[Finding]]:
    try:
        status = AuditStatus(report.get("status"))
    except (TypeError, ValueError) as exc:
        raise ValueError("Audit report has an invalid status") from exc
    findings = report.get("findings")
    if not isinstance(findings, Sequence) or isinstance(findings, (str, bytes)):
        raise ValueError("Audit report findings must be a sequence")
    if not all(isinstance(finding, Mapping) for finding in findings):
        raise ValueError("Audit report findings must be mappings")
    if any(
        not all(isinstance(finding.get(field), str) for field in ("kind", "uuid", "detail"))
        for finding in findings
    ):
        raise ValueError("Audit report findings have invalid fields")
    if not _finite_time(report.get("checked_at")):
        raise ValueError("Audit report checked_at must be finite")
    for field in ("checkpoint_id", "btt_version"):
        if report.get(field) is not None and not isinstance(report.get(field), str):
            raise ValueError(f"Audit report {field} must be a string or null")
    if status == AuditStatus.HEALTHY and findings:
        raise ValueError("A healthy audit report cannot contain findings")
    return status, cast(Sequence[Finding], findings)


def _report_key(report: AuditReport, status: AuditStatus, findings: Sequence[Finding]) -> str:
    stable_findings = sorted((item["kind"], item["uuid"], item["detail"]) for item in findings)
    identity = [status, report.get("checkpoint_id"), report.get("btt_version"), stable_findings]
    return hashlib.sha256(json.dumps(identity, separators=(",", ":")).encode()).hexdigest()


def _observe_candidate(state: MonitorState, key: str, now: float) -> bool:
    since = state["candidate_since"]
    if state["candidate_key"] != key or since is None or now < since:
        _clear_candidate(state)
        state["candidate_key"] = key
        state["candidate_since"] = now
        state["candidate_observations"] = 1
        return False
    state["candidate_observations"] += 1
    if state["candidate_confirmed"]:
        return True
    return state["candidate_observations"] >= 2 and now - since >= DEBOUNCE_SECONDS


def _deliver(
    state: MonitorState,
    status: AuditStatus,
    notification: GuardNotification,
    notification_kind: Literal["alert", "recovery"],
    sender: Sender,
    now: float,
) -> MonitorResult:
    retry_at = state["retry_not_before"]
    if retry_at is not None and now < retry_at:
        return MonitorResult(state, status, "cooldown", notification_kind)
    delivered = bool(sender(notification))
    if not delivered:
        state["candidate_confirmed"] = True
        state["retry_not_before"] = now + DELIVERY_RETRY_SECONDS
        return MonitorResult(state, status, "delivery-failed", notification_kind)
    if notification_kind == "alert":
        state["alert_active"] = True
        state["last_alert_key"] = state["candidate_key"].rsplit(":", 1)[-1]
    else:
        state["alert_active"] = False
        state["last_alert_key"] = None
    _clear_candidate(state)
    return MonitorResult(state, status, "sent", notification_kind)


def monitor_report(
    report: AuditReport, *, state: object, sender: Sender, now: float
) -> MonitorResult:
    """Advance one observation; the caller owns locking and private JSON I/O."""
    if not _finite_time(now):
        raise ValueError("Monitor time must be finite")
    current = validate_state(state)
    status, findings = _validated_report(report)
    if status == AuditStatus.HEALTHY:
        if not current["alert_active"]:
            _clear_candidate(current)
            return MonitorResult(current, status, "not-needed")
        if not _observe_candidate(current, "recovery", now):
            return MonitorResult(current, status, "pending", "recovery")
        return _deliver(current, status, recovery_notification(), "recovery", sender, now)

    incident_key = _report_key(report, status, findings)
    if current["last_alert_key"] == incident_key:
        _clear_candidate(current)
        return MonitorResult(current, status, "deduplicated", "alert")
    candidate_key = f"alert:{status.value}:{incident_key}"
    if not _observe_candidate(current, candidate_key, now):
        return MonitorResult(current, status, "pending", "alert")
    counts = finding_counts(findings)
    return _deliver(current, status, alert_notification(status, counts), "alert", sender, now)
