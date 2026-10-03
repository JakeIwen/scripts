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

if __package__:
    from .catalog import (
        CatalogConflict,
        CatalogError,
        MediaAssetCatalog,
        ensure_pre_v2_backup,
    )
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
        _UNSET,
    )
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
    from .service import ProgressStore, VideoService, active_service
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
else:  # Direct execution from the Pi's flat deployment directory.
    from catalog import (  # type: ignore[no-redef]
        CatalogConflict,
        CatalogError,
        MediaAssetCatalog,
        ensure_pre_v2_backup,
    )
    from config import (  # type: ignore[no-redef]
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
        _UNSET,
    )
    from library import MediaLibrary, default_sources  # type: ignore[no-redef]
    from media_models import LibrarySource, MediaItem, Show, public_progress, seconds_text  # type: ignore[no-redef]
    from naming import (  # type: ignore[no-redef]
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
    from sonos_volume import AudioPreparingError, SonosVolumeController  # type: ignore[no-redef]
    from vlc_player import RoomPreparationError, VlcController, native  # type: ignore[no-redef]
    from routes import (  # type: ignore[no-redef]
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
    from service import ProgressStore, VideoService, active_service  # type: ignore[no-redef]
    from video_qbittorrent import (  # type: ignore[no-redef]
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
