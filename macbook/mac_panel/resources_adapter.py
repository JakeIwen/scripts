"""Resources section: one shared snapshot of processes, sessions and system numbers.

Page polls and the history sampler read the same snapshot: a single collection at a time,
reused for SNAPSHOT_TTL seconds. A failed process table fails the section (502); any other
probe only marks itself unavailable. Session naming (file reads and lsof) runs on its own
thread and is waited for at most ENRICHMENT_BUDGET plus a grace period, so a stalled read
there never holds the collection past its bound: the snapshot goes out with fallback names.
Signals go through `process_signals`, which never uses this snapshot.
"""
from functools import lru_cache
import logging
import os
from pathlib import Path
import pwd
import threading
import time

from cli_runner import CliRunner
from llm_sessions import NameSource, SessionDirectory, SessionInfo
from panel_contracts import (AppRecord, ApiError, GroupProcessRecord, KindTotals, Level,
                             ProcessRecord, ResourceGroupRecord, ResourcesHealth,
                             ResourcesPayload, SignalResult, SystemRecord)
from process_signals import ProcessSignaller
from process_table import ProcessReader, ProcessTable
from resource_groups import (JOBS_ROOT, SESSION_KINDS, TOTAL_KINDS, Group, Grouping, Kind,
                             build_grouping, is_compute_root)
from signal_policy import PolicyContext, Scope, SignalPolicy, tree_blocked
from system_metrics import Pressure, Probe, SystemFacts, SystemMetrics, Thermal, cpu_percent

SNAPSHOT_TTL = 5
ENRICHMENT_BUDGET = 1
# Beyond the budget the naming thread gets this long to return what it has.
ENRICHMENT_GRACE = 0.25
# Every process is listed so the page's filter finds an idle one by name; the cap only bounds a runaway table.
PROCESS_LIST_CAP = 2000
TOP_APPS = 25
DISK_WARN_FRACTION = 0.10
LEVEL_RANK = {Level.OK: 0, Level.UNKNOWN: 1, Level.WARN: 2, Level.ERROR: 3}
GROUP_ORDER = {kind: index for index, kind in enumerate(Kind)}
LOG = logging.getLogger(__name__)


@lru_cache(maxsize=256)
def user_name(uid) -> str:
    try:
        return pwd.getpwuid(uid).pw_name
    except KeyError:
        return str(uid)


def top_union(items, limit, *keys):
    """The first `limit` items by each key, merged and sorted by the first key, descending."""
    chosen = {}
    for key in keys:
        for item in sorted(items, key=key, reverse=True)[:limit]:
            chosen[id(item)] = item
    return sorted(chosen.values(), key=keys[0], reverse=True)


def health(facts: SystemFacts, table: ProcessTable) -> ResourcesHealth:
    levels, reasons = [Level.OK], []
    pressure = facts.get(Probe.PRESSURE)
    if pressure is Pressure.CRITICAL:
        levels.append(Level.ERROR)
        reasons.append('Memory pressure is critical')
    elif pressure is Pressure.WARN:
        levels.append(Level.WARN)
        reasons.append('Memory pressure is elevated')
    elif pressure is None:
        # Stays in the reasons and the unavailable list even when another warning outranks it.
        levels.append(Level.UNKNOWN)
        reasons.append('Memory pressure is unavailable')
    if facts.get(Probe.THERMAL) is Thermal.WARNING:
        levels.append(Level.WARN)
        reasons.append('A thermal warning is recorded')
    disk = facts.get(Probe.DISK)
    if disk and disk['total'] and disk['free'] / disk['total'] < DISK_WARN_FRACTION:
        levels.append(Level.WARN)
        reasons.append(f"Disk has {disk['free'] / disk['total']:.0%} free")
    unavailable = list(facts.unavailable)
    if pressure is None and Probe.PRESSURE.value not in unavailable:
        unavailable.append(Probe.PRESSURE.value)
    return ResourcesHealth(level=max(levels, key=LEVEL_RANK.get).value, reasons=reasons,
                           unavailable=unavailable)


def system_record(facts: SystemFacts, table: ProcessTable) -> SystemRecord:
    memory = facts.get(Probe.MEMORY) or {}
    swap = facts.get(Probe.SWAP) or {}
    power = facts.get(Probe.POWER) or {}
    disk = facts.get(Probe.DISK) or {}
    pressure, thermal = facts.get(Probe.PRESSURE), facts.get(Probe.THERMAL)
    total_cpu = sum(row.cpu for row in table.rows.values())
    return SystemRecord(
        cpu_percent=cpu_percent(total_cpu, facts.get(Probe.NCPU)), ncpu=facts.get(Probe.NCPU),
        load=facts.get(Probe.LOAD), process_count=len(table.rows),
        uptime_seconds=facts.get(Probe.UPTIME),
        memory={'total': facts.get(Probe.MEMORY_TOTAL),
                **{key: memory.get(key) for key in
                   ('used', 'app', 'wired', 'compressed', 'cached', 'free', 'other')},
                'swap_used': swap.get('used'), 'swap_total': swap.get('total'),
                'pressure': pressure.value if pressure else None},
        thermal=thermal.value if thermal else None,
        power={key: power.get(key) for key in ('source', 'percent', 'charging')},
        disk={key: disk.get(key) for key in ('total', 'free')})


