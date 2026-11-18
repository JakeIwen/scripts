"""Read and parse the process table; raw argv stays inside this backend.

`argv_text` and `executable` hold arguments as typed, which can include prompts and
secrets, so they are excluded from repr and never leave the backend: everything the page
sees goes through `display_name`, which keeps only the program and, for interpreters,
the script's base name or `-m module`.
"""
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from functools import cached_property
import os
import re

from panel_contracts import ApiError

FIELDS = 'pid=,ppid=,pgid=,uid=,nice=,state=,pcpu=,rss=,lstart=,command='
TABLE_COMMAND = ('/bin/ps', '-axww', '-o', FIELDS)
# Fixed locale and zone make lstart a stable identity token across reads.
PS_ENV = {'LC_ALL': 'C', 'TZ': 'UTC'}
TABLE_TIMEOUT = 5
LSTART_FORMAT = '%a %b %d %H:%M:%S %Y'
LEADING_FIELDS = 8
LSTART_TOKENS = 5
RSS_UNIT = 1024
# An executable path with spaces ("/Applications/Google Chrome.app/…/Google Chrome") has no
# argv boundary in `ps`; at most this many space-joined prefixes are tried against the disk.
MAX_PATH_SPACES = 12


class Family(str, Enum):
    PYTHON = 'python'
    NODE = 'node'
    SHELL = 'shell'
    RUBY = 'ruby'
    PERL = 'perl'


SHELLS = frozenset(('zsh', 'bash', 'sh', 'dash', 'fish', 'ksh', 'tcsh', 'csh'))
PYTHON_NAME = re.compile(r'python(?:w)?(?:\d+(?:\.\d+)*)?', re.I)
FAMILY_NAMES = {'node': Family.NODE, 'nodejs': Family.NODE, 'ruby': Family.RUBY,
                'perl': Family.PERL, **dict.fromkeys(SHELLS, Family.SHELL)}


@dataclass(frozen=True)
class FamilyOptions:
    # Options that consume the next token, which must never be mistaken for a script.
    with_value: frozenset
    # Options after which the program text is inline: shown as the option alone.
    inline: frozenset
    module: str | None = None


FAMILY_OPTIONS = {
    Family.PYTHON: FamilyOptions(frozenset(('-W', '-X', '-Q')), frozenset(('-c',)), '-m'),
    Family.NODE: FamilyOptions(frozenset(('-r', '--require', '--import', '--loader', '-C',
                                          '--experimental-loader', '--conditions', '--input-type',
                                          '--env-file', '--inspect-port', '--title')),
                               frozenset(('-e', '--eval', '-p', '--print'))),
    Family.SHELL: FamilyOptions(frozenset(('-o', '+o', '-O', '+O')), frozenset(('-c',))),
    Family.RUBY: FamilyOptions(frozenset(('-I', '-r', '-C', '-E', '-F')), frozenset(('-e',))),
    Family.PERL: FamilyOptions(frozenset(('-I', '-M', '-m')), frozenset(('-e', '-E'))),
}
SCRIPT_SUFFIXES = frozenset(('.py', '.js', '.mjs', '.cjs', '.ts', '.sh', '.zsh', '.bash',
                             '.rb', '.pl'))
SAFE_NAME = re.compile(r'[\w.@+-]{1,80}')
SHORT_CLUSTER = re.compile(r'-[A-Za-z]{2,8}')
MODULE_NAME = re.compile(r'[A-Za-z_]\w*(?:\.[A-Za-z_]\w*){0,8}')


@dataclass(frozen=True)
class Invocation:
    """What an interpreter runs: a script token, a module or inline text (internal only)."""
    family: Family | None
    script: str | None = None
    module: str | None = None
    inline: str | None = None


