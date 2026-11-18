"""Carry out one confirmed signal request against the user's own processes.

Every request starts from a fresh process table (never the cached snapshot), and each
target is re-read and re-checked against the policy immediately before it is signalled.
That narrows, but cannot close, the window in which a pid could be reused between the
last check and `os.kill`.
"""
from collections import Counter
from dataclasses import dataclass
from enum import Enum
import os
import signal

from panel_contracts import ApiError, SignalResult
from process_table import ProcessRow
from resource_groups import SESSION_KINDS, Kind, build_grouping
from signal_policy import Scope, SignalPolicy, tree_blocked
from wire_validation import confirm, field, object_fields

STARTED_LIMIT = 64
# lower-priority sets at least this nice value and never raises priority back.
NICE_FLOOR = 10


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


ACTION_SIGNALS = {Action.TERMINATE: signal.SIGTERM, Action.KILL: signal.SIGKILL,
                  Action.PAUSE: signal.SIGSTOP, Action.RESUME: signal.SIGCONT}
ACTION_ORDER = {Action.TERMINATE: Order.DEEPEST_FIRST, Action.KILL: Order.DEEPEST_FIRST,
                Action.PAUSE: Order.ROOT_FIRST, Action.RESUME: Order.DEEPEST_FIRST,
                Action.LOWER_PRIORITY: Order.ROOT_FIRST}
NOT_A_SESSION_ROOT = 'Whole-tree actions apply only to a session root process'


@dataclass(frozen=True)
class SignalRequest:
    action: Action
    pid: int
    started: str
    scope: Scope


@dataclass(frozen=True)
class Target:
    row: ProcessRow
    depth: int
    kind: Kind
    scope: Scope
    # For a tree member, the pids its parent must still be among; None for the root.
    tree: frozenset | None = None


def parse_request(body) -> SignalRequest:
    if not isinstance(body, dict):
        raise ApiError(400, 'Body must be an object')
    confirm(body)
    object_fields(body, ('action', 'pid', 'started', 'scope', 'confirmed'))
    try:
        action = Action(field(body, 'action', str, status=400))
        scope = Scope(field(body, 'scope', str, status=400))
    except ValueError:
        raise ApiError(400, 'Unknown action or scope') from None
    pid = field(body, 'pid', int, status=400)
    started = field(body, 'started', str, status=400)
    if pid <= 0 or not started or len(started) > STARTED_LIMIT:
        raise ApiError(400, 'Invalid pid or start time')
    return SignalRequest(action, pid, started, scope)


def ordered(targets, action):
    if ACTION_ORDER[action] is Order.DEEPEST_FIRST:
        return sorted(targets, key=lambda target: -target.depth)
    return sorted(targets, key=lambda target: target.depth)


class ProcessSignaller:
    def __init__(self, reader, policy: SignalPolicy, kill=os.kill,
                 getpriority=os.getpriority, setpriority=os.setpriority):
        self.reader, self.policy = reader, policy
        self.kill, self.getpriority, self.setpriority = kill, getpriority, setpriority

    def mutate(self, body) -> SignalResult:
        request = parse_request(body)
        table = self.reader.table()
        row = table.rows.get(request.pid)
        if row is None or row.started != request.started:
            raise ApiError(409, 'That process has exited or changed since the page loaded; refresh')
        grouping = build_grouping(table)
        lineage = self.policy.lineage(table)
        targets, skipped = self.targets(request, row, grouping, lineage)
        counts = Counter({Outcome.SKIPPED: skipped})
        for target in ordered(targets, request.action):
            counts[self.apply(request.action, target, lineage)] += 1
        return SignalResult(**{outcome.value: counts[outcome] for outcome in Outcome})

    def targets(self, request, row, grouping, lineage) -> tuple[list[Target], int]:
        kind = grouping.kind(row.pid)
        if request.scope is Scope.PROCESS:
            reason = self.policy.blocked(row, kind, Scope.PROCESS, lineage)
            if reason:
                raise ApiError(403, reason)
            return [Target(row, 0, kind, Scope.PROCESS)], 0
        group = grouping.group_of(row.pid)
        if group is None or group.root.pid != row.pid or group.kind not in SESSION_KINDS:
            raise ApiError(403, NOT_A_SESSION_ROOT)
        reason = tree_blocked(self.policy, group, lineage)
        if reason:
            raise ApiError(403, reason)
        tree = frozenset(member.row.pid for member in group.members)
        targets, skipped = [Target(row, 0, kind, Scope.TREE)], 0
        for member in group.members[1:]:
            if self.policy.blocked(member.row, kind, Scope.TREE, lineage):
                skipped += 1
            else:
                targets.append(Target(member.row, member.depth, kind, Scope.TREE, tree))
        return targets, skipped

    def recheck(self, target, lineage) -> Outcome | None:
        """None when the pid is still the process that was chosen and still allowed."""
        try:
            fresh = self.reader.row(target.row.pid)
        except ApiError:
            return Outcome.CHANGED
        if fresh is None:
            return Outcome.GONE
        if fresh.started != target.row.started or fresh.uid != target.row.uid:
            return Outcome.CHANGED
        # An exec keeps the pid and start time but can turn it into a protected program.
        if self.policy.blocked(fresh, target.kind, target.scope, lineage):
            return Outcome.CHANGED
        if target.tree is not None and fresh.ppid not in target.tree:
            return Outcome.CHANGED
        return None

    def apply(self, action, target, lineage) -> Outcome:
        outcome = self.recheck(target, lineage)
        if outcome is not None:
            return outcome
        pid = target.row.pid
        try:
            if action is Action.LOWER_PRIORITY:
                current = self.getpriority(os.PRIO_PROCESS, pid)
                if current >= NICE_FLOOR:
                    return Outcome.UNCHANGED
                self.setpriority(os.PRIO_PROCESS, pid, max(current, NICE_FLOOR))
            else:
                self.kill(pid, ACTION_SIGNALS[action])
        except ProcessLookupError:
            return Outcome.GONE
        except OSError:
            # PermissionError and anything else the kernel refuses: never a partial raise.
            return Outcome.REFUSED
        return Outcome.APPLIED
