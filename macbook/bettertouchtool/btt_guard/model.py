"""Canonical persisted/wire contracts for BTT Guard (schema version 1)."""
from enum import Enum
from typing import Any, Optional, TypedDict

SCHEMA_VERSION = 1


class AuditStatus(str, Enum):
    HEALTHY = 'healthy'
    DRIFT = 'drift'
    UNAVAILABLE = 'unavailable'


class InstallerCheckpointOperation(str, Enum):
    BEFORE = 'before'
    AFTER = 'after'


class InstallerCheckpointStatus(str, Enum):
    TAKEN = 'taken'
    SKIPPED = 'skipped'
    FAILED = 'failed'


class InstallerCheckpointReason(str, Enum):
    OPT_OUT = 'opt_out'
    NO_WRITES = 'no_writes'
    NO_CHECKPOINT = 'no_checkpoint'
    BASELINE_DRIFT = 'baseline_drift'
    BASELINE_UNAVAILABLE = 'baseline_unavailable'
    BASELINE_FAILED = 'baseline_failed'
    CHECKPOINT_CHANGED = 'checkpoint_changed'
    FOREIGN_FINDINGS = 'foreign_findings'
    AUDIT_UNAVAILABLE = 'audit_unavailable'
    AUDIT_FAILED = 'audit_failed'
    INVALID_EDITS = 'invalid_edits'
    SAVED_UNVERIFIED = 'saved_unverified'
    CAPTURE_FAILED = 'capture_failed'
    EXPORT_UNAVAILABLE = 'export_unavailable'
    HELPER_FAILED = 'helper_failed'


class ExportUnavailableError(RuntimeError):
    """The owning user's BTT Apple Events export could not be read."""


class EditAcceptanceAction(str, Enum):
    """The scheduled monitor's decision about drift made inside a running BTT."""
    NOT_NEEDED = 'not_needed'
    SETTLING = 'settling'
    ACCEPTED = 'accepted'
    REFUSED = 'refused'
    INELIGIBLE = 'ineligible'


class EditAcceptanceReason(str, Enum):
    CAPTURE_REFUSED = 'capture_refused'
    EXPORT_UNAVAILABLE = 'export_unavailable'
    CAPTURE_FAILED = 'capture_failed'


class AcceptanceStateKind(str, Enum):
    NONE = 'none'
    OK = 'ok'
    UNREADABLE = 'unreadable'


class FindingKind(str, Enum):
    MISSING = 'missing'
    REPARENTED = 'reparented'
    PRESET_CHANGED = 'preset_changed'
    DISABLED = 'disabled'
    ENABLED_CHANGED = 'enabled_changed'
    TYPE_CHANGED = 'type_changed'
    ORDER_CHANGED = 'order_changed'
    ACTION_CHANGED = 'action_changed'
    CONFIG_CHANGED = 'config_changed'
    DEPENDENCY_CHANGED = 'dependency_changed'
    UNASSIGNED = 'unassigned'
    CHECKPOINT_MISSING = 'checkpoint_missing'
    CHECKPOINT_INVALID = 'checkpoint_invalid'
    DATABASE_UNAVAILABLE = 'database_unavailable'
    UNSUPPORTED_SCHEMA = 'unsupported_schema'
    MONITOR_STATE_INVALID = 'monitor_state_invalid'


class ServiceVerb(str, Enum):
    PREPARE = 'prepare'
    INSTALL = 'install'
    STATUS = 'status'
    START = 'start'
    STOP = 'stop'
    VERIFY = 'verify'
    UNINSTALL = 'uninstall'


class Finding(TypedDict):
    kind: str
    uuid: str
    detail: str  # Field names/diagnostic categories only, never action payloads.


class RecordState(TypedDict):
    uuid: str
    parent: Optional[str]
    preset: Optional[str]
    app_scope: list[str]
    trigger_type: int
    enabled: bool
    order: int
    action_type: int
    action_category: int
    payload: dict[str, Any]
    config: dict[str, Any]


class ConfigSnapshot(TypedDict):
    schema_version: int
    captured_at: float
    btt_version: str
    database_name: str
    roots: list[str]
    presets: dict[str, bool]
    records: dict[str, RecordState]
    findings: list[Finding]


class AuditReport(TypedDict):
    status: str
    checkpoint_id: Optional[str]
    checked_at: float
    btt_version: Optional[str]
    findings: list[Finding]


class InstallerBaseline(TypedDict):
    state_dir: str
    audit: Optional[AuditReport]
    reason: Optional[str]


class InstallerCheckpointRequest(TypedDict, total=False):
    """Fixed package-bridge request; AFTER requires all fields."""
    operation: str
    baseline: InstallerBaseline
    touched_uuids: list[str]
    installer_name: str
    wrote: bool


class InstallerCheckpointResult(TypedDict):
    status: str
    reason: Optional[str]
    checkpoint_id: Optional[str]


class InstallerEditReceipt(TypedDict):
    """Verified worker mutations, returned by the guarded native installers."""
    message: str
    wrote: bool
    touched_uuids: list[str]


class ToolsInstallerReceipt(InstallerEditReceipt):
    """install_tools_menu_item.js output; saved_plan uses btt_common.disk_errors."""
    saved_plan: dict[str, Any]


class BttProcess(TypedDict):
    pid: int
    started: str


class AcceptanceBaseline(TypedDict):
    """The BTT session seen at the last healthy scheduled audit."""
    checkpoint_id: str
    btt_version: Optional[str]
    process: BttProcess


class AcceptanceCandidate(TypedDict):
    key: str
    since: float


class AcceptanceEvent(TypedDict):
    at: float
    checkpoint_id: Optional[str]
    reason: Optional[str]
    findings: int


class EditAcceptanceState(TypedDict):
    version: int
    baseline: Optional[AcceptanceBaseline]
    candidate: Optional[AcceptanceCandidate]
    refused_key: Optional[str]
    last_accepted: Optional[AcceptanceEvent]
    last_refused: Optional[AcceptanceEvent]


class AcceptanceStatus(TypedDict):
    """Read-only `guard acceptance` output for the Mac Panel."""
    state: str
    settle_seconds: int
    pending_since: Optional[float]
    accepts_at: Optional[float]
    last_accepted: Optional[AcceptanceEvent]
    last_refused: Optional[AcceptanceEvent]


class PruneResult(TypedDict):
    """`guard prune` output: `entries` are `checkpoints/<id>` or `rejected-captures/<id>`."""
    applied: bool
    retention_days: int
    entries: list


class CheckpointManifest(TypedDict):
    schema_version: int
    checkpoint_id: str
    created_at: float
    btt_version: str
    roots: list[str]
    files: dict[str, str]  # Relative artifact path -> sha256.


class RepairOperation(TypedDict):
    uuid: str
    parent: Optional[str]
    mode: str  # create or reattach; rewrites of changed payloads are not automatic.
    definition: dict[str, Any]


class RepairPlanBase(TypedDict):
    schema_version: int
    checkpoint_id: str
    operations: list[RepairOperation]
    conflicts: list[Finding]


class RepairPlan(RepairPlanBase, total=False):
    runtime_snapshot: dict[str, Any]


class MonitorHealth(TypedDict):
    schema_version: int
    run_id: str
    pid: int
    uid: int
    root: str
    completed_at: float
    audit_status: str