@dataclass(frozen=True)
class ProcessRow:
    pid: int
    ppid: int
    pgid: int
    uid: int
    nice: int
    stopped: bool
    cpu: float
    rss_bytes: int
    # The `lstart` text: opaque, compared only for identity.
    started: str
    argv_text: str = field(repr=False)
    executable: str = field(repr=False)

    @cached_property
    def arguments(self) -> tuple[str, ...]:
        return tuple(self.argv_text[len(self.executable):].split())

    @cached_property
    def exe(self) -> str:
        """Executable base name; a login shell's leading '-' is dropped."""
        name = os.path.basename(self.executable.rstrip('/')) or self.executable
        return name[1:] if name.startswith('-') and '/' not in self.executable else name

    @cached_property
    def invocation(self) -> Invocation:
        return invocation(self.exe, self.arguments)

    @cached_property
    def name(self) -> str:
        return display_name(self)


@dataclass(frozen=True)
class ProcessTable:
    rows: dict[int, ProcessRow]
    malformed: int = 0

    @cached_property
    def children(self) -> dict[int, list[int]]:
        index: dict[int, list[int]] = {}
        for row in self.rows.values():
            if row.ppid != row.pid:
                index.setdefault(row.ppid, []).append(row.pid)
        for pids in index.values():
            pids.sort()
        return index

    def descendants(self, pid) -> list[tuple[ProcessRow, int]]:
        """(row, depth below pid) in breadth-first order, excluding pid itself."""
        result, queue, seen = [], deque([(pid, 0)]), {pid}
        while queue:
            current, depth = queue.popleft()
            for child in self.children.get(current, ()):
                if child not in seen and child in self.rows:
                    seen.add(child)
                    result.append((self.rows[child], depth + 1))
                    queue.append((child, depth + 1))
        return result

    def ancestors(self, pid) -> list[int]:
        """Parent first, up to and including pid 1 when present; cycle-safe."""
        result, seen = [], {pid}
        row = self.rows.get(pid)
        while row is not None and row.ppid not in seen and row.ppid > 0:
            result.append(row.ppid)
            seen.add(row.ppid)
            row = self.rows.get(row.ppid)
        return result

    def top_ancestor(self, pid) -> int:
        """The highest ancestor below pid 1 (pid itself for a child of launchd)."""
        top = pid
        for ancestor in self.ancestors(pid):
            if ancestor <= 1:
                break
            top = ancestor
        return top


def resolve_executable(argv_text, is_file=os.path.isfile):
    """argv[0] as typed: the first token, or the shortest space-joined prefix naming a file."""
    first = argv_text.split(' ', 1)[0]
    if not first.startswith('/') or is_file(first):
        return first
    end = len(first)
    for _ in range(MAX_PATH_SPACES):
        end = argv_text.find(' ', end + 1)
        candidate = argv_text if end < 0 else argv_text[:end]
        if is_file(candidate):
            return candidate
        if end < 0:
            break
    return first


def parse_row(line, is_file=os.path.isfile) -> ProcessRow | None:
    parts = line.split(None, LEADING_FIELDS + LSTART_TOKENS)
    if len(parts) != LEADING_FIELDS + LSTART_TOKENS + 1:
        return None
    try:
        pid, ppid, pgid, uid, nice = (int(value) for value in parts[:5])
        state, cpu, rss = parts[5], float(parts[6]), int(parts[7])
        started = ' '.join(parts[LEADING_FIELDS:LEADING_FIELDS + LSTART_TOKENS])
        datetime.strptime(started, LSTART_FORMAT)
    except ValueError:
        return None
    if pid < 0 or rss < 0 or not cpu >= 0:
        return None
    argv_text = parts[-1].rstrip('\n')
    return ProcessRow(pid=pid, ppid=ppid, pgid=pgid, uid=uid, nice=nice,
                      stopped=state.startswith('T'), cpu=cpu, rss_bytes=rss * RSS_UNIT,
                      started=started, argv_text=argv_text,
                      executable=resolve_executable(argv_text, is_file))


