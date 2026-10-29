"""Read-only exclusion evidence from qBittorrent and its watch folder."""
from . import config, transport
from .filters import release_name


def names(get=None):
    torrents = (get or transport.get_json)(config.QBITTORRENT_URL + '/api/v2/torrents/info')
    if not isinstance(torrents, list) or any(not isinstance(row, dict) or not isinstance(row.get('name'), str)
                                            for row in torrents):
        raise ValueError('qBittorrent returned invalid torrent data')
    found = {release_name(row['name']) for row in torrents}
    # Missing/inaccessible watch directories are not evidence of an empty queue.
    for path in config.TFILES_DIR.iterdir():
        if path.suffix.lower() == '.torrent':
            found.add(release_name(path.name))
    return found
