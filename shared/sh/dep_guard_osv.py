"""Read cached OSV malware evidence; query only registry-confirmed public names."""
from dataclasses import dataclass
from datetime import datetime
from functools import partial
import re
from threading import Lock
from types import MappingProxyType
from typing import Mapping, Optional

from dep_guard_specs import canonical_name
from dep_guard_versions import contains, git_range, intervals, version_key

_API = 'https://api.osv.dev/v1/'
_DAY = 86400
_MAX_VERSIONS = 200
_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}\Z')


def _id(value):
    return isinstance(value, str) and _ID.fullmatch(value) is not None


def _add_version(versions, value):
    """Keep previously retained versions; return False only on overflow."""
    if value in versions:
        return True
    if len(versions) == _MAX_VERSIONS:
        return False
    versions.add(value)
    return True


@dataclass(frozen=True)
class _Rule:
    versions: frozenset = frozenset()
    spans: frozenset = frozenset()
    # A bounded projection of a valid advisory, not a claim that all versions are bad.
    needs_probe: bool = False
    incomplete: bool = False

    def merge(self, other):
        versions = set(self.versions)
        overflow = not all(_add_version(versions, v) for v in sorted(other.versions))
        return _Rule(frozenset(versions), self.spans | other.spans,
                     self.needs_probe or other.needs_probe or overflow,
                     self.incomplete or other.incomplete)

    def known(self):
        return bool(self.versions or self.spans or self.needs_probe)

    def whole(self):
        return (None, None, False) in self.spans

    def matches(self, eco, version):
        return (any(version_key(eco, v) == version for v in self.versions) or
                any(contains(eco, span, version) for span in self.spans))

    def serialize(self):
        return {'versions': sorted(self.versions), 'spans': list(map(list, self.spans)),
                'needs_probe': self.needs_probe, 'incomplete': self.incomplete}

    @classmethod
    def restore(cls, eco, data):
        if not isinstance(data, dict) or set(data) != {'versions', 'spans', 'needs_probe', 'incomplete'}:
            return None
        vs, spans = data['versions'], data['spans']
        if (not isinstance(vs, list) or len(vs) > _MAX_VERSIONS or not isinstance(spans, list)
                or not all(isinstance(v, str) and version_key(eco, v) is not None for v in vs)
                or any(type(data[k]) is not bool for k in ('needs_probe', 'incomplete'))):
            return None
        for span in spans:
            if not isinstance(span, list) or len(span) != 3 or type(span[2]) is not bool:
                return None
            if any(v is not None and (not isinstance(v, str) or version_key(eco, v) is None) for v in span[:2]):
                return None
        rule = cls(frozenset(vs), frozenset(map(tuple, spans)), data['needs_probe'], data['incomplete'])
        return rule if rule.known() else None


@dataclass(frozen=True)
class OSVEvidence:
    ecosystem: str
    rules: Mapping
    complete: bool  # the name search AND every required detail were checked
    exact: Mapping  # pin -> (confirmed matches, search complete)

    def decision(self, pin: Optional[str]):
        deny, notes = [], []
        version = version_key(self.ecosystem, pin) if isinstance(pin, str) else None
        hits, checked = self.exact.get(pin, (set(), False))
        uncertain = not self.complete or (pin in self.exact and not checked)
        for aid, rule in self.rules.items():
            if rule.whole():
                deny.append(f'{aid}: OSV marks the whole package as malicious (no inline override)')
            elif version is None:
                sample = ', '.join(sorted(rule.versions)[:5])
                affected = 'the affected versions' + (f' (including {sample})' if sample else '')
                deny.append(f'{aid}: pin an exact version outside {affected} (no inline override)')
            elif aid in hits or rule.matches(self.ecosystem, version):
                deny.append(f'{aid}: version {pin} is affected by malware (no inline override)')
            elif (rule.needs_probe or rule.incomplete) and not checked:
                uncertain = True
        if uncertain:
            notes.append(f'OSV malware check incomplete for {pin}' if version is not None else 'OSV malware check incomplete')
        elif self.rules and not deny:
            notes.append(f'{pin} outside the affected versions in ' + ', '.join(sorted(self.rules)))
        return deny, notes


