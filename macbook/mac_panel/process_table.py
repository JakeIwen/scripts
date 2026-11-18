"""Read and parse the process table; raw argv stays inside this backend.

`argv_text` and `executable` hold arguments as typed, which can include prompts and
secrets, so they are excluded from repr and never leave the backend: everything the page
sees goes through `display_name`, which keeps only the program and, for interpreters,
a script's base name or `-m module` when `process_invocation` can parse it with confidence.

The executable is never guessed from the flattened command line and nothing is looked up
on disk. Two extra `ps` passes give argv[0] (`comm`, the real boundary of the first argument,
spaces included) and the kernel's own short process name (`ucomm`, set at exec from the file
that was run). Each pass carries `lstart`, so rows join only on the same (pid, start). When
argv[0] contains whitespace it may be a title carrying arguments, so none of it is shown:
the kernel is asked for the running file's base name instead (`executable_name`).
"""
from collections import deque
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from functools import cached_property
import os
import time

from executable_name import executable_name
from panel_contracts import ApiError
from process_invocation import SAFE_NAME, SAFE_PATH, Invocation, invocation, invocation_detail

FIELDS = 'pid=,ppid=,pgid=,uid=,nice=,state=,pcpu=,rss=,tty=,lstart=,command='
TABLE_COMMAND = ('/bin/ps', '-axww', '-o', FIELDS)
ARGV0_COMMAND = ('/bin/ps', '-axww', '-o', 'pid=,lstart=,comm=')
KERNEL_NAME_COMMAND = ('/bin/ps', '-axww', '-o', 'pid=,lstart=,ucomm=')
TABLE_COMMANDS = (TABLE_COMMAND, ARGV0_COMMAND, KERNEL_NAME_COMMAND)
# Fixed locale and zone make lstart a stable identity token across reads.
PS_ENV = {'LC_ALL': 'C', 'TZ': 'UTC'}
# All three passes together.
TABLE_TIMEOUT = 5
LSTART_FORMAT = '%a %b %d %H:%M:%S %Y'
LEADING_FIELDS = 9
LSTART_TOKENS = 5
RSS_UNIT = 1024
NO_TTY = frozenset(('??', '-'))
# The kernel keeps at most this many characters of a process name (MAXCOMLEN).
KERNEL_NAME_LIMIT = 16
# Processes start and exit between the three passes, so a few unjoined rows are normal; a
# table with more than this share (beyond the allowance) unreadable is a failed read.
MALFORMED_SHARE = 0.05
MALFORMED_ALLOWANCE = 10
# Kernel executable-name lookups per table read (about 20 µs each); rows beyond the cap
# show the kernel's short name. About 80 rows need one on this Mac (2026-11-18).
EXECUTABLE_LOOKUP_LIMIT = 256


@dataclass(frozen=True)
class ProcessRow:
    pid: int
    ppid: int
    pgid: int
    uid: int
    nice: int
    stopped: bool
    # Exited, waiting for its parent to reap it: no argv left, only the kernel's name.
    zombie: bool
    cpu: float
    rss_bytes: int
    # The `lstart` text: opaque, compared only for identity.
    started: str
    tty: str | None
    argv_text: str = field(repr=False)
    # argv[0] exactly as the process holds it.
    executable: str = field(repr=False)
    # The kernel's process name (`ucomm`), at most KERNEL_NAME_LIMIT characters.
    kernel_name: str = ''
    # The running file's base name from the kernel; looked up only when argv[0] has whitespace.
    executable_file: str = ''

    @cached_property
    def arguments_text(self) -> str:
        """Everything after argv[0]: argument text whose word boundaries are unknown."""
        return self.argv_text[len(self.executable):]

    @cached_property
    def arguments(self) -> tuple[str, ...]:
        return tuple(self.arguments_text.split())

    @cached_property
    def exe(self) -> str:
        return program_name(self.executable, self.kernel_name, self.executable_file)

    @cached_property
    def invocation(self) -> Invocation:
        return invocation(self.exe, self.arguments_text)

    @cached_property
    def name(self) -> str:
        return display_name(self)


