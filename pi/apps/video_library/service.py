"""Service orchestration boundary for the video library."""

from __future__ import annotations

import random
import sqlite3
import threading
import time
from typing import Any, Callable

if __package__:
    from . import identity, playback
    from .catalog import CatalogError, MediaAssetCatalog, ensure_pre_v2_backup
    from .config import (
        LEGACY_POSITIONS_PATH,
        POLL_INTERVAL,
        QBITTORRENT_CLIENT_ID,
        QBITTORRENT_FINAL_ROOTS,
        QBITTORRENT_TEMP_ROOTS,
        QBITTORRENT_TIMEOUT,
        QBITTORRENT_URL,
        SCAN_INTERVAL,
        STATE_PATH,
    )
    from .library import LibraryViewMixin, MediaLibrary, default_sources
    from .legacy_progress import LegacyProgressMixin, ProgressStore
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
        LEGACY_POSITIONS_PATH,
        POLL_INTERVAL,
        QBITTORRENT_CLIENT_ID,
        QBITTORRENT_FINAL_ROOTS,
        QBITTORRENT_TEMP_ROOTS,
        QBITTORRENT_TIMEOUT,
        QBITTORRENT_URL,
        SCAN_INTERVAL,
        STATE_PATH,
    )
    from library import (  # type: ignore[no-redef]
        LibraryViewMixin,
        MediaLibrary,
        default_sources,
    )
    from legacy_progress import LegacyProgressMixin, ProgressStore  # type: ignore[no-redef]
    from sonos_volume import SonosVolumeController  # type: ignore[no-redef]
    from vlc_player import VlcController  # type: ignore[no-redef]
    from video_qbittorrent import (  # type: ignore[no-redef]
        QbittorrentClient,
        QbittorrentError,
    )


class VideoService(LegacyProgressMixin, LibraryViewMixin, playback.PlaybackMixin, identity.CatalogIdentityMixin):
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
