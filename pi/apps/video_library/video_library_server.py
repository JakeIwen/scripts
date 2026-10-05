#!/usr/bin/env python3
"""Compatibility entry point for the video library service."""

from __future__ import annotations

import hashlib
import importlib
import ipaddress
import json
import math
import os
import random
import re
import signal
import sqlite3
import subprocess
import threading
import time
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import unquote, urlsplit

from flask import Flask, jsonify, render_template, request

from .catalog import MediaAssetCatalog, ensure_pre_v2_backup
from .catalog_values import CatalogConflict, CatalogError
from .config import (
    CATEGORY_PRIORITY,
    DBUS_PROPERTIES,
    DEFAULT_FAVORITES,
    DISPLAY,
    LEGACY_LINE_RE,
    LEGACY_POSITIONS_PATH,
    MIN_CONTINUE_POSITION,
    MKVMERGE,
    MPRIS_NAME,
    MPRIS_PATH,
    MPRIS_PLAYER,
    MPRIS_ROOT,
    PKILL,
    PLAYER_UNIT,
    POLL_INTERVAL,
    PORT,
    QBITTORRENT_CLIENT_ID,
    QBITTORRENT_FINAL_ROOTS,
    QBITTORRENT_TEMP_ROOTS,
    QBITTORRENT_TIMEOUT,
    QBITTORRENT_URL,
    REAR_SONOS_UIDS,
    RESUME_REWIND,
    ROOM_PREPARE_TIMEOUT,
    RUNTIME_DIR,
    SCAN_INTERVAL,
    SESSION_BUS,
    SNS,
    SONOS_DISCOVERY_TTL,
    STATE_PATH,
    SYSTEMCTL,
    SYSTEMD_RUN,
    VIDEO_EXTENSIONS,
    VLC,
    VLC_FIXED_VOLUME,
    WATCHED_FRACTION,
    XSET,
)
from .legacy_progress import ProgressStore, _UNSET
from .library import MediaLibrary, default_sources
from .media_models import LibrarySource, MediaItem, Show, public_progress, seconds_text
from .naming import (
    BARE_EPISODE_RE,
    E_ONLY_EPISODE_RE,
    EPISODE_RE,
    FALLBACK_EPISODE_RE,
    FEATURE_YEAR_RE,
    PART_EPISODE_RE,
    SEASON_PATH_RE,
    X_EPISODE_RE,
    canonical_series,
    clean_name,
    natural_key,
    normalized,
    parse_candidate,
    stable_id,
)
from .players.sonos_volume import AudioPreparingError, SonosVolumeController
from .players.vlc_player import RoomPreparationError, VlcController, native
from .routes import (
    APP_ICON,
    api_control,
    api_error,
    api_fullscreen,
    api_library,
    api_play,
    api_play_local,
    api_position,
    api_progress,
    api_rate,
    api_rescan,
    api_search,
    api_seek,
    api_show,
    api_sleep,
    api_status,
    api_surprise,
    api_torrent_reconcile,
    api_volume,
    app,
    app_icon,
    disable_api_cache,
    form_boolean,
    index,
    manifest,
    reject_cross_origin_mutations,
    require_loopback_peer,
)
from .service import VideoService, active_service
from .video_qbittorrent import (
    QbittorrentAuthenticationError,
    QbittorrentClient,
    QbittorrentConfigurationError,
    QbittorrentError,
    QbittorrentProtocolError,
    QbittorrentUnavailable,
    ResolvedTorrentFile,
    TorrentFileIdentity,
)

if __name__ == "__main__":
    os.environ.setdefault("DISPLAY", DISPLAY)
    os.environ.setdefault("XDG_RUNTIME_DIR", RUNTIME_DIR)
    os.environ.setdefault("DBUS_SESSION_BUS_ADDRESS", SESSION_BUS)
    service = active_service()
    service.start()
    app.run(host="0.0.0.0", port=PORT, threaded=True)
