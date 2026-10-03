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
    from .library import LibraryViewMixin, MediaLibrary, default_sources
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
    from .players import sonos_volume, vlc_player
    from .players.sonos_volume import AudioPreparingError, SonosVolumeController
    from .players.vlc_player import RoomPreparationError, VlcController, native
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
    from library import LibraryViewMixin, MediaLibrary, default_sources  # type: ignore[no-redef]
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
    import identity  # type: ignore[no-redef]
    import library  # type: ignore[no-redef]
    import media_models  # type: ignore[no-redef]
    import naming  # type: ignore[no-redef]
    import playback  # type: ignore[no-redef]
    import routes  # type: ignore[no-redef]
    import service  # type: ignore[no-redef]
    import sonos_volume  # type: ignore[no-redef]
    import vlc_player  # type: ignore[no-redef]
    from sonos_volume import AudioPreparingError, SonosVolumeController  # type: ignore[no-redef]
    from vlc_player import RoomPreparationError, VlcController, native  # type: ignore[no-redef]
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






class VideoService(LibraryViewMixin, playback.PlaybackMixin, identity.CatalogIdentityMixin):
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
