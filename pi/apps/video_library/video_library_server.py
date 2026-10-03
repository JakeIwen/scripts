#!/usr/bin/env python3
"""Movies and television library, resume state, and VLC remote control.

The service indexes the cleaned symlinks produced by ``alias_media.sh`` and
controls the same desktop VLC session used by the shell helpers in ``.bashrc``.
It never mounts storage and never accepts a filesystem path from a client.
"""

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
    from . import identity, library, media_models, naming, playback, routes, service
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
    from .media_models import MediaItem, public_progress, seconds_text
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
    from .players import sonos_volume, vlc_player
    from .players.sonos_volume import AudioPreparingError
    from .players.vlc_player import RoomPreparationError
    from .catalog import (
        CatalogConflict,
        CatalogError,
        MediaAssetCatalog,
        ensure_pre_v2_backup,
    )
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
    from media_models import MediaItem, public_progress, seconds_text  # type: ignore[no-redef]
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
    import identity  # type: ignore[no-redef]
    import library  # type: ignore[no-redef]
    import media_models  # type: ignore[no-redef]
    import naming  # type: ignore[no-redef]
    import playback  # type: ignore[no-redef]
    import routes  # type: ignore[no-redef]
    import service  # type: ignore[no-redef]
    import sonos_volume  # type: ignore[no-redef]
    import vlc_player  # type: ignore[no-redef]
    from sonos_volume import AudioPreparingError  # type: ignore[no-redef]
    from vlc_player import RoomPreparationError  # type: ignore[no-redef]
    from catalog import (  # type: ignore[no-redef]
        CatalogConflict,
        CatalogError,
        MediaAssetCatalog,
        ensure_pre_v2_backup,
    )
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


app = Flask(__name__)


