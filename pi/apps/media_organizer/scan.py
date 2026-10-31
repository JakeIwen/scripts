"""Read New and join payload roots to current torrent ownership."""
from pathlib import Path
from . import config, records
from .paths import confined, new_directory, require_media
from .qbittorrent_client import Client


def torrent_paths(torrent):
    return {Path(torrent['content_path']), Path(torrent['save_path']) / torrent['name']}


def owns_path(torrent, path):
    # A mis-saved torrent can live *inside* a New entry. Never label its
    # enclosing directory untracked and bypass qBittorrent with os.rename.
    return any(candidate == path or path in candidate.parents
               for candidate in torrent_paths(torrent))


def complete(torrent):
    state = torrent['state'].lower()
    return (torrent['progress'] >= 1 and torrent['amount_left'] == 0
            and not any(word in state for word in ('down', 'check', 'meta', 'alloc'))
            and state not in ('error', 'missingfiles', 'unknown'))


def existing_shows():
    result = {}
    for category in config.SHOW_FOLDER_CATEGORIES:
        folder = confined(config.MEDIA_ROOT / config.TORRENT_DIR / category, config.MEDIA_ROOT)
        result[category] = sorted(
            (child.name for child in folder.iterdir()
             if child.is_dir() and not child.is_symlink() and not child.name.startswith('.')),
            key=str.casefold,
        ) if folder.exists() else []
    return result


def scan(client=None):
    require_media()
    # An API failure must never turn tracked payloads into renameable untracked files.
    torrents = (client or Client()).list_torrents()
    entries = []
    for child in sorted(new_directory().iterdir(), key=lambda p: p.name.casefold()):
        matches = [torrent for torrent in torrents if owns_path(torrent, child)]
        torrent = matches[0] if len(matches) == 1 else None
        skip = None
        size, kind = 0, 'file'
        try:
            confined(child, new_directory())
            stat = child.stat()
            size, kind = stat.st_size, 'dir' if child.is_dir() else 'file'
            if not (child.is_dir() or child.is_file()) or child.name.startswith('.'):
                skip = records.SKIP_UNREADABLE
            elif kind == 'file' and size <= config.MIN_MEDIA_BYTES:
                skip = records.SKIP_NOT_MEDIA  # nohup.out and stray scripts are not for Movies
        except (OSError, ValueError):
            skip = records.SKIP_UNREADABLE
        if len(matches) > 1:
            skip = records.SKIP_UNREADABLE
        done = complete(torrent) if torrent else not matches
        if torrent:
            if torrent['state'] == 'moving':
                skip = records.SKIP_MOVING
            elif not done:
                skip = records.SKIP_INCOMPLETE
            elif Path(torrent['save_path']) != new_directory():
                skip = records.SKIP_UNREADABLE
        entries.append(records.ScanEntry(
            child.name, str(child), kind, size,
            torrent['hash'] if torrent else None, torrent['name'] if torrent else None,
            torrent['state'] if torrent else None, bool(matches), done, skip,
        ))
    return entries, existing_shows()
