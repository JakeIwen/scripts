"""The per-target re-decision made from a fresh table just before each signal.

Nothing captured when the request was planned is trusted except identities: the root's and
the target's (pid, start, uid). Kind, group membership, nesting and the policy are all
derived again from the fresh table. Ordering keeps this sound for whole-tree actions:
terminate, kill and resume go deepest first, so every target is re-checked before any of
its own ancestors has been signalled (a descendant is never orphaned by us before its own
check); pause and lower-priority go root first, and neither makes a process exit.
"""
from dataclasses import dataclass
from enum import Enum

from panel_contracts import ApiError
from process_table import TABLE_TIMEOUT, ProcessRow
from resource_groups import SESSION_KINDS, build_grouping
from signal_policy import Scope, tree_blocked

# A whole-tree action re-reads the full table once per target (about 80 ms each), so the
# tree is capped and the whole mutation has a time bound.
TREE_TARGET_LIMIT = 48
MUTATION_BUDGET = 8.0

STOPPED_TIME = 'Stopped at the time limit before every process was handled; refresh and retry'
STOPPED_UNREADABLE = 'The process table could not be read again; nothing more was signalled'
STOPPED_ROOT = 'The session root exited or changed; nothing more was signalled'
STOPPED_NESTED = 'A session started inside this one; nothing more was signalled'
STOPPED_TOO_LARGE = 'The session grew past the size limit; nothing more was signalled'


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
    APPLIED = 'applied'
    GONE = 'gone'
    CHANGED = 'changed'
    SKIPPED = 'skipped'
    REFUSED = 'refused'
    UNCHANGED = 'unchanged'


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


@dataclass(frozen=True)
class Halt:
    """Stop the whole action: `outcome` is counted for every target not yet handled."""
    outcome: Outcome
    reason: str


def ordered(targets, action):
    if ACTION_ORDER[action] is Order.DEEPEST_FIRST:
        return sorted(targets, key=lambda target: -target.depth)
    return sorted(targets, key=lambda target: target.depth)


def same_process(fresh, captured) -> bool:
    return (fresh is not None and fresh.started == captured.started
            and fresh.uid == captured.uid)


def session_halt(policy, plan, fresh, grouping, context) -> Halt | None:
    """The root is still the same session root, whole, un-nested and allowed; else stop."""
    if not same_process(fresh.rows.get(plan.root.pid), plan.root):
        return Halt(Outcome.CHANGED, STOPPED_ROOT)
    group = grouping.groups.get(plan.group_id)
    if group is None or group.root.pid != plan.root.pid or group.kind not in SESSION_KINDS:
        return Halt(Outcome.CHANGED, STOPPED_ROOT)
    if group.nested:
        return Halt(Outcome.CHANGED, STOPPED_NESTED)
    if len(group.members) > TREE_TARGET_LIMIT:
        return Halt(Outcome.CHANGED, STOPPED_TOO_LARGE)
    reason = tree_blocked(policy, group, context)
    return Halt(Outcome.CHANGED, reason) if reason else None


def verify(reader, policy, plan: Plan, target: Target, deadline, clock, jobs_root):
    """None when the target may be signalled now; else an Outcome for it or a Halt for all."""
    remaining = deadline - clock()
    if remaining <= 0:
        return Halt(Outcome.SKIPPED, STOPPED_TIME)
    try:
        fresh = reader.table(timeout=min(TABLE_TIMEOUT, remaining))
    except ApiError:
        return Halt(Outcome.CHANGED, STOPPED_UNREADABLE)
    grouping = build_grouping(fresh, jobs_root)
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
