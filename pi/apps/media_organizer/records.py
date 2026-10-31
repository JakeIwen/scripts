"""Wire records shared by the CLI, the dashboard and the qBittorrent hook.

Every record round-trips through ``to_dict``/``from_dict`` as plain JSON.  The
dashboard's React feature mirrors these shapes in its schema; change both.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from .config import CATEGORIES, CATEGORY_MOVIES

# Why an item in New is not a candidate this run.
SKIP_INCOMPLETE = "incomplete"      # torrent still downloading or checking
SKIP_MOVING = "moving"              # qBittorrent is relocating it right now
SKIP_UNREADABLE = "unreadable"      # stat failed or path vanished mid-scan
SKIP_NOT_MEDIA = "not-media"        # a loose file too small to be a video (logs, scripts)
SKIP_REASONS = (SKIP_INCOMPLETE, SKIP_MOVING, SKIP_UNREADABLE, SKIP_NOT_MEDIA)

CLASSIFIER_CODEX = "codex"
CLASSIFIER_OFFLINE = "offline"
CLASSIFIERS = (CLASSIFIER_CODEX, CLASSIFIER_OFFLINE)

PLAN_PROPOSED = "proposed"
PLAN_APPLIED = "applied"      # every accepted row moved
PLAN_PARTIAL = "partial"      # some accepted rows moved, some failed or left
PLAN_DISCARDED = "discarded"  # superseded by a newer plan
PLAN_STATUSES = (PLAN_PROPOSED, PLAN_APPLIED, PLAN_PARTIAL, PLAN_DISCARDED)

ITEM_MOVED = "moved"
ITEM_FAILED = "failed"
ITEM_REVERTED = "reverted"
ITEM_STATUSES = (ITEM_MOVED, ITEM_FAILED, ITEM_REVERTED)

RUN_MANUAL = "manual"
RUN_AUTO = "auto"
RUN_MODES = (RUN_MANUAL, RUN_AUTO)

ALIAS_SCHEDULED = "scheduled"
ALIAS_SKIPPED = "skipped"
ALIAS_FAILED = "failed"
ALIAS_STATUSES = (ALIAS_SCHEDULED, ALIAS_SKIPPED, ALIAS_FAILED)

JOB_SCAN = "scan"
JOB_PROPOSE = "propose"
JOB_APPLY = "apply"
JOB_UNDO = "undo"
JOB_ALIAS = "alias"
JOB_STAGES = (JOB_SCAN, JOB_PROPOSE, JOB_APPLY, JOB_UNDO, JOB_ALIAS)


@dataclass
class ScanEntry:
    """One top-level entry of ``torrent/New``."""

    name: str
    path: str
    kind: str                      # "dir" | "file"
    size_bytes: int
    torrent_hash: str | None       # qBittorrent id; None when untracked
    torrent_name: str | None
    torrent_state: str | None
    tracked: bool
    complete: bool                 # payload fully downloaded (True for untracked)
    skip_reason: str | None        # one of SKIP_REASONS, or None when eligible

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ScanEntry":
        return cls(**data)


@dataclass
class Proposal:
    """Where the classifier thinks one entry belongs."""

    category: str                  # one of CATEGORIES
    show: str | None               # show folder for TV/Documentaries, None for Movies
    confidence: float              # 0..1
    reason: str
    existing_show: bool            # show matches a folder already on disk
    source: str                    # one of CLASSIFIERS

    def __post_init__(self) -> None:
        if self.category not in CATEGORIES:
            raise ValueError(f"unknown category: {self.category!r}")
        if self.category == CATEGORY_MOVIES:
            self.show = None
        elif not self.show:
            raise ValueError(f"{self.category} proposal needs a show")
        if not 0.0 <= float(self.confidence) <= 1.0:
            raise ValueError("confidence must be within 0..1")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Proposal":
        return cls(**data)


@dataclass
class Decision:
    """What the reviewer (or the auto rule) settled on for a row."""

    category: str
    show: str | None
    accepted: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Decision":
        return cls(**data)


@dataclass
class PlanRow:
    entry: ScanEntry
    proposal: Proposal | None      # None when the classifier had no answer
    decision: Decision | None      # None for skipped entries
    target_path: str | None        # computed from decision; None until decided
    auto_eligible: bool            # passes the auto-apply rule as proposed

    def to_dict(self) -> dict[str, Any]:
        return {
            "entry": self.entry.to_dict(),
            "proposal": self.proposal.to_dict() if self.proposal else None,
            "decision": self.decision.to_dict() if self.decision else None,
            "target_path": self.target_path,
            "auto_eligible": self.auto_eligible,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PlanRow":
        return cls(
            entry=ScanEntry.from_dict(data["entry"]),
            proposal=Proposal.from_dict(data["proposal"]) if data.get("proposal") else None,
            decision=Decision.from_dict(data["decision"]) if data.get("decision") else None,
            target_path=data.get("target_path"),
            auto_eligible=bool(data.get("auto_eligible", False)),
        )


@dataclass
class Plan:
    id: str                        # UTC timestamp slug, e.g. 20261029T061500Z
    created_at: str                # ISO 8601 UTC
    status: str                    # one of PLAN_STATUSES
    classifier: str                # one of CLASSIFIERS
    existing_shows: dict[str, list[str]]   # category -> sorted show folders
    rows: list[PlanRow] = field(default_factory=list)
    run_ids: list[str] = field(default_factory=list)
    classifier_error: str | None = None    # why the LLM answer was unusable

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "created_at": self.created_at,
            "status": self.status,
            "classifier": self.classifier,
            "existing_shows": self.existing_shows,
            "rows": [row.to_dict() for row in self.rows],
            "run_ids": list(self.run_ids),
            "classifier_error": self.classifier_error,
        }

    def summary(self) -> dict[str, Any]:
        accepted = sum(1 for r in self.rows if r.decision and r.decision.accepted)
        return {
            "id": self.id,
            "created_at": self.created_at,
            "status": self.status,
            "classifier": self.classifier,
            "row_count": len(self.rows),
            "accepted_count": accepted,
            "auto_count": sum(1 for r in self.rows if r.auto_eligible),
            "classifier_error": self.classifier_error,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Plan":
        return cls(
            id=data["id"],
            created_at=data["created_at"],
            status=data["status"],
            classifier=data["classifier"],
            existing_shows={k: list(v) for k, v in data.get("existing_shows", {}).items()},
            rows=[PlanRow.from_dict(r) for r in data.get("rows", [])],
            run_ids=list(data.get("run_ids", [])),
            classifier_error=data.get("classifier_error"),
        )


@dataclass
class RunItem:
    name: str
    torrent_hash: str | None
    old_path: str
    new_path: str
    mirror_old: str | None         # mirror rename applied, when any
    mirror_new: str | None
    status: str                    # one of ITEM_STATUSES
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RunItem":
        return cls(**data)


@dataclass
class Run:
    """The journal of one apply (or the undo of one)."""

    id: str
    plan_id: str
    mode: str                      # one of RUN_MODES
    started_at: str
    finished_at: str | None
    items: list[RunItem] = field(default_factory=list)
    alias_status: str | None = None     # one of ALIAS_STATUSES
    undoable: bool = False
    undone_at: str | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["items"] = [item.to_dict() for item in self.items]
        return data

    def summary(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "plan_id": self.plan_id,
            "mode": self.mode,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "moved_count": sum(1 for i in self.items if i.status == ITEM_MOVED),
            "failed_count": sum(1 for i in self.items if i.status == ITEM_FAILED),
            "reverted_count": sum(1 for i in self.items if i.status == ITEM_REVERTED),
            "alias_status": self.alias_status,
            "undoable": self.undoable,
            "undone_at": self.undone_at,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Run":
        return cls(
            id=data["id"],
            plan_id=data["plan_id"],
            mode=data["mode"],
            started_at=data["started_at"],
            finished_at=data.get("finished_at"),
            items=[RunItem.from_dict(i) for i in data.get("items", [])],
            alias_status=data.get("alias_status"),
            undoable=bool(data.get("undoable", False)),
            undone_at=data.get("undone_at"),
            error=data.get("error"),
        )


@dataclass
class Settings:
    """Organizer preferences; stored only when they differ from these defaults."""

    auto_apply: bool = True                 # the completion hook may move things
    auto_apply_min_confidence: float = 0.85
    auto_apply_new_shows: bool = False      # a show with no folder yet waits for review
    classifier: str = CLASSIFIER_CODEX
    codex_model: str = ""                   # empty: Codex's configured default
    mirror_rename: bool = True              # rename the backup copy too
    run_alias_after_apply: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Settings":
        known = {f: data[f] for f in cls.__dataclass_fields__ if f in data}
        settings = cls(**known)
        if settings.classifier not in CLASSIFIERS:
            raise ValueError(f"unknown classifier: {settings.classifier!r}")
        if not 0.0 <= float(settings.auto_apply_min_confidence) <= 1.0:
            raise ValueError("auto_apply_min_confidence must be within 0..1")
        return settings


@dataclass
class Status:
    """What the dashboard tile shows at a glance (``status --json``)."""

    media_mounted: bool
    mirror_available: bool
    qbittorrent_available: bool
    new_count: int
    tracked_count: int
    untracked_count: int
    incomplete_count: int
    latest_plan: dict[str, Any] | None     # Plan.summary()
    recent_runs: list[dict[str, Any]]      # Run.summary(), newest first
    settings: dict[str, Any]
    busy: dict[str, Any] | None            # {"stage": JOB_*, "started_at": iso, "pid": int} when a
                                           # job runs, plus busy.note()'s detail/done/total progress
    last_error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
