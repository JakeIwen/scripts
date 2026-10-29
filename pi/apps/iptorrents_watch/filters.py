"""Pure show-name, release-quality and newer-than decisions."""
import re
import unicodedata
from dataclasses import replace

SEASON = re.compile(r'(?<![a-z0-9])s(\d{1,3})(?:[ ._-]*e(\d{1,3}))?(?!\d)', re.I)
ALLOWED_CODEC = re.compile(r'(?<![a-z0-9])(?:x264|h[. ]?264|avc)(?![a-z0-9])', re.I)
DISALLOWED = re.compile(r'(?<![a-z0-9])(?:x265|h[. ]?265|hevc|av1|2160p|720p|480p|remux|3d)(?![a-z0-9])', re.I)
RESOLUTION = re.compile(r'(?<![a-z0-9])1080p(?![a-z0-9])', re.I)


def normalized(name):
    text = unicodedata.normalize('NFKD', name).casefold().replace('&', ' and ')
    text = re.sub(r"['’]", '', text)
    return ' '.join(re.findall(r'[^\W_]+', text))


def same_show(left, right):
    left, right = normalized(left), normalized(right)
    if left == right:
        return True
    # A release may omit the show's year, but two explicit remake years differ.
    pattern = r'^(.*?) ((?:19|20)\d{2})$'
    a, b = re.match(pattern, left), re.match(pattern, right)
    if a and b:
        return a.groups() == b.groups()
    return (a[1] if a else left) == (b[1] if b else right)


def release_name(name):
    name = re.sub(r'\.torrent$', '', name, flags=re.I)
    name = re.sub(r'\s*\[IPT\]$', '', name, flags=re.I)
    name = re.sub(r'\.(?:mkv|mp4|avi|m4v|ts)$', '', name, flags=re.I)
    return normalized(name)


def newer(season, episode, show):
    have_season = show.have_season if show.have_season is not None else -1
    have_episode = show.have_episode if show.have_episode is not None else -1
    return season > have_season or (episode is not None and season == have_season and episode > have_episode)


def select(candidate, show, existing=()):
    name = candidate.name
    if (not candidate.category.casefold().startswith('tv/') or not RESOLUTION.search(name)
            or not ALLOWED_CODEC.search(name) or DISALLOWED.search(name)):
        return None
    match = SEASON.search(name)
    if not match or not same_show(name[:match.start()], show.name):
        return None
    season = int(match[1])
    episode = int(match[2]) if match[2] is not None else None
    if not newer(season, episode, show) or release_name(name) in existing:
        return None
    return replace(candidate, show=show.name, season=season, episode=episode)
