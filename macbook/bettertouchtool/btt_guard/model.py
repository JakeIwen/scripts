"""Canonical persisted/wire contracts for BTT Guard (schema version 1)."""
from enum import Enum
from typing import Any, Optional, TypedDict

SCHEMA_VERSION = 1


class AuditStatus(str, Enum):
    HEALTHY = 'healthy'
    DRIFT = 'drift'
    UNAVAILABLE = 'unavailable'


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
