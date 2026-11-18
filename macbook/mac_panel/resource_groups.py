"""The one declaration of how processes group: kinds, roots and membership.

Which processes root a group comes from `process_roles`, which errs toward protection: a
command that merely mentions `model-gateway.js` outside any session is grouped as infra and
so is read-only. Membership then decides: a process inside a session belongs to that
session, so a session's own `grep model-gateway.js` stays an ordinary member. The one
exception is Codex: `ps` flattens its arguments, so whether a Codex process is a session or
a server can be misread, and every Codex process therefore roots its own group wherever it
runs. A session with any other group's root beneath it lists that group in `nested`, and
the signal policy refuses its whole-tree action.
"""
from dataclasses import dataclass, field
from enum import Enum

from process_roles import (CLAUDE, CODEX_LABELS, CODEX_VALUE_OPTIONS, CodexRole, codex_role,
                           infra_label, is_codex, is_compute_root)
from process_table import ProcessRow, ProcessTable


class Kind(str, Enum):
    CLAUDE = 'claude'
    CODEX = 'codex'
    INFRA = 'infra'
    COMPUTE = 'compute'
    APP = 'app'


SESSION_KINDS = frozenset((Kind.CLAUDE, Kind.CODEX))
# The kinds the payload totals; apps are reported per tree instead.
TOTAL_KINDS = (Kind.CLAUDE, Kind.CODEX, Kind.INFRA, Kind.COMPUTE)

# Subcommands worth naming in a session's detail; anything else (including a prompt
# operand, which must never be shown) reads as an interactive session.
CODEX_DETAILS = {'exec': 'exec', 'e': 'exec', 'resume': 'resumed'}

COMPUTE_WORKER_LABEL = 'worker'
# ssh options that consume the next token; a control master runs with -N and -M.
SSH_VALUE_LETTERS = frozenset('BbcDEeFIiJLlmOoPpQRSWw')
SSH_MASTER_FLAGS = frozenset('NM')


def codex_subcommand(row: ProcessRow) -> str | None:
    """The first operand after value options: display detail only, never a safety decision."""
    tokens = iter(row.arguments)
    for token in tokens:
        if token in CODEX_VALUE_OPTIONS:
            next(tokens, None)
        elif not token.startswith('-'):
            return token
    return None


def session_kind(row: ProcessRow) -> Kind | None:
    if row.exe == CLAUDE:
        return Kind.CLAUDE
    if is_codex(row) and codex_role(row) is CodexRole.SESSION:
        return Kind.CODEX
    return None


def codex_detail(row: ProcessRow) -> str:
    return CODEX_DETAILS.get(codex_subcommand(row) or '', 'interactive')


def is_control_master(row: ProcessRow) -> bool:
    if row.exe != 'ssh':
        return False
    letters, tokens = set(), iter(row.arguments)
    for token in tokens:
        if not token.startswith('-') or token == '-':
            break
        letters.update(token[1:])
        if token[-1] in SSH_VALUE_LETTERS:
            next(tokens, None)
    return SSH_MASTER_FLAGS <= letters


def job_label(row: ProcessRow) -> str:
    """A job row's name: its program only. Job paths and arguments can carry anything."""
    return f'job · {row.exe}'


@dataclass(frozen=True)
class Member:
    row: ProcessRow
    depth: int


@dataclass
class Group:
    id: str
    kind: Kind
    root: ProcessRow
    label: str | None
    members: list[Member] = field(default_factory=list)
    # Sessions only: the session this one runs inside, for display.
    parent: str | None = None
    # Sessions only: every other group whose root runs beneath this one's processes.
    nested: list[str] = field(default_factory=list)
    # A Codex root whose role could not be established: nothing in it is signalled.
    role_unknown: bool = False


@dataclass
class Grouping:
    groups: dict[str, Group]
    # pid -> group id for every grouped process; apps are absent.
    membership: dict[int, str]

    def kind(self, pid) -> Kind:
        gid = self.membership.get(pid)
        return self.groups[gid].kind if gid else Kind.APP

    def group_of(self, pid) -> Group | None:
        gid = self.membership.get(pid)
        return self.groups[gid] if gid else None


