"""Durable retirement records and side-effect-free weekly decisions."""
from dataclasses import asdict, dataclass
from enum import Enum


STATE_SCHEMA = 1


class ProbeOutcome(str, Enum):
    SHIPPED = "shipped"
    NEGATIVE = "negative"
    ERROR = "error"


@dataclass(frozen=True)
class ProbeResult:
    outcome: ProbeOutcome
    version: str = "unknown"
    turn_requests: int = 0
    returncode: object = None
    detail: str = ""


@dataclass(frozen=True)
class PRStatus:
    state: str = "unknown"
    merged: bool = False
    merged_at: object = None
    error: str = ""


class Notice(str, Enum):
    RETIRED = "retirement_notified"
    CLOSED_UNMERGED = "closed_unmerged_notified"
    PROBE_ERRORS = "probe_error_notified"


@dataclass
class RetirementState:
    retired: bool = False
    pi_disabled: bool = False
    retirement_notified: bool = False
    closed_unmerged_notified: bool = False
    probe_error_streak: int = 0
    probe_error_notified: bool = False
    last_run: dict = None

    def as_dict(self):
        return {"schema": STATE_SCHEMA, **asdict(self)}

    @classmethod
    def from_dict(cls, data):
        if (not isinstance(data, dict) or type(data.get("schema")) is not int
                or data["schema"] != STATE_SCHEMA):
            raise ValueError("invalid retirement state schema")
        names = set(cls.__dataclass_fields__)
        if set(data) != names | {"schema"}:
            raise ValueError("invalid retirement state fields")
        for name in names - {"probe_error_streak", "last_run"}:
            if not isinstance(data[name], bool):
                raise ValueError("invalid retirement state flag: " + name)
        count = data["probe_error_streak"]
        if type(count) is not int or count < 0 or not isinstance(data["last_run"], (dict, type(None))):
            raise ValueError("invalid retirement probe history")
        if data["last_run"] is not None:
            previous = data["last_run"].get("probe")
            if not isinstance(previous, dict) or not isinstance(previous.get("version"), str):
                raise ValueError("invalid saved retirement probe")
        return cls(**{name: data[name] for name in names})


@dataclass(frozen=True)
class Decision:
    retire: bool = False
    notices: tuple = ()


def decide(state, probe, pr):
    if probe.outcome == ProbeOutcome.SHIPPED:
        notices = () if state.retirement_notified else (Notice.RETIRED,)
        return Decision(True, notices)
    if probe.outcome == ProbeOutcome.ERROR:
        notices = ((Notice.PROBE_ERRORS,) if state.probe_error_streak >= 2
                   and not state.probe_error_notified else ())
        return Decision(notices=notices)
    notices = ((Notice.CLOSED_UNMERGED,) if pr.state == "closed" and not pr.merged
               and not state.closed_unmerged_notified else ())
    return Decision(notices=notices)


def record_probe(state, probe, pr, checked_at):
    if probe.outcome == ProbeOutcome.ERROR:
        state.probe_error_streak += 1
    else:
        state.probe_error_streak = 0
        state.probe_error_notified = False
    state.last_run = {"checked_at": checked_at, "probe": asdict(probe), "pr": asdict(pr)}