def native(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): native(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [native(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return float(value)
        except (TypeError, ValueError):
            return str(value)


@dataclass(frozen=True)
class LibrarySource:
    name: str
    mount_path: str
    index_path: str


@dataclass
class Show:
    key: str
    id: str
    name: str
    kind: str
    episodes: list[MediaItem]

    @property
    def new(self) -> bool:
        return any(item.new for item in self.episodes)


class MediaLibrary:
    def __init__(
        self,
        sources: Iterable[LibrarySource],
        *,
        require_mount: bool = True,
        mount_check: Callable[[str], bool] = os.path.ismount,
    ):
        self.sources = tuple(sources)
        self.require_mount = require_mount
        self.mount_check = mount_check
        self.lock = threading.RLock()
        self.items: dict[str, MediaItem] = {}
        self.items_by_key: dict[str, MediaItem] = {}
        self.items_by_path: dict[str, MediaItem] = {}
        self.items_by_rel: dict[str, MediaItem] = {}
        self.shows: dict[str, Show] = {}
        self.available = False
        self.source: LibrarySource | None = None
        self.error: str | None = "library has not been scanned"
        self.last_scan = 0.0

    def _available_source(self) -> LibrarySource | None:
        for source in self.sources:
            if self.require_mount and not self.mount_check(source.mount_path):
                continue
            if os.path.isdir(source.index_path):
                return source
        return None

    @staticmethod
    def _inside(path: str, parent: str) -> bool:
        try:
            return os.path.commonpath((os.path.realpath(path), os.path.realpath(parent))) == os.path.realpath(parent)
        except ValueError:
            return False

    @staticmethod
    def _lexically_inside(path: str, parent: str) -> bool:
        try:
            return os.path.commonpath((os.path.abspath(path), os.path.abspath(parent))) == os.path.abspath(parent)
        except ValueError:
            return False

    def scan(self) -> bool:
        source = self._available_source()
        if not source:
            with self.lock:
                self.available = False
                self.source = None
                self.error = "media drive is not mounted or its links index is unavailable"
                self.last_scan = time.time()
            return False

        found: dict[str, MediaItem] = {}
        found_by_target: dict[str, MediaItem] = {}
        for category in CATEGORY_PRIORITY:
            category_path = os.path.join(source.index_path, category)
            if not os.path.isdir(category_path):
                continue
            for root, dirs, files in os.walk(category_path, followlinks=False):
                dirs[:] = sorted((name for name in dirs if not name.startswith(".")), key=natural_key)
                for filename in sorted(files, key=natural_key):
                    if filename.startswith("."):
                        continue
                    link_path = os.path.join(root, filename)
                    if not os.path.islink(link_path):
                        continue
                    real_path = os.path.realpath(link_path)
                    if not self._inside(real_path, source.mount_path):
                        continue
                    if not os.path.isfile(real_path) or Path(real_path).suffix.casefold() not in VIDEO_EXTENSIONS:
                        continue
                    relative = os.path.relpath(link_path, source.index_path)
                    candidate = parse_candidate(
                        category,
                        relative,
                        link_path,
                        real_path,
                        source.name,
                        library_root=source.mount_path,
                    )
                    existing = found_by_target.get(real_path) or found.get(candidate.key)
                    if existing:
                        existing.categories.update(candidate.categories)
                        existing.aliases.update(candidate.aliases)
                        existing.mtime = max(existing.mtime, candidate.mtime)
                        if candidate.rank < existing.rank:
                            categories, aliases, mtime = existing.categories, existing.aliases, existing.mtime
                            found.pop(existing.key, None)
                            found[candidate.key] = candidate
                            candidate.categories = categories
                            candidate.aliases = aliases
                            candidate.mtime = mtime
                            for target, value in tuple(found_by_target.items()):
                                if value is existing:
                                    found_by_target[target] = candidate
                            found_by_target[real_path] = candidate
                        else:
                            found_by_target[real_path] = existing
                    else:
                        found[candidate.key] = candidate
                        found_by_target[real_path] = candidate

        shows_by_name: dict[tuple[str, str], list[MediaItem]] = {}
        for item in found.values():
            if item.media_type == "episode" and item.series:
                identity = (item.series_kind or "tv", canonical_series(item.series))
                shows_by_name.setdefault(identity, []).append(item)
        shows = {}
        for (show_kind, show_name_key), episodes in shows_by_name.items():
            episodes.sort(
                key=lambda item: (
                    item.season if item.season is not None else 9999,
                    item.episode if item.episode is not None else 9999,
                    natural_key(item.rel_path),
                )
            )
            key = f"show:{show_kind}:{show_name_key}"
            show = Show(
                key=key,
                id=stable_id(key),
                name=episodes[0].series or show_name_key,
                kind=show_kind,
                episodes=episodes,
            )
            shows[show.id] = show

        by_path: dict[str, MediaItem] = {}
        by_rel: dict[str, MediaItem] = {}
        by_id: dict[str, MediaItem] = {}
        for item in found.values():
            by_id[item.id] = item
            by_path[os.path.abspath(item.path)] = item
            by_path[os.path.realpath(item.path)] = item
            for alias in item.aliases:
                by_rel[alias] = item
                by_path[os.path.abspath(os.path.join(source.index_path, alias.lstrip("/")))] = item
        for target, item in found_by_target.items():
            by_path[os.path.realpath(target)] = item

        with self.lock:
            self.items = by_id
            self.items_by_key = found
            self.items_by_path = by_path
            self.items_by_rel = by_rel
            self.shows = shows
            self.available = True
            self.source = source
            self.error = None
            self.last_scan = time.time()
        return True

    def item_for_path(self, path: str | None) -> MediaItem | None:
        if not path:
            return None
        with self.lock:
            return self.items_by_path.get(os.path.abspath(path)) or self.items_by_path.get(os.path.realpath(path))

    def item_for_rel(self, rel_path: str) -> MediaItem | None:
        normalized_rel = "/" + rel_path.lstrip("/")
        with self.lock:
            return self.items_by_rel.get(normalized_rel)

    def resolve_for_play(self, item: MediaItem) -> str:
        """Revalidate an indexed link and return a symlink-independent target."""
        with self.lock:
            source = self.source
            if not self.available or source is None or item.source != source.name:
                raise RuntimeError(self.error or "media library unavailable")
            try:
                mounted = not self.require_mount or self.mount_check(source.mount_path)
            except Exception:
                mounted = False
            if not mounted or not os.path.isdir(source.index_path):
                raise RuntimeError("media drive is no longer mounted")
            if not self._lexically_inside(item.path, source.index_path):
                raise RuntimeError(f"media link escaped the active index: {item.title}")
            if not os.path.islink(item.path):
                raise RuntimeError(f"media disappeared from the index: {item.title}")
            real_path = os.path.realpath(item.path)
            if (
                not self._inside(real_path, source.mount_path)
                or not os.path.isfile(real_path)
                or Path(real_path).suffix.casefold() not in VIDEO_EXTENSIONS
            ):
                raise RuntimeError(f"media target is no longer safe to play: {item.title}")
            return real_path

    def snapshot(self) -> tuple[list[MediaItem], list[Show]]:
        with self.lock:
            return list(self.items.values()), list(self.shows.values())


class ProgressStore:
    def __init__(self, path: str):
        self.path = path
        if path != ":memory:":
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self.connection = sqlite3.connect(path, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        with self.connection:
            self.connection.execute(
                """
                CREATE TABLE IF NOT EXISTS progress (
                    media_key TEXT PRIMARY KEY,
                    position REAL NOT NULL DEFAULT 0,
                    duration REAL NOT NULL DEFAULT 0,
                    updated REAL NOT NULL,
                    finished INTEGER NOT NULL DEFAULT 0,
                    finished_override INTEGER,
                    play_count INTEGER NOT NULL DEFAULT 0,
                    title TEXT,
                    rel_path TEXT
                )
                """
            )
            columns = {
                row[1]
                for row in self.connection.execute("PRAGMA table_info(progress)").fetchall()
            }
            if "finished_override" not in columns:
                self.connection.execute(
                    "ALTER TABLE progress ADD COLUMN finished_override INTEGER"
                )
            self.connection.execute(
                "CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )

    def get(self, media_key: str) -> dict[str, Any] | None:
        with self.lock:
            row = self.connection.execute(
                "SELECT * FROM progress WHERE media_key = ?", (media_key,)
            ).fetchone()
        return dict(row) if row else None

    def all(self) -> dict[str, dict[str, Any]]:
        with self.lock:
            rows = self.connection.execute("SELECT * FROM progress").fetchall()
        return {row["media_key"]: dict(row) for row in rows}

    def record(
        self,
        media_key: str,
        *,
        position: float | None = None,
        duration: float | None = None,
        finished: bool | None = None,
        finished_override: bool | None | object = _UNSET,
        title: str | None = None,
        rel_path: str | None = None,
        updated: float | None = None,
        increment_play: bool = False,
        only_if_absent: bool = False,
    ) -> dict[str, Any]:
        with self.lock:
            current = self.get(media_key) or {}
            if only_if_absent and current:
                return current
            value = {
                "media_key": media_key,
                "position": float(current.get("position", 0) if position is None else position),
                "duration": float(current.get("duration", 0) if duration is None else duration),
                "updated": float(updated if updated is not None else time.time()),
                "finished": int(current.get("finished", 0) if finished is None else bool(finished)),
                "finished_override": (
                    current.get("finished_override")
                    if finished_override is _UNSET
                    else (None if finished_override is None else int(bool(finished_override)))
                ),
                "play_count": int(current.get("play_count", 0)) + (1 if increment_play else 0),
                "title": title if title is not None else current.get("title"),
                "rel_path": rel_path if rel_path is not None else current.get("rel_path"),
            }
            with self.connection:
                self.connection.execute(
                    """
                    INSERT OR REPLACE INTO progress
                    (media_key, position, duration, updated, finished, finished_override,
                     play_count, title, rel_path)
                    VALUES (:media_key, :position, :duration, :updated, :finished,
                            :finished_override, :play_count, :title, :rel_path)
                    """,
                    value,
                )
            return value

    def clear(self, media_key: str) -> bool:
        with self.lock, self.connection:
            result = self.connection.execute(
                "DELETE FROM progress WHERE media_key = ?", (media_key,)
            )
        return bool(result.rowcount)

    def mark(self, media_key: str, finished: bool) -> dict[str, Any]:
        current = self.get(media_key) or {}
        return self.record(
            media_key,
            position=(current.get("duration") or 0) if finished else 0,
            finished=finished,
            finished_override=finished,
        )

    def metadata(self, key: str) -> str | None:
        with self.lock:
            row = self.connection.execute(
                "SELECT value FROM metadata WHERE key = ?", (key,)
            ).fetchone()
        return str(row[0]) if row else None

    def set_metadata(self, key: str, value: str) -> None:
        with self.lock, self.connection:
            self.connection.execute(
                "INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)",
                (key, value),
            )


class SonosVolumeController:
    """Read and set only the physical rear stereo zone used for movie audio."""

    def __init__(
        self,
        *,
        discover_func: Callable[..., Any] | None = None,
        rear_uids: Iterable[str] = REAR_SONOS_UIDS,
        clock: Callable[[], float] = time.monotonic,
        cache_ttl: float = SONOS_DISCOVERY_TTL,
    ):
        self.discover_func = discover_func
        self.rear_uids = tuple(rear_uids)
        if len(self.rear_uids) != 2 or len(set(self.rear_uids)) != 2:
            raise ValueError("exactly two distinct rear Sonos UIDs are required")
        self.clock = clock
        self.cache_ttl = cache_ttl
        self.lock = threading.RLock()
        self.zones: dict[str, Any] = {}
        self.zones_at = 0.0

    def invalidate(self) -> None:
        with self.lock:
            self.zones_at = 0.0

    def _get_zones(self, *, force: bool = False) -> dict[str, Any]:
        with self.lock:
            now = self.clock()
            if not force and self.zones_at and now - self.zones_at < self.cache_ttl:
                return self.zones
            if self.discover_func is None:
                from soco.discovery import discover

                found = discover(timeout=5, include_invisible=True) or set()
            else:
                try:
                    found = self.discover_func(timeout=5, include_invisible=True) or set()
                except TypeError:
                    found = self.discover_func(timeout=5) or set()
            self.zones = {
                str(zone.uid): zone
                for zone in found
                if str(getattr(zone, "uid", "")) in self.rear_uids
            }
            self.zones_at = now
            return self.zones

    def _rear_zone(self, *, force: bool = False) -> Any:
        zones = self._get_zones(force=force)
        missing = [uid for uid in self.rear_uids if uid not in zones]
        if missing:
            self.invalidate()
            raise RuntimeError("both physical rear Sonos speakers were not discovered")
        visible = [zones[uid] for uid in self.rear_uids if zones[uid].is_visible]
        if len(visible) != 1:
            self.invalidate()
            if visible:
                raise RuntimeError("rear Sonos speakers are not paired as one stereo zone")
            raise RuntimeError("rear Sonos stereo zone is not visible")
        return visible[0]

    def snapshot(self) -> dict[str, Any]:
        try:
            with self.lock:
                zone = self._rear_zone()
                return {
                    "available": True,
                    "device": str(zone.player_name),
                    "volume": int(zone.volume),
                    "muted": bool(zone.mute),
                }
        except Exception as exc:
            return {
                "available": False,
                "device": None,
                "volume": None,
                "muted": None,
                "error": str(exc),
            }

    def set_volume(self, volume: int) -> int:
        if isinstance(volume, bool) or not 0 <= int(volume) <= 100:
            raise ValueError("Sonos volume must be from 0 to 100")
        value = int(volume)
        with self.lock:
            zone = self._rear_zone(force=True)
            zone.volume = value
            return value


class VlcController:
    def __init__(
        self,
        *,
        dbus_module: Any = None,
        run: Callable[..., Any] = subprocess.run,
        popen: Callable[..., Any] = subprocess.Popen,
        killpg: Callable[[int, int], None] = os.killpg,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self._dbus_module = dbus_module
        self.run = run
        self.popen = popen
        self.killpg = killpg
        self.sleep = sleep
        self.lock = threading.RLock()
        self.room_lock = threading.RLock()
        self.room_process: Any = None

    def _dbus(self):
        if self._dbus_module is None:
            self._dbus_module = importlib.import_module("dbus")
        return self._dbus_module

    def _interfaces(self):
        module = self._dbus()
        bus = module.SessionBus()
        obj = bus.get_object(MPRIS_NAME, MPRIS_PATH)
        return (
            module,
            module.Interface(obj, DBUS_PROPERTIES),
            module.Interface(obj, MPRIS_PLAYER),
            module.Interface(obj, MPRIS_ROOT),
        )

    def snapshot(self) -> dict[str, Any]:
        try:
            _module, props, _player, _root = self._interfaces()
            player_values = native(props.GetAll(MPRIS_PLAYER))
            try:
                root_values = native(props.GetAll(MPRIS_ROOT))
            except Exception:
                root_values = {}
            metadata = player_values.get("Metadata") or {}
            url = str(metadata.get("xesam:url") or "")
            path = unquote(urlsplit(url).path) if url.startswith("file:") else None
            duration_us = int(metadata.get("mpris:length") or 0)
            position_us = int(player_values.get("Position") or 0)
            snapshot = {
                "available": True,
                "state": str(player_values.get("PlaybackStatus") or "Stopped").upper(),
                "path": path,
                "url": url,
                "title": str(metadata.get("xesam:title") or (os.path.basename(path) if path else "")),
                "position": max(0.0, position_us / 1_000_000),
                "duration": max(0.0, duration_us / 1_000_000),
                "track_id": metadata.get("mpris:trackid"),
                "volume": float(player_values.get("Volume", 0.0)),
                "rate": float(player_values.get("Rate", 1.0)),
                "fullscreen": bool(root_values.get("Fullscreen", False)),
                "can_fullscreen": bool(root_values.get("CanSetFullscreen", False)),
                "can_seek": bool(player_values.get("CanSeek", False)),
                "can_next": bool(player_values.get("CanGoNext", False)),
                "can_previous": bool(player_values.get("CanGoPrevious", False)),
                "can_play": bool(player_values.get("CanPlay", False)),
                "can_pause": bool(player_values.get("CanPause", False)),
                "can_control": bool(player_values.get("CanControl", False)),
            }
            try:
                self.enforce_fixed_volume(snapshot)
            except Exception as exc:
                snapshot["volume_error"] = str(exc)
            return snapshot
        except Exception as exc:
            return {
                "available": False,
                "state": "OFFLINE",
                "error": str(exc),
                "position": 0.0,
                "duration": 0.0,
            }

    def action(self, name: str) -> None:
        methods = {
            "toggle": "PlayPause",
            "play": "Play",
            "pause": "Pause",
            "next": "Next",
            "previous": "Previous",
            "stop": "Stop",
        }
        if name not in methods:
            raise ValueError(f"unknown player action '{name}'")
        with self.lock:
            _module, _props, player, _root = self._interfaces()
            getattr(player, methods[name])()

    def seek(self, seconds: float) -> None:
        with self.lock:
            module, _props, player, _root = self._interfaces()
            player.Seek(module.Int64(int(seconds * 1_000_000)))

    def set_position(self, track_id: Any, seconds: float) -> None:
        with self.lock:
            module, _props, player, _root = self._interfaces()
            player.SetPosition(track_id, module.Int64(int(seconds * 1_000_000)))

    def set_volume(self, value: float) -> float:
        value = max(0.0, min(1.25, float(value)))
        with self.lock:
            module, props, _player, _root = self._interfaces()
            props.Set(MPRIS_PLAYER, "Volume", module.Double(value))
        return value

    def enforce_fixed_volume(self, snapshot: dict[str, Any]) -> bool:
        if not snapshot.get("available"):
            return False
        try:
            current = float(snapshot.get("volume"))
        except (TypeError, ValueError):
            current = math.nan
        changed = not math.isfinite(current) or abs(current - VLC_FIXED_VOLUME) > 0.005
        if changed:
            self.set_volume(VLC_FIXED_VOLUME)
        snapshot["volume"] = VLC_FIXED_VOLUME
        return changed

    def set_rate(self, value: float) -> float:
        value = max(0.5, min(2.0, float(value)))
        with self.lock:
            module, props, _player, _root = self._interfaces()
            props.Set(MPRIS_PLAYER, "Rate", module.Double(value))
        return value

    def toggle_fullscreen(self) -> bool:
        snapshot = self.snapshot()
        if not snapshot.get("available"):
            raise RuntimeError("VLC is not running")
        if not snapshot.get("can_fullscreen"):
            raise RuntimeError("this VLC session does not expose fullscreen control")
        value = not snapshot.get("fullscreen", False)
        with self.lock:
            module, props, _player, _root = self._interfaces()
            props.Set(MPRIS_ROOT, "Fullscreen", module.Boolean(value))
        return value

    def quit(self) -> None:
        try:
            _module, _props, _player, root = self._interfaces()
            root.Quit()
        except Exception:
            pass

    def _command(self, args: list[str], timeout: float = 10) -> Any:
        return self.run(args, capture_output=True, text=True, timeout=timeout, check=False)

    def _stop_existing(self) -> None:
        self.quit()
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if not self.snapshot().get("available"):
                break
            self.sleep(0.15)
        if self.snapshot().get("available"):
            self._command(
                [PKILL, "-TERM", "-u", str(os.getuid()), "-x", "vlc"],
                timeout=5,
            )
        self._command([SYSTEMCTL, "--user", "stop", PLAYER_UNIT], timeout=5)
        self._command([SYSTEMCTL, "--user", "reset-failed", PLAYER_UNIT], timeout=5)

    def _prepare_room(self, task: str = "rear_movie") -> Any:
        if task not in ("rear_movie", "rear_movie_resume"):
            raise ValueError("unknown rear-room preparation task")
        self._command([XSET, "-display", DISPLAY, "dpms", "force", "on"], timeout=5)
        with self.room_lock:
            process = self.room_process
            if process is not None:
                try:
                    if process.poll() is None:
                        return process
                except Exception:
                    pass
                self.room_process = None
            if os.path.isfile(SNS) and os.access(SNS, os.R_OK):
                self.room_process = self.popen(
                    ["/bin/bash", SNS, task],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
            return self.room_process

    def _terminate_room_process(self, process: Any) -> None:
        try:
            pid = int(process.pid)
        except (AttributeError, TypeError, ValueError):
            pid = None
        if pid is not None:
            try:
                self.killpg(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        else:
            try:
                process.terminate()
            except Exception:
                pass
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            if pid is not None:
                try:
                    self.killpg(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            else:
                try:
                    process.kill()
                except Exception:
                    pass
            try:
                process.wait(timeout=5)
            except Exception:
                pass
        except Exception:
            pass
        with self.room_lock:
            if self.room_process is process:
                self.room_process = None

    def prepare_room(self, *, wait: bool = False) -> None:
        process = self._prepare_room("rear_movie_resume")
        if process is None:
            raise RoomPreparationError("rear_movie setup script is unavailable")
        if not wait:
            return
        try:
            returncode = process.wait(timeout=ROOM_PREPARE_TIMEOUT)
        except subprocess.TimeoutExpired as exc:
            self._terminate_room_process(process)
            raise RoomPreparationError(
                f"rear_movie did not finish within {ROOM_PREPARE_TIMEOUT:g} seconds"
            ) from exc
        with self.room_lock:
            if self.room_process is process:
                self.room_process = None
        if returncode:
            raise RoomPreparationError(
                f"rear_movie exited with status {returncode}"
            )

    def room_preparing(self) -> bool:
        with self.room_lock:
            process = self.room_process
            if process is None:
                return False
            try:
                if process.poll() is None:
                    return True
            except Exception:
                pass
            self.room_process = None
            return False

    def _subtitle_index(self, path: str) -> int | None:
        media_path = os.path.realpath(path)
        if Path(media_path).suffix.casefold() != ".mkv" or not os.path.isfile(MKVMERGE):
            return None
        try:
            result = self._command([MKVMERGE, "-J", media_path], timeout=20)
            if result.returncode:
                return None
            tracks = [track for track in json.loads(result.stdout).get("tracks", []) if track.get("type") == "subtitles"]
            for index, track in enumerate(tracks):
                properties = track.get("properties") or {}
                if properties.get("language") == "eng" and properties.get("forced_track") is False:
                    return index
        except (OSError, ValueError, TypeError):
            return None
        return None

    def launch(self, paths: list[str], *, position: float = 0, subtitles: str = "auto") -> dict[str, Any]:
        if not paths:
            raise ValueError("no media paths supplied")
        if subtitles not in ("auto", "off"):
            raise ValueError("subtitles must be auto or off")
        with self.lock:
            self._stop_existing()
            self._prepare_room()
            command = [
                VLC,
                "--control=dbus",
                "--audio-language=eng,en",
                "--sub-language=eng,en",
                "--avcodec-hw=v4l2-request",
                "--no-video-title-show",
                f"--volume={round(VLC_FIXED_VOLUME * 256)}",
                "--no-volume-save",
                "--gain=1.0",
            ]
            if subtitles == "off":
                command.append("--sub-track=-1")
            else:
                subtitle_index = self._subtitle_index(paths[0])
                if subtitle_index is not None:
                    command.append(f"--sub-track={subtitle_index}")
            command.extend(paths)
            launch = [
                SYSTEMD_RUN,
                "--user",
                f"--unit={PLAYER_UNIT.removesuffix('.service')}",
                "--collect",
                "--quiet",
                "--service-type=exec",
                f"--setenv=DISPLAY={DISPLAY}",
                f"--setenv=XDG_RUNTIME_DIR={RUNTIME_DIR}",
                f"--setenv=DBUS_SESSION_BUS_ADDRESS={SESSION_BUS}",
                *command,
            ]
            result = self._command(launch, timeout=15)
            if result.returncode:
                message = (result.stderr or result.stdout or "systemd-run failed").strip()
                raise RuntimeError(message)

            deadline = time.monotonic() + 12
            expected_path = os.path.realpath(paths[0])
            snapshot: dict[str, Any] = {}
            while time.monotonic() < deadline:
                snapshot = self.snapshot()
                current_path = snapshot.get("path")
                requested_track_ready = bool(
                    snapshot.get("available")
                    and snapshot.get("track_id") is not None
                    and current_path
                    and os.path.realpath(str(current_path)) == expected_path
                )
                if requested_track_ready:
                    break
                self.sleep(0.25)
            else:
                if not snapshot.get("available"):
                    raise RuntimeError("VLC did not appear on the session bus")
                raise RuntimeError("VLC did not load the requested video")

            self.set_volume(VLC_FIXED_VOLUME)
            snapshot["volume"] = VLC_FIXED_VOLUME
            if position <= 0:
                return snapshot

            last_error: Exception | None = None
            while time.monotonic() < deadline:
                try:
                    self.set_position(snapshot["track_id"], position)
                    self.sleep(0.15)
                    snapshot = self.snapshot()
                    current_path = snapshot.get("path")
                    if (
                        snapshot.get("track_id") is not None
                        and current_path
                        and os.path.realpath(str(current_path)) == expected_path
                        and abs(float(snapshot.get("position") or 0) - position) <= 3
                    ):
                        return snapshot
                except Exception as exc:
                    last_error = exc
                self.sleep(0.25)
            if last_error is not None:
                raise RuntimeError(f"VLC could not resume the video: {last_error}")
            raise RuntimeError("VLC did not accept the resume position")


class VideoService(playback.PlaybackMixin, identity.CatalogIdentityMixin):
    def __init__(
        self,
        library: MediaLibrary,
        store: ProgressStore,
        player: VlcController,
        *,
        sonos: SonosVolumeController | None = None,
        catalog: MediaAssetCatalog | None = None,
        qbittorrent: QbittorrentClient | Any | None = None,
        legacy_positions: str = LEGACY_POSITIONS_PATH,
        clock: Callable[[], float] = time.time,
        randomizer: random.Random | Any = random,
    ):
        self.library = library
        self.store = store
        self.player = player
        self.sonos = sonos
        self.catalog = catalog
        self.qbittorrent = qbittorrent
        self.legacy_positions = legacy_positions
        self.clock = clock
        self.random = randomizer
        self.control_lock = threading.RLock()
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.playback = playback.PlaybackState()
        self.last_error: str | None = None
        self.session_recovery_pending = False
        self.session_recovery_error: str | None = None
        self.identity_error: str | None = None

    def start(self) -> None:
        self._retry_session_recovery()
        self.rescan()
        if not self.thread:
            self.thread = threading.Thread(target=self._loop, name="video-library-poller", daemon=True)
            self.thread.start()

    def _loop(self) -> None:
        next_scan = time.monotonic() + SCAN_INTERVAL
        while not self.stop_event.wait(POLL_INTERVAL):
            try:
                self.status()
                self._pause_for_expired_sleep_timer()
                if time.monotonic() >= next_scan:
                    reconciliation = self.reconcile_torrents()
                    if not reconciliation.get("updated"):
                        self.rescan()
                    next_scan = time.monotonic() + SCAN_INTERVAL
            except Exception as exc:
                self.last_error = str(exc)

    def rescan(self) -> bool:
        available = self.library.scan()
        if available:
            # Bind durable asset/work IDs before auditing the raw legacy log so
            # matched records are attributable on the very first v2 startup.
            if self.catalog is not None:
                self._sync_library_identities()
            imported = self.import_legacy_positions()
            # Legacy rows imported above are written through the untouched v1
            # table.  Reconcile them once so the v2 playhead and projection
            # shadow start from the same checkpoint.
            if self.catalog is not None and imported:
                self._sync_library_identities()
        return available

    def import_legacy_positions(self) -> int:
        try:
            stamp = str(os.path.getmtime(self.legacy_positions))
            with open(self.legacy_positions, encoding="utf-8", errors="replace") as handle:
                lines = handle.readlines()
        except OSError:
            return 0
        legacy_current = self.store.metadata("legacy_positions_mtime") == stamp
        audit_current = (
            self.catalog is None
            or self.store.metadata("video_v2_legacy_positions_mtime") == stamp
        )
        if legacy_current and audit_current:
            return 0
        base_updated = float(stamp) - len(lines)
        latest: dict[str, tuple[MediaItem, float, float]] = {}
        import_id = None
        if self.catalog is not None and not audit_current:
            import_id = self.catalog.begin_import(
                source_kind="legacy-vlc-position-log",
                source_ref=os.path.basename(self.legacy_positions),
                source_digest=hashlib.sha256("".join(lines).encode("utf-8")).hexdigest(),
            )
        for index, line in enumerate(lines):
            match = LEGACY_LINE_RE.match(line)
            if not match:
                if import_id is not None:
                    self.catalog.record_import(
                        import_id,
                        source_key=f"line:{index}",
                        action="unparsed",
                        raw={"line": line.rstrip("\n")},
                    )
                continue
            item = self.library.item_for_rel(match.group("rel"))
            if not item:
                if import_id is not None:
                    self.catalog.record_import(
                        import_id,
                        source_key=f"line:{index}",
                        action="unresolved",
                        source_updated=base_updated + index,
                        raw={
                            "line": line.rstrip("\n"),
                            "relative_path": match.group("rel"),
                            "position_microseconds": int(match.group("micros")),
                        },
                    )
                continue
            latest[item.key] = (
                item,
                int(match.group("micros")) / 1_000_000,
                base_updated + index,
            )
            if import_id is not None:
                self.catalog.record_import(
                    import_id,
                    source_key=f"line:{index}",
                    action="matched",
                    source_updated=base_updated + index,
                    asset_id=item.asset_id,
                    work_id=item.work_id,
                    raw={
                        "line": line.rstrip("\n"),
                        "relative_path": match.group("rel"),
                        "position_microseconds": int(match.group("micros")),
                    },
                )
        if not legacy_current:
            for item, position, updated in latest.values():
                self.store.record(
                    item.key,
                    position=position,
                    updated=updated,
                    title=item.title,
                    rel_path=item.rel_path,
                    only_if_absent=True,
                )
            self.store.set_metadata("legacy_positions_mtime", stamp)
        if import_id is not None:
            self.catalog.finish_import(
                import_id,
                summary={
                    "lines": len(lines),
                    "matched_keys": len(latest),
                },
            )
            self.store.set_metadata("video_v2_legacy_positions_mtime", stamp)
        return len(latest)

    def show_for_item(self, item: MediaItem) -> Show | None:
        if not item.series:
            return None
        show_id = stable_id(
            f"show:{item.series_kind or 'tv'}:{canonical_series(item.series)}"
        )
        return self.library.shows.get(show_id)

    def _next_episode(self, show: Show, progress: dict[str, dict[str, Any]]) -> MediaItem:
        unfinished = [
            item
            for item in show.episodes
            if (progress.get(item.key) or {}).get("position", 0) >= MIN_CONTINUE_POSITION
            and not (progress.get(item.key) or {}).get("finished")
        ]
        if unfinished:
            return max(unfinished, key=lambda item: progress[item.key].get("updated", 0))
        for item in show.episodes:
            record = progress.get(item.key)
            if not record or not record.get("finished"):
                return item
        return show.episodes[0]

    def _show_dict(self, show: Show, progress: dict[str, dict[str, Any]]) -> dict[str, Any]:
        next_item = self._next_episode(show, progress)
        watched = sum(bool((progress.get(item.key) or {}).get("finished")) for item in show.episodes)
        latest = max((progress.get(item.key, {}).get("updated", 0) for item in show.episodes), default=0)
        return {
            "id": show.id,
            "name": show.name,
            "type": "show",
            "kind": show.kind,
            "episodes": len(show.episodes),
            "watched": watched,
            "new": show.new,
            "last_watched": int(latest),
            "next": next_item.as_dict(progress.get(next_item.key)),
        }

    def _favorite_specs(self, shows: list[Show]) -> list[dict[str, Any]]:
        by_name = {
            canonical_series(show.name): show for show in shows if show.kind == "tv"
        }
        result = []
        for name, no_subtitles in DEFAULT_FAVORITES:
            wanted = canonical_series(name)
            show = by_name.get(wanted)
            if not show:
                show = next((candidate for key, candidate in by_name.items() if wanted in key or key in wanted), None)
            if show:
                result.append(
                    {
                        "id": show.id,
                        "name": show.name,
                        "no_subtitles": no_subtitles,
                    }
                )
        return result

    def library_payload(self) -> dict[str, Any]:
        items, shows = self.library.snapshot()
        progress = self._progress_all()
        continuing = [
            item
            for item in items
            if (progress.get(item.key) or {}).get("position", 0) >= MIN_CONTINUE_POSITION
            and not (progress.get(item.key) or {}).get("finished")
        ]
        continuing.sort(key=lambda item: progress[item.key].get("updated", 0), reverse=True)
        features = [item for item in items if item.media_type != "episode"]
        movies = sorted((item for item in features if item.media_type == "movie"), key=lambda item: natural_key(item.title))
        documentary_features = sorted(
            (item for item in features if item.media_type == "documentary"),
            key=lambda item: natural_key(item.title),
        )
        show_cards = [self._show_dict(show, progress) for show in shows]
        show_cards.sort(key=lambda show: natural_key(show["name"]))
        documentary_show_ids = {show.id for show in shows if show.kind == "documentary"}
        documentary_shows = [
            show for show in show_cards if show["id"] in documentary_show_ids
        ]
        documentaries = sorted(
            [
                *(item.as_dict(progress.get(item.key)) for item in documentary_features),
                *documentary_shows,
            ],
            key=lambda value: natural_key(value.get("name") or value.get("title") or ""),
        )
        recent = sorted((item for item in items if item.new), key=lambda item: item.mtime, reverse=True)[:60]

        up_next = [show for show in show_cards if show["last_watched"] and show["watched"] < show["episodes"]]
        up_next.sort(key=lambda show: show["last_watched"], reverse=True)
        return {
            "ok": True,
            "library": self.status()["library"],
            "continue": [item.as_dict(progress.get(item.key)) for item in continuing[:20]],
            "up_next": up_next[:12],
            "new": [item.as_dict(progress.get(item.key)) for item in recent],
            "favorites": self._favorite_specs(shows),
            "movies": [item.as_dict(progress.get(item.key)) for item in movies],
            "documentaries": documentaries,
            "shows": [show for show in show_cards if show.get("kind") == "tv"],
        }

    @staticmethod
    def _score(query: str, text: str) -> float:
        query_tokens = normalized(query).split()
        text_tokens = normalized(text).split()
        if not query_tokens or not text_tokens:
            return 0.0
        if normalized(query) in normalized(text):
            return 1.0 - min(0.2, (len(text_tokens) - len(query_tokens)) * 0.01)
        best = []
        for query_token in query_tokens:
            best.append(max(SequenceMatcher(None, query_token, token).ratio() for token in text_tokens))
        return sum(best) / len(best)

    def search(self, query: str, limit: int = 24) -> list[dict[str, Any]]:
        query = query.strip()
        if not query:
            return []
        items, shows = self.library.snapshot()
        progress = self._progress_all()
        candidates: list[tuple[float, str, Any]] = []
        for show in shows:
            score = self._score(query, show.name)
            if score >= 0.5:
                candidates.append((score, "show", show))
        for item in items:
            label = " ".join(filter(None, (item.series, item.episode_code, item.episode_title, item.title)))
            score = self._score(query, label)
            if score >= 0.58:
                candidates.append((score, "item", item))
        candidates.sort(key=lambda row: (-row[0], natural_key(row[2].name if row[1] == "show" else row[2].title)))
        result = []
        seen = set()
        for score, kind, value in candidates:
            if value.id in seen:
                continue
            seen.add(value.id)
            card = self._show_dict(value, progress) if kind == "show" else value.as_dict(progress.get(value.key))
            card["score"] = round(score, 3)
            result.append(card)
            if len(result) >= limit:
                break
        return result

    def show_payload(self, show_id: str) -> dict[str, Any]:
        show = self.library.shows.get(show_id)
        if not show:
            raise KeyError("unknown show id")
        progress = self._progress_all()
        return {
            "ok": True,
            "show": self._show_dict(show, progress),
            "episodes": [item.as_dict(progress.get(item.key)) for item in show.episodes],
        }

    def _resolve_play(self, item_id: str | None, show_id: str | None, query: str | None, shuffle: bool) -> tuple[MediaItem, list[MediaItem]]:
        progress = self._progress_all()
        show = None
        item = None
        if item_id:
            item = self.library.items.get(item_id)
            if not item:
                raise KeyError("unknown media id")
            show = self.show_for_item(item)
        elif show_id:
            show = self.library.shows.get(show_id)
            if not show:
                raise KeyError("unknown show id")
            item = self.random.choice(show.episodes) if shuffle else self._next_episode(show, progress)
        elif query:
            matches = self.search(query, limit=1)
            if not matches:
                raise KeyError(f"no match for '{query}'")
            match = matches[0]
            if match["type"] == "show":
                show = self.library.shows[match["id"]]
                item = self.random.choice(show.episodes) if shuffle else self._next_episode(show, progress)
            else:
                item = self.library.items[match["id"]]
                show = self.show_for_item(item)
        else:
            raise ValueError("play requires item, show, or q")

        assert item is not None
        if show and not shuffle:
            start = show.episodes.index(item)
            queue = show.episodes[start:]
        else:
            queue = [item]
        return item, queue

    def surprise(self, media_type: str = "any", subtitles: str = "auto") -> dict[str, Any]:
        items, _shows = self.library.snapshot()
        progress = self._progress_all()
        choices = [item for item in items if not (progress.get(item.key) or {}).get("finished")]
        if media_type == "movie":
            choices = [item for item in choices if item.media_type in ("movie", "documentary")]
        elif media_type == "show":
            choices = [item for item in choices if item.media_type == "episode"]
        elif media_type != "any":
            raise ValueError("type must be any, movie, or show")
        if not choices:
            raise RuntimeError("no unwatched choices are available")
        chosen = self.random.choice(choices)
        return self.play(item_id=chosen.id, restart=True, subtitles=subtitles)


def default_sources() -> tuple[LibrarySource, ...]:
    return (
        LibrarySource("movingparts", "/mnt/movingparts", "/mnt/movingparts/links"),
        LibrarySource("bigboi", "/mnt/bigboi", "/mnt/bigboi/mp_backup/links"),
    )


_service: VideoService | None = None
_service_lock = threading.Lock()


def active_service() -> VideoService:
    global _service
    if _service is None:
        with _service_lock:
            if _service is None:
                store = ProgressStore(STATE_PATH)
                ensure_pre_v2_backup(STATE_PATH)
                catalog = MediaAssetCatalog(
                    connection=store.connection,
                    lock=store.lock,
                )
                identity_warnings: list[str] = []
                rollback_reconciled = False
                session_recovery_complete = False
                session_recovery_error: str | None = None
                try:
                    # An old server may have changed or deleted v1 progress
                    # after the last v2 projection.  Reconcile that intent
                    # before orphan recovery advances the exact event/session
                    # state covered by the compatibility shadow.  The normal
                    # library rescan then projects the post-recovery state.
                    catalog.reconcile_v1_progress()
                    rollback_reconciled = True
                except (CatalogError, sqlite3.Error, OSError, ValueError) as exc:
                    session_recovery_error = (
                        "could not reconcile rollback progress; prior session "
                        f"recovery deferred: {exc}"
                    )
                if rollback_reconciled:
                    try:
                        catalog.recover_open_sessions()
                        session_recovery_complete = True
                    except (CatalogError, sqlite3.Error, OSError, ValueError) as exc:
                        session_recovery_error = (
                            f"could not close prior playback sessions: {exc}"
                        )
                qbittorrent = None
                qbittorrent_error = None
                try:
                    qbittorrent = QbittorrentClient(
                        base_url=QBITTORRENT_URL,
                        client_id=QBITTORRENT_CLIENT_ID,
                        temp_roots=QBITTORRENT_TEMP_ROOTS,
                        final_roots=QBITTORRENT_FINAL_ROOTS,
                        timeout=QBITTORRENT_TIMEOUT,
                    )
                except (QbittorrentError, ValueError) as exc:
                    qbittorrent_error = f"qBittorrent identity disabled: {exc}"
                    identity_warnings.append(qbittorrent_error)
                _service = VideoService(
                    MediaLibrary(default_sources()),
                    store,
                    VlcController(),
                    sonos=SonosVolumeController(),
                    catalog=catalog,
                    qbittorrent=qbittorrent,
                )
                _service.session_recovery_pending = not session_recovery_complete
                _service.session_recovery_error = session_recovery_error
                _service.identity_error = "; ".join(identity_warnings) or None
    return _service


def api_error(message: Any, status: int):
    return jsonify({"ok": False, "message": str(message)}), status


@app.before_request
def reject_cross_origin_mutations():
    if request.method not in ("POST", "PUT", "PATCH", "DELETE"):
        return None
    origin = request.headers.get("Origin")
    referer = request.headers.get("Referer")
    if origin:
        if urlsplit(origin).netloc != request.host:
            return api_error("cross-origin control request rejected", 403)
        if request.headers.get("X-Van-Video") != "1":
            return api_error("video control header missing", 403)
    elif referer and urlsplit(referer).netloc != request.host:
        return api_error("cross-origin control request rejected", 403)
    return None


@app.after_request
def disable_api_cache(response):
    if request.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/api/status")
def api_status():
    return jsonify(active_service().status())


@app.route("/api/library")
def api_library():
    return jsonify(active_service().library_payload())


@app.route("/api/search")
def api_search():
    query = request.args.get("q", "").strip()
    return jsonify({"ok": True, "q": query, "matches": active_service().search(query)})


@app.route("/api/shows/<show_id>")
def api_show(show_id: str):
    try:
        return jsonify(active_service().show_payload(show_id))
    except KeyError as exc:
        return api_error(exc.args[0], 404)


@app.route("/api/rescan", methods=["POST"])
def api_rescan():
    service = active_service()
    available = service.rescan()
    payload = service.library_payload()
    payload["message"] = "Library rescanned" if available else service.library.error
    return jsonify(payload), 200 if available else 503


def form_boolean(name: str, default: bool = False) -> bool:
    raw = request.form.get(name)
    if raw is None:
        return default
    if raw.casefold() not in ("1", "0", "true", "false", "on", "off"):
        raise ValueError(f"{name} must be true or false")
    return raw.casefold() in ("1", "true", "on")


@app.route("/api/play", methods=["POST"])
def api_play():
    try:
        result = active_service().play(
            item_id=request.form.get("item") or None,
            show_id=request.form.get("show") or None,
            query=request.form.get("q") or None,
            restart=form_boolean("restart"),
            shuffle=form_boolean("shuffle"),
            subtitles=request.form.get("subtitles", "auto"),
        )
        return jsonify(result)
    except KeyError as exc:
        return api_error(exc.args[0], 404)
    except ValueError as exc:
        return api_error(exc, 400)
    except RuntimeError as exc:
        return api_error(exc, 503)
    except Exception as exc:
        return api_error(f"playback failed: {exc}", 502)


def require_loopback_peer():
    try:
        peer = ipaddress.ip_address(request.remote_addr or "")
    except ValueError:
        return api_error("local media controls require a loopback connection", 403)
    if not peer.is_loopback:
        return api_error("local media controls require a loopback connection", 403)
    return None


@app.route("/api/play-local", methods=["POST"])
def api_play_local():
    rejected = require_loopback_peer()
    if rejected is not None:
        return rejected
    try:
        return jsonify(
            active_service().play_local(
                request.form.get("path", ""),
                restart=form_boolean("restart"),
                subtitles=request.form.get("subtitles", "auto"),
            )
        )
    except FileNotFoundError as exc:
        return api_error(exc, 404)
    except ValueError as exc:
        return api_error(exc, 400)
    except RuntimeError as exc:
        return api_error(exc, 503)
    except Exception as exc:
        return api_error(f"local playback failed: {exc}", 502)


@app.route("/api/torrents/reconcile", methods=["POST"])
def api_torrent_reconcile():
    rejected = require_loopback_peer()
    if rejected is not None:
        return rejected
    torrent_id = request.form.get("torrent", "").strip() or None
    if torrent_id is not None and not re.fullmatch(r"[0-9a-fA-F]{40}", torrent_id):
        return api_error("torrent must be a 40-character qBittorrent ID", 400)
    try:
        return jsonify(active_service().reconcile_torrents(torrent_id))
    except QbittorrentError as exc:
        return api_error(f"qBittorrent reconciliation failed: {exc}", 503)
    except CatalogConflict as exc:
        return api_error(f"media identity conflict: {exc}", 409)
    except Exception as exc:
        return api_error(f"torrent reconciliation failed: {exc}", 502)


@app.route("/api/surprise", methods=["POST"])
def api_surprise():
    try:
        return jsonify(
            active_service().surprise(
                request.form.get("type", "any"),
                request.form.get("subtitles", "auto"),
            )
        )
    except ValueError as exc:
        return api_error(exc, 400)
    except RuntimeError as exc:
        return api_error(exc, 503)
    except Exception as exc:
        return api_error(f"playback failed: {exc}", 502)


@app.route("/api/control", methods=["POST"])
def api_control():
    action = request.form.get("action", "")
    if action not in ("toggle", "play", "pause", "next", "previous", "stop"):
        return api_error("unknown control action", 400)
    try:
        return jsonify(active_service().control_player(action))
    except RoomPreparationError as exc:
        return api_error(f"rear movie audio setup failed: {exc}", 503)
    except Exception as exc:
        return api_error(f"VLC control failed: {exc}", 502)


@app.route("/api/seek", methods=["POST"])
def api_seek():
    try:
        seconds = float(request.form.get("seconds", ""))
        if not math.isfinite(seconds) or abs(seconds) > 3600:
            raise ValueError
    except (TypeError, ValueError):
        return api_error("seconds must be between -3600 and 3600", 400)
    try:
        active_service().player.seek(seconds)
        return jsonify({"ok": True, "message": f"Seek {seconds:+g} seconds"})
    except Exception as exc:
        return api_error(f"VLC seek failed: {exc}", 502)


@app.route("/api/position", methods=["POST"])
def api_position():
    try:
        position = float(request.form.get("position", ""))
        if not math.isfinite(position) or position < 0:
            raise ValueError
    except (TypeError, ValueError):
        return api_error("position must be a non-negative number of seconds", 400)

    service = active_service()
    snapshot = service.player.snapshot()
    try:
        duration = float(snapshot.get("duration") or 0)
    except (TypeError, ValueError):
        duration = 0
    if (
        not snapshot.get("available")
        or snapshot.get("track_id") is None
        or not snapshot.get("can_seek")
        or not math.isfinite(duration)
        or duration <= 0
    ):
        return api_error("the current VLC track is not seekable", 409)
    if position > duration:
        return api_error(f"position must be between 0 and {duration:g}", 400)
    try:
        service.player.set_position(snapshot["track_id"], position)
        return jsonify(
            {
                "ok": True,
                "position": position,
                "message": f"Jumped to {seconds_text(position)}",
            }
        )
    except Exception as exc:
        return api_error(f"VLC position change failed: {exc}", 502)


@app.route("/api/volume", methods=["POST"])
def api_volume():
    raw = request.form.get("value", "").strip()
    try:
        if not re.fullmatch(r"\d{1,3}", raw):
            raise ValueError
        volume = int(raw)
        if not 0 <= volume <= 100:
            raise ValueError
    except (TypeError, ValueError):
        return api_error("Sonos volume must be a whole number from 0 to 100", 400)
    try:
        audio = active_service().set_audio_volume(volume)
        return jsonify(
            {
                "ok": True,
                "audio": audio,
                "volume": audio["volume"],
                "message": f"{audio['device']} Sonos volume {audio['volume']}%",
            }
        )
    except AudioPreparingError as exc:
        return api_error(exc, 409)
    except Exception as exc:
        return api_error(f"Sonos volume failed: {exc}", 502)


@app.route("/api/rate", methods=["POST"])
def api_rate():
    try:
        value = float(request.form.get("value", ""))
        if not math.isfinite(value) or not 0.5 <= value <= 2:
            raise ValueError
    except (TypeError, ValueError):
        return api_error("rate must be from 0.5 to 2", 400)
    try:
        actual = active_service().player.set_rate(value)
        return jsonify({"ok": True, "rate": actual, "message": f"Playback speed {actual:g}×"})
    except Exception as exc:
        return api_error(f"VLC speed failed: {exc}", 502)


@app.route("/api/fullscreen", methods=["POST"])
def api_fullscreen():
    try:
        value = active_service().player.toggle_fullscreen()
        return jsonify({"ok": True, "fullscreen": value, "message": "Fullscreen on" if value else "Fullscreen off"})
    except RuntimeError as exc:
        return api_error(exc, 409)
    except Exception as exc:
        return api_error(f"fullscreen control failed: {exc}", 502)


@app.route("/api/sleep", methods=["POST"])
def api_sleep():
    try:
        minutes = int(request.form.get("minutes", ""))
    except (TypeError, ValueError):
        return api_error("sleep timer must be a whole number of minutes", 400)
    try:
        return jsonify(active_service().set_sleep_timer(minutes))
    except ValueError as exc:
        return api_error(exc, 400)


@app.route("/api/progress", methods=["POST"])
def api_progress():
    item_id = request.form.get("item", "")
    action = request.form.get("action", "")
    service = active_service()
    item = service.library.items.get(item_id)
    if not item:
        return api_error("unknown media id", 404)
    if action == "clear":
        changed = service.update_progress(item, action)
        message = "Progress cleared" if changed else "Progress was already clear"
    elif action == "watched":
        service.update_progress(item, action)
        message = "Marked watched"
    elif action == "unwatched":
        service.update_progress(item, action)
        message = "Marked unwatched"
    else:
        return api_error("action must be clear, watched, or unwatched", 400)
    return jsonify({"ok": True, "message": message})


APP_ICON = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512">
<rect width="512" height="512" rx="112" fill="#111820"/>
<rect x="78" y="116" width="356" height="280" rx="40" fill="#263746" stroke="#8ed3c7" stroke-width="18"/>
<path d="M230 194 338 256 230 318Z" fill="#f6c76d"/>
<path d="M133 92v48M205 92v48M307 92v48M379 92v48" stroke="#f6c76d" stroke-width="18" stroke-linecap="round"/>
</svg>"""


@app.route("/manifest.webmanifest")
def manifest():
    response = jsonify(
        {
            "name": "Van Movies & TV",
            "short_name": "Movies & TV",
            "id": "/",
            "start_url": "/",
            "scope": "/",
            "display": "standalone",
            "background_color": "#0b1117",
            "theme_color": "#111820",
            "icons": [{"src": "/app-icon.svg", "sizes": "any", "type": "image/svg+xml"}],
        }
    )
    response.mimetype = "application/manifest+json"
    return response


@app.route("/app-icon.svg")
def app_icon():
    response = app.response_class(APP_ICON, mimetype="image/svg+xml")
    response.headers["Cache-Control"] = "public, max-age=86400"
    return response


@app.route("/")
def index():
    return render_template("video_library.html")


if __name__ == "__main__":
    os.environ.setdefault("DISPLAY", DISPLAY)
    os.environ.setdefault("XDG_RUNTIME_DIR", RUNTIME_DIR)
    os.environ.setdefault("DBUS_SESSION_BUS_ADDRESS", SESSION_BUS)
    service = active_service()
    service.start()
    app.run(host="0.0.0.0", port=PORT, threaded=True)
