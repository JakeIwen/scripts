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


class SettingsPayload(TypedDict):
    tools: list[dict]


class DeferredPayload(TypedDict):
    checker: dict
    jobs_dir: str
    jobs: list[dict]
    tasks: list[TaskRecord]


class LogRecord(TypedDict):
    path: str
    text: str
    modified: float | None
    error: str | None


class ServiceRecord(TypedDict):
    label: str
    kind: str
    loaded: bool | None
    state: str
    pid: int | None
    last_exit: int | None
    plist: str | None
    logs: list[LogRecord]
    error: str | None


class ServicesPayload(TypedDict):
    services: list[ServiceRecord]


class CronJob(TypedDict):
    expression: str
    schedule: str
    command: str
    description: str
    owner_kind: str
    owner: str
    log_path: str | None
    log_modified: float | None


class CrontabPayload(TypedDict):
    jobs: list[CronJob]
    in_sync: bool
    order_changed: bool
    live_only: list[str]
    repo_only: list[str]


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
