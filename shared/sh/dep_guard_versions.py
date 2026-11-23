"""Comparable OSV release keys, or None when the ecosystem's ordering is unknown."""
import re
from functools import lru_cache

from dep_guard_specs import NPM, PYPI

_NUM = r'(0|[1-9][0-9]*)'
_SEMVER = re.compile(r'v?' + r'\.'.join([_NUM] * 3) +
                     r'(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?'
                     r'(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?')
_PEP440 = re.compile(r'v?(?:(\d+)!)?(\d+(?:\.\d+)*)(?:[._-]?(a|alpha|b|beta|rc|c|pre|preview)[._-]?(\d*))?'
                     r'(?:-(\d+)|[._-]?(post|rev|r)[._-]?(\d*))?(?:[._-]?(dev)[._-]?(\d*))?'
                     r'(?:\+([a-z0-9]+(?:[._-][a-z0-9]+)*))?', re.I | re.ASCII)
_PRE = {'alpha': 0, 'a': 0, 'beta': 1, 'b': 1, 'c': 2, 'rc': 2, 'pre': 2, 'preview': 2}


@lru_cache(maxsize=512)
def version_key(eco, text):
    if not isinstance(text, str) or not 0 < len(text) <= 256:
        return None
    if eco == NPM:
        match = _SEMVER.fullmatch(text)
        if not match:
            return None
        pre = match[4].split('.') if match[4] else []
        if any(p.isdigit() and p.startswith('0') and len(p) > 1 for p in pre):
            return None
        return (tuple(map(int, match.groups()[:3])), not pre,
                tuple((0, int(p)) if p.isdigit() else (1, p) for p in pre))
    match = _PEP440.fullmatch(text.lower()) if eco == PYPI else None
    if not match:
        return None
    epoch, rel, pre, pn, post_n, post, post_n2, dev, dn, local = match.groups()
    release = list(map(int, rel.split('.')))
    while release and release[-1] == 0:
        release.pop()
    # A standalone dev release precedes even the first alpha, unlike a final release.
    phase = _PRE[pre] if pre else (-1 if dev and not (post or post_n) else 3)
    return (int(epoch or 0), tuple(release), phase, int(pn or 0),
            bool(post or post_n), int(post_n or post_n2 or 0), not dev, int(dn or 0),
            tuple((1, int(p)) if p.isdigit() else (0, p) for p in re.split(r'[._-]', local or '') if p))


def intervals(eco, record):
    """Validate an entire ordered OSV event stream before deriving any intervals."""
    if not isinstance(record, dict) or record.get('type') not in (
            ('SEMVER', 'ECOSYSTEM') if eco == NPM else ('ECOSYSTEM',)):
        return None
    events = record.get('events')
    if not isinstance(events, list) or not events:
        return None
    result, opened, lower, previous = [], False, None, None
    for event in events:
        if not isinstance(event, dict) or len(event) != 1:
            return None
        kind, val = next(iter(event.items()))
        if not isinstance(val, str):
            return None
        start = kind == 'introduced'
        zero = start and val == '0'
        value = None if zero else version_key(eco, val)
        if ((not zero and value is None) or (previous is not None and (zero or value < previous))
                or start == opened or (not start and kind not in ('last_affected', 'fixed', 'limit'))):
            return None
        if start:
            lower = None if zero else val
        else:
            result.append((lower, val, kind == 'last_affected'))
        opened, previous = start, value
    if opened:
        result.append((lower, None, False))
    return result


def git_range(record):
    """A commit range establishes version-specific coverage, not registry ordering."""
    if (not isinstance(record, dict) or record.get('type') != 'GIT'
            or not isinstance(record.get('repo'), str) or not record['repo'].strip()):
        return False
    events = record.get('events')
    if not isinstance(events, list) or not events:
        return False
    opened = False
    for event in events:
        if not isinstance(event, dict) or len(event) != 1:
            return False
        kind, value = next(iter(event.items()))
        if not isinstance(value, str) or (not (kind == 'introduced' and value == '0') and
                not re.fullmatch('[0-9a-fA-F]{40}(?:[0-9a-fA-F]{24})?', value)):
            return False
        start = kind == 'introduced'
        if opened == start or not (start or kind in ('fixed', 'last_affected', 'limit')):
            return False
        opened = start
    return True


def contains(eco, interval, version):
    lower, upper, inclusive = interval
    return ((lower is None or version_key(eco, lower) <= version) and
            (upper is None or version < version_key(eco, upper) or inclusive and version == version_key(eco, upper)))
