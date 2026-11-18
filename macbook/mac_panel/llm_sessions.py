"""Name Claude and Codex sessions from their own records, never from argv or transcripts.

Claude Code keeps `~/.claude/sessions/<pid>.json` per interactive session (name, status,
cwd); only those `.json` files are opened, by name, for pids that are `claude` processes in
the current table, never the `.key` files beside them. Codex keeps no per-pid record: its
thread title is found by locating the session's rollout file and looking the rollout id up
in `~/.codex/session_index.jsonl`. A rollout the process holds open (lsof) proves the match;
a rollout whose timestamp matches the process start is only a guess, used when exactly one
unclaimed process and one unclaimed rollout fall in the window, never cached, and marked as
such (`NameSource`) so a destructive confirmation can say the name is unproven. Naming runs
under a time budget and falls back to "codex · <cwd name>"; it never delays the core numbers.
"""
from dataclasses import dataclass
from enum import Enum
import json
import os
from pathlib import Path
import re
import stat
import time

from panel_contracts import ApiError
from process_table import ProcessRow, normal_started, started_epoch
from resource_groups import Kind, codex_detail

CLAUDE_SESSIONS = Path('.claude/sessions')
CODEX_INDEX = Path('.codex/session_index.jsonl')
CODEX_ROLLOUTS = Path('.codex/sessions')
REGISTRY_LIMIT = 64 * 1024
# At most this many registry files are opened per snapshot (one per Claude process).
REGISTRY_FILES = 64
INDEX_LIMIT = 4 * 1024 * 1024
ROLLOUT_NAME = re.compile(r'rollout-(\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2})-'
                          r'([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.jsonl')
ROLLOUT_STAMP = '%Y-%m-%dT%H-%M-%S'
# Codex creates its rollout within a second or two of starting.
ROLLOUT_WINDOW = 5
# Directory entries examined per snapshot across the dated rollout folders.
ROLLOUT_SCAN = 2000
NAME_LIMIT = 120
LSOF = '/usr/sbin/lsof'
LSOF_FLOOR = 0.05
SESSION_STATUSES = frozenset(('busy', 'idle'))


class NameSource(str, Enum):
    """How a session's name was established; the page shows unproven names as such."""
    REGISTRY = 'registry'          # Claude's own record for this exact process
    ROLLOUT_OPEN = 'rollout-open'  # the Codex rollout this process holds open
    ROLLOUT_TIME = 'rollout-time'  # the only Codex rollout created near its start: a guess
    FALLBACK = 'fallback'          # kind and working directory only


@dataclass(frozen=True)
class SessionInfo:
    name: str
    detail: str
    status: str | None
    cwd: str | None
    source: NameSource = NameSource.FALLBACK


@dataclass(frozen=True)
class CodexFacts:
    rollout: str | None
    cwd: str | None


def bounded_read(path, limit, tail=False) -> bytes | None:
    """A regular file's bytes (its last `limit` bytes when tail), refusing symlinks."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    except OSError:
        return None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            return None
        if info.st_size > limit:
            if not tail:
                return None
            os.lseek(fd, info.st_size - limit, os.SEEK_SET)
        return os.read(fd, limit)
    except OSError:
        return None
    finally:
        os.close(fd)


def short_text(value) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()[:NAME_LIMIT]


def path_name(cwd) -> str | None:
    return (os.path.basename(cwd.rstrip('/')) or cwd) if cwd else None


def read_claude_registry(home: Path, pids, deadline, clock) -> dict[int, dict]:
    """`<pid>.json` for each given pid, opened by name; no directory listing."""
    directory = home / CLAUDE_SESSIONS
    entries = {}
    for pid in sorted(pids)[:REGISTRY_FILES]:
        if clock() >= deadline:
            break
        raw = bounded_read(directory / f'{int(pid)}.json', REGISTRY_LIMIT)
        try:
            value = json.loads(raw) if raw else None
        except ValueError:
            continue
        if isinstance(value, dict) and value.get('pid') == pid:
            entries[pid] = value
    return entries


def claude_info(row: ProcessRow, entry: dict | None, cwd: str | None) -> SessionInfo:
    """A registry entry names the session only when it matches this process's start."""
    if not registered(row, entry):
        name = path_name(cwd)
        return SessionInfo(f'claude · {name}' if name else 'claude', 'headless', None, cwd)
    cwd = short_path(entry.get('cwd'))
    status = entry.get('status')
    # Each field is checked on its own: a malformed one is dropped, not fatal.
    return SessionInfo(short_text(entry.get('name')) or path_name(cwd) or 'claude',
                       short_text(entry.get('kind')) or 'interactive',
                       status if isinstance(status, str) and status in SESSION_STATUSES else None,
                       cwd, NameSource.REGISTRY)