def _detail(key, aid, data, probe=None):
    """None,False is an invalid detail; None,True is an explicit withdrawal."""
    eco, name = key
    if not isinstance(data, dict) or data.get('id') != aid:
        return None, False
    if 'withdrawn' in data:
        try:
            timestamp = datetime.fromisoformat(data['withdrawn'].replace('Z', '+00:00'))
        except (ValueError, TypeError, AttributeError):
            return None, False
        return None, timestamp.tzinfo is not None
    entries = data.get('affected')
    if not isinstance(entries, list):
        return None, False
    versions, spans_seen = set(), set()
    incomplete = needs_probe = False
    for entry in entries:
        pkg = entry.get('package') if isinstance(entry, dict) else None
        if (not isinstance(pkg, dict) or not isinstance(pkg.get('ecosystem'), str)
                or not isinstance(pkg.get('name'), str)):
            incomplete = True
            continue
        if pkg['ecosystem'] != eco:
            continue
        affected_name = canonical_name(eco, pkg['name'])
        if affected_name is None:
            incomplete = True
        if affected_name != name:
            continue
        vs, ranges = entry.get('versions', []), entry.get('ranges', [])
        if not (vs or ranges):
            incomplete = True
        if isinstance(vs, list):
            for v in vs:
                value = version_key(eco, v) if isinstance(v, str) else None
                if value is None:
                    incomplete = True
                elif probe is None or not versions or probe == value:
                    # For /query compare against the full wire list, even past the stored cap.
                    needs_probe |= not _add_version(versions, v)
        else:
            incomplete = True
        if isinstance(ranges, list):
            for record in ranges:
                if git_range(record):
                    needs_probe = True
                    continue
                spans = intervals(eco, record)
                if spans is None:
                    incomplete = True
                else:
                    spans_seen.update(spans)
        else:
            incomplete = True
    rule = _Rule(frozenset(versions), frozenset(spans_seen), needs_probe, incomplete)
    return (rule, not incomplete) if rule.known() else (None, False)


def _page(data):
    """A valid empty protobuf result is {}; an error object is not an empty page."""
    if not isinstance(data, dict) or set(data) - {'vulns', 'next_page_token'}:
        return set(), None, False
    vs = data.get('vulns', [])
    if not isinstance(vs, list):
        return set(), None, False
    ids, ok = set(), True
    for v in vs:
        if not isinstance(v, dict) or not _id(v.get('id')):
            ok = False
        elif v['id'].startswith('MAL-'):
            ids.add(v['id'])
    token = data.get('next_page_token', '')
    if not isinstance(token, str) or len(token) > 4096:
        return ids, None, False
    return ids, token or None, ok


def _data(reply):
    if isinstance(reply, (tuple, list)) and len(reply) == 2 and reply[0] == 200:
        return reply[1]
    return None


def _batch(network, tokens):
    queries = [dict(package={'ecosystem': eco, 'name': name}, **({'page_token': t} if t else {}))
               for (eco, name), t in tokens.items()]
    return network.get_json(_API + 'querybatch', method='POST', body={'queries': queries})


def _read(cache, key):
    try:
        return cache.get(key)
    except (ValueError, OSError, RuntimeError):
        return None


def _write(cache, key, value, ttl):
    try:
        cache.put(key, value, ttl)
    except (ValueError, OSError, RuntimeError):
        pass  # Persistence is optional; the caller retains its in-memory decision.


def _stream_map(network, jobs, receive):
    """Deliver completed replies before a stalled sibling; late workers cannot mutate a closed batch."""
    lock = Lock()
    waiting = set(jobs)

    def run(key, request):
        reply = request()
        with lock:
            if key in waiting and network.remaining() > 0:
                receive(key, _data(reply))
                waiting.remove(key)

    if jobs and network.remaining() > 0:
        network.map({k: partial(run, k, request) for k, request in jobs.items()})
    with lock:
        for key in waiting:
            receive(key, None)
        waiting.clear()


def _cache_ids(value):
    if isinstance(value, list) and all(_id(i) and i.startswith('MAL-') for i in value):
        return set(value)
    return None


