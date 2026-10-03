"""Service orchestration boundary for the video library."""

from __future__ import annotations

import hashlib
import os
import random
import sqlite3
import threading
import time
from typing import Any, Callable

if __package__:
    from . import identity, playback
    from .catalog import CatalogError, MediaAssetCatalog, ensure_pre_v2_backup
    from .config import (
        LEGACY_LINE_RE,
        LEGACY_POSITIONS_PATH,
        POLL_INTERVAL,
        QBITTORRENT_CLIENT_ID,
        QBITTORRENT_FINAL_ROOTS,
        QBITTORRENT_TEMP_ROOTS,
        QBITTORRENT_TIMEOUT,
        QBITTORRENT_URL,
        SCAN_INTERVAL,
        STATE_PATH,
        _UNSET,
    )
    from .library import LibraryViewMixin, MediaLibrary, default_sources
    from .media_models import MediaItem
    from .players.sonos_volume import SonosVolumeController
    from .players.vlc_player import VlcController
    from .video_qbittorrent import QbittorrentClient, QbittorrentError
else:
    import identity  # type: ignore[no-redef]
    import playback  # type: ignore[no-redef]
    from catalog import (  # type: ignore[no-redef]
        CatalogError,
        MediaAssetCatalog,
        ensure_pre_v2_backup,
    )
    from config import (  # type: ignore[no-redef]
        LEGACY_LINE_RE,
        LEGACY_POSITIONS_PATH,
        POLL_INTERVAL,
        QBITTORRENT_CLIENT_ID,
        QBITTORRENT_FINAL_ROOTS,
        QBITTORRENT_TEMP_ROOTS,
        QBITTORRENT_TIMEOUT,
        QBITTORRENT_URL,
        SCAN_INTERVAL,
        STATE_PATH,
        _UNSET,
    )
    from library import (  # type: ignore[no-redef]
        LibraryViewMixin,
        MediaLibrary,
        default_sources,
    )
    from media_models import MediaItem  # type: ignore[no-redef]
    from sonos_volume import SonosVolumeController  # type: ignore[no-redef]
    from vlc_player import VlcController  # type: ignore[no-redef]
    from video_qbittorrent import (  # type: ignore[no-redef]
        QbittorrentClient,
        QbittorrentError,
    )


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