class SnapshotBuilder:
    """Turns one table, grouping and set of session names into the wire payload."""

    def __init__(self, table, grouping: Grouping, infos: dict[int, SessionInfo], policy,
                 context: PolicyContext):
        self.table, self.grouping, self.infos = table, grouping, infos
        self.policy, self.context = policy, context
        self.names = {gid: self.group_name(group) for gid, group in grouping.groups.items()}

    def blocked(self, row, kind=None):
        kind = self.grouping.kind(row.pid) if kind is None else kind
        return self.policy.blocked(row, kind, Scope.PROCESS, self.context)

    def group_name(self, group: Group) -> str:
        info = self.infos.get(group.root.pid)
        if group.kind in SESSION_KINDS:
            return info.name if info else group.kind.value
        return group.label or group.root.name

    def group(self, group: Group) -> ResourceGroupRecord:
        info = self.infos.get(group.root.pid) if group.kind in SESSION_KINDS else None
        processes = [GroupProcessRecord(
            pid=m.row.pid, ppid=m.row.ppid, depth=m.depth, name=m.row.name, cpu=round(m.row.cpu, 1),
            rss=m.row.rss_bytes, nice=m.row.nice, stopped=m.row.stopped, started=m.row.started,
            blocked=self.blocked(m.row, group.kind)) for m in group.members]
        source = None
        if group.kind in SESSION_KINDS:
            source = (info.source if info else NameSource.FALLBACK).value
        return ResourceGroupRecord(
            id=group.id, kind=group.kind.value, name=self.names[group.id], name_source=source,
            detail=info.detail if info else group.root.name,
            status=info.status if info else None, cwd=info.cwd if info else None,
            parent=group.parent, root_pid=group.root.pid, started=group.root.started,
            cpu=round(sum(m.row.cpu for m in group.members), 1),
            rss=sum(m.row.rss_bytes for m in group.members), count=len(group.members),
            tree_blocked=tree_blocked(self.policy, group, self.context, self.names),
            processes=processes)

    def groups(self) -> list[ResourceGroupRecord]:
        # Kinds in declaration order; compute's worker row leads its job rows.
        ordered = sorted(self.grouping.groups.values(), key=lambda group: (
            GROUP_ORDER[group.kind], not is_compute_root(group.root),
            -sum(m.row.cpu for m in group.members), group.root.pid))
        return [self.group(group) for group in ordered]

    def totals(self, groups) -> dict[str, KindTotals]:
        totals = {kind.value: KindTotals(cpu=0.0, rss=0, count=0) for kind in TOTAL_KINDS}
        for record in groups:
            entry = totals[record['kind']]
            entry['cpu'] = round(entry['cpu'] + record['cpu'], 1)
            entry['rss'] += record['rss']
            entry['count'] += record['count']
        return totals

    def apps(self) -> list[AppRecord]:
        trees: dict[int, list] = {}
        for pid, row in self.table.rows.items():
            if pid > 1 and self.grouping.kind(pid) is Kind.APP:
                trees.setdefault(self.table.top_ancestor(pid), []).append(row)
        records = []
        for root_pid, rows in trees.items():
            root = self.table.rows[root_pid]
            records.append(AppRecord(name=root.name, root_pid=root_pid, started=root.started,
                                     cpu=round(sum(row.cpu for row in rows), 1),
                                     rss=sum(row.rss_bytes for row in rows), count=len(rows)))
        return top_union(records, TOP_APPS, lambda r: r['cpu'], lambda r: r['rss'])

    def processes(self) -> list[ProcessRecord]:
        ordered = sorted(self.table.rows.values(), key=lambda row: (-row.cpu, -row.rss_bytes, row.pid))
        # A paused process is idle and small, so it must survive the cap to stay resumable from the page.
        rows = ordered[:PROCESS_LIST_CAP] + [row for row in ordered[PROCESS_LIST_CAP:] if row.stopped]
        return [ProcessRecord(pid=row.pid, ppid=row.ppid, user=user_name(row.uid), name=row.name,
                              cpu=round(row.cpu, 1), rss=row.rss_bytes, nice=row.nice,
                              stopped=row.stopped, started=row.started,
                              group=self.grouping.membership.get(row.pid), blocked=self.blocked(row))
                for row in rows]


