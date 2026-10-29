"""Serialized library refresh, cached site searches and candidate reconciliation."""
import time
from dataclasses import replace
from . import config, existing, filters, state, wantlist
from .records import Candidate, CANDIDATE_NEW, LOGIN_NEEDED, LOGIN_OK, LOGIN_UNKNOWN
from .site import Site
from .status import status
from .transport import LoginNeeded, SiteUnavailable


def search(show, site, cache, force):
    key = filters.normalized(show.name)
    saved = cache.get(key)
    stamp = time.time()
    if not force and saved and 0 <= stamp - saved['checked_at'] < config.SEARCH_CACHE_SECONDS:
        state.log(f'Cached search: {show.name}')
        return [Candidate.from_dict(row) for row in saved['rows']]
    last_request = state.metadata().get('last_site_request', 0)
    delay = min(config.REQUEST_SPACING_SECONDS, config.REQUEST_SPACING_SECONDS - (stamp - last_request))
    if delay > 0:
        time.sleep(delay)
    state.log(f'Search: {show.name}')
    state.metadata(last_site_request=time.time())
    rows = site.search(show.name)
    state.metadata(login=LOGIN_OK)
    cache[key] = {'checked_at': time.time(), 'rows': [row.to_dict() for row in rows]}
    state.write_json(config.STATE_DIR / 'search-cache.json', cache)
    return rows


def merge(previous, found, wanted, checked, owned):
    shows = {filters.normalized(show.name): show for show in wanted if not show.paused}
    kept = {}
    for row in previous:
        key = filters.normalized(row.show)
        show = shows.get(key)
        if row.status != CANDIDATE_NEW:
            kept[row.torrent_id] = row
        elif show and key not in checked and filters.select(row, show, owned):
            kept[row.torrent_id] = row
    old = {row.torrent_id: row for row in previous}
    for row in found:
        known = old.get(row.torrent_id)
        if known:
            row = replace(row, first_seen=known.first_seen, status=known.status,
                          downloaded_at=known.downloaded_at, file_path=known.file_path, error=known.error)
        kept[row.torrent_id] = row
    return list(kept.values())


def run(show=None, site=None, get=None):
    with state.operation(checking=True):
        try:
            state.log('Check started')
            wanted = wantlist.refresh(get)
            selected = [row for row in wanted if not row.paused
                        and (show is None or filters.normalized(row.name) == filters.normalized(show))]
            if show is not None and not selected:
                raise ValueError('show is not wanted or is paused')
            owned = existing.names(get)
            cache = state.read(config.STATE_DIR / 'search-cache.json', {})
            found = []
            site = site or Site()
            for row in selected:
                results = search(row, site, cache, force=show is not None)
                found.extend(candidate for item in results if (candidate := filters.select(item, row, owned)))
            checked = {filters.normalized(row.name) for row in selected}
            state.save_candidates(merge(state.candidates(), found, wanted, checked, owned))
            state.metadata(last_check=state.now(), last_error=None)
            state.log(f'Check complete: {len(selected)} shows, {len(found)} matching releases')
        except Exception as error:
            changes = {'last_error': str(error)}
            if isinstance(error, LoginNeeded):
                changes['login'] = LOGIN_NEEDED
            elif isinstance(error, SiteUnavailable):
                changes['login'] = LOGIN_UNKNOWN
            state.metadata(**changes)
            state.log('Check failed: ' + str(error))
            raise
    return status()
