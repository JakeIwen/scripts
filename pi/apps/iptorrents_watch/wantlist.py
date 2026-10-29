"""Library-derived shows merged with durable manual entries and pause choices."""
import time
from dataclasses import replace
from urllib.parse import quote
from . import config, state, transport
from .filters import normalized
from .records import SOURCE_LIBRARY, SOURCE_MANUAL, WantedShow


def store():
    return state.read(config.WATCHLIST_PATH, {'manual': [], 'library': [], 'paused': [], 'removed': []})


def position(show):
    return (show.have_season if show.have_season is not None else -1,
            show.have_episode if show.have_episode is not None else -1)


def merged(data=None):
    data = store() if data is None else data
    result = {normalized(row['name']): WantedShow.from_dict(row) for row in data['library']}
    for row in data['manual']:
        manual = WantedShow.from_dict(row)
        key = normalized(manual.name)
        library = result.get(key)
        if library:
            season, episode = ((manual.have_season, manual.have_episode) if position(manual) > position(library)
                               else (library.have_season, library.have_episode))
            manual = replace(manual, library_show_id=library.library_show_id,
                             last_watched=library.last_watched, have_season=season, have_episode=episode)
        result[key] = manual
    return sorted((replace(row, paused=key in data['paused']) for key, row in result.items()
                   if key not in data['removed']), key=lambda row: row.name.casefold())


def derive(payload, details, timestamp=None):
    timestamp = time.time() if timestamp is None else timestamp
    if not isinstance(payload, dict) or not isinstance(payload.get('shows'), list):
        raise ValueError('video library returned invalid shows')
    up_next = {str(row['id']) for row in payload.get('up_next', [])}
    result = []
    for row in payload['shows']:
        watched = row.get('last_watched') or 0
        if row['kind'] != 'tv' or not (watched >= timestamp - config.RECENTLY_WATCHED_DAYS * 86400
                                     or str(row['id']) in up_next):
            continue
        episodes = details(str(row['id']))['episodes']
        positions = [(ep['season'], ep['episode']) for ep in episodes
                     if type(ep.get('season')) is int and type(ep.get('episode')) is int]
        season, episode = max(positions, default=(None, None))
        result.append(WantedShow(row['name'], SOURCE_LIBRARY, str(row['id']), int(watched) or None,
                                 season, episode, state.now()))
    return result


def refresh(get=None):
    """Caller holds operation lock; failed reads leave the prior want list intact."""
    get = get or transport.get_json
    try:
        payload = get(config.VIDEO_LIBRARY_URL + '/api/library')
        rows = derive(payload, lambda key: get(config.VIDEO_LIBRARY_URL + '/api/shows/' + quote(key, safe='')))
    except (RuntimeError, ValueError, KeyError, TypeError):
        state.metadata(library_available=False)
        raise RuntimeError('video library is unavailable or returned invalid show data') from None
    data = store()
    old = {normalized(row['name']): row for row in data['library']}
    data['library'] = [replace(row, added_at=old.get(normalized(row.name), {}).get('added_at', row.added_at)).to_dict()
                       for row in rows]
    state.write_json(config.WATCHLIST_PATH, data)
    state.metadata(library_available=True)
    return merged(data)


def listing():
    with state.operation():
        return refresh()


def edit(action, name, season=None, episode=None):
    name = name.strip()
    key = normalized(name)
    if not key or len(name) > 200 or any(ord(char) < 32 for char in name):
        raise ValueError('show name must be nonempty text without control characters (at most 200 characters)')
    if any(value is not None and (type(value) is not int or value < 0) for value in (season, episode)):
        raise ValueError('season and episode must be nonnegative integers')
    if episode is not None and season is None:
        raise ValueError('--episode requires --season')
    with state.operation():
        data = store()
        existing = next((row for row in merged(data) if normalized(row.name) == key), None)
        if action != 'add' and existing is None:
            raise ValueError('show is not on the want list')
        if action == 'add':
            manual = WantedShow(name, SOURCE_MANUAL, None, None, season, episode,
                                existing.added_at if existing else state.now())
            data['manual'] = [row for row in data['manual'] if normalized(row['name']) != key] + [manual.to_dict()]
            data['removed'] = [item for item in data['removed'] if item != key]
        elif action == 'remove':
            data['manual'] = [row for row in data['manual'] if normalized(row['name']) != key]
            data['removed'] = sorted(set(data['removed']) | {key})
        elif action in ('pause', 'resume'):
            data['paused'] = sorted((set(data['paused']) | {key}) if action == 'pause'
                                    else (set(data['paused']) - {key}))
        else:
            raise ValueError('unknown wantlist action')
        state.write_json(config.WATCHLIST_PATH, data)
        return merged(data)
