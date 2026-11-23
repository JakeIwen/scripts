"""Schedule ready public-name checks without a barrier on unrelated package probes."""
from dataclasses import asdict, dataclass
from functools import partial
from queue import Empty, Queue
from threading import Thread
from typing import Optional

from dep_guard_lookup import cached_metadata, count, fact_key, metadata, presence, statistics_groups, stats_batch
from dep_guard_verdict import DAY, LOW_USE, POPULAR, Downloads, Existence, Metadata, Stat


@dataclass
class Facts:
    existence: Existence = Existence.UNKNOWN
    # None means no answer yet, including when the invocation needs a fresh probe.
    stats: Optional[Downloads] = None
    meta: Optional[Metadata] = None


class ReadyJobs:
    """Only the coordinator applies answers; network workers never own the result ledger."""
    def __init__(self):
        self.events = Queue()
        self.pending = 0

    def submit(self, work, accept):
        self.pending += 1

        def run():
            try:
                answer = work()
            except Exception:
                answer = None  # Other completed checks must still reach the verdict.
            self.events.put((accept, answer, True))

        Thread(target=run, daemon=True).start()

    def interim(self, accept, answer):
        self.events.put((accept, answer, False))

    def receive(self, remaining):
        try:
            event = self.events.get(timeout=remaining)
        except Empty:
            return False
        while True:
            accept, answer, finished = event
            self.pending -= finished
            accept(answer)
            try:
                event = self.events.get_nowait()
            except Empty:
                return True


class PackageChecks:
    def __init__(self, requests, net, cache, cached_advisories, lookup_advisories):
        self.net, self.cache = net, cache
        self.lookup_advisories = lookup_advisories
        self.pins = {k: {i.pin for i in invs if i.pin} for k, invs in requests.items()}
        # This read is allowed only AFTER route eligibility. It never sends a
        # name to OSV; even an existence refresh failure cannot erase a known MAL.
        self.advisories = cached_advisories(requests, cache, pins=self.pins)
        self.facts = {k: {i.override: Facts() for i in invs} for k, invs in requests.items()}
        self.jobs = ReadyJobs()
        self.activated, self.started_stats, self.started_meta = set(), set(), set()

    def update_cache(self, key, source, value, ttl):
        try:
            self.cache.put(fact_key(key, source), value, ttl)
        except OSError:
            pass  # The ledger already contains the answer.

    def start_existence(self):
        for k, scopes in self.facts.items():
            previous = self.cache.get(fact_key(k, 'exists'))
            for override, facts in scopes.items():
                if previous == Existence.FOUND or (not override and previous == Existence.MISSING):
                    facts.existence = Existence(previous)
            if any(f.existence is Existence.UNKNOWN for f in scopes.values()):
                self.jobs.submit(partial(presence, k, self.net), partial(self.accept_existence, k))

    def accept_existence(self, k, answer):
        if answer not in (Existence.FOUND, Existence.MISSING):
            return
        for f in self.facts[k].values():
            if f.existence is Existence.UNKNOWN:
                f.existence = answer
        self.update_cache(k, 'exists', answer.value, 7 * DAY if answer is Existence.FOUND else 3600)

    def activate(self, keys):
        for k in keys:
            # Snapshot the normal and override lanes BEFORE starting a refresh.
            # A failed refresh (or a successful one) is not the unmarked command's evidence.
            n = self.cache.get(fact_key(k, 'downloads'))
            m = cached_metadata(self.cache, k)
            for override, f in self.facts[k].items():
                if f.existence is Existence.FOUND:
                    f.meta = m
                    if count(n) and not (override and n < LOW_USE):
                        f.stats = Downloads(Stat.MEASURED, n)
        if keys:
            def report(k, snapshot):
                self.jobs.interim(self.accept_osv, {k: snapshot})
            self.jobs.submit(partial(self.lookup_advisories, keys, self.net, self.cache,
                                     pins={k: self.pins[k] for k in keys}, publish=report), self.accept_osv)
            self.activated.update(keys)

    def accept_osv(self, answers):
        if answers:
            self.advisories.update(answers)

    def accept_stats(self, keys, answers):
        for k in keys:
            answer = (answers or {}).get(k, Downloads())
            for f in self.facts[k].values():
                if f.existence is Existence.FOUND and f.stats is None:
                    f.stats = answer
            if answer.state is Stat.MEASURED:
                self.update_cache(k, 'downloads', answer.count, 7 * DAY)

    def accept_meta(self, k, answer):
        for f in self.facts[k].values():
            if f.existence is Existence.FOUND and f.meta is None:
                f.meta = answer or Metadata()
        if answer and answer.first is not None:
            self.update_cache(k, 'metadata', asdict(answer), 30 * DAY)

    def start_ready(self):
        public = [k for k, scopes in self.facts.items() if any(f.existence is Existence.FOUND for f in scopes.values())]
        self.activate([k for k in public if k not in self.activated])
        stats = []
        for k in public:
            active = [f for f in self.facts[k].values() if f.existence is Existence.FOUND]
            if k not in self.started_stats and any(f.stats is None for f in active):
                stats.append(k)
            if (k not in self.started_meta and any(f.meta is None and f.stats is not None
                                                   and (f.stats.count or 0) < POPULAR for f in active)):
                self.started_meta.add(k)
                self.jobs.submit(partial(metadata, k, self.net), partial(self.accept_meta, k))
        self.started_stats.update(stats)
        for group in statistics_groups(stats):
            self.jobs.submit(partial(stats_batch, group, self.net), partial(self.accept_stats, group))

    def run(self, publish):
        self.start_existence()
        while True:
            self.start_ready()
            publish()
            if not self.jobs.pending or not self.jobs.receive(self.net.remaining()):
                break
        return not self.jobs.pending