def group_id(kind, pid):
    return f'{kind.value}:{pid}'


def nearest(chain, roots):
    return next((pid for pid in chain if pid in roots), None)


def outermost(chain, roots):
    return next((pid for pid in reversed(chain) if pid in roots), None)


def compute_assignment(table, root, owned):
    """Split a worker's processes: the root and its control masters, then one group per job."""
    assign = {}
    for child in table.children.get(root, ()):
        if child not in owned:
            continue
        target = root if is_control_master(table.rows[child]) else child
        assign[child] = target
        for row, _depth in table.descendants(child):
            if row.pid in owned:
                assign[row.pid] = target
    assign[root] = root
    return assign


def build_grouping(table: ProcessTable) -> Grouping:
    # A zombie is named only by the kernel ("codex"), with no arguments: it roots nothing.
    live = {pid: row for pid, row in table.rows.items() if not row.zombie}
    sessions = {pid: kind for pid, row in live.items() if (kind := session_kind(row))}
    # Every other Codex process: a read-only infra root of its own, never another's member.
    services = {pid: codex_role(row) for pid, row in live.items()
                if is_codex(row) and pid not in sessions}
    boundaries = {**sessions, **dict.fromkeys(services, Kind.INFRA)}
    computes = {pid for pid, row in live.items() if is_compute_root(row)}
    infras = {pid: label for pid, row in live.items() if (label := infra_label(row))}
    owner: dict[int, tuple[Kind, int]] = {}
    for pid in table.rows:
        chain = [pid, *table.ancestors(pid)]
        if (root := nearest(chain, boundaries)) is not None:
            owner[pid] = (boundaries[root], root)
        elif (root := nearest(chain, computes)) is not None:
            owner[pid] = (Kind.COMPUTE, root)
        elif (root := outermost(chain, infras)) is not None:
            owner[pid] = (Kind.INFRA, root)
    for root in computes:
        if owner.get(root, (None,))[0] is not Kind.COMPUTE:
            continue
        owned = {pid for pid, (kind, top) in owner.items() if kind is Kind.COMPUTE and top == root}
        for pid, target in compute_assignment(table, root, owned).items():
            owner[pid] = (Kind.COMPUTE, target)
    membership = {pid: group_id(kind, root) for pid, (kind, root) in owner.items()}
    groups = {}
    for pid, (kind, root) in owner.items():
        gid = membership[pid]
        if gid not in groups:
            label = group_label(kind, root, services, infras, computes, table)
            groups[gid] = Group(gid, kind, table.rows[root], label,
                                role_unknown=services.get(root) is CodexRole.UNCERTAIN)
    for group in groups.values():
        group.members = ordered_members(table, group, membership)
    link_nested(table, groups, membership, sessions)
    return Grouping(groups, membership)


def group_label(kind, root, services, infras, computes, table) -> str | None:
    if kind is Kind.INFRA:
        return CODEX_LABELS[services[root]] if root in services else infras.get(root)
    if kind is Kind.COMPUTE:
        return COMPUTE_WORKER_LABEL if root in computes else job_label(table.rows[root])
    return None


def link_nested(table, groups, membership, sessions):
    """Every session lists each other group whose root runs beneath any of its processes,
    at any depth; a session also records the nearest session it runs inside."""
    for group in sorted(groups.values(), key=lambda item: item.root.pid):
        for ancestor in table.ancestors(group.root.pid):
            outer = groups.get(membership.get(ancestor))
            if outer is not None and outer is not group and outer.kind in SESSION_KINDS:
                if group.id not in outer.nested:
                    outer.nested.append(group.id)
        if group.kind in SESSION_KINDS:
            outer = nearest(table.ancestors(group.root.pid), sessions)
            if outer is not None:
                group.parent = group_id(sessions[outer], outer)


def ordered_members(table, group, membership) -> list[Member]:
    """Depth-first from the root, so the page can draw the tree in order."""
    result, stack = [], [(group.root.pid, 0)]
    while stack:
        pid, depth = stack.pop()
        result.append(Member(table.rows[pid], depth))
        for child in reversed(table.children.get(pid, ())):
            if membership.get(child) == group.id:
                stack.append((child, depth + 1))
    return result
