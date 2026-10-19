"""Wire vocabulary and response records for Mac Panel."""
from dataclasses import dataclass
from enum import Enum
from typing import Any, TypedDict


class Section(str, Enum):
    SETTINGS = 'settings'
    DEFERRED = 'deferred'
    SERVICES = 'services'
    CRONTAB = 'crontab'
    RESTORE = 'llm-restore'
    GUARD = 'guard'
    NTFY = 'ntfy'


class TaskState(str, Enum):
    RUNNING = 'running'
    DONE = 'done'
    FAILED = 'failed'


class JobState(str, Enum):
    PENDING = 'pending'
    FAILED = 'failed'


class OwnerKind(str, Enum):
    TRACKED = 'tracked'
    MANAGED = 'managed'
    LIVE = 'live-only'


class ServiceKind(str, Enum):
    AGENT = 'agent'
    DAEMON = 'daemon'


class LogStream(str, Enum):
    STDOUT = 'stdout'
    STDERR = 'stderr'


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class Reply:
    body: Any
    status: int = 200


class TaskRecord(TypedDict):
    id: str
    job: str
    state: str
    exit_status: int | None
    message: str
    started_at: float
    finished_at: float | None


class SettingsPayload(TypedDict):
    tools: list[dict]


class DeferredPayload(TypedDict):
    checker: dict
    jobs_dir: str
    jobs: list[dict]
    tasks: list[TaskRecord]


class ServiceLogMeta(TypedDict):
    """A declared log without its text; the inventory ships this every poll."""
    path: str
    stream: str
    modified: float | None
    error: str | None


class LogRecord(ServiceLogMeta):
    """A bounded, redacted tail, fetched only for a service the page has open."""
    text: str


class ServiceRecord(TypedDict):
    label: str
    kind: str
    loaded: bool | None
    state: str
    pid: int | None
    last_exit: int | None
    plist: str | None
    logs: list[ServiceLogMeta]
    error: str | None


class ServicesPayload(TypedDict):
    services: list[ServiceRecord]


class ServiceLogsPayload(TypedDict):
    label: str
    logs: list[LogRecord]


class CronJob(TypedDict):
    id: str
    expression: str
    schedule: str
    command: str
    program: str
    description: str
    owner_kind: str
    owner: str
    log_path: str | None
    log_modified: float | None
    paused: bool
    managed_block: str | None
    managed_switch: str | None


class CrontabPayload(TypedDict):
    revision: str
    uncommitted: bool
    jobs: list[CronJob]
    in_sync: bool
    order_changed: bool
    live_only: list[str]
    repo_only: list[str]
    installable: bool


class RestorePayload(TypedDict):
    enabled: bool
    installed: bool
    up_to_date: bool
    last_snapshot: float | None
    sessions_open: int | None


class GuardPayload(TypedDict):
    checkpoints: list[dict]
    audit: dict
    service: dict


class AlertRecord(TypedDict):
    source: str
    event: str
    matcher: str
    script: str
    destination: str
    configuration: str


class NtfyPayload(TypedDict):
    alerts: list[AlertRecord]
    errors: list[str]


class Ownership(TypedDict):
    pid: int
    instance: str
    token: str
    port: int
    started_at: float


class Health(TypedDict):
    ok: bool
    pid: int
    instance: str
    started_at: float
    source_changed: bool