class _Search:
    def __init__(self, key, cache, pins=(), publish=None):
        self.key = key
        self.publish = publish
        eco, name = key
        self.cache = cache
        self.prefix = f'{eco}:osv:{name}:'
        self.rules, self.exact, self.complete = {}, {}, False
        self.ids = set()
        self.tried = set()
        self.seen_tokens = set()
        self.page_ok = True
        self.refreshed = False
        self.previous = _cache_ids(_read(cache, self.prefix + 'known')) or set()
        complete = _cache_ids(_read(cache, self.prefix + 'complete'))
        for aid in self.previous.union(complete or ()):
            rule = _Rule.restore(eco, _read(cache, self.prefix + aid))
            if rule:
                self.rules[aid] = rule
        # Independently observed positives can be newer than an incomplete/evicted snapshot.
        if complete is not None and not self.rules.keys() - complete:
            self.complete = all(i in self.rules and not self.rules[i].incomplete for i in complete)
        for pin in pins:
            if version_key(eco, pin) is not None:
                self.load_pin(pin, complete)

    def load_pin(self, pin, manifest):
        indexed = _cache_ids(_read(self.cache, self.prefix + 'pin@' + pin)) or set()
        candidates = indexed | self.previous | (manifest or set())
        hits = {i for i in candidates if _read(self.cache, self.prefix + i + '@' + pin) is True}
        if hits:
            if manifest is None or hits - manifest:
                # Only a fresh exhaustive scan can resolve disagreeing cached facts.
                self.complete = False
            for aid in hits - self.rules.keys():
                # The independently expiring detail may be evicted before the pin fact.
                self.rules[aid] = _Rule(versions=frozenset([pin]), incomplete=True)
            v = version_key(self.key[0], pin)
            unresolved = any(i not in hits and (r.incomplete or r.needs_probe) and
                             not r.matches(self.key[0], v) for i, r in self.rules.items())
            self.exact[pin] = hits, not unresolved

    def snapshot(self):
        exact = {p: (frozenset(ids), checked) for p, (ids, checked) in self.exact.items()}
        return OSVEvidence(self.key[0], MappingProxyType(self.rules.copy()), self.complete, MappingProxyType(exact))

    def emit(self):
        if self.publish:
            self.publish(self.key, self.snapshot())

    def page(self, data):
        ids, token, ok = _page(data)
        self.ids.update(ids)
        self.page_ok &= ok
        if token in self.seen_tokens:
            self.page_ok = False
            return None
        self.seen_tokens.add(token)
        return token

    def details(self):
        rules = self.rules
        needed = {i for i in self.ids - self.tried if i not in rules or rules[i].incomplete}
        self.tried.update(needed)
        return needed

    def accept(self, aid, detail):
        rule, ok = _detail(self.key, aid, detail)
        if not ok:
            self.page_ok = False
        if rule:
            old = self.rules.get(aid)
            if not ok and old:
                # Admit new positive facts without evicting old ones from the bounded list.
                rule = old.merge(rule)
            self.rules[aid] = rule
            self.emit()
            self.refreshed = True
            # Persist independently complete positive facts, not the unfinished name scan.
            _write(self.cache, self.prefix + aid, rule.serialize(), 7 * _DAY)
            _write(self.cache, self.prefix + 'known', sorted(self.previous.union(self.rules)), 7 * _DAY)
        elif ok:  # an explicit withdrawal
            self.ids.discard(aid)
            self.rules.pop(aid, None)

    def finish(self):
        rules = self.rules
        if self.page_ok and all(i in rules and not rules[i].incomplete for i in self.ids):
            self.complete = True
            self.rules = {i: rules[i] for i in self.ids}
            for pin, (hits, _) in list(self.exact.items()):
                for aid in hits - self.ids:
                    _write(self.cache, self.prefix + aid + '@' + pin, None, 7 * _DAY)
                if hits & self.ids:
                    self.exact[pin] = hits & self.ids, False
                else:
                    del self.exact[pin]
            _write(self.cache, self.prefix + 'complete', sorted(self.ids), _DAY)
            known = self.ids
        else:
            known = self.previous.union(rules)
        if known != self.previous or self.refreshed:
            _write(self.cache, self.prefix + 'known', sorted(known), 7 * _DAY)


