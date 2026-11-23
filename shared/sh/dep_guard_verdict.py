"""Package reputation policy. Only registry existence and measured signals can deny."""
from dataclasses import dataclass
from enum import Enum
from typing import Optional


DAY = 86400
POPULAR = 50_000
LOW_USE = 1_000
YOUNG_DAYS = 90
INITIAL_DAYS = 14


class Existence(str, Enum):
    FOUND = 'found'
    MISSING = 'not-found'
    UNKNOWN = 'unavailable'


class Stat(str, Enum):
    MEASURED = 'measured'
    NO_ROW = 'no-statistics'
    UNKNOWN = 'unavailable'


@dataclass(frozen=True)
class Downloads:
    state: Stat = Stat.UNKNOWN
    count: Optional[int] = None


@dataclass(frozen=True)
class Metadata:
    first: Optional[float] = None
    versions: Optional[int] = None
    maintainers: Optional[int] = None


def overrideable(reason, instruction, override):
    if override:
        return [], [reason + ' (DEP_GUARD_ALLOW override)']
    return [reason + '; DEP_GUARD_ALLOW=<name> ' + instruction], []


def verdict(inv, existence, stats, meta, advisory, now):
    """Return (blocks, notes) for one actual operand, not for its alias."""
    if existence is Existence.MISSING:
        return overrideable('not found on the public registry',
                            'only for a private package you know exists on this registry', inv.override)
    if existence is not Existence.FOUND:
        return [], ['public registry existence check unavailable (offline or unfinished); not checked']
    blocks, notes = advisory.decision(inv.pin) if advisory is not None else ([], ['OSV malware check unavailable'])
    if stats.state is Stat.MEASURED and stats.count >= POPULAR:
        return blocks, notes
    if stats.state is Stat.UNKNOWN:
        notes.append('weekly downloads unavailable')
    if meta.first is None:
        notes.append('age not checked (metadata unavailable)')
    else:
        days = (now - meta.first) / DAY
        if days < YOUNG_DAYS:
            why = None
            if stats.state is Stat.MEASURED and stats.count < LOW_USE:
                why = 'new package (under 90 days), only %d weekly downloads' % stats.count
            elif stats.state is Stat.NO_ROW and days < INITIAL_DAYS:
                why = 'first published under 14 days ago, no weekly download statistics yet'
            if why:
                deny, warn = overrideable(why, 'only with the operator’s confirmation after disclosing its age and low use', inv.override)
                blocks.extend(deny)
                notes.extend(warn)
            else:
                notes.append('new package (under 90 days); ' + (
                    'use unconfirmed' if stats.count is None else '%d weekly downloads' % stats.count))
    if meta.versions is not None and meta.versions <= 2 and meta.maintainers == 1:
        notes.append('few versions (<= 2) and one maintainer')
    return blocks, notes
