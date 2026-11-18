"""Name Claude and Codex sessions from their own records, never from argv or transcripts.

Claude Code keeps `~/.claude/sessions/<pid>.json` per interactive session (name, status,
cwd); only those `.json` files are opened, never the `.key` files beside them. Codex keeps
no per-pid record: its thread title is found by locating the session's rollout file (open
in the process, or else the one whose timestamp matches the process start) and looking the
rollout id up in `~/.codex/session_index.jsonl`. Codex naming is best-effort under a time
budget and falls back to "codex · <cwd name>"; it never delays the core numbers.
"""
from collections import OrderedDict
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import stat
import time

from panel_contracts import ApiError
from process_table import ProcessRow, started_epoch
from resource_groups import Kind, codex_detail

CLAUDE_SESSIONS = Path('.claude/sessions')
CODEX_INDEX = Path('.codex/session_index.jsonl')
CODEX_ROLLOUTS = Path('.codex/sessions')
REGISTRY_NAME = re.compile(r'(\d{1,10})\.json')
REGISTRY_LIMIT = 64 * 1024
REGISTRY_FILES = 512
INDEX_LIMIT = 4 * 1024 * 1024
ROLLOUT_NAME = re.compile(r'rollout-(\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2})-'
                          r'([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.jsonl')
ROLLOUT_STAMP = '%Y-%m-%dT%H-%M-%S'
# Codex creates its rollout within a second or two of starting.
ROLLOUT_WINDOW = 5
ROLLOUT_LISTING = 5000
NAME_LIMIT = 120
NAME_CACHE = 256
LSOF = '/usr/sbin/lsof'
LSOF_FLOOR = 0.05
SESSION_STATUSES = frozenset(('busy', 'idle'))


@dataclass(frozen=True)
class SessionInfo:
    name: str
    detail: str
    status: str | None
    cwd: str | None


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


def read_claude_registry(home: Path) -> dict[int, dict]:
    directory = home / CLAUDE_SESSIONS
    try:
        names = sorted(os.listdir(directory))
    except OSError:
        return {}
    entries = {}
    for name in names:
        match = REGISTRY_NAME.fullmatch(name)
        if not match:
            continue
        if len(entries) >= REGISTRY_FILES:
            break
        raw = bounded_read(directory / name, REGISTRY_LIMIT)
        try:
            value = json.loads(raw) if raw else None
        except ValueError:
            continue
        pid = int(match.group(1))
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
    return SessionInfo(short_text(entry.get('name')) or path_name(cwd) or 'claude',
                       short_text(entry.get('kind')) or 'interactive',
                       status if status in SESSION_STATUSES else None, cwd)


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


class CodexNamer:
    def __init__(self, runner, home: Path, clock=time.monotonic):
        self.runner, self.home, self.clock = runner, home, clock
        self.cache: OrderedDict[tuple[int, str], CodexFacts] = OrderedDict()
        self.index_key, self.index = None, {}

    def remember(self, key, facts):
        self.cache[key] = facts
        self.cache.move_to_end(key)
        while len(self.cache) > NAME_CACHE:
            self.cache.popitem(last=False)

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

    def rollout_by_start(self, row, claimed) -> str | None:
        start = started_epoch(row.started)
        if start is None:
            return None
        days = {time.strftime('%Y/%m/%d', time.localtime(start + offset))
                for offset in (-ROLLOUT_WINDOW, 0, ROLLOUT_WINDOW)}
        matches = set()
        for day in days:
            try:
                names = os.listdir(self.home / CODEX_ROLLOUTS / day)[:ROLLOUT_LISTING]
            except OSError:
                continue
            for name in names:
                match = ROLLOUT_NAME.fullmatch(name)
                stamp = match and rollout_stamp_epoch(match.group(1))
                if stamp is not None and abs(stamp - start) <= ROLLOUT_WINDOW:
                    matches.add(match.group(2))
        matches -= claimed
        return matches.pop() if len(matches) == 1 else None

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

    def unresolved(self, rows: list[ProcessRow]) -> list[int]:
        """Pids whose rollout is not cached yet: the ones worth an lsof lookup."""
        return [row.pid for row in rows
                if not (facts := self.cache.get((row.pid, row.started))) or not facts.rollout]

    def describe(self, rows: list[ProcessRow], found: dict[int, CodexFacts],
                 deadline) -> dict[int, SessionInfo]:
        if not rows:
            return {}
        known = {row.pid: self.cache.get((row.pid, row.started)) for row in rows}
        claimed = {facts.rollout for facts in known.values() if facts and facts.rollout}
        result = {}
        for row in rows:
            facts = known[row.pid] or CodexFacts(None, None)
            fresh = found.get(row.pid, CodexFacts(None, None))
            rollout = facts.rollout or fresh.rollout
            if rollout is None and self.clock() < deadline:
                rollout = self.rollout_by_start(row, claimed)
            if rollout:
                claimed.add(rollout)
            facts = CodexFacts(rollout, facts.cwd or fresh.cwd)
            if facts.rollout or facts.cwd:
                self.remember((row.pid, row.started), facts)
            result[row.pid] = facts
        names = self.thread_names() if self.clock() < deadline else self.index
        return {row.pid: codex_info(row, result[row.pid], names) for row in rows}


def codex_info(row, facts: CodexFacts, names) -> SessionInfo:
    title = names.get(facts.rollout) if facts.rollout else None
    place = path_name(facts.cwd)
    fallback = f'codex · {place}' if place else 'codex'
    return SessionInfo(title or fallback, codex_detail(row), None, facts.cwd)


class SessionDirectory:
    """Session names for every Claude and Codex session root in a snapshot."""

    def __init__(self, runner, home: Path, clock=time.monotonic):
        self.runner, self.home, self.clock = runner, home, clock
        self.codex = CodexNamer(runner, home, clock)

    def describe(self, roots: dict[int, tuple[Kind, ProcessRow]], deadline) -> dict[int, SessionInfo]:
        registry = read_claude_registry(self.home)
        claude = {pid: row for pid, (kind, row) in roots.items() if kind is Kind.CLAUDE}
        headless = [pid for pid, row in claude.items() if not registered(row, registry.get(pid))]
        codex = [row for kind, row in roots.values() if kind is Kind.CODEX]
        # One lsof call covers headless Claude cwds and uncached Codex rollouts.
        found = self.codex.lsof(headless + self.codex.unresolved(codex), deadline)
        result = {pid: claude_info(row, registry.get(pid), found[pid].cwd if pid in found else None)
                  for pid, row in claude.items()}
        result.update(self.codex.describe(codex, found, deadline))
        return result


def registered(row, entry) -> bool:
    proc_start = entry.get('procStart') if entry else None
    return entry is not None and not (isinstance(proc_start, str) and proc_start != row.started)
