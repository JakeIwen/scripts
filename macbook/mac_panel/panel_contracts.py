"""Wire vocabulary and response records for Mac Panel."""
from dataclasses import dataclass
from enum import Enum
from typing import Any, TypedDict


class Section(str, Enum):
    METERED = 'metered'
    AMPHETAMINE = 'amphetamine'
    SETTINGS = 'settings'
    AUTOMATIONS = 'automations'
    DEFERRED = 'deferred'
    SERVICES = 'services'
    CRONTAB = 'crontab'
    RESTORE = 'llm-restore'
    GUARD = 'guard'
    NTFY = 'ntfy'
    USAGE = 'usage'
    LOGS = 'logs'


class TaskState(str, Enum):
    RUNNING = 'running'
    DONE = 'done'
    FAILED = 'failed'


class JobState(str, Enum):
    PENDING = 'pending'
    FAILED = 'failed'


class ServiceKind(str, Enum):
    AGENT = 'agent'
    DAEMON = 'daemon'


class LogStream(str, Enum):
    STDOUT = 'stdout'
    STDERR = 'stderr'


class ApiError(Exception):
    def __init__(self, status: int, message: str, code: str | None = None):
        super().__init__(message)
        self.status = status
        # A machine-readable reason the page may act on (a busy rotator, say); None for plain errors.
        self.code = code


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


class SettingsToolRecord(TypedDict):
    tool: str
    # tool_settings' Declaration and SettingsState dicts; both None when the
    # declaration can't be loaded, and error then says why.
    declaration: dict | None
    state: dict | None
    error: str | None


class SettingsPayload(TypedDict):
    tools: list[SettingsToolRecord]


AutomationActionRecord = TypedDict('AutomationActionRecord', {
    'name': str, 'label': str, 'description': str, 'confirm': bool, 'async': bool,
})


class AutomationStatusField(TypedDict):
    label: str
    value: str | None
    time: float | None
    level: str | None


class AutomationStatusRecord(TypedDict):
    level: str
    summary: str
    fields: list[AutomationStatusField]


class AutomationRecord(TypedDict):
    tool: str
    title: str
    description: str
    service: str | None
    settings: str | None
    actions: list[AutomationActionRecord]
    status: AutomationStatusRecord | None
    error: str | None


class AutomationsPayload(TypedDict):
    tasks: list[TaskRecord]
    automations: list[AutomationRecord]


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
    # True only for an unloaded job that launchd lists as disabled (stopped on purpose).
    disabled: bool
    state: str
    pid: int | None
    last_exit: int | None
    plist: str | None
    logs: list[ServiceLogMeta]
    actions: list[str]
    sudo_rule: str
    error: str | None


class ServicesPayload(TypedDict):
    services: list[ServiceRecord]
    sudo_install_command: str


class ServiceLogsPayload(TypedDict):
    label: str
    logs: list[LogRecord]


class CronJob(TypedDict):
    next_runs: list[float]
    id: str
    expression: str
    schedule: str
    command: str
    program: str
    description: str
    log_path: str | None
    log_modified: float | None
    paused: bool
    managed_block: str | None
    managed_switch: str | None


class CrontabPayload(TypedDict):
    tasks: list[TaskRecord]
    revision: str
    uncommitted: bool
    jobs: list[CronJob]
    in_sync: bool
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
    # `guard acceptance`: the scheduled monitor's pending and last automatic acceptances.
    acceptance: dict


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


class HistoryPoint(TypedDict):
    ts: float
    level: str
    value: float | None
    text: str | None


class HistorySample(HistoryPoint):
    section: str
    key: str


class HistoryPayload(TypedDict):
    series: dict[str, list[HistoryPoint]]
    spacing: dict[str, float]


class UsageWindow(TypedDict):
    used: float
    left: float
    resets_at: str | None


class UsageCredits(TypedDict, total=False):
    left: float
    allowance_per_day: float
    burn_per_day: float
    expires: str
    days_left: float


class ClaudePosition(TypedDict, total=False):
    left: float
    elapsed_pct: float
    line_left: float
    ahead: float
    resets_in_h: float


class GptPosition(ClaudePosition, total=False):
    on_credits: bool


class UsageAvailability(TypedDict):
    claude: str
    gpt: str


class _UsageRequired(TypedDict):
    fetched_at: str
    age_minutes: float
    claude_weekly: UsageWindow | None
    claude_session: UsageWindow | None
    fable_weekly: UsageWindow | None
    codex_weekly: UsageWindow | None
    codex_session: UsageWindow | None
    credits: UsageCredits | None
    availability: UsageAvailability
    reasons: list[str]
    route: dict[str, str]


class UsagePayload(_UsageRequired, total=False):
    claude_position: ClaudePosition | None
    gpt_position: GptPosition
    lean: str
    lines: list[str]
    mode_source: str
    expires_at: str
    availability_detail: dict[str, dict[str, Any]]


class LogPolicy(TypedDict):
    max_mb: float
    keep: int
    compress: bool
    enabled: bool
    override: bool


class LogEntry(TypedDict):
    path: str
    source: str
    bytes: int
    archived_bytes: int
    archives: int
    mtime: float | None
    growth_mb_per_day: float | None
    policy: LogPolicy
    last_rotated: float | None
    state: str
    note: str | None


class LogsPayload(TypedDict):
    generated_at: float | None
    revision: str
    defaults: dict
    totals: dict
    logs: list[LogEntry]
    ignored: list[str]
    # The log-rotate LaunchAgent: installed, loaded (None = launchd unreadable), pid, last exit.
    service: dict


class Level(str, Enum):
    OK = 'ok'
    IDLE = 'idle'
    UNKNOWN = 'unknown'
    WARN = 'warn'
    ERROR = 'error'


class ServiceAction(str, Enum):
    RESTART = 'restart'
    START = 'start'
    STOP = 'stop'


class SudoRule(str, Enum):
    PRESENT = 'present'
    MISSING = 'missing'
    NOT_APPLICABLE = 'n/a'