def has_whitespace(text) -> bool:
    return any(char.isspace() for char in text.strip())


def program_name(argv0, kernel_name, executable_file='') -> str:
    """The executable's base name from argv[0], or from the kernel when argv[0] is a title.

    Without whitespace, argv[0] names the program: a path's base name or a plain word (`-zsh`
    for a login shell). With whitespace it may be a title the process gave itself (`tmux:
    server`, `Google Chrome Helper private draft`), which can carry anything, so none of it
    is used: the running file's base name from the kernel stands in when the kernel's short
    name begins it (which also rejects a lookup that reached a reused pid), else the short
    name itself.
    """
    text = argv0.strip()
    if has_whitespace(text):
        if kernel_name and executable_file[:KERNEL_NAME_LIMIT] == kernel_name:
            return executable_file
        return kernel_name or '?'
    if text.startswith('/'):
        return os.path.basename(text.rstrip('/')) or kernel_name or '?'
    plain = text[1:] if text.startswith('-') else text
    if SAFE_NAME.fullmatch(plain):
        return plain
    if SAFE_PATH.fullmatch(plain):
        return os.path.basename(plain)
    return kernel_name or '?'


def display_name(row) -> str:
    """The executable base name, plus a confidently parsed script or module; nothing else."""
    detail = invocation_detail(row.invocation)
    return f'{row.exe} {detail}' if detail else row.exe


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
        """Parent first, up to and including pid 1 when present; cycle-safe.

        The last entry can be a pid that is not in the table (a parent that exited or whose
        row was unreadable); `complete_chain` is the check for decisions that depend on it.
        """
        result, seen = [], {pid}
        row = self.rows.get(pid)
        while row is not None and row.ppid not in seen and row.ppid > 0:
            result.append(row.ppid)
            seen.add(row.ppid)
            row = self.rows.get(row.ppid)
        return result

    def complete_chain(self, pid) -> list[int] | None:
        """Every ancestor up to the top of the tree, each present; None when any is missing."""
        if pid not in self.rows:
            return None
        chain = self.ancestors(pid)
        if any(ancestor not in self.rows for ancestor in chain):
            return None
        top = self.rows[chain[-1]] if chain else self.rows[pid]
        return chain if top.ppid <= 0 else None

    def chain_reaches(self, pid, root) -> bool:
        """True when root is an ancestor of pid and every process between them is present."""
        for ancestor in self.ancestors(pid):
            if ancestor not in self.rows:
                return False
            if ancestor == root:
                return True
        return False

    def top_ancestor(self, pid) -> int:
        """The highest ancestor present in the table below pid 1 (pid itself at the top)."""
        top = pid
        for ancestor in self.ancestors(pid):
            if ancestor <= 1 or ancestor not in self.rows:
                break
            top = ancestor
        return top


def split_identity(line, extra):
    """(pid, lstart text, the `extra` leading fields, remainder) or None."""
    parts = line.split(None, 1 + extra + LSTART_TOKENS)
    if len(parts) != 2 + extra + LSTART_TOKENS:
        return None
    try:
        pid = int(parts[0])
        started = ' '.join(parts[1 + extra:1 + extra + LSTART_TOKENS])
        datetime.strptime(started, LSTART_FORMAT)
    except ValueError:
        return None
    return pid, started, parts[1:1 + extra], parts[-1]


def parse_names(text) -> dict[tuple[int, str], str]:
    """A `pid=,lstart=,<name>=` pass: name by (pid, start)."""
    names = {}
    for line in text.splitlines():
        parsed = split_identity(line, 0)
        if parsed is not None:
            pid, started, _, name = parsed
            names[(pid, started)] = name.rstrip('\n')
    return names


