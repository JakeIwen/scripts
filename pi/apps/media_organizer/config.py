"""Paths and constants for the media organizer (one definition each)."""

from __future__ import annotations

import os
from pathlib import Path

MEDIA_ROOT = Path(os.environ.get("VAN_MEDIA_ROOT", "/mnt/movingparts"))
MIRROR_ROOT = Path(os.environ.get("VAN_MEDIA_MIRROR_ROOT", "/mnt/bigboi/mp_backup"))
MIRROR_MOUNT = Path(os.environ.get("VAN_MEDIA_MIRROR_MOUNT", "/mnt/bigboi"))
TORRENT_DIR = "torrent"
LINKS_DIR = "links"
NEW_DIR = "New"
INCOMPLETE_DIR = "incomplete"

# Categories a finished item can be filed under.  TV and Documentaries hold a
# show folder per series; Movies is flat.
CATEGORY_TV = "TV"
CATEGORY_DOCUMENTARIES = "Documentaries"
CATEGORY_MOVIES = "Movies"
CATEGORIES = (CATEGORY_TV, CATEGORY_DOCUMENTARIES, CATEGORY_MOVIES)
SHOW_FOLDER_CATEGORIES = (CATEGORY_TV, CATEGORY_DOCUMENTARIES)

QBITTORRENT_URL = os.environ.get("VAN_VIDEO_QBITTORRENT_URL", "http://127.0.0.1:8080")
QBITTORRENT_TIMEOUT = float(os.environ.get("VAN_VIDEO_QBITTORRENT_TIMEOUT", "3"))
# qBittorrent moves the payload itself after set-location; same-filesystem
# moves are renames, so a long wait means something is wrong.
MOVE_WAIT_SECONDS = float(os.environ.get("VAN_MEDIA_MOVE_WAIT", "120"))

STATE_DIR = Path(
    os.path.expanduser(
        os.environ.get("VAN_MEDIA_ORGANIZER_STATE", "~/.local/share/media-organizer")
    )
)
PLANS_DIR = STATE_DIR / "plans"
RUNS_DIR = STATE_DIR / "runs"
SETTINGS_PATH = STATE_DIR / "settings.json"
LOG_PATH = Path(os.environ.get("VAN_MEDIA_ORGANIZER_LOG", "/home/pi/log/media_organizer.log"))
# Shared with alias_media.sh: the hook and the organizer never move or link
# at the same time.
LOCK_PATH = Path(os.environ.get("VAN_ALIAS_MEDIA_LOCK", "/run/lock/alias-media.lock"))
# pi_backup.sh holds this exclusively for its whole media rsync (same path is
# spelled there); organizer moves hold it shared, so the two never overlap.
MIRROR_LOCK_PATH = Path(os.environ.get("VAN_MEDIA_MIRROR_LOCK", "/run/lock/media-mirror.lock"))
# The completion hook waits out a running backup sync; interactive applies refuse.
MIRROR_LOCK_WAIT_SECONDS = float(os.environ.get("VAN_MEDIA_MIRROR_LOCK_WAIT", str(4 * 3600)))

ALIAS_MEDIA_SCRIPT = Path(
    os.environ.get("VAN_ALIAS_MEDIA_SCRIPT", "/home/pi/scripts/alias_media.sh")
)
CODEX = os.environ.get("VAN_MEDIA_CODEX", os.path.expanduser("~/.local/bin/codex"))
CODEX_TIMEOUT = float(os.environ.get("VAN_MEDIA_CODEX_TIMEOUT", "240"))

MIN_MEDIA_BYTES = 70_000_000  # alias_media.sh's "real video" floor
VIDEO_EXTENSIONS = (".mkv", ".avi", ".mp4")
ARCHIVE_EXTENSIONS = (".rar",)

# Alias builder compatibility settings; separate from organizer activity logs.
ALIAS_LOG_PATH = Path(os.environ.get("VAN_ALIAS_MEDIA_LOG", "/home/pi/log/alias_media.log"))
ALIAS_RECONCILE_URL = "http://127.0.0.1:8789/api/torrents/reconcile"
ALIAS_NOTIFY_TIMEOUT = 2
ALIAS_EXTRACTED_MIN_BYTES = 300 * 1024 * 1024  # GNU find -size +300M
ALIAS_DIRECTORY_MODE = 0o777
ALIAS_MODE_FULL = "full"
ALIAS_MODE_NEW = "new"
ALIAS_MODES = (ALIAS_MODE_FULL, ALIAS_MODE_NEW)
ALIAS_OUTPUT_ROOT_ENV = "VAN_ALIAS_OUTPUT_ROOT"
