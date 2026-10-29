"""Paths and constants for the IPTorrents watcher (one definition each)."""

from __future__ import annotations

import os
from pathlib import Path

SITE_URL = os.environ.get("VAN_IPT_SITE_URL", "https://iptorrents.com")
COOKIE_PATH = Path(os.path.expanduser(os.environ.get("VAN_IPT_COOKIES", "~/.config/jacobr/iptorrents.json")))
STATE_DIR = Path(os.path.expanduser(os.environ.get("VAN_IPT_STATE", "~/.local/share/iptorrents-watch")))
WATCHLIST_PATH = STATE_DIR / "watchlist.json"
CANDIDATES_PATH = STATE_DIR / "candidates.json"
STATUS_PATH = STATE_DIR / "status.json"
LOG_PATH = Path(os.environ.get("VAN_IPT_LOG", "/home/pi/log/iptorrents_watch.log"))
# qBittorrent's watch folder (scan_dirs); the Mac drops .torrent files here too.
TFILES_DIR = Path(os.environ.get("VAN_IPT_TFILES", "/home/pi/tfiles"))
VIDEO_LIBRARY_URL = os.environ.get("VAN_VIDEO_LIBRARY_URL", "http://127.0.0.1:8789")
QBITTORRENT_URL = os.environ.get("VAN_VIDEO_QBITTORRENT_URL", "http://127.0.0.1:8080")

USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Gecko/20100101 Firefox/128.0"
REQUEST_TIMEOUT = float(os.environ.get("VAN_IPT_TIMEOUT", "30"))
REQUEST_SPACING_SECONDS = float(os.environ.get("VAN_IPT_SPACING", "2.5"))  # one person browsing, not a scraper
SEARCH_CACHE_SECONDS = float(os.environ.get("VAN_IPT_SEARCH_CACHE", str(6 * 3600)))
RECENTLY_WATCHED_DAYS = int(os.environ.get("VAN_IPT_RECENT_DAYS", "90"))
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
MAX_TORRENT_BYTES = 2 * 1024 * 1024