def _batch_pages(searches, tokens, batch):
    rows = batch.get('results') if isinstance(batch, dict) and set(batch) == {'results'} else None
    if not isinstance(rows, list) or len(rows) != len(tokens):
        rows = [None] * len(tokens)  # positional association cannot be trusted
    more = {}
    for key, row in zip(tokens, rows):
        token = searches[key].page(row)
        if token:
            more[key] = token
    return more


def _search_names(searches, network):
    tokens = {k: None for k in searches}
    owners = {}
    while tokens or owners:
        jobs = {i: partial(network.get_json, _API + 'vulns/' + i) for i in owners}
        if tokens:
            jobs[None] = partial(_batch, network, tokens.copy())
        more = {}

        def receive(aid, data):
            if aid is None:
                more.update(_batch_pages(searches, tokens, data))
            else:
                for search in owners[aid]:
                    search.accept(aid, data)

        _stream_map(network, jobs, receive)
        tokens, owners = more, {}
        for s in searches.values():
            for aid in s.details():
                owners.setdefault(aid, []).append(s)
    for s in searches.values():
        s.finish()
        s.emit()


def _pin_page(search, pin, body):
    ids, token, ok = _page(body)
    hits, _ = search.exact[pin]
    records = body.get('vulns') if isinstance(body, dict) else None
    for record in records if isinstance(records, list) else ():
        if not isinstance(record, dict) or not _id(record.get('id')) or record['id'] not in ids:
            continue
        aid = record['id']
        version = version_key(search.key[0], pin)
        rule, valid = _detail(search.key, aid, record, probe=version)
        ok &= valid
        if not rule:
            continue
        if not (rule.needs_probe or rule.matches(search.key[0], version)):
            ok = False  # the returned detail contradicts its inclusion in this pin's result
            continue
        hits.add(aid)
        if aid not in search.rules:
            # A name scan missing this advisory cannot certify negative coverage.
            search.complete = search.page_ok = False
            _write(search.cache, search.prefix + 'complete', None, _DAY)
            search.accept(aid, record)
            search.finish()
        search.emit()
        _write(search.cache, search.prefix + aid + '@' + pin, True, 7 * _DAY)
        _write(search.cache, search.prefix + 'pin@' + pin, sorted(hits), 7 * _DAY)
    return token, ok


def _probe_pins(searches, network, pins):
    pending, seen = {}, {}
    for key, versions in pins.items():
        if key not in searches:
            continue
        s = searches[key]
        for pin in versions:
            v = version_key(key[0], pin)
            if v is None:
                continue
            unknown = {i for i, r in s.rules.items() if r.needs_probe and not r.matches(key[0], v)}
            if unknown:
                hits, _ = s.exact.get(pin, (set(), False))
                s.exact[pin] = hits, not unknown - hits
                if unknown - hits:
                    pending[key, pin] = None
                    seen[key, pin] = set()
    while pending:
        jobs = {}
        for (key, pin), token in pending.items():
            query = {'package': {'ecosystem': key[0], 'name': key[1]}, 'version': pin}
            if token:
                query['page_token'] = token
            jobs[key, pin] = partial(network.get_json, _API + 'query', method='POST', body=query)
        more = {}

        def receive(pair, data):
            key, pin = pair
            s = searches[key]
            token, ok = _pin_page(s, pin, data)
            hits, _ = s.exact[pin]
            if not ok:
                seen[pair].add(False)
            if token and token not in seen[pair]:
                seen[pair].add(token)
                more[pair] = token
            s.exact[pin] = hits, ok and not token and False not in seen[pair]
            s.emit()

        _stream_map(network, jobs, receive)
        pending = more


def cached_advisories(keys, cache, pins=None):
    """Read only existing facts for source-eligible names. Never write or make requests."""
    pins = pins or {}
    return {k: _Search(k, cache, pins.get(k, ())).snapshot() for k in keys}


def lookup_advisories(keys, network, cache, pins=None, publish=None):
    """Registry-confirmed names only. publish(key, immutable evidence) runs on response delivery."""
    pins = pins or {}
    searches = {k: _Search(k, cache, pins.get(k, ()), publish) for k in keys}
    _search_names({k: s for k, s in searches.items() if not s.complete}, network)
    _probe_pins(searches, network, pins)
    return {k: s.snapshot() for k, s in searches.items()}
