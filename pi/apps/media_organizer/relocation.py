"""Move validation, crash intent, and mirror renames shared with rollback."""
import os
from pathlib import Path
from . import config, records, runs
from .paths import component, confined, destination, new_directory, require_media
from .scan import scan, owns_path, complete

PENDING = 'Relocation pending verification'
UNDO_PENDING = 'Rollback pending verification'


def verify_source(row, client):
    require_media()
    entries, _ = scan(client)
    fresh = next((entry for entry in entries if entry.name == row.entry.name), None)
    if (fresh is None or fresh.skip_reason or fresh.torrent_hash != row.entry.torrent_hash
            or fresh.tracked != row.entry.tracked):
        raise ValueError('source or torrent ownership changed since proposal')
    if fresh.tracked:
        torrent = next(t for t in client.list_torrents() if t['hash'] == fresh.torrent_hash)
        if not payload_root_is(torrent, Path(fresh.path)):
            raise ValueError('torrent payload root differs from the scanned entry')


def payload_root_is(torrent, path):
    """True when set-location moves exactly ``path``: the content root, or a
    single-file torrent whose file sits inside that root folder."""
    content, save_path = Path(torrent['content_path']), Path(torrent['save_path'])
    if content == path:
        return True
    try:
        return path.parent == save_path and content.relative_to(save_path).parts[0] == path.name
    except ValueError:
        return False


def ensure_absent(path):
    confined(path, config.MEDIA_ROOT)
    if os.path.lexists(path):
        raise FileExistsError('destination already exists; refusing to merge or overwrite')


def make_item(row, target):
    old, new = Path(row.entry.path), Path(target)
    if row.entry.tracked:
        old, new = old.parent, new.parent
    return records.RunItem(row.entry.name, row.entry.torrent_hash, str(old), str(new),
                           None, None, records.ITEM_FAILED, PENDING)


def move_payload(item, client):
    if item.torrent_hash:
        client.set_location(item.torrent_hash, item.new_path)
        client.wait_for_location(item.torrent_hash, item.new_path, config.MOVE_WAIT_SECONDS)
    else:
        os.rename(item.old_path, item.new_path)


def mirror_paths(old_payload, new_payload):
    old = config.MIRROR_ROOT / old_payload.relative_to(config.MEDIA_ROOT)
    new = config.MIRROR_ROOT / new_payload.relative_to(config.MEDIA_ROOT)
    confined(config.MIRROR_ROOT, config.MIRROR_MOUNT)
    confined(old, config.MIRROR_ROOT)
    confined(new, config.MIRROR_ROOT)
    return old, new


def mirror_move(item, run, old_payload, new_payload):
    if not config.MIRROR_MOUNT.is_mount():
        return
    old, new = mirror_paths(old_payload, new_payload)
    if not old.exists():
        return
    if os.path.lexists(new):
        raise FileExistsError('mirror destination exists; mirror left unchanged')
    new.parent.mkdir(parents=True, exist_ok=True)
    # Record intent before rename: undo can distinguish both sides after a crash.
    item.mirror_old, item.mirror_new = str(old), str(new)
    runs.save(run)
    os.rename(old, new)


def rollback_paths(item):
    component(item.name)
    if item.torrent_hash:
        if Path(item.old_path) != new_directory():
            raise ValueError('journal original location is not New')
        destination(Path(item.new_path))
        old, new = Path(item.old_path) / item.name, Path(item.new_path) / item.name
    else:
        old, new = Path(item.old_path), Path(item.new_path)
        if old != new_directory() / item.name or new.name != item.name:
            raise ValueError('invalid rollback payload paths')
        destination(new.parent)
    confined(old, config.MEDIA_ROOT)
    confined(new, config.MEDIA_ROOT)
    return old, new


def verify_undo(item, client, recovering=False):
    require_media()
    old, new = rollback_paths(item)
    torrents = client.list_torrents()
    already = recovering and old.exists() and not os.path.lexists(new)
    if item.torrent_hash:
        torrent = next((t for t in torrents if t['hash'] == item.torrent_hash), None)
        expected = old if already else new
        if (not torrent or Path(torrent['save_path']) != expected.parent
                or not payload_root_is(torrent, expected)
                or torrent['state'] == 'moving' or not complete(torrent)):
            raise ValueError('torrent location or state changed; refusing rollback')
    elif any(owns_path(t, old if already else new) for t in torrents):
        raise ValueError('untracked payload is now owned by a torrent')
    if not already:
        ensure_absent(old)
        if not new.exists():
            raise ValueError('payload no longer at journal destination')
    return old, new, already


def undo_payload(item, client, recovering=False):
    old, new, already = verify_undo(item, client, recovering)
    if not already:
        if item.torrent_hash:
            client.restore_location(item.torrent_hash, item.old_path)
            client.wait_for_location(item.torrent_hash, item.old_path, config.MOVE_WAIT_SECONDS)
        else:
            os.rename(new, old)
    return old, new


def undo_mirror(item, old_payload, new_payload):
    if item.mirror_old is None and item.mirror_new is None:
        return
    if not config.MIRROR_MOUNT.is_mount():
        raise ValueError('mirror is unavailable; mirror rollback not performed')
    old, new = mirror_paths(old_payload, new_payload)
    if (item.mirror_old, item.mirror_new) != (str(old), str(new)):
        raise ValueError('invalid mirror journal paths')
    if old.exists() and not os.path.lexists(new):
        return  # Intent was saved, but the mirror rename never happened.
    if os.path.lexists(old) or not new.exists():
        raise ValueError('mirror location changed; refusing rollback')
    old.parent.mkdir(parents=True, exist_ok=True)
    os.rename(new, old)
