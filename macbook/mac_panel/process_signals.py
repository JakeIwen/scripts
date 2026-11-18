"""Carry out one confirmed signal request against the user's own processes.

Every request starts from a fresh process table (never the cached snapshot). Immediately
before each signal another full table is read and the whole decision is made again from it
(`signal_recheck`): the root and the target are still the processes that were chosen, a
tree target is still a member of the same session by today's membership rules, and the
policy still allows it. That narrows, but cannot close, the window in which a pid could be
reused between the last check and `os.kill`.
"""
from collections import Counter
import os
import signal
import time

from panel_contracts import ApiError, SignalResult
from resource_groups import SESSION_KINDS, build_grouping
from signal_policy import Scope, SignalPolicy, tree_blocked
from signal_recheck import (MUTATION_BUDGET, TREE_TARGET_LIMIT, Action, Halt, Outcome, Plan,
                            SignalRequest, Target, ordered, verify)
from wire_validation import confirm, field, object_fields

STARTED_LIMIT = 64
# lower-priority sets at least this nice value and never raises priority back.
NICE_FLOOR = 10

ACTION_SIGNALS = {Action.TERMINATE: signal.SIGTERM, Action.KILL: signal.SIGKILL,
                  Action.PAUSE: signal.SIGSTOP, Action.RESUME: signal.SIGCONT}
NOT_A_SESSION_ROOT = 'Whole-tree actions apply only to a session root process'
TREE_TOO_LARGE = (f'More than {TREE_TARGET_LIMIT} processes in this session; '
                  'end parts of it first')


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


class ProcessSignaller:
    def __init__(self, reader, policy: SignalPolicy, kill=os.kill,
                 getpriority=os.getpriority, setpriority=os.setpriority,
                 clock=time.monotonic, jobs_root=None):
        self.reader, self.policy = reader, policy
        self.kill, self.getpriority, self.setpriority = kill, getpriority, setpriority
        self.clock, self.jobs_root = clock, jobs_root

    def mutate(self, body) -> SignalResult:
        request = parse_request(body)
        deadline = self.clock() + MUTATION_BUDGET
        table = self.reader.table()
        row = table.rows.get(request.pid)
        if row is None or row.started != request.started:
            raise ApiError(409, 'That process has exited or changed since the page loaded; refresh')
        grouping = build_grouping(table, self.jobs_root)
        plan = self.plan(request, row, grouping, self.policy.context(table, grouping))
        return self.carry_out(plan, deadline)

    def plan(self, request, row, grouping, context) -> Plan:
        if request.scope is Scope.PROCESS:
            reason = self.policy.blocked(row, grouping.kind(row.pid), Scope.PROCESS, context)
            if reason:
                raise ApiError(403, reason)
            return Plan(request, row, None, [Target(row, 0)], 0)
        group = grouping.group_of(row.pid)
        if group is None or group.root.pid != row.pid or group.kind not in SESSION_KINDS:
            raise ApiError(403, NOT_A_SESSION_ROOT)
        reason = tree_blocked(self.policy, group, context)
        if reason:
            raise ApiError(403, reason)
        if len(group.members) > TREE_TARGET_LIMIT:
            raise ApiError(403, TREE_TOO_LARGE)
        targets, skipped = [Target(row, 0)], 0
        for member in group.members[1:]:
            if self.policy.blocked(member.row, group.kind, Scope.TREE, context):
                skipped += 1
            else:
                targets.append(Target(member.row, member.depth))
        return Plan(request, row, group.id, targets, skipped)

    def carry_out(self, plan: Plan, deadline) -> SignalResult:
        counts, stopped = Counter({Outcome.SKIPPED: plan.skipped}), None
        pending = ordered(plan.targets, plan.request.action)
        for index, target in enumerate(pending):
            verdict = verify(self.reader, self.policy, plan, target, deadline, self.clock,
                             self.jobs_root)
            if isinstance(verdict, Halt):
                # Nothing more is signalled; every target not yet reached is counted.
                counts[verdict.outcome] += len(pending) - index
                stopped = verdict.reason
                break
            counts[verdict or self.apply(plan.request.action, target.row.pid)] += 1
        return SignalResult(**{outcome.value: counts[outcome] for outcome in Outcome},
                            stopped=stopped)

    def apply(self, action, pid) -> Outcome:
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