def resource_samples(payload: ResourcesPayload):
    """History rows: machine CPU, memory and swap, each kind's CPU and RSS, and the level."""
    def numeric(key, value):
        return key, Level.OK if value is not None else Level.UNKNOWN, value, None
    system = payload['system']
    rows = [numeric('system.cpu', system['cpu_percent']),
            numeric('system.memory_used', system['memory']['used']),
            numeric('system.swap_used', system['memory']['swap_used'])]
    for kind, totals in payload['totals'].items():
        rows += [numeric(f'{kind}.cpu', totals['cpu']), numeric(f'{kind}.rss', totals['rss'])]
    reasons = payload['health']['reasons']
    rows.append(('level', Level(payload['health']['level']), None, '; '.join(reasons) or None))
    return rows


class BoundedCall:
    """Runs one call on a daemon thread; the caller waits on `done` for as long as it likes."""

    def __init__(self, function, *args):
        self.value, self.error, self.done = None, None, threading.Event()
        threading.Thread(target=self.run, args=(function, args), daemon=True,
                         name='resources-naming').start()

    def run(self, function, args):
        try:
            self.value = function(*args)
        except Exception as exc:  # noqa: BLE001 - reported by the waiting caller
            self.error = exc
        finally:
            self.done.set()


class ResourcesAdapter:
    def __init__(self, runner=None, reader=None, metrics=None, clock=time.monotonic,
                 wall_clock=time.time, home=None, kill=os.kill, getpriority=os.getpriority,
                 setpriority=os.setpriority, policy=None):
        runner = runner or CliRunner()
        home = Path(home) if home else Path.home()
        self.jobs_root = str(home / JOBS_ROOT)
        self.reader = reader or ProcessReader(runner, clock)
        self.metrics = metrics or SystemMetrics(runner, clock=clock, wall_clock=wall_clock)
        self.sessions = SessionDirectory(runner, home, clock)
        self.policy = policy or SignalPolicy()
        self.signaller = ProcessSignaller(self.reader, self.policy, kill, getpriority, setpriority,
                                          clock, self.jobs_root)
        self.clock, self.wall_clock = clock, wall_clock
        # The naming call still running from an earlier snapshot, if one overran its wait.
        self.naming: BoundedCall | None = None
        # Held for the whole collection: concurrent readers wait and then share its result.
        self.collect_lock = threading.Lock()
        # Guards only the cache fields, so a mutation never waits on a slow collection.
        self.state_lock = threading.Lock()
        self.cached, self.cached_at, self.generation = None, None, 0

    def fresh(self):
        with self.state_lock:
            if self.cached_at is not None and self.clock() - self.cached_at < SNAPSHOT_TTL:
                return self.cached
        return None

    def read(self) -> ResourcesPayload:
        with self.collect_lock:
            cached = self.fresh()
            if cached is None:
                with self.state_lock:
                    generation = self.generation
                try:
                    cached = self.collect()
                except ApiError as exc:
                    # A failed table is shared for the TTL too, so polls do not pile up behind it.
                    cached = exc
                with self.state_lock:
                    self.cached = cached
                    # A signal sent mid-collection makes this result stale at once.
                    self.cached_at = self.clock() if generation == self.generation else None
        if isinstance(cached, ApiError):
            raise cached
        return cached

    def describe_sessions(self, grouping) -> dict[int, SessionInfo]:
        """Session names, or {} (fallback names) when naming fails or overruns its budget."""
        roots = {group.root.pid: (group.kind, group.root) for group in grouping.groups.values()
                 if group.kind in SESSION_KINDS}
        if not roots:
            return {}
        if self.naming is not None and not self.naming.done.is_set():
            # One stalled naming call at a time; this snapshot uses fallback names.
            return {}
        self.naming = BoundedCall(self.sessions.describe, roots, self.clock() + ENRICHMENT_BUDGET)
        if not self.naming.done.wait(ENRICHMENT_BUDGET + ENRICHMENT_GRACE):
            LOG.warning('Session naming overran its budget; using fallback names')
            return {}
        if self.naming.error is not None:
            # Names are enrichment; the numbers must not wait on or fail with them.
            LOG.error('Session naming failed', exc_info=self.naming.error)
            return {}
        return self.naming.value

    def collect(self) -> ResourcesPayload:
        table = self.reader.table()
        facts = self.metrics.collect()
        grouping = build_grouping(table, self.jobs_root)
        builder = SnapshotBuilder(table, grouping, self.describe_sessions(grouping), self.policy,
                                  self.policy.context(table, grouping))
        groups = builder.groups()
        return ResourcesPayload(sampled_at=self.wall_clock(), health=health(facts, table),
                                system=system_record(facts, table), totals=builder.totals(groups),
                                groups=groups, apps=builder.apps(), processes=builder.processes())

    def mutate(self, body) -> SignalResult:
        try:
            return self.signaller.mutate(body)
        finally:
            # The next read shows the outcome instead of a snapshot from before it.
            with self.state_lock:
                self.generation += 1
                self.cached_at = None