def parse_row(line, argv0s, kernel_names) -> ProcessRow | None:
    # The main pass prints lstart after the other fixed fields; reorder to share the splitter.
    parts = line.split(None, LEADING_FIELDS)
    if len(parts) != LEADING_FIELDS + 1:
        return None
    parsed = split_identity(' '.join((parts[0], parts[LEADING_FIELDS])), 0)
    if parsed is None:
        return None
    pid, started, _, argv_text = parsed
    try:
        ppid, pgid, uid, nice = (int(value) for value in parts[1:5])
        state, cpu, rss, tty = parts[5], float(parts[6]), int(parts[7]), parts[8]
    except ValueError:
        return None
    argv0, kernel_name = argv0s.get((pid, started)), kernel_names.get((pid, started))
    if pid < 0 or rss < 0 or not cpu >= 0 or argv0 is None or kernel_name is None:
        return None
    argv_text, argv0 = argv_text.rstrip(), argv0.rstrip()
    # The command is argv joined by spaces, so it must begin with this argv[0].
    if not argv0 or not (argv_text == argv0 or argv_text.startswith(argv0 + ' ')):
        return None
    return ProcessRow(pid=pid, ppid=ppid, pgid=pgid, uid=uid, nice=nice,
                      stopped=state.startswith('T'), zombie=state.startswith('Z'),
                      cpu=cpu, rss_bytes=rss * RSS_UNIT,
                      started=started, tty=None if tty in NO_TTY else tty, argv_text=argv_text,
                      executable=argv0, kernel_name=kernel_name.strip()[:KERNEL_NAME_LIMIT])


def parse_table(text, argv0_text, kernel_text) -> ProcessTable:
    argv0s, kernel_names = parse_names(argv0_text), parse_names(kernel_text)
    rows, malformed = {}, 0
    for line in text.splitlines():
        if not line.strip():
            continue
        row = parse_row(line, argv0s, kernel_names)
        if row is None or row.pid in rows:
            malformed += 1
            continue
        rows[row.pid] = row
    return ProcessTable(rows, malformed)


def usable(table: ProcessTable) -> bool:
    """A table with no rows, or mostly unreadable rows, is a failed read, not an idle machine."""
    total = len(table.rows) + table.malformed
    return bool(table.rows) and table.malformed <= max(MALFORMED_ALLOWANCE, MALFORMED_SHARE * total)


def started_epoch(started) -> float | None:
    try:
        return datetime.strptime(started, LSTART_FORMAT).replace(tzinfo=timezone.utc).timestamp()
    except ValueError:
        return None


def normal_started(text) -> str | None:
    """An lstart-format text with its day padding normalised, or None when malformed."""
    if not isinstance(text, str):
        return None
    joined = ' '.join(text.split())
    try:
        datetime.strptime(joined, LSTART_FORMAT)
    except ValueError:
        return None
    return joined


def with_executable_files(table: ProcessTable, lookup) -> ProcessTable:
    """The table with each titled row's running file name, up to EXECUTABLE_LOOKUP_LIMIT."""
    rows, looked_up = dict(table.rows), 0
    for pid, row in table.rows.items():
        if row.zombie or not has_whitespace(row.executable):
            continue
        if looked_up >= EXECUTABLE_LOOKUP_LIMIT:
            break
        looked_up += 1
        if name := lookup(pid):
            rows[pid] = replace(row, executable_file=name)
    return ProcessTable(rows, table.malformed)


class ProcessReader:
    """Whole-table reads through the panel's subprocess boundary."""

    def __init__(self, runner, clock=time.monotonic, executable_name=executable_name):
        self.runner, self.clock, self.executable_name = runner, clock, executable_name

    def table(self, timeout=TABLE_TIMEOUT) -> ProcessTable:
        deadline = self.clock() + timeout
        outputs = []
        for argv in TABLE_COMMANDS:
            remaining = deadline - self.clock()
            if remaining <= 0:
                raise ApiError(502, 'Process table timed out')
            try:
                result = self.runner.run(argv, timeout=remaining, env=PS_ENV)
            except ApiError:
                # A runner timeout (504) or launch failure is the section's declared 502.
                raise ApiError(502, 'Process table unavailable') from None
            if result.returncode != 0 or not result.stdout.strip():
                raise ApiError(502, 'Process table unavailable')
            outputs.append(result.stdout)
        table = parse_table(*outputs)
        if not usable(table):
            raise ApiError(502, f'Process table unreadable ({table.malformed} malformed rows)')
        return with_executable_files(table, self.executable_name)
