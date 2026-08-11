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

try:
    from .video_asset_catalog import (
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
except ImportError:  # Direct execution from the Pi's flat deployment directory.
    from video_asset_catalog import (  # type: ignore[no-redef]
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


PORT = int(os.environ.get("VAN_VIDEO_PORT", "8789"))
STATE_PATH = os.path.expanduser(
    os.environ.get(
        "VAN_VIDEO_STATE_PATH",
        "~/.local/share/van-video-library/progress.sqlite3",
    )
)
LEGACY_POSITIONS_PATH = os.path.expanduser(
    os.environ.get("VAN_VIDEO_LEGACY_POSITIONS", "~/vlc-positions.txt")
)
SCAN_INTERVAL = float(os.environ.get("VAN_VIDEO_SCAN_INTERVAL", "900"))
POLL_INTERVAL = float(os.environ.get("VAN_VIDEO_POLL_INTERVAL", "3"))
RESUME_REWIND = float(os.environ.get("VAN_VIDEO_RESUME_REWIND", "12"))
MIN_CONTINUE_POSITION = float(os.environ.get("VAN_VIDEO_MIN_CONTINUE", "30"))
WATCHED_FRACTION = float(os.environ.get("VAN_VIDEO_WATCHED_FRACTION", "0.92"))
VLC_FIXED_VOLUME = float(os.environ.get("VAN_VIDEO_VLC_FIXED_VOLUME", "1.0"))
if not math.isfinite(VLC_FIXED_VOLUME) or not 0 <= VLC_FIXED_VOLUME <= 1.25:
    raise ValueError("VAN_VIDEO_VLC_FIXED_VOLUME must be from 0 to 1.25")

REAR_SONOS_UIDS = tuple(
    value.strip()
    for value in os.environ.get(
        "VAN_VIDEO_REAR_SONOS_UIDS",
        "RINCON_7828CA20F21A01400,RINCON_7828CA20F1DA01400",
    ).split(",")
    if value.strip()
)
SONOS_DISCOVERY_TTL = float(os.environ.get("VAN_VIDEO_SONOS_DISCOVERY_TTL", "10"))
ROOM_PREPARE_TIMEOUT = float(
    os.environ.get("VAN_VIDEO_ROOM_PREPARE_TIMEOUT", "45")
)
if not math.isfinite(ROOM_PREPARE_TIMEOUT) or ROOM_PREPARE_TIMEOUT <= 0:
    raise ValueError("VAN_VIDEO_ROOM_PREPARE_TIMEOUT must be positive")

DISPLAY = os.environ.get("VAN_VIDEO_DISPLAY", ":0")
RUNTIME_DIR = os.environ.get("VAN_VIDEO_XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
SESSION_BUS = os.environ.get(
    "VAN_VIDEO_SESSION_BUS", f"unix:path={RUNTIME_DIR}/bus"
)
PLAYER_UNIT = os.environ.get("VAN_VIDEO_PLAYER_UNIT", "van-video-player.service")

VLC = os.environ.get("VAN_VIDEO_VLC", "/usr/bin/vlc")
SYSTEMD_RUN = os.environ.get("VAN_VIDEO_SYSTEMD_RUN", "/usr/bin/systemd-run")
SYSTEMCTL = os.environ.get("VAN_VIDEO_SYSTEMCTL", "/usr/bin/systemctl")
XSET = os.environ.get("VAN_VIDEO_XSET", "/usr/bin/xset")
SNS = os.environ.get("VAN_VIDEO_SONOS_SETUP", "/home/pi/sns.sh")
MKVMERGE = os.environ.get("VAN_VIDEO_MKVMERGE", "/usr/bin/mkvmerge")
PKILL = os.environ.get("VAN_VIDEO_PKILL", "/usr/bin/pkill")

QBITTORRENT_URL = os.environ.get(
    "VAN_VIDEO_QBITTORRENT_URL", "http://127.0.0.1:8080"
)
QBITTORRENT_CLIENT_ID = os.environ.get("VAN_VIDEO_QBITTORRENT_CLIENT_ID", "vanpi")
QBITTORRENT_TIMEOUT = float(os.environ.get("VAN_VIDEO_QBITTORRENT_TIMEOUT", "3"))
QBITTORRENT_TEMP_ROOTS = tuple(
    path
    for path in os.environ.get(
        "VAN_VIDEO_QBITTORRENT_TEMP_ROOTS",
        os.pathsep.join(
            (
                "/mnt/movingparts/torrent/incomplete",
                "/mnt/bigboi/mp_backup/torrent/incomplete",
            )
        ),
    ).split(os.pathsep)
    if path
)
QBITTORRENT_FINAL_ROOTS = tuple(
    path
    for path in os.environ.get(
        "VAN_VIDEO_QBITTORRENT_FINAL_ROOTS",
        os.pathsep.join(
            (
                "/mnt/movingparts/torrent",
                "/mnt/bigboi/mp_backup/torrent",
            )
        ),
    ).split(os.pathsep)
    if path
)

VIDEO_EXTENSIONS = {
    ".mkv",
    ".avi",
    ".mp4",
    ".m4v",
    ".mov",
    ".webm",
    ".mpg",
    ".mpeg",
    ".ts",
}
CATEGORY_PRIORITY = {"TV": 0, "Movies": 1, "Documentaries": 2, "New": 3}
EPISODE_RE = re.compile(
    r"(?i)(?:^|[._\s-])S(?P<season>\d{1,2})[._\s-]*E(?P<episode>\d{1,3})"
)
X_EPISODE_RE = re.compile(
    r"(?i)(?:^|[._\s-])(?P<season>\d{1,2})x(?P<episode>\d{1,3})"
)
PART_EPISODE_RE = re.compile(
    r"(?i)(?:^|[._\s-])Part[._\s-]*(?P<episode>\d{1,3})(?:$|[._\s-])"
)
E_ONLY_EPISODE_RE = re.compile(
    r"(?i)(?:^|[._\s-])E(?P<episode>\d{1,3})(?:E\d{1,3})?(?:$|[._\s-])"
)
SEASON_PATH_RE = re.compile(r"(?i)(?:^|[/\\])S(?P<season>\d{1,2})(?:[/\\]|$)")
BARE_EPISODE_RE = re.compile(
    r"(?i)^(?:[._\s-]*)(?P<episode>\d{1,3})(?:$|[._\s-])"
)
FALLBACK_EPISODE_RE = re.compile(r"^(?P<code>\d{3})(?:\s|$)")
FEATURE_YEAR_RE = re.compile(r"(?<!\d)(?P<year>(?:19|20)\d{2})(?!\d)")
LEGACY_LINE_RE = re.compile(
    r"^(?P<rel>/.*?)\s+(?P<micros>\d+)\s+\d{1,3}:\d{2}:\d{2}\s*$"
)

MPRIS_NAME = "org.mpris.MediaPlayer2.vlc"
MPRIS_PATH = "/org/mpris/MediaPlayer2"
MPRIS_ROOT = "org.mpris.MediaPlayer2"
MPRIS_PLAYER = "org.mpris.MediaPlayer2.Player"
DBUS_PROPERTIES = "org.freedesktop.DBus.Properties"

DEFAULT_FAVORITES = (
    ("The Simpsons", False),
    ("South Park", False),
    ("Rick and Morty", True),
    ("Metalocalypse", True),
    ("Gospel", True),
)

_UNSET = object()


app = Flask(__name__)


def natural_key(value: str) -> list[Any]:
    return [int(part) if part.isdigit() else part.casefold() for part in re.split(r"(\d+)", value)]


def normalized(value: str) -> str:
    value = value.casefold().replace("&", " and ")
    return " ".join(re.sub(r"[^a-z0-9]+", " ", value).split())


def canonical_series(value: str) -> str:
    name = normalized(value)
    name = re.sub(r"^(?:the)\s+", "", name)
    name = re.sub(r"\s+(?:19|20)\d{2}$", "", name)
    return name


def clean_name(value: str) -> str:
    if Path(value).suffix.casefold() in VIDEO_EXTENSIONS:
        value = str(Path(value).with_suffix(""))
    return " ".join(re.sub(r"[._]+", " ", value).split())


def stable_id(key: str) -> str:
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


def seconds_text(seconds: float | int | None) -> str:
    total = max(0, int(seconds or 0))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


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
class MediaItem:
    key: str
    id: str
    title: str
    path: str
    real_path: str
    rel_path: str
    media_type: str
    source: str
    series_kind: str | None = None
    year: int | None = None
    series: str | None = None
    season: int | None = None
    episode: int | None = None
    episode_title: str | None = None
    mtime: float = 0.0
    categories: set[str] = field(default_factory=set)
    aliases: set[str] = field(default_factory=set)
    rank: tuple[Any, ...] = field(default_factory=tuple)
    asset_id: str | None = None
    work_id: str | None = None

    @property
    def episode_code(self) -> str | None:
        if self.season is None or self.episode is None:
            return None
        return f"S{self.season:02d}E{self.episode:02d}"

    @property
    def new(self) -> bool:
        return "New" in self.categories

    def as_dict(self, progress: dict[str, Any] | None = None) -> dict[str, Any]:
        value = {
            "id": self.id,
            "title": self.title,
            "type": self.media_type,
            "year": self.year,
            "series": self.series,
            "season": self.season,
            "episode": self.episode,
            "episode_code": self.episode_code,
            "episode_title": self.episode_title,
            "new": self.new,
            "categories": sorted(self.categories),
            "source": self.source,
            "series_kind": self.series_kind,
            "updated": int(self.mtime),
        }
        if progress:
            value["progress"] = public_progress(progress)
        return value


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


def parse_candidate(category: str, relative: str, link_path: str, real_path: str, source: str) -> MediaItem:
    parts = Path(relative).parts
    content_parts = parts[1:] if parts and parts[0] == category else parts
    basename = parts[-1]
    basename_match = EPISODE_RE.search(basename) or X_EPISODE_RE.search(basename)
    path_match = None
    if category == "TV" and not basename_match:
        for segment in reversed(Path(real_path).parts[:-1]):
            path_match = EPISODE_RE.search(segment) or X_EPISODE_RE.search(segment)
            if path_match:
                break
    match = basename_match or path_match
    part_match = PART_EPISODE_RE.search(basename)
    part_is_episode = bool(part_match and category in ("TV", "Documentaries"))
    e_only_match = E_ONLY_EPISODE_RE.search(basename) if category == "TV" else None
    bare_match = None
    if category == "TV" and len(content_parts) > 1:
        series_prefix = content_parts[0]
        if basename.casefold().startswith(series_prefix.casefold()):
            bare_match = BARE_EPISODE_RE.search(basename[len(series_prefix) :])
    season = episode = None
    episode_title = None
    series = None

    series_kind = "documentary" if category == "Documentaries" else "tv"

    if match:
        season = int(match.group("season"))
        episode = int(match.group("episode"))
        prefix = basename[: match.start()].strip(" ._-") if basename_match else ""
        suffix = basename[match.end() :].strip(" ._-") if basename_match else ""
        series = clean_name(
            content_parts[0]
            if category == "TV" and len(content_parts) > 1
            else prefix
        )
        episode_title = clean_name(suffix) or None
    elif e_only_match:
        season_matches = list(SEASON_PATH_RE.finditer(real_path))
        season = int(season_matches[-1].group("season")) if season_matches else None
        episode = int(e_only_match.group("episode"))
        series = clean_name(content_parts[0] if len(content_parts) > 1 else basename)
        suffix = basename[e_only_match.end() :].strip(" ._-")
        episode_title = clean_name(suffix) or None
    elif bare_match:
        season = 1
        episode = int(bare_match.group("episode"))
        offset = len(content_parts[0])
        suffix = basename[offset + bare_match.end() :].strip(" ._-")
        series = clean_name(content_parts[0])
        episode_title = clean_name(suffix) or None
    elif part_is_episode:
        season = 1
        assert part_match is not None
        episode = int(part_match.group("episode"))
        prefix = basename[: part_match.start()].strip(" ._-")
        suffix = basename[part_match.end() :].strip(" ._-")
        series = clean_name(
            content_parts[0]
            if category == "TV" and len(content_parts) > 1
            else prefix
        )
        episode_title = clean_name(suffix) or None
    elif category == "TV":
        series = clean_name(content_parts[0] if len(content_parts) > 1 else basename)
        basename_words = normalized(clean_name(basename))
        series_words = normalized(series)
        remainder = (
            basename_words[len(series_words) :].strip()
            if series_words and basename_words.startswith(series_words)
            else basename_words
        )
        fallback = FALLBACK_EPISODE_RE.search(remainder)
        if fallback:
            code = fallback.group("code")
            season, episode = int(code[:-2]), int(code[-2:])

    is_episode = bool(category == "TV" or match or part_is_episode or bare_match)
    if is_episode:
        series = series or clean_name(
            content_parts[0] if len(content_parts) > 1 else basename
        )
        code_key = f"s{season}:e{episode}" if season is not None and episode is not None else normalized(relative)
        key = f"episode:{series_kind}:{canonical_series(series)}:{code_key}"
        title = episode_title or (
            f"{series} {match.group(0).strip(' ._-')}"
            if match
            else clean_name(basename)
        )
        media_type = "episode"
        year = None
    else:
        title = clean_name(basename)
        year_matches = list(FEATURE_YEAR_RE.finditer(title))
        year_match = year_matches[-1] if year_matches else None
        without_year = (
            " ".join((title[: year_match.start()] + " " + title[year_match.end() :]).split())
            if year_match
            else title
        )
        if year_match and without_year:
            year = int(year_match.group("year"))
            title = without_year
        else:
            year = None
        media_type = "documentary" if category == "Documentaries" else "movie"
        key = f"feature:{normalized(title)}:{year or ''}"
        series_kind = None

    rel_path = "/" + relative.replace(os.sep, "/")
    try:
        mtime = os.path.getmtime(real_path)
    except OSError:
        mtime = 0.0
    rank = (
        CATEGORY_PRIORITY.get(category, 99),
        len(content_parts),
        normalized(relative),
    )
    return MediaItem(
        key=key,
        id=stable_id(key),
        title=title,
        path=link_path,
        real_path=real_path,
        rel_path=rel_path,
        media_type=media_type,
        source=source,
        series_kind=series_kind,
        year=year,
        series=series,
        season=season,
        episode=episode,
        episode_title=episode_title,
        mtime=mtime,
        categories={category},
        aliases={rel_path},
        rank=rank,
    )


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
                    candidate = parse_candidate(category, relative, link_path, real_path, source.name)
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


def public_progress(value: dict[str, Any]) -> dict[str, Any]:
    position = float(value.get("position") or 0)
    duration = float(value.get("duration") or 0)
    return {
        "position": position,
        "position_text": seconds_text(position),
        "duration": duration,
        "duration_text": seconds_text(duration) if duration else None,
        "fraction": min(1.0, position / duration) if duration else None,
        "updated": int(value.get("updated") or 0),
        "finished": bool(value.get("finished")),
        "play_count": int(value.get("play_count") or 0),
    }


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


class AudioPreparingError(RuntimeError):
    """Raised when rear-room setup would overwrite a Sonos volume change."""


class RoomPreparationError(RuntimeError):
    """Raised when rear_movie cannot be completed before playback resumes."""


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


class VideoService:
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
        self.sleep_deadline: float | None = None
        self.last_error: str | None = None
        self.last_saved_key: str | None = None
        self.last_saved_position: float | None = None
        self.last_saved_duration: float | None = None
        self.last_saved_at = 0.0
        self.last_saved_state: str | None = None
        self.audio_was_preparing = False
        self.last_audio_volume: int | None = None
        self.active_session_id: str | None = None
        self.active_asset_id: str | None = None
        self.active_work_id: str | None = None
        self.active_track_id: str | None = None
        self.active_item: MediaItem | None = None
        self.active_legacy_key: str | None = None
        self.active_title: str | None = None
        self.active_rel_path: str | None = None
        self.active_complete = True
        self.last_snapshot: dict[str, Any] | None = None
        self.pending_explicit_launch: dict[str, Any] | None = None
        self.session_recovery_pending = False
        self.session_recovery_error: str | None = None
        self.identity_error: str | None = None

    @staticmethod
    def _torrent_metadata(resolved: ResolvedTorrentFile) -> dict[str, Any]:
        return {
            "torrent_name": resolved.torrent_name,
            "torrent_state": resolved.torrent_state,
            "relative_path": resolved.relative_path,
            "progress": resolved.progress,
            "piece_range": list(resolved.piece_range),
            "priority": resolved.priority,
            "availability": resolved.availability,
            "complete": resolved.complete,
        }

    def _record_torrent_result(
        self,
        resolved: ResolvedTorrentFile,
        *,
        preferred_asset_id: str | None = None,
    ) -> str:
        if self.catalog is None:
            raise RuntimeError("media identity catalog is unavailable")
        identity = resolved.identity
        metadata = self._torrent_metadata(resolved)
        matched_path = resolved.matched_path
        asset_id = self.catalog.resolve_or_create_torrent_asset(
            client_id=identity.client_id,
            torrent_id=identity.torrent_id,
            file_index=identity.file_index,
            info_hash_v1=resolved.infohash_v1,
            info_hash_v2=resolved.infohash_v2,
            expected_size=resolved.expected_size,
            path=matched_path,
            metadata=metadata,
            preferred_asset_id=preferred_asset_id,
        )
        candidates = tuple(dict.fromkeys((*resolved.temporary_paths, *resolved.final_paths)))
        for candidate in candidates:
            if os.path.isfile(candidate):
                for observed_path in dict.fromkeys(
                    (candidate, os.path.realpath(candidate))
                ):
                    self.catalog.record_location(
                        asset_id,
                        observed_path,
                        location_kind=(
                            "torrent-final"
                            if candidate in resolved.final_paths and resolved.complete
                            else "torrent-temporary"
                        ),
                        source=identity.client_id,
                        metadata={"complete": resolved.complete},
                    )
            elif self.catalog.resolve_path(candidate) == asset_id:
                self.catalog.retire_location(candidate)
        return asset_id

    def _ensure_asset_work(
        self,
        asset_id: str,
        *,
        item: MediaItem | None = None,
        title: str | None = None,
    ) -> str:
        if self.catalog is None:
            raise RuntimeError("media identity catalog is unavailable")
        asset = self.catalog.lookup_asset(asset_id)
        if asset is None:
            raise RuntimeError("media asset disappeared from the catalog")
        work_id = str(asset["work_id"]) if asset.get("work_id") else None
        preferred_work = item.work_id if item is not None else None
        if work_id is None and preferred_work is not None:
            self.catalog.bind_work(asset_id, preferred_work)
            work_id = preferred_work
        if work_id is None:
            work_id = self.catalog.create_work(
                item.media_type if item is not None else "local-media",
                title=(item.title if item is not None else title),
                year=item.year if item is not None else None,
                series=item.series if item is not None else None,
                season=item.season if item is not None else None,
                episode=item.episode if item is not None else None,
                metadata={"created_from": "library" if item is not None else "play-local"},
            )
            self.catalog.bind_work(asset_id, work_id)
        return work_id

    def _resolve_asset_for_path(
        self,
        path: str,
        *,
        item: MediaItem | None = None,
        use_qbittorrent: bool = True,
    ) -> tuple[str | None, str | None, bool]:
        """Resolve identity as a best effort; failure never blocks local playback."""

        if self.catalog is None:
            return None, None, not self._path_is_probably_incomplete(path)
        requested_path = os.path.abspath(path)
        normalized_path = os.path.realpath(requested_path)
        complete_without_qbittorrent = not self._path_is_probably_incomplete(
            normalized_path
        )
        try:
            seed_asset_id: str | None = None
            if (
                item is not None
                and item.asset_id is not None
                and os.path.realpath(item.real_path) == normalized_path
            ):
                seed_asset_id = item.asset_id
            if seed_asset_id is None:
                seed_asset_id = self.catalog.resolve_path(normalized_path)
            # Validate current stat identity even when this pathname has been
            # seen before.  A reused name must not inherit another file's
            # playhead; a v1-only seed with no physical evidence may adopt it.
            preferred = self.catalog.resolve_or_create_provisional_file(
                normalized_path,
                preferred_asset_id=seed_asset_id,
                metadata={"created_from": "library" if item else "play-local"},
            )
        except (CatalogError, sqlite3.Error, OSError, ValueError) as exc:
            self.identity_error = f"media identity tracking degraded: {exc}"
            return None, None, complete_without_qbittorrent

        resolved: ResolvedTorrentFile | None = None
        qb_warning: str | None = None
        if use_qbittorrent and self.qbittorrent is not None:
            for candidate in dict.fromkeys((requested_path, normalized_path)):
                try:
                    resolved = self.qbittorrent.resolve_path(candidate)
                    if resolved is not None:
                        qb_warning = None
                        break
                except QbittorrentError as exc:
                    qb_warning = str(exc)
                    continue
                except Exception as exc:
                    qb_warning = f"qBittorrent identity lookup failed: {exc}"
                    continue
        try:
            if resolved is not None:
                try:
                    asset_id = self._record_torrent_result(
                        resolved, preferred_asset_id=preferred
                    )
                except CatalogConflict:
                    # The path/parser evidence was stale, but qB's torrent-file
                    # locator is exact. Resolve that asset without merging it.
                    asset_id = self._record_torrent_result(resolved)
                complete = resolved.complete
            else:
                asset_id = preferred
                self.catalog.record_location(
                    asset_id,
                    normalized_path,
                    location_kind="library" if item is not None else "file",
                )
                complete = complete_without_qbittorrent
            work_id = self._ensure_asset_work(
                asset_id,
                item=item,
                title=item.title if item is not None else clean_name(Path(path).name),
            )
            if item is not None and os.path.realpath(item.real_path) == normalized_path:
                item.asset_id = asset_id
                item.work_id = work_id
            self._apply_deferred_legacy_positions(
                asset_id,
                work_id,
                requested_path=requested_path,
                normalized_path=normalized_path,
                item=item,
                torrent=resolved,
            )
            self.identity_error = qb_warning
            return asset_id, work_id, complete
        except (CatalogError, sqlite3.Error, OSError, ValueError) as exc:
            self.identity_error = f"media identity tracking degraded: {exc}"
            return None, None, complete_without_qbittorrent

    def _asset_identity_label(self, asset_id: str | None) -> str:
        if self.catalog is None or asset_id is None:
            return "unavailable"
        try:
            asset = self.catalog.lookup_asset(asset_id)
        except (CatalogError, sqlite3.Error, OSError, ValueError):
            return "unavailable"
        return "torrent" if asset and asset.get("asset_kind") == "torrent" else "catalog"

    def _legacy_path_evidence(
        self,
        *,
        requested_path: str,
        normalized_path: str,
        item: MediaItem | None,
        torrent: ResolvedTorrentFile | None,
    ) -> set[str]:
        evidence = {requested_path, normalized_path}
        candidates = [requested_path, normalized_path]
        if item is not None:
            evidence.update((item.path, item.real_path, item.rel_path, *item.aliases))
            candidates.extend((item.path, item.real_path))
        if torrent is not None:
            torrent_paths = (
                torrent.matched_path,
                *torrent.temporary_paths,
                *torrent.final_paths,
            )
            evidence.update(torrent_paths)
            candidates.extend(torrent_paths)

        temp_roots = tuple(getattr(self.qbittorrent, "temp_roots", ()) or ())
        temp_roots = tuple(dict.fromkeys((*temp_roots, *QBITTORRENT_TEMP_ROOTS)))
        final_roots = tuple(getattr(self.qbittorrent, "final_roots", ()) or ())
        final_roots = tuple(dict.fromkeys((*final_roots, *QBITTORRENT_FINAL_ROOTS)))
        for candidate in candidates:
            candidate_path = os.path.abspath(candidate)
            for marker, roots in (("incomplete", temp_roots), (None, final_roots)):
                for root in roots:
                    root_path = os.path.abspath(os.fspath(root))
                    try:
                        if os.path.commonpath((candidate_path, root_path)) != root_path:
                            continue
                        relative = os.path.relpath(candidate_path, root_path)
                    except (OSError, TypeError, ValueError):
                        continue
                    if relative == os.curdir or relative.startswith(os.pardir):
                        continue
                    prefix = f"/{marker}" if marker else ""
                    evidence.add(f"{prefix}/{relative}".replace(os.sep, "/"))
        return {value.replace(os.sep, "/") for value in evidence if value}

    def _apply_deferred_legacy_positions(
        self,
        asset_id: str,
        work_id: str | None,
        *,
        requested_path: str,
        normalized_path: str,
        item: MediaItem | None,
        torrent: ResolvedTorrentFile | None,
    ) -> None:
        """Resolve raw legacy rows only when exact path evidence becomes known."""

        if self.catalog is None:
            return
        evidence = self._legacy_path_evidence(
            requested_path=requested_path,
            normalized_path=normalized_path,
            item=item,
            torrent=torrent,
        )
        for record in self.catalog.list_import_records(action="unresolved"):
            if record.get("source_kind") != "legacy-vlc-position-log":
                continue
            try:
                raw = json.loads(record.get("raw_json") or "{}")
                legacy_path = str(raw["relative_path"]).replace(os.sep, "/")
                micros = int(raw["position_microseconds"])
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                continue
            if legacy_path not in evidence:
                continue
            applied = self.catalog.apply_imported_playhead(
                asset_id,
                position=micros / 1_000_000,
                source_updated=float(
                    record.get("source_updated") or record.get("imported_at") or self.clock()
                ),
                event_key=f"legacy-raw:{record['import_record_id']}",
                source=str(record["source_ref"]),
            )
            self.catalog.record_import(
                str(record["import_id"]),
                source_key=str(record["source_key"]),
                action="applied" if applied else "matched-stale",
                content_digest=record.get("content_digest"),
                source_updated=record.get("source_updated"),
                asset_id=asset_id,
                work_id=work_id,
                raw=raw,
            )

    def _path_is_probably_incomplete(self, path: str) -> bool:
        """Fail safe on partial media even when qBittorrent is unavailable.

        Configured qB temporary roots are authoritative.  The directory-name
        fallback keeps injected/offline clients and legacy ``playp`` paths safe
        without making torrent identity a prerequisite for playback.
        """

        normalized = os.path.realpath(os.path.abspath(path))
        roots = tuple(getattr(self.qbittorrent, "temp_roots", ()) or ())
        roots = tuple(dict.fromkeys((*roots, *QBITTORRENT_TEMP_ROOTS)))
        for root in roots:
            try:
                normalized_root = os.path.realpath(os.path.abspath(os.fspath(root)))
                if os.path.commonpath((normalized, normalized_root)) == normalized_root:
                    return True
            except (OSError, TypeError, ValueError):
                continue
        return "incomplete" in {part.casefold() for part in Path(normalized).parts}

    def _legacy_progress_for_asset(
        self, asset_id: str, work_id: str | None = None
    ) -> dict[str, Any] | None:
        if self.catalog is None:
            return None
        if work_id is None:
            asset = self.catalog.lookup_asset(asset_id)
            work_id = str(asset["work_id"]) if asset and asset.get("work_id") else None
        work = self.catalog.get_work_watch_state(work_id) if work_id else None
        state = self.catalog.get_asset_state(asset_id)
        if state is None and work is None:
            return None
        state = state or {
            "position": 0,
            "duration": 0,
            "completed": 0,
            "play_count": 0,
            "updated_at": float((work or {}).get("updated_at") or self.clock()),
        }
        override = work.get("watched_override") if work else None
        finished = (
            bool(work.get("watched"))
            if work is not None
            else bool(state.get("completed"))
        )
        return {
            "position": float(state.get("position") or 0),
            "duration": float(state.get("duration") or 0),
            "updated": float(
                max(state.get("updated_at") or 0, (work or {}).get("updated_at") or 0)
            ),
            "finished": int(finished),
            "finished_override": override,
            "play_count": int(
                state.get("play_count") or (work or {}).get("play_count") or 0
            ),
        }

    def _project_item_progress(
        self,
        item: MediaItem,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> dict[str, Any] | None:
        if self.catalog is None or item.asset_id is None:
            return self.store.get(item.key)
        progress = self._legacy_progress_for_asset(item.asset_id, item.work_id)
        if progress is None:
            return self.store.get(item.key)
        self.catalog.project_v1_progress(
            item.key,
            position=progress["position"],
            duration=progress["duration"],
            updated=progress["updated"],
            finished=bool(progress["finished"]),
            finished_override=(
                None
                if progress.get("finished_override") is None
                else bool(progress["finished_override"])
            ),
            play_count=progress["play_count"],
            title=item.title,
            rel_path=item.rel_path,
            asset_id=item.asset_id,
            connection=connection,
        )
        return {**progress, "title": item.title, "rel_path": item.rel_path}

    def _bind_library_item(self, item: MediaItem) -> None:
        if self.catalog is None:
            return
        legacy_asset_id = self.catalog.resolve_legacy_key(item.key)
        # A current exact target is stronger evidence than a parser key.  This
        # avoids silently turning a replacement encode into the old asset while
        # still letting the legacy key seed identity on the first v2 scan.
        path_asset_id = self.catalog.resolve_path(item.real_path)
        if path_asset_id is None:
            path_asset_id = self.catalog.resolve_path(item.path)
        asset_id = self.catalog.resolve_or_create_provisional_file(
            item.real_path,
            preferred_asset_id=path_asset_id or legacy_asset_id,
            metadata={"created_from": "library", "source": item.source},
        )
        if legacy_asset_id is not None and legacy_asset_id != asset_id:
            legacy_asset = self.catalog.lookup_asset(legacy_asset_id)
            if legacy_asset and legacy_asset.get("work_id"):
                item.work_id = str(legacy_asset["work_id"])
        work_id = self._ensure_asset_work(asset_id, item=item)
        item.asset_id = asset_id
        item.work_id = work_id
        legacy_asset = (
            self.catalog.lookup_asset(legacy_asset_id)
            if legacy_asset_id is not None and legacy_asset_id != asset_id
            else None
        )
        if legacy_asset is not None and legacy_asset.get("asset_kind") == "legacy-v1":
            self.catalog.transfer_playhead(
                legacy_asset_id,
                asset_id,
                reason="legacy-v1 parser row attached to exact library asset",
            )
        try:
            self.catalog.bind_legacy_key(
                asset_id,
                item.key,
                metadata={"title": item.title, "rel_path": item.rel_path},
            )
        except CatalogConflict as exc:
            # v1 can project only one preferred variant.  Preserve the older
            # binding for rollback instead of implicitly merging exact assets.
            self.identity_error = f"legacy media key needs review: {exc}"
        self.catalog.record_location(
            asset_id, item.real_path, location_kind="library-target", source=item.source
        )
        self.catalog.record_location(
            asset_id, item.path, location_kind="library-link", source=item.source
        )
        for alias in item.aliases:
            self.catalog.record_alias(
                asset_id,
                alias,
                namespace="library-relative-path",
                provenance=item.source,
            )
        self.catalog.record_alias(
            asset_id,
            item.key,
            namespace="parser-key",
            provenance=item.source,
        )
        self._apply_deferred_legacy_positions(
            asset_id,
            work_id,
            requested_path=item.path,
            normalized_path=item.real_path,
            item=item,
            torrent=None,
        )
        self._project_item_progress(item)

    def _retry_session_recovery(self) -> None:
        """Reconcile rollback intent before closing or replacing old sessions."""

        if self.catalog is None or not self.session_recovery_pending:
            return
        with self.control_lock:
            if not self.session_recovery_pending:
                return
            try:
                self.catalog.reconcile_v1_progress()
            except (CatalogError, sqlite3.Error, OSError, ValueError) as exc:
                self.session_recovery_error = (
                    "could not reconcile rollback progress; prior session "
                    f"recovery deferred: {exc}"
                )
                raise
            try:
                self.catalog.recover_open_sessions()
            except (CatalogError, sqlite3.Error, OSError, ValueError) as exc:
                self.session_recovery_error = (
                    f"could not close prior playback sessions: {exc}"
                )
                raise
            self.session_recovery_pending = False
            self.session_recovery_error = None

    def _sync_library_identities(self) -> None:
        if self.catalog is None:
            return
        self._retry_session_recovery()
        self.catalog.reconcile_v1_progress()
        items, _shows = self.library.snapshot()
        # A library can contain thousands of aliases.  Nested catalog methods
        # reuse this caller-owned transaction, avoiding thousands of fsyncs on
        # the Pi while retaining per-item idempotence.
        with self.catalog.transaction():
            for item in items:
                try:
                    self._bind_library_item(item)
                except (CatalogError, sqlite3.Error, OSError, ValueError) as exc:
                    self.identity_error = f"could not catalog {item.title}: {exc}"
        if self.active_asset_id is not None:
            with self.control_lock:
                matches = [
                    item for item in items if item.asset_id == self.active_asset_id
                ]
                if len(matches) == 1:
                    active = matches[0]
                    self.active_item = active
                    self.active_work_id = active.work_id
                    self.active_legacy_key = active.key
                    self.active_title = active.title
                    self.active_rel_path = active.rel_path

    def _progress_all(self) -> dict[str, dict[str, Any]]:
        progress = self.store.all()
        if self.catalog is None:
            return progress
        items, _shows = self.library.snapshot()
        for item in items:
            if item.asset_id is None:
                continue
            try:
                value = self._legacy_progress_for_asset(item.asset_id, item.work_id)
            except (CatalogError, sqlite3.Error, OSError, ValueError) as exc:
                self.identity_error = f"media identity tracking degraded: {exc}"
                continue
            if value is not None:
                progress[item.key] = {
                    **value,
                    "media_key": item.key,
                    "title": item.title,
                    "rel_path": item.rel_path,
                }
        return progress

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

    @staticmethod
    def _is_finished(position: float, duration: float) -> bool:
        if duration <= 0:
            return False
        return position / duration >= WATCHED_FRACTION or duration - position <= 90

    def _reset_save_throttle(self) -> None:
        self.last_saved_key = None
        self.last_saved_position = None
        self.last_saved_duration = None
        self.last_saved_state = None
        self.last_saved_at = 0.0

    @staticmethod
    def _launch_path_keys(path: Any) -> frozenset[str]:
        if not isinstance(path, str) or not path or "\x00" in path:
            return frozenset()
        try:
            absolute = os.path.abspath(os.path.expanduser(path))
            return frozenset((os.path.normpath(absolute), os.path.realpath(absolute)))
        except (OSError, TypeError, ValueError):
            return frozenset()

    def _remember_explicit_launch(
        self, path: str, snapshot: dict[str, Any]
    ) -> None:
        """Retain one explicit replay intent until its exact track is adopted."""

        if self.catalog is None:
            self.pending_explicit_launch = None
            return
        path_keys = self._launch_path_keys(path)
        if not path_keys:
            self.pending_explicit_launch = None
            return
        self.pending_explicit_launch = {
            "path_keys": path_keys,
            "track_id": (
                str(snapshot["track_id"])
                if snapshot.get("track_id") is not None
                else None
            ),
        }

    def _pending_explicit_launch_matches(self, snapshot: dict[str, Any]) -> bool:
        pending = self.pending_explicit_launch
        if pending is None:
            return False
        observed_paths = self._launch_path_keys(snapshot.get("path"))
        if not observed_paths.intersection(pending["path_keys"]):
            return False
        expected_track = pending.get("track_id")
        observed_track = snapshot.get("track_id")
        return not (
            expected_track is not None
            and observed_track is not None
            and str(observed_track) != expected_track
        )

    def _clear_pending_explicit_launch(self) -> None:
        self.pending_explicit_launch = None

    def _begin_catalog_session(
        self,
        *,
        asset_id: str,
        work_id: str | None,
        path: str,
        snapshot: dict[str, Any],
        item: MediaItem | None,
        complete: bool,
        clear_override: bool,
    ) -> None:
        if self.catalog is None:
            return
        self._retry_session_recovery()
        position = max(0.0, float(snapshot.get("position") or 0))
        with self.catalog.transaction() as db:
            if clear_override and work_id is not None:
                self.catalog.clear_work_watched_override(work_id, connection=db)
                self.catalog.set_work_watched(
                    work_id,
                    False,
                    manual=False,
                    asset_id=asset_id,
                    connection=db,
                )
            session_id = self.catalog.start_session(
                asset_id,
                position=position,
                reset_completed=clear_override,
                launch_path=path,
                player_instance=str(snapshot.get("track_id") or "vlc-mpris"),
                metadata={"complete_at_start": bool(complete)},
                connection=db,
            )
            if item is not None:
                self._project_item_progress(item, connection=db)
        self.active_session_id = session_id
        self.active_asset_id = asset_id
        self.active_work_id = work_id
        self.active_track_id = (
            str(snapshot["track_id"]) if snapshot.get("track_id") is not None else None
        )
        self.active_item = item
        self.active_legacy_key = item.key if item is not None else None
        self.active_title = item.title if item is not None else clean_name(Path(path).name)
        self.active_rel_path = item.rel_path if item is not None else None
        self.active_complete = bool(complete)
        self.last_snapshot = dict(snapshot)
        self._reset_save_throttle()

    def _finish_active_session(
        self,
        reason: str,
        snapshot: dict[str, Any] | None = None,
    ) -> bool:
        if self.catalog is not None and self.active_session_id is not None:
            try:
                value = snapshot or self.last_snapshot or {}
                position = max(0.0, float(value.get("position") or 0))
                duration = max(0.0, float(value.get("duration") or 0))
                completed = self.active_complete and self._is_finished(
                    position, duration
                )
                with self.catalog.transaction() as db:
                    self.catalog.finish_session(
                        self.active_session_id,
                        reason=reason,
                        position=position,
                        duration=duration,
                        completed=completed,
                        connection=db,
                    )
                    if self.active_item is not None:
                        self._project_item_progress(self.active_item, connection=db)
            except (CatalogError, sqlite3.Error, OSError, ValueError) as exc:
                self.identity_error = f"could not finish playback session: {exc}"
                # Keep the pinned session in memory when the durable finish
                # transaction fails.  A later healthy poll can retry the exact
                # same session instead of orphaning it until process restart.
                return False
        self.active_session_id = None
        self.active_asset_id = None
        self.active_work_id = None
        self.active_track_id = None
        self.active_item = None
        self.active_legacy_key = None
        self.active_title = None
        self.active_rel_path = None
        self.active_complete = True
        self.last_snapshot = None
        self._reset_save_throttle()
        return True

    def _checkpoint_active(
        self,
        snapshot: dict[str, Any],
        *,
        force: bool = False,
    ) -> None:
        if (
            self.catalog is None
            or self.active_session_id is None
            or self.active_asset_id is None
        ):
            return
        position = max(0.0, float(snapshot.get("position") or 0))
        duration = max(0.0, float(snapshot.get("duration") or 0))
        state = str(snapshot.get("state") or "").upper()
        now = self.clock()
        changed_position = (
            self.last_saved_position is None
            or abs(position - self.last_saved_position) >= 1
        )
        changed_duration = (
            self.last_saved_duration is None
            or abs(duration - self.last_saved_duration) >= 1
        )
        changed_state = state != self.last_saved_state
        due = now < self.last_saved_at or now - self.last_saved_at >= 10
        completed = self.active_complete and self._is_finished(position, duration)
        state_before = self.catalog.get_asset_state(self.active_asset_id) or {}
        became_finished = completed and not bool(state_before.get("completed"))
        if not (
            force
            or changed_state
            or became_finished
            or (due and (changed_position or changed_duration))
        ):
            self.last_snapshot = dict(snapshot)
            return
        with self.catalog.transaction() as db:
            self.catalog.checkpoint(
                self.active_session_id,
                position=position,
                duration=duration,
                completed=completed,
                playback_state=state,
                event_type="state_changed" if changed_state else "checkpoint",
                authoritative_order=True,
                connection=db,
            )
            if self.active_item is not None:
                self._project_item_progress(self.active_item, connection=db)
        self.last_saved_key = self.active_legacy_key or self.active_asset_id
        self.last_saved_position = position
        self.last_saved_duration = duration
        self.last_saved_state = state
        self.last_saved_at = now
        self.last_snapshot = dict(snapshot)

    def _record_legacy_snapshot(
        self, snapshot: dict[str, Any], *, force: bool = False
    ) -> MediaItem | None:
        """Original parser-key tracker retained as the instant rollback model."""

        item = self.library.item_for_path(snapshot.get("path"))
        if not item or not snapshot.get("available"):
            return item
        if snapshot.get("state") not in ("PLAYING", "PAUSED", "PAUSED_PLAYBACK", "STOPPED"):
            return item
        position = float(snapshot.get("position") or 0)
        duration = float(snapshot.get("duration") or 0)
        if position or duration:
            now = self.clock()
            previous = self.store.get(item.key)
            automatic_finished = self._is_finished(position, duration)
            override = (previous or {}).get("finished_override")
            finished = bool(override) if override is not None else automatic_finished
            changed_item = item.key != self.last_saved_key
            changed_position = (
                self.last_saved_position is None
                or abs(position - self.last_saved_position) >= 1
            )
            changed_duration = (
                self.last_saved_duration is None
                or abs(duration - self.last_saved_duration) >= 1
            )
            changed_state = str(snapshot.get("state") or "") != self.last_saved_state
            became_finished = finished and not (previous or {}).get("finished")
            due = now < self.last_saved_at or now - self.last_saved_at >= 10
            if force or changed_item or changed_state or became_finished or (
                due and (changed_position or changed_duration)
            ):
                self.store.record(
                    item.key,
                    position=position,
                    duration=duration,
                    finished=finished,
                    title=item.title,
                    rel_path=item.rel_path,
                )
                self.last_saved_key = item.key
                self.last_saved_position = position
                self.last_saved_duration = duration
                self.last_saved_state = str(snapshot.get("state") or "")
                self.last_saved_at = now
        return item

    def _record_snapshot(
        self, snapshot: dict[str, Any], *, force: bool = False
    ) -> MediaItem | None:
        # Browser requests and the background poller can arrive together.  The
        # active session fields are one state machine and must move atomically.
        with self.control_lock:
            try:
                return self._record_snapshot_locked(snapshot, force=force)
            except (CatalogError, sqlite3.Error, OSError, ValueError) as exc:
                self.identity_error = f"media identity tracking degraded: {exc}"
                return self._record_legacy_snapshot(snapshot, force=force)

    def _record_snapshot_locked(
        self, snapshot: dict[str, Any], *, force: bool = False
    ) -> MediaItem | None:
        if self.catalog is None:
            return self._record_legacy_snapshot(snapshot, force=force)

        available = bool(snapshot.get("available"))
        state = str(snapshot.get("state") or "").upper()
        path = snapshot.get("path")
        track_id = (
            str(snapshot["track_id"]) if snapshot.get("track_id") is not None else None
        )
        if not available:
            item = self.active_item
            self._clear_pending_explicit_launch()
            if self.active_session_id is not None:
                self._finish_active_session("player_offline")
            return item

        if (
            self.active_session_id is not None
            and track_id is not None
            and self.active_track_id is not None
            and track_id != self.active_track_id
        ):
            if not self._finish_active_session("track_changed"):
                # This snapshot belongs to the new track.  Until the old pinned
                # session can be closed, never checkpoint it into the old asset.
                return self.active_item

        if (
            self.active_session_id is None
            and state in ("PLAYING", "PAUSED", "PAUSED_PLAYBACK")
            and path
        ):
            clear_override = self._pending_explicit_launch_matches(snapshot)
            if self.pending_explicit_launch is not None and not clear_override:
                # A different path/track won the race before the pending launch
                # could be adopted.  Never apply its replay intent elsewhere.
                self._clear_pending_explicit_launch()
            item = self.library.item_for_path(str(path))
            asset_id, work_id, complete = self._resolve_asset_for_path(
                str(path), item=item
            )
            if asset_id is not None:
                self._begin_catalog_session(
                    asset_id=asset_id,
                    work_id=work_id,
                    path=str(path),
                    snapshot=snapshot,
                    item=item,
                    complete=complete,
                    clear_override=clear_override,
                )
                if clear_override:
                    self._clear_pending_explicit_launch()
        elif (
            self.active_session_id is None
            and state == "STOPPED"
            and self.pending_explicit_launch is not None
        ):
            self._clear_pending_explicit_launch()
        item = self.active_item or self.library.item_for_path(path)
        if self.active_session_id is not None and state in (
            "PLAYING",
            "PAUSED",
            "PAUSED_PLAYBACK",
            "STOPPED",
        ):
            self._checkpoint_active(snapshot, force=force)
            if state == "STOPPED":
                self._finish_active_session("stopped", snapshot)
        elif self.active_session_id is None and item is not None:
            # Identity tracking may be degraded; keep the exact old behavior so
            # playback itself and rollback progress never depend on v2.
            self._record_legacy_snapshot(snapshot, force=force)
        return item

    def status(self) -> dict[str, Any]:
        snapshot = self.player.snapshot()
        raw_volume_error = snapshot.pop("volume_error", None)
        vlc_volume_error = (
            f"could not hold VLC volume at its fixed level: {raw_volume_error}"
            if raw_volume_error
            else None
        )
        item = self._record_snapshot(snapshot)
        position = float(snapshot.get("position") or 0)
        duration = float(snapshot.get("duration") or 0)
        player = dict(snapshot)
        for private_field in ("path", "url", "track_id", "volume"):
            player.pop(private_field, None)
        if item:
            player["title"] = item.title
            try:
                progress = (
                    self._legacy_progress_for_asset(item.asset_id, item.work_id)
                    if item.asset_id is not None
                    else self.store.get(item.key)
                )
            except (CatalogError, sqlite3.Error, OSError, ValueError) as exc:
                self.identity_error = f"media identity tracking degraded: {exc}"
                progress = self.store.get(item.key)
            player["item"] = item.as_dict(progress)
            if item.series:
                show = self.show_for_item(item)
                player["show_id"] = show.id if show else None
        else:
            player["item"] = None
        player["position_text"] = seconds_text(position)
        player["duration_text"] = seconds_text(duration) if duration else None
        player["remaining"] = max(0.0, duration - position) if duration else None
        player["remaining_text"] = f"-{seconds_text(duration - position)}" if duration else None
        player["fraction"] = min(1.0, position / duration) if duration else None
        source = self.library.source
        sleep_remaining = max(0, int(self.sleep_deadline - self.clock())) if self.sleep_deadline else 0
        history_error = "; ".join(
            value
            for value in (self.identity_error, self.session_recovery_error)
            if value
        ) or None
        return {
            "ok": True,
            "library": {
                "available": self.library.available,
                "source": source.name if source else None,
                "error": self.library.error,
                "last_scan": int(self.library.last_scan),
                "items": len(self.library.items),
                "shows": len(self.library.shows),
            },
            "player": player,
            "audio": self.audio_status(vlc_volume_error=vlc_volume_error),
            "history": {
                "version": 2 if self.catalog is not None else 1,
                "available": self.catalog is not None,
                "session_active": self.active_session_id is not None,
                "degraded": bool(history_error),
                "error": history_error,
            },
            "sleep_timer": {
                "active": bool(self.sleep_deadline),
                "remaining": sleep_remaining,
                "remaining_text": seconds_text(sleep_remaining) if sleep_remaining else None,
            },
            "error": self.last_error,
        }

    def room_preparing(self) -> bool:
        check = getattr(self.player, "room_preparing", None)
        return bool(check and check())

    def audio_status(self, *, vlc_volume_error: str | None = None) -> dict[str, Any]:
        fixed_percent = round(VLC_FIXED_VOLUME * 100)
        if self.room_preparing():
            self.audio_was_preparing = True
            return {
                "available": False,
                "preparing": True,
                "device": None,
                "volume": None,
                "muted": None,
                "vlc_fixed": fixed_percent,
                "error": vlc_volume_error,
            }
        if self.sonos is None:
            return {
                "available": False,
                "preparing": False,
                "device": None,
                "volume": None,
                "muted": None,
                "vlc_fixed": fixed_percent,
                "error": vlc_volume_error or "Sonos volume control is unavailable",
            }
        if self.audio_was_preparing:
            invalidate = getattr(self.sonos, "invalidate", None)
            if invalidate is not None:
                invalidate()
            self.audio_was_preparing = False
        try:
            value = dict(self.sonos.snapshot())
            self._remember_audio_volume(value)
            value["vlc_fixed"] = fixed_percent
            if vlc_volume_error:
                existing_error = value.get("error")
                value["error"] = "; ".join(
                    part for part in (existing_error, vlc_volume_error) if part
                )
            return value
        except Exception as exc:
            return {
                "available": False,
                "preparing": False,
                "device": None,
                "volume": None,
                "muted": None,
                "vlc_fixed": fixed_percent,
                "error": vlc_volume_error or str(exc),
            }

    def _remember_audio_volume(self, audio: dict[str, Any]) -> int | None:
        if audio.get("available"):
            try:
                volume = int(audio.get("volume"))
            except (TypeError, ValueError):
                volume = -1
            if 0 <= volume <= 100:
                self.last_audio_volume = volume
        return self.last_audio_volume

    def _prepare_audio_for_resume(self) -> None:
        desired_volume = self.last_audio_volume
        if self.sonos is not None:
            try:
                desired_volume = self._remember_audio_volume(
                    dict(self.sonos.snapshot())
                )
            except Exception:
                pass

        prepare = getattr(self.player, "prepare_room", None)
        if not callable(prepare):
            raise RoomPreparationError("rear_movie preparation is unavailable")

        self.audio_was_preparing = True
        prepare(wait=True)

        if self.sonos is not None:
            invalidate = getattr(self.sonos, "invalidate", None)
            if invalidate is not None:
                invalidate()
            try:
                if desired_volume is not None:
                    self.sonos.set_volume(desired_volume)
                audio = dict(self.sonos.snapshot())
            except Exception as exc:
                raise RoomPreparationError(
                    f"could not restore rear Sonos volume: {exc}"
                ) from exc
            if not audio.get("available"):
                raise RoomPreparationError(
                    audio.get("error") or "rear Sonos validation failed"
                )
            self._remember_audio_volume(audio)
        self.audio_was_preparing = False

    def control_player(self, action: str) -> dict[str, Any]:
        if action not in ("toggle", "play", "pause", "next", "previous", "stop"):
            raise ValueError("unknown control action")
        with self.control_lock:
            snapshot = self.player.snapshot()
            state = str(snapshot.get("state") or "").upper()
            resumes_playback = bool(
                snapshot.get("available")
                and action in ("toggle", "play")
                and state in ("PAUSED", "PAUSED_PLAYBACK", "STOPPED")
            )
            if action in ("next", "previous", "stop"):
                self.bookmark()
            if resumes_playback:
                try:
                    self._prepare_audio_for_resume()
                except Exception as exc:
                    self.last_error = str(exc)
                    if isinstance(exc, RoomPreparationError):
                        raise
                    raise RoomPreparationError(str(exc)) from exc
            self.player.action("play" if resumes_playback else action)
            if action == "stop":
                self.player.quit()
                if self.active_session_id is not None:
                    self._finish_active_session("user_stop", snapshot)
            if resumes_playback:
                self.last_error = None
            return {
                "ok": True,
                "message": (
                    "rear movie audio ready · playing"
                    if resumes_playback
                    else action.replace("toggle", "play / pause")
                ),
            }

    def set_audio_volume(self, volume: int) -> dict[str, Any]:
        with self.control_lock:
            if self.room_preparing():
                self.audio_was_preparing = True
                raise AudioPreparingError("rear movie audio is still preparing")
            if self.sonos is None:
                raise RuntimeError("Sonos volume control is unavailable")
            if self.audio_was_preparing:
                invalidate = getattr(self.sonos, "invalidate", None)
                if invalidate is not None:
                    invalidate()
                self.audio_was_preparing = False
            actual = int(self.sonos.set_volume(volume))
            value = dict(self.sonos.snapshot())
            if not value.get("available"):
                raise RuntimeError(value.get("error") or "rear Sonos volume is unavailable")
            value["volume"] = actual
            self.last_audio_volume = actual
            return value

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

    def bookmark(self) -> None:
        self._record_snapshot(self.player.snapshot(), force=True)

    def _replace_active_playback(self) -> bool:
        self.bookmark()
        if self.active_session_id is not None:
            return self._finish_active_session("replaced", self.last_snapshot)
        return True

    def play(
        self,
        *,
        item_id: str | None = None,
        show_id: str | None = None,
        query: str | None = None,
        restart: bool = False,
        shuffle: bool = False,
        subtitles: str = "auto",
    ) -> dict[str, Any]:
        with self.control_lock:
            if not self.library.available:
                self.rescan()
            if not self.library.available:
                raise RuntimeError(self.library.error or "media library unavailable")
            item, queue = self._resolve_play(item_id, show_id, query, shuffle)
            paths = [self.library.resolve_for_play(queued) for queued in queue]
            history_ready = self._replace_active_playback()
            progress = self._progress_all().get(item.key)
            position = 0.0
            if progress and not restart and not progress.get("finished"):
                position = max(0.0, float(progress.get("position") or 0) - RESUME_REWIND)
            if self.sonos is not None:
                invalidate = getattr(self.sonos, "invalidate", None)
                if invalidate is not None:
                    invalidate()
                self.audio_was_preparing = True
            asset_id, work_id, complete = self._resolve_asset_for_path(
                paths[0], item=item
            )
            snapshot = self.player.launch(
                paths, position=position, subtitles=subtitles
            )
            self._remember_explicit_launch(paths[0], snapshot)
            if self.catalog is not None and asset_id is not None and history_ready:
                self._begin_catalog_session(
                    asset_id=asset_id,
                    work_id=work_id,
                    path=paths[0],
                    snapshot=snapshot,
                    item=item,
                    complete=complete,
                    clear_override=True,
                )
                self._clear_pending_explicit_launch()
            elif self.catalog is None or asset_id is None:
                self.store.record(
                    item.key,
                    position=position,
                    finished=False,
                    finished_override=None,
                    title=item.title,
                    rel_path=item.rel_path,
                    increment_play=True,
                )
            verb = "Resuming" if position else "Playing"
            label = f"{item.series} {item.episode_code}" if item.series and item.episode_code else item.title
            current_progress = (
                self._legacy_progress_for_asset(item.asset_id, item.work_id)
                if item.asset_id is not None
                else self.store.get(item.key)
            )
            return {
                "ok": True,
                "message": f"{verb} {label}" + (" · random episode" if shuffle else ""),
                "item": item.as_dict(current_progress),
                "queued": len(queue),
            }

    def play_local(
        self,
        path: str,
        *,
        restart: bool = False,
        subtitles: str = "auto",
    ) -> dict[str, Any]:
        """Play any local regular file; identity lookup is never a prerequisite."""

        if subtitles not in ("auto", "off"):
            raise ValueError("subtitles must be auto or off")
        if not isinstance(path, str) or not path or "\x00" in path:
            raise ValueError("a local media path is required")
        absolute = os.path.abspath(os.path.expanduser(path))
        real_path = os.path.realpath(absolute)
        if not os.path.isfile(real_path):
            raise FileNotFoundError("local media file is unavailable")
        with self.control_lock:
            item = self.library.item_for_path(real_path)
            asset_id, work_id, complete = self._resolve_asset_for_path(
                absolute, item=item
            )
            try:
                progress = (
                    self._legacy_progress_for_asset(asset_id, work_id)
                    if asset_id is not None
                    else (self.store.get(item.key) if item is not None else None)
                )
            except (CatalogError, sqlite3.Error, OSError, ValueError) as exc:
                self.identity_error = f"media identity tracking degraded: {exc}"
                asset_id = None
                work_id = None
                progress = self.store.get(item.key) if item is not None else None
            position = 0.0
            if progress and not restart and not progress.get("finished"):
                position = max(
                    0.0, float(progress.get("position") or 0) - RESUME_REWIND
                )
            history_ready = self._replace_active_playback()
            if self.sonos is not None:
                invalidate = getattr(self.sonos, "invalidate", None)
                if invalidate is not None:
                    invalidate()
                self.audio_was_preparing = True
            snapshot = self.player.launch(
                [real_path], position=position, subtitles=subtitles
            )
            self._remember_explicit_launch(real_path, snapshot)
            if self.catalog is not None and asset_id is not None and history_ready:
                try:
                    self._begin_catalog_session(
                        asset_id=asset_id,
                        work_id=work_id,
                        path=real_path,
                        snapshot=snapshot,
                        item=item,
                        complete=complete,
                        clear_override=True,
                    )
                    self._clear_pending_explicit_launch()
                except (CatalogError, sqlite3.Error, OSError, ValueError) as exc:
                    self.identity_error = f"media identity tracking degraded: {exc}"
                    asset_id = None
            elif item is not None and (self.catalog is None or asset_id is None):
                self.store.record(
                    item.key,
                    position=position,
                    finished=False,
                    finished_override=None,
                    title=item.title,
                    rel_path=item.rel_path,
                    increment_play=True,
                )
            return {
                "ok": True,
                "message": "Resuming local media" if position else "Playing local media",
                "tracked": asset_id is not None and history_ready,
                "identity": self._asset_identity_label(asset_id),
            }

    def reconcile_torrents(self, torrent_id: str | None = None) -> dict[str, Any]:
        """Refresh known qB paths without changing any qBittorrent state."""

        if self.catalog is None or self.qbittorrent is None:
            return {
                "ok": True,
                "available": False,
                "checked": 0,
                "updated": 0,
            }
        if torrent_id:
            results = tuple(
                self.qbittorrent.reconcile_completed_torrent(torrent_id)
            )
        else:
            results_list: list[ResolvedTorrentFile] = []
            locators = self.catalog.list_torrent_locators()
            wanted = {
                (
                    str(locator["client_id"]),
                    str(locator["torrent_id"]),
                    int(locator["file_index"]),
                )
                for locator in locators
            }
            torrents = sorted({(client, torrent) for client, torrent, _index in wanted})
            for client, known_torrent_id in torrents:
                if client != self.qbittorrent.client_id:
                    continue
                try:
                    records = self.qbittorrent.reconcile_completed_torrent(
                        known_torrent_id
                    )
                except (
                    QbittorrentUnavailable,
                    QbittorrentAuthenticationError,
                    QbittorrentConfigurationError,
                    QbittorrentProtocolError,
                ) as exc:
                    self.identity_error = f"qBittorrent reconciliation failed: {exc}"
                    return {
                        "ok": True,
                        "available": False,
                        "checked": len(results_list),
                        "updated": 0,
                    }
                except QbittorrentError:
                    continue
                results_list.extend(
                    record
                    for record in records
                    if (
                        record.identity.client_id,
                        record.identity.torrent_id,
                        record.identity.file_index,
                    )
                    in wanted
                )
            results = tuple(results_list)
        updated = 0
        for resolved in results:
            existing = self.catalog.lookup_torrent_asset(
                client_id=resolved.identity.client_id,
                torrent_id=resolved.identity.torrent_id,
                file_index=resolved.identity.file_index,
                info_hash_v1=resolved.infohash_v1 or None,
                info_hash_v2=resolved.infohash_v2 or None,
            )
            self._record_torrent_result(
                resolved, preferred_asset_id=existing
            )
            updated += 1
        if updated:
            self.rescan()
        return {
            "ok": True,
            "available": True,
            "checked": len(results),
            "updated": updated,
        }

    def update_progress(self, item: MediaItem, action: str) -> bool:
        if action not in ("clear", "watched", "unwatched"):
            raise ValueError("action must be clear, watched, or unwatched")
        if self.catalog is None or item.asset_id is None or item.work_id is None:
            if action == "clear":
                return self.store.clear(item.key)
            self.store.mark(item.key, action == "watched")
            return True
        with self.control_lock, self.catalog.transaction() as db:
            target_asset_id = item.asset_id
            if action == "clear":
                existed = self.store.get(item.key) is not None
                self.catalog.clear_playhead(
                    target_asset_id,
                    clear_work_auto=True,
                    connection=db,
                )
                self.catalog.project_v1_clear(
                    item.key,
                    asset_id=target_asset_id,
                    connection=db,
                )
                return existed
            watched = action == "watched"
            self.catalog.set_work_watched(
                item.work_id,
                watched,
                manual=True,
                asset_id=target_asset_id,
                connection=db,
            )
            self._project_item_progress(item, connection=db)
            return True

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

    def set_sleep_timer(self, minutes: int) -> dict[str, Any]:
        if minutes < 0 or minutes > 8 * 60:
            raise ValueError("sleep timer must be from 0 to 480 minutes")
        with self.control_lock:
            self.sleep_deadline = self.clock() + minutes * 60 if minutes else None
        return {
            "ok": True,
            "message": f"Sleep timer set for {minutes} minutes" if minutes else "Sleep timer off",
        }

    def _pause_for_expired_sleep_timer(self) -> bool:
        with self.control_lock:
            if self.sleep_deadline is None or self.clock() < self.sleep_deadline:
                return False
            self.player.action("pause")
            self.sleep_deadline = None
            return True


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
