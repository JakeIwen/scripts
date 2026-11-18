"""The one declaration of how processes group: kinds, roots and membership.

Which processes root a group comes from `process_roles`, which errs toward protection: a
command that merely mentions `model-gateway.js` outside any session is grouped as infra and
so is read-only. Membership then decides: a process inside a session belongs to that
session, so a session's own `grep model-gateway.js` stays an ordinary member.
"""
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
import re

from process_roles import (CLAUDE, CODEX_VALUE_OPTIONS, CodexRole, codex_role, infra_label,
                           is_codex, is_compute_root)
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
CODEX_DETAILS = {'exec': 'exec', 'e': 'exec', 'resume': 'resumed', 'review': 'review'}

COMPUTE_WORKER_LABEL = 'worker'
# Relative to the home directory; a job is named only from an argument under this root.
JOBS_ROOT = Path('Library/Caches/van-compute/jobs')
JOB_NAME = re.compile(r'[A-Za-z0-9._-]{1,80}')
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
    if is_codex(row) and codex_role(row) is not CodexRole.APP_SERVER:
        return Kind.CODEX
    return None


def role_unknown(row: ProcessRow) -> bool:
    """A session root that might be an app server: grouped as a session, signalled never."""
    return is_codex(row) and codex_role(row) is CodexRole.UNCERTAIN


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


def job_label(row: ProcessRow, jobs_root: str) -> str:
    """The job directory named by an argument that starts with the jobs root, else the program."""
    prefix = jobs_root.rstrip('/') + '/'
    for token in row.arguments:
        if token.startswith(prefix):
            name = token[len(prefix):].split('/', 1)[0]
            if JOB_NAME.fullmatch(name) and name not in ('.', '..'):
                return name
    return f'job · {row.name}'


def default_jobs_root() -> str:
    return str(Path.home() / JOBS_ROOT)


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
    parent: str | None = None
    nested: list[str] = field(default_factory=list)
    # The root's role could not be established (`role_unknown`): nothing in it is signalled.
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


def build_grouping(table: ProcessTable, jobs_root: str | None = None) -> Grouping:
    jobs_root = jobs_root or default_jobs_root()
    # A zombie is named only by the kernel ("codex"), with no arguments: it roots nothing.
    live = {pid: row for pid, row in table.rows.items() if not row.zombie}
    sessions = {pid: kind for pid, row in live.items() if (kind := session_kind(row))}
    computes = {pid for pid, row in live.items() if is_compute_root(row)}
    infras = {pid: label for pid, row in live.items() if (label := infra_label(row))}
    owner: dict[int, tuple[Kind, int]] = {}
    for pid in table.rows:
        chain = [pid, *table.ancestors(pid)]
        if (root := nearest(chain, sessions)) is not None:
            owner[pid] = (sessions[root], root)
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
            row = table.rows[root]
            label = (infras.get(root) if kind is Kind.INFRA else
                     (COMPUTE_WORKER_LABEL if root in computes else job_label(row, jobs_root))
                     if kind is Kind.COMPUTE else None)
            groups[gid] = Group(gid, kind, row, label,
                                role_unknown=kind in SESSION_KINDS and role_unknown(row))
    for group in groups.values():
        group.members = ordered_members(table, group, membership)
        if group.kind in SESSION_KINDS:
            outer = nearest(table.ancestors(group.root.pid), sessions)
            if outer is not None:
                group.parent = group_id(sessions[outer], outer)
                groups[group.parent].nested.append(group.id)
    return Grouping(groups, membership)


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
