"""Pure display identity and plug-in decisions."""
from dataclasses import dataclass
from enum import Enum
import re

MONITOR_NAME = "Q24h-10"
MONITOR_VENDOR = 12462
MONITOR_MODEL = 26280
MIRROR_NAME = "mirror"
WAKE_SETTLE_SECONDS = 5.0
_DISPLAY_NUMBER_SUFFIX = re.compile(r" \(\d+\)$")
DisplayKey = tuple[int, int, int]


class DimOutcome(str, Enum):
    DIMMED = "dimmed"
    NOTHING_TO_DO = "nothing-to-do"
    DISABLED = "disabled"
    FAILED = "failed"


class RunTrigger(str, Enum):
    PLUG_IN = "plug-in"
    MANUAL = "manual"


@dataclass(frozen=True)
class DimResult:
    outcome: DimOutcome
    changed: int
    detail: str


@dataclass(frozen=True)
class DisplayInfo:
    display_id: int
    name: str
    built_in: bool
    vendor: int
    model: int
    unit: int
    serial: int = 0

    @property
    def key(self) -> DisplayKey:
        return (self.vendor, self.model, self.unit)

    @property
    def matches_target(self) -> bool:
        return not self.built_in and (self.vendor, self.model) == (MONITOR_VENDOR, MONITOR_MODEL)

    @property
    def label(self) -> str:
        return self.name if display_name_matches(self.name) else f"{MONITOR_NAME} ({self.name})"


def display_name_matches(name: str) -> bool:
    return _DISPLAY_NUMBER_SUFFIX.sub("", name) == MONITOR_NAME


def matching_snapshot(displays: list[DisplayInfo]) -> frozenset[DisplayKey]:
    return frozenset(display.key for display in displays if display.matches_target)


def matching_monitor_names(displays: list[DisplayInfo]) -> list[str]:
    return sorted(display.label for display in displays if display.matches_target)


class PlugTracker:
    """Decide whether a settled matching-display snapshot is a plug-in event."""

    def __init__(self, initial_snapshot: frozenset[DisplayKey]):
        self._baseline = frozenset(initial_snapshot)
        self._deadline: float | None = None
        self._asleep = False

    @property
    def asleep(self) -> bool:
        return self._asleep

    def schedule(self, now: float, settle_seconds: float) -> None:
        if self._asleep:
            return
        candidate = now + settle_seconds
        self._deadline = candidate if self._deadline is None else max(self._deadline, candidate)

    def will_sleep(self) -> None:
        self._asleep = True
        self._deadline = None

    def did_wake(self, now: float, settle_seconds: float) -> None:
        self._asleep = False
        self.schedule(now, max(settle_seconds, WAKE_SETTLE_SECONDS))

    def due(self, now: float) -> bool:
        return not self._asleep and self._deadline is not None and now >= self._deadline

    def evaluate(self, snapshot: frozenset[DisplayKey], now: float, *, fallback: bool = False) -> bool:
        if self._asleep:
            return False
        if self._deadline is not None:
            if now < self._deadline:
                return False
            self._deadline = None
        elif not fallback:
            return False
        current = frozenset(snapshot)
        plugged_in = bool(current - self._baseline)
        self._baseline = current
        return plugged_in

    def abandon_pending_check(self) -> None:
        self._deadline = None
