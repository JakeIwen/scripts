"""Public registry and download lookups; no telemetry before a positive existence probe."""
from dataclasses import asdict
from datetime import datetime
from urllib.parse import quote

from dep_guard_specs import NPM
from dep_guard_verdict import DAY, LOW_USE, POPULAR, Downloads, Existence, Metadata, Stat
NPM_REGISTRY = 'https://registry.npmjs.org/'
PYPI_BASE = 'https://pypi.org/pypi/'
NPM_STATS = 'https://api.npmjs.org/downloads/point/last-week/'
PYPI_STATS = 'https://pypistats.org/api/packages/'
META_LIMIT = 2 * 1024 * 1024


def fact_key(key, source):
    ecosystem, name = key
    return '%s:%s:%s' % (ecosystem, source, name)


def presence(key, net):
    ecosystem, name = key
    escaped = quote(name, safe='@')
    if ecosystem == NPM:
        status, tags = net.get_json(NPM_REGISTRY + '-/package/' + escaped + '/dist-tags')
        valid = isinstance(tags, dict) and all(isinstance(v, str) for v in tags.values())
    else:
        status, _ = net.get_json(PYPI_BASE + escaped + '/json', method='HEAD')
        valid = True
    if status == 404:
        return Existence.MISSING
    return Existence.FOUND if status == 200 and valid else Existence.UNKNOWN


def existence_facts(keys, overrides, net, cache):
    facts, tasks = {}, {}
    for key in keys:
        cached = cache.get(fact_key(key, 'exists'))
        if cached == Existence.FOUND or (cached == Existence.MISSING and key not in overrides):
            facts[key] = Existence(cached)
        else:
            tasks[key] = lambda k=key: presence(k, net)
    for key, answer in net.map(tasks).items():
        facts[key] = answer
        if answer is not Existence.UNKNOWN:
            cache.put(fact_key(key, 'exists'), answer.value, 7 * DAY if answer is Existence.FOUND else 3600)
    return facts


def count(value):
    return type(value) is int and value >= 0


def downloads(row, status, ecosystem):
    if status == 404:
        return Downloads(Stat.NO_ROW)
    if status != 200 or not isinstance(row, dict):
        return Downloads()
    if ecosystem == NPM:
        if isinstance(row.get('error'), str) and 'not found' in row['error'].lower():
            return Downloads(Stat.NO_ROW)
        value = row.get('downloads')
    else:
        value = row.get('data', {})
        value = value.get('last_week') if isinstance(value, dict) else None
    return Downloads(Stat.MEASURED, value) if count(value) else Downloads()


def stats_batch(keys, net):
    ecosystem = keys[0][0]
    if ecosystem == NPM:
        url = NPM_STATS + ','.join(quote(name, safe='@') for _, name in keys)
    else:
        url = PYPI_STATS + quote(keys[0][1], safe='') + '/recent'
    status, data = net.get_json(url)
    if len(keys) == 1:
        return {keys[0]: downloads(data, status, ecosystem)}
    # The bulk API represents an individual package with no statistics as null.
    if status != 200 or not isinstance(data, dict):
        return {}
    return {k: (Downloads(Stat.NO_ROW) if data[k[1]] is None else downloads(data[k[1]], status, ecosystem))
            for k in keys if k[1] in data}


def download_facts(keys, overrides, net, cache):
    facts, groups, unscoped = {}, [], []
    for key in keys:
        value = cache.get(fact_key(key, 'downloads'))
        if count(value) and not (key in overrides and value < LOW_USE):
            facts[key] = Downloads(Stat.MEASURED, value)
        elif key[0] == NPM and not key[1].startswith('@'):
            unscoped.append(key)
        else:
            groups.append([key])
    if unscoped:
        groups.append(unscoped)
    fetched = net.map({i: lambda g=g: stats_batch(g, net) for i, g in enumerate(groups)})
    for batch in fetched.values():
        for key, fact in batch.items():
            facts[key] = fact
            if fact.state is Stat.MEASURED:
                cache.put(fact_key(key, 'downloads'), fact.count, 7 * DAY)
    return facts


def upload_time(s):
    if not isinstance(s, str):
        return None
    try:
        parsed = datetime.fromisoformat(s.replace('Z', '+00:00'))
        return parsed.timestamp() if parsed.tzinfo else None
    except (OSError, ValueError, OverflowError):
        return None


def metadata(key, net):
    ecosystem, name = key
    escaped = quote(name, safe='@')
    url = NPM_REGISTRY + escaped if ecosystem == NPM else PYPI_BASE + escaped + '/json'
    status, data = net.get_json(url, limit=META_LIMIT)
    if status != 200 or not isinstance(data, dict):
        return Metadata()
    if ecosystem == NPM:
        times, versions, maintainers = (data.get(k) for k in ('time', 'versions', 'maintainers'))
        return Metadata(upload_time(times.get('created')) if isinstance(times, dict) else None,
                        len(versions) if isinstance(versions, dict) else None,
                        len(maintainers) if isinstance(maintainers, list) else None)
    releases = data.get('releases')
    if not isinstance(releases, dict):
        return Metadata()
    dates = (upload_time(row.get('upload_time_iso_8601')) for rows in releases.values()
             if isinstance(rows, list) for row in rows if isinstance(row, dict))
    return Metadata(min((d for d in dates if d is not None), default=None))


def reputation_facts(keys, overrides, net, cache):
    stats = download_facts(keys, overrides, net, cache)
    facts, tasks = {}, {}
    for key in keys:
        if (stats.get(key, Downloads()).count or 0) >= POPULAR:
            continue
        record = cache.get(fact_key(key, 'metadata'))
        if (isinstance(record, dict) and type(record.get('first')) in (int, float)
                and all(record.get(k) is None or count(record[k]) for k in ('versions', 'maintainers'))):
            facts[key] = Metadata(record['first'], record.get('versions'), record.get('maintainers'))
        else:
            tasks[key] = lambda k=key: metadata(k, net)
    for key, fact in net.map(tasks).items():
        facts[key] = fact
        if fact.first is not None:
            cache.put(fact_key(key, 'metadata'), asdict(fact), 30 * DAY)
    return stats, facts