def parse_table(text, is_file=os.path.isfile) -> ProcessTable:
    rows, malformed = {}, 0
    for line in text.splitlines():
        if not line.strip():
            continue
        row = parse_row(line, is_file)
        if row is None or row.pid in rows:
            malformed += 1
            continue
        rows[row.pid] = row
    return ProcessTable(rows, malformed)


def started_epoch(started) -> float | None:
    try:
        return datetime.strptime(started, LSTART_FORMAT).replace(tzinfo=timezone.utc).timestamp()
    except ValueError:
        return None


def interpreter_family(exe) -> Family | None:
    if PYTHON_NAME.fullmatch(exe):
        return Family.PYTHON
    return FAMILY_NAMES.get(exe)


class OptionRole(Enum):
    FLAG = 'flag'
    VALUE = 'value'
    INLINE = 'inline'
    MODULE = 'module'


def option_role(options, token) -> OptionRole | None:
    """How an interpreter treats token; None means it is the script operand."""
    if token == options.module:
        return OptionRole.MODULE
    if token in options.inline:
        return OptionRole.INLINE
    if token in options.with_value:
        return OptionRole.VALUE
    if not (token.startswith('-') or token.startswith('+')):
        return None
    if SHORT_CLUSTER.fullmatch(token):
        # `bash -lc 'text'`, `python -Bm mod`: a cluster acts like its letters.
        letters = {f'-{letter}' for letter in token[1:]}
        if options.module and token.endswith(options.module[1:]):
            return OptionRole.MODULE
        if letters & options.inline:
            return OptionRole.INLINE
        if f'-{token[-1]}' in options.with_value:
            return OptionRole.VALUE
    return OptionRole.FLAG


def invocation(exe, arguments) -> Invocation:
    family = interpreter_family(exe)
    if family is None:
        return Invocation(None)
    options = FAMILY_OPTIONS[family]
    tokens = iter(arguments)
    for token in tokens:
        role = option_role(options, token)
        if role is OptionRole.MODULE:
            return Invocation(family, module=next(tokens, None))
        if role is OptionRole.INLINE:
            return Invocation(family, inline=token)
        if role is OptionRole.VALUE:
            next(tokens, None)
        elif role is None:
            return Invocation(family, script=token)
    return Invocation(family)


def script_label(script) -> str | None:
    """A script's base name, only when the token plainly is a file path or script name."""
    base = os.path.basename(script.rstrip('/'))
    if '/' not in script and os.path.splitext(base)[1] not in SCRIPT_SUFFIXES:
        return None
    return base if SAFE_NAME.fullmatch(base) else None


def display_name(row) -> str:
    """The executable base name, plus an interpreter's script or module; nothing else."""
    call = row.invocation
    detail = None
    if call.module is not None:
        detail = f'-m {call.module}' if MODULE_NAME.fullmatch(call.module) else '-m'
    elif call.inline is not None:
        detail = call.inline
    elif call.script is not None:
        detail = script_label(call.script)
    return f'{row.exe} {detail}' if detail else row.exe


class ProcessReader:
    """Whole-table and single-pid reads through the panel's subprocess boundary."""

    def __init__(self, runner, is_file=os.path.isfile):
        self.runner, self.is_file = runner, is_file

    def table(self, timeout=TABLE_TIMEOUT) -> ProcessTable:
        result = self.runner.run(TABLE_COMMAND, timeout=timeout, env=PS_ENV)
        if result.returncode != 0 or not result.stdout.strip():
            raise ApiError(502, 'Process table unavailable')
        return parse_table(result.stdout, self.is_file)

    def row(self, pid, timeout=TABLE_TIMEOUT) -> ProcessRow | None:
        """A fresh read of one pid; None when it no longer exists."""
        result = self.runner.run(('/bin/ps', '-ww', '-o', FIELDS, '-p', str(int(pid))),
                                 timeout=timeout, env=PS_ENV)
        rows = parse_table(result.stdout, self.is_file).rows if result.returncode == 0 else {}
        return rows.get(pid)
