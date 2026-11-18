"""The one declaration of how processes group: kinds, positional matches and membership.

Matching looks only at an argument's position (executable, interpreter script or module,
subcommand), never at a substring of the whole command line, so an unrelated command that
merely mentions `van_compute.worker` or `model-gateway.js` is not grouped with them.
"""
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
import re

from process_invocation import Family
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

# Codex global options that consume the next token; the first other operand is its subcommand.
CODEX_VALUE_OPTIONS = frozenset(('-c', '--config', '-m', '--model', '-p', '--profile', '-C',
                                 '--cd', '-s', '--sandbox', '-a', '--ask-for-approval', '-i',
                                 '--image', '--enable', '--disable', '--local-provider',
                                 '--add-dir'))
CODEX_APP_SERVER = 'app-server'
# Subcommands worth naming in a session's detail; anything else (including a prompt
# operand, which must never be shown) reads as an interactive session.
CODEX_DETAILS = {'exec': 'exec', 'e': 'exec', 'resume': 'resumed', 'review': 'review'}

INFRA_EXECUTABLES = {'claude-code-proxy': 'model gateway proxy',
                     'llm_switchboard': 'LLM switchboard'}
INFRA_SCRIPTS = {'model-gateway.js': 'model gateway', 'usage-server.mjs': 'usage server'}
INFRA_SCRIPT_PATHS = {('@playwright', 'mcp', 'cli.js'): 'Playwright MCP'}
INFRA_MODULES = {'llm_switchboard': 'LLM switchboard'}
INFRA_SUBCOMMANDS = {('codex', CODEX_APP_SERVER): 'Codex app server'}

COMPUTE_MODULE = 'van_compute.worker'
COMPUTE_WORKER_LABEL = 'worker'
# Relative to the home directory; a job is named only from an argument under this root.
JOBS_ROOT = Path('Library/Caches/van-compute/jobs')
JOB_NAME = re.compile(r'[A-Za-z0-9._-]{1,80}')
# ssh options that consume the next token; a control master runs with -N and -M.
SSH_VALUE_LETTERS = frozenset('BbcDEeFIiJLlmOoPpQRSWw')
SSH_MASTER_FLAGS = frozenset('NM')


def codex_subcommand(row: ProcessRow) -> str | None:
    tokens = iter(row.arguments)
    for token in tokens:
        if token in CODEX_VALUE_OPTIONS:
            next(tokens, None)
        elif not token.startswith('-'):
            return token
    return None


def is_codex_app_server(row: ProcessRow) -> bool:
    # A `-c` value containing a space shifts every later token, so the positional
    # subcommand can be misread; any whole `app-server` token also counts. The error then
    # falls on the side of infra (never signalled), not on a session tree's actions.
    return codex_subcommand(row) == CODEX_APP_SERVER or CODEX_APP_SERVER in row.arguments


def session_kind(row: ProcessRow) -> Kind | None:
    if row.exe == 'claude':
        return Kind.CLAUDE
    if row.exe == 'codex' and not is_codex_app_server(row):
        return Kind.CODEX
    return None


def codex_detail(row: ProcessRow) -> str:
    return CODEX_DETAILS.get(codex_subcommand(row) or '', 'interactive')


def script_parts(script):
    return tuple(part for part in script.split('/') if part)


def infra_label(row: ProcessRow) -> str | None:
    if row.exe in INFRA_EXECUTABLES:
        return INFRA_EXECUTABLES[row.exe]
    if row.exe == 'codex':
        return INFRA_SUBCOMMANDS[(row.exe, CODEX_APP_SERVER)] if is_codex_app_server(row) else None
    call = row.invocation
    if call.module is not None:
        return INFRA_MODULES.get(call.module)
    if call.script is not None:
        parts = script_parts(call.script)
        for suffix, label in INFRA_SCRIPT_PATHS.items():
            if parts[-len(suffix):] == suffix:
                return label
        return INFRA_SCRIPTS.get(parts[-1] if parts else '')
    return None


def is_compute_root(row: ProcessRow) -> bool:
    call = row.invocation
    return call.family is Family.PYTHON and call.module == COMPUTE_MODULE


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
            groups[gid] = Group(gid, kind, row, label)
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