def short_path(value) -> str | None:
    return value if isinstance(value, str) and value.startswith('/') and len(value) < 1024 else None


def parse_lsof(text) -> dict[int, CodexFacts]:
    """`lsof -Fpfn` records: cwd and any open rollout file per pid."""
    facts, pid, fd = {}, None, None
    for line in text.splitlines():
        tag, value = line[:1], line[1:]
        if tag == 'p':
            pid = int(value) if value.isdecimal() else None
            if pid is not None:
                facts[pid] = CodexFacts(None, None)
        elif tag == 'f':
            fd = value
        elif tag == 'n' and pid is not None:
            current = facts[pid]
            if fd == 'cwd':
                facts[pid] = CodexFacts(current.rollout, short_path(value))
            elif (match := ROLLOUT_NAME.search(os.path.basename(value))):
                facts[pid] = CodexFacts(match.group(2), current.cwd)
    return facts


def rollout_stamp_epoch(stamp) -> float | None:
    try:
        return time.mktime(time.strptime(stamp, ROLLOUT_STAMP))
    except (ValueError, OverflowError):
        return None


def rollout_days(starts) -> list[str]:
    """The dated folders a rollout created at most ROLLOUT_WINDOW after a start can be in."""
    days = set()
    for start in starts:
        days.update(time.strftime('%Y/%m/%d', time.localtime(start + offset))
                    for offset in (0, ROLLOUT_WINDOW))
    return sorted(days)


