"""The per-target re-decision made from a fresh table just before each signal.

Nothing captured when the request was planned is trusted except identities: the root's, the
target's and every member's (pid, start, uid). Kind, group membership, nesting and the
policy are all derived again from the fresh table, and a member the plan never saw stops
the action before any of its ancestors can be signalled. Ordering keeps this sound for whole-tree actions:
terminate, kill and resume go deepest first, so every target is re-checked before any of
its own ancestors has been signalled (a descendant is never orphaned by us before its own
check); pause and lower-priority go root first, and neither makes a process exit.
"""
from dataclasses import dataclass
from enum import Enum

from panel_contracts import ApiError, StopRecord
from process_table import TABLE_TIMEOUT, ProcessRow
from resource_groups import SESSION_KINDS, build_grouping
from signal_policy import Scope, tree_blocked

# Each target costs a full table read, so the whole mutation has a time bound.
MUTATION_BUDGET = 8.0



class Action(str, Enum):
    TERMINATE = 'terminate'
    KILL = 'kill'
    PAUSE = 'pause'
    RESUME = 'resume'
    LOWER_PRIORITY = 'lower-priority'


class Order(Enum):
    # Ending a parent first would reparent its surviving children to launchd, after which
    # they fail the membership check and are left running.
    DEEPEST_FIRST = 'deepest-first'
    # A stopped or deprioritised parent spawns nothing new while its children are handled.
    ROOT_FIRST = 'root-first'


class Outcome(str, Enum):
    """The reply's counts: one per target, or `not_reached` for each one a Halt left alone."""
    APPLIED = 'applied'
    GONE = 'gone'
    CHANGED = 'changed'
    SKIPPED = 'skipped'
    REFUSED = 'refused'
    UNCHANGED = 'unchanged'
    NOT_REACHED = 'not_reached'


class StopCode(str, Enum):
    TIME_LIMIT = 'time-limit'
    SESSION_CHANGED = 'session-changed'
    NESTED_SESSION = 'nested-session'
    UNREADABLE = 'unreadable'
    ROOT_CHANGED = 'root-changed'


STOP_TEXTS = {
    StopCode.TIME_LIMIT: 'Stopped at the time limit before every process was handled; refresh and retry',
    StopCode.SESSION_CHANGED: 'The session gained a process the plan had not seen; nothing more was signalled',
    StopCode.NESTED_SESSION: 'A session started inside this one; nothing more was signalled',
    StopCode.UNREADABLE: 'The process table could not be read again; nothing more was signalled',
    StopCode.ROOT_CHANGED: 'The session root exited or changed; nothing more was signalled',
}


ACTION_ORDER = {Action.TERMINATE: Order.DEEPEST_FIRST, Action.KILL: Order.DEEPEST_FIRST,
                Action.PAUSE: Order.ROOT_FIRST, Action.RESUME: Order.DEEPEST_FIRST,
                Action.LOWER_PRIORITY: Order.ROOT_FIRST}


@dataclass(frozen=True)
class SignalRequest:
    action: Action
    pid: int
    started: str
    scope: Scope


@dataclass(frozen=True)
class Target:
    # Captured at planning; only its identity (pid, started, uid) is relied on later.
    row: ProcessRow
    depth: int


@dataclass(frozen=True)
class Plan:
    request: SignalRequest
    root: ProcessRow
    # The session group id for a whole-tree action; None for one process.
    group_id: str | None
    targets: list[Target]
    skipped: int
    # (pid, start) of every member the plan saw, targets and skipped ones alike.
    members: frozenset = frozenset()


@dataclass(frozen=True)
class Halt:
    """Stop the whole action; every target not yet handled is counted as not reached."""
    code: StopCode
    # A policy reason that says more than the code's own text.
    detail: str | None = None

    def record(self) -> StopRecord:
        return StopRecord(code=self.code.value, text=self.detail or STOP_TEXTS[self.code])


def ordered(targets, action):
    if ACTION_ORDER[action] is Order.DEEPEST_FIRST:
        return sorted(targets, key=lambda target: -target.depth)
    return sorted(targets, key=lambda target: target.depth)


def same_process(fresh, captured) -> bool:
    return (fresh is not None and fresh.started == captured.started
            and fresh.uid == captured.uid)


def session_halt(policy, plan, fresh, grouping, context) -> Halt | None:
    """The root is still the same session root, whole, un-nested, unchanged in membership
    beyond what the plan saw, and allowed; else stop."""
    if not same_process(fresh.rows.get(plan.root.pid), plan.root):
        return Halt(StopCode.ROOT_CHANGED)
    group = grouping.groups.get(plan.group_id)
    if group is None or group.root.pid != plan.root.pid or group.kind not in SESSION_KINDS:
        return Halt(StopCode.ROOT_CHANGED)
    if group.nested:
        return Halt(StopCode.NESTED_SESSION)
    # Ending an ancestor of a process the plan never saw would orphan it, still running.
    if any((member.row.pid, member.row.started) not in plan.members for member in group.members):
        return Halt(StopCode.SESSION_CHANGED)
    reason = tree_blocked(policy, group, context)
    return Halt(StopCode.SESSION_CHANGED, reason) if reason else None


def verify(reader, policy, plan: Plan, target: Target, deadline, clock):
    """None when the target may be signalled now; else an Outcome for it or a Halt for all.

    The caller checks the deadline once more after this returns None, immediately before
    acting: the read, regrouping and policy all take time.
    """
    remaining = deadline - clock()
    if remaining <= 0:
        return Halt(StopCode.TIME_LIMIT)
    try:
        fresh = reader.table(timeout=min(TABLE_TIMEOUT, remaining))
    except ApiError:
        # The read was given only what was left of the budget, so running out under it is
        # the time limit, not an unreadable table.
        return Halt(StopCode.TIME_LIMIT if clock() >= deadline else StopCode.UNREADABLE)
    grouping = build_grouping(fresh)
    context = policy.context(fresh, grouping)
    if plan.group_id is not None:
        halt = session_halt(policy, plan, fresh, grouping, context)
        if halt is not None:
            return halt
    row = fresh.rows.get(target.row.pid)
    if row is None:
        return Outcome.GONE
    if not same_process(row, target.row):
        return Outcome.CHANGED
    if plan.group_id is not None and row.pid != plan.root.pid:
        # Exclusive membership by today's rules, over an ancestry with no gaps up to the root.
        if (grouping.membership.get(row.pid) != plan.group_id
                or not fresh.chain_reaches(row.pid, plan.root.pid)):
            return Outcome.CHANGED
    # An exec keeps the pid and start time but can turn it into a protected program.
    if policy.blocked(row, grouping.kind(row.pid), plan.request.scope, context):
        return Outcome.CHANGED
    return None
