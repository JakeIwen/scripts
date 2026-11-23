"""Public registry and download lookups; no telemetry before a positive existence probe."""
from datetime import datetime
from urllib.parse import quote

from dep_guard_specs import NPM
from dep_guard_verdict import Downloads, Existence, Metadata, Stat
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


def statistics_groups(keys):
    unscoped = []
    for key in keys:
        if key[0] == NPM and not key[1].startswith('@'):
            unscoped.append(key)
        else:
            yield [key]
    if unscoped:
        yield unscoped


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


def cached_metadata(cache, key):
    record = cache.get(fact_key(key, 'metadata'))
    if (isinstance(record, dict) and type(record.get('first')) in (int, float)
            and all(record.get(k) is None or count(record[k]) for k in ('versions', 'maintainers'))):
        return Metadata(record['first'], record.get('versions'), record.get('maintainers'))
    return None