class CodexNamer:
    """Codex titles: proven by an open rollout, else a guarded start-time match, else none.

    Nothing is cached between snapshots except the session index (by mtime and size): an
    interactive Codex process can move to another thread, so only a current proof counts.
    """

    def __init__(self, runner, home: Path, clock=time.monotonic):
        self.runner, self.home, self.clock = runner, home, clock
        self.index_key, self.index = None, {}

    def lsof(self, pids, deadline) -> dict[int, CodexFacts]:
        remaining = deadline - self.clock()
        if not pids or remaining < LSOF_FLOOR:
            return {}
        try:
            result = self.runner.run((LSOF, '-a', '-p', ','.join(map(str, sorted(pids))), '-Fpfn'),
                                     timeout=remaining)
        except ApiError:
            return {}
        # lsof exits 1 when any pid has gone; the rest of its output still stands.
        return parse_lsof(result.stdout)

    def rollout_stamps(self, starts, deadline) -> dict[str, float]:
        """rollout id -> creation stamp, from the dated folders near the given starts."""
        stamps, examined = {}, 0
        for day in rollout_days(starts):
            try:
                entries = os.scandir(self.home / CODEX_ROLLOUTS / day)
            except OSError:
                continue
            with entries:
                for entry in entries:
                    examined += 1
                    if examined > ROLLOUT_SCAN or self.clock() >= deadline:
                        return stamps
                    match = ROLLOUT_NAME.fullmatch(entry.name)
                    stamp = match and rollout_stamp_epoch(match.group(1))
                    if stamp is not None:
                        stamps[match.group(2)] = stamp
        return stamps

    def guesses(self, rows, claimed, deadline) -> dict[int, str]:
        """pid -> rollout id where exactly one process and one unclaimed rollout share a window."""
        starts = {row.pid: start for row in rows if (start := started_epoch(row.started)) is not None}
        if not starts or self.clock() >= deadline:
            return {}
        stamps = {rollout: stamp for rollout, stamp in self.rollout_stamps(starts.values(), deadline).items()
                  if rollout not in claimed}
        near = lambda at, values: [key for key, value in values.items() if abs(value - at) <= ROLLOUT_WINDOW]
        result = {}
        for pid, start in starts.items():
            candidates = near(start, stamps)
            if (len(near(start, starts)) == 1 and len(candidates) == 1
                    and near(stamps[candidates[0]], starts) == [pid]):
                result[pid] = candidates[0]
        return result

    def thread_names(self) -> dict[str, str]:
        path = self.home / CODEX_INDEX
        try:
            info = path.stat()
        except OSError:
            return {}
        key = (info.st_mtime_ns, info.st_size)
        if key != self.index_key:
            raw = bounded_read(path, INDEX_LIMIT, tail=True) or b''
            lines = raw.decode('utf-8', errors='replace').splitlines()
            if info.st_size > INDEX_LIMIT:
                lines = lines[1:]
            names = {}
            for line in lines:
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                if isinstance(record, dict) and isinstance(record.get('id'), str):
                    if (name := short_text(record.get('thread_name'))):
                        names[record['id']] = name
            self.index_key, self.index = key, names
        return self.index

    def describe(self, rows: list[ProcessRow], found: dict[int, CodexFacts],
                 deadline) -> dict[int, SessionInfo]:
        if not rows:
            return {}
        facts = {row.pid: found.get(row.pid, CodexFacts(None, None)) for row in rows}
        # Rollouts proven open by any process are reserved before any start-time guess.
        claimed = {item.rollout for item in facts.values() if item.rollout}
        unproven = [row for row in rows if not facts[row.pid].rollout]
        guessed = self.guesses(unproven, claimed, deadline)
        names = self.thread_names() if self.clock() < deadline else self.index
        return {row.pid: codex_info(row, facts[row.pid], guessed.get(row.pid), names)
                for row in rows}


def codex_info(row, facts: CodexFacts, guessed: str | None, names) -> SessionInfo:
    place = path_name(facts.cwd)
    fallback = f'codex · {place}' if place else 'codex'
    for rollout, source in ((facts.rollout, NameSource.ROLLOUT_OPEN),
                            (guessed, NameSource.ROLLOUT_TIME)):
        if rollout and (title := names.get(rollout)):
            return SessionInfo(title, codex_detail(row), None, facts.cwd, source)
    return SessionInfo(fallback, codex_detail(row), None, facts.cwd, NameSource.FALLBACK)


class SessionDirectory:
    """Session names for every Claude and Codex session root in a snapshot."""

    def __init__(self, runner, home: Path, clock=time.monotonic):
        self.runner, self.home, self.clock = runner, home, clock
        self.codex = CodexNamer(runner, home, clock)

    def describe(self, roots: dict[int, tuple[Kind, ProcessRow]], deadline) -> dict[int, SessionInfo]:
        claude = {pid: row for pid, (kind, row) in roots.items() if kind is Kind.CLAUDE}
        registry = read_claude_registry(self.home, claude, deadline, self.clock)
        headless = [pid for pid, row in claude.items() if not registered(row, registry.get(pid))]
        codex = [row for kind, row in roots.values() if kind is Kind.CODEX]
        # One lsof call covers headless Claude cwds and Codex rollouts.
        found = self.codex.lsof(headless + [row.pid for row in codex], deadline)
        result = {pid: claude_info(row, registry.get(pid), found[pid].cwd if pid in found else None)
                  for pid, row in claude.items()}
        result.update(self.codex.describe(codex, found, deadline))
        return result


def registered(row, entry) -> bool:
    """The entry is this process's own: a well-formed `procStart` equal to its start time."""
    if not isinstance(entry, dict):
        return False
    proc_start = normal_started(entry.get('procStart'))
    return proc_start is not None and proc_start == normal_started(row.started)
