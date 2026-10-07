"""Media identity boundary for the video library."""

from __future__ import annotations

import os
import sqlite3
from functools import wraps
from pathlib import Path
from typing import Any, Callable

from .catalog import CatalogConflict, CatalogError
from .config import QBITTORRENT_TEMP_ROOTS
from .deferred_positions import DeferredLegacyPositions
from .media_models import MediaItem
from .naming import clean_name
from .video_qbittorrent import (
    QbittorrentAuthenticationError,
    QbittorrentConfigurationError,
    QbittorrentError,
    QbittorrentProtocolError,
    QbittorrentUnavailable,
    ResolvedTorrentFile,
)


_CATALOG_DEGRADED = object()


def catalog_degrades(
    *,
    prefix: str | Callable[..., str] = "media identity tracking degraded",
    fallback: Callable[..., Any] | None = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    def decorate(function: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(function)
        def degrading(self: Any, *args: Any, **kwargs: Any) -> Any:
            try:
                return function(self, *args, **kwargs)
            except (CatalogError, sqlite3.Error, OSError, ValueError) as exc:
                message_prefix = (
                    prefix(self, *args, **kwargs) if callable(prefix) else prefix
                )
                self.identity_error = f"{message_prefix}: {exc}"
            if fallback is None:
                return None
            return fallback(self, *args, **kwargs)

        return degrading

    return decorate


def _degraded_sentinel(_self: Any, *_args: Any, **_kwargs: Any) -> object:
    return _CATALOG_DEGRADED


def _resolve_catalog_asset_fallback(
    _self: Any,
    resolved: ResolvedTorrentFile | None,
    preferred: str,
    complete_without_qbittorrent: bool,
    normalized_path: str,
    item: MediaItem | None,
    path: str,
    requested_path: str,
    qb_warning: str | None,
) -> tuple[None, None, bool]:
    return None, None, complete_without_qbittorrent


def _catalog_item_prefix(_self: Any, item: MediaItem, **_kwargs: Any) -> str:
    return f"could not catalog {item.title}"


def _legacy_snapshot_fallback(
    self: Any, snapshot: dict[str, Any], *, force: bool = False
) -> MediaItem | None:
    return self._record_legacy_snapshot(snapshot, force=force)


def _status_store_fallback(self: Any, item: MediaItem) -> dict[str, Any] | None:
    return self.store.get(item.key)


class CatalogIdentityMixin:
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

    @catalog_degrades(fallback=_degraded_sentinel)
    def _seed_catalog_asset(
        self,
        normalized_path: str,
        item: MediaItem | None,
    ) -> str | object:
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
        return preferred

    @catalog_degrades(fallback=_resolve_catalog_asset_fallback)
    def _resolve_catalog_asset(
        self,
        resolved: ResolvedTorrentFile | None,
        preferred: str,
        complete_without_qbittorrent: bool,
        normalized_path: str,
        item: MediaItem | None,
        path: str,
        requested_path: str,
        qb_warning: str | None,
    ) -> tuple[str | None, str | None, bool]:
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
        preferred = self._seed_catalog_asset(normalized_path, item)
        if preferred is _CATALOG_DEGRADED:
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
        return self._resolve_catalog_asset(
            resolved,
            preferred,
            complete_without_qbittorrent,
            normalized_path,
            item,
            path,
            requested_path,
            qb_warning,
        )

    def _asset_identity_label(self, asset_id: str | None) -> str:
        if self.catalog is None or asset_id is None:
            return "unavailable"
        try:
            asset = self.catalog.lookup_asset(asset_id)
        except (CatalogError, sqlite3.Error, OSError, ValueError):
            return "unavailable"
        return "torrent" if asset and asset.get("asset_kind") == "torrent" else "catalog"

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

    def _bind_library_item(
        self, item: MediaItem, *, pending_positions: DeferredLegacyPositions | None = None
    ) -> None:
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
            pending_positions=pending_positions,
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

    @catalog_degrades(prefix=_catalog_item_prefix)
    def _bind_catalog_item(
        self, item: MediaItem, *, pending_positions: DeferredLegacyPositions | None = None
    ) -> None:
        self._bind_library_item(item, pending_positions=pending_positions)

    def _sync_library_identities(self) -> None:
        if self.catalog is None:
            return
        self._retry_session_recovery()
        self.catalog.reconcile_v1_progress()
        items, _shows = self.library.snapshot()
        # Keep atomic binding without reserving the SQLite writer during history
        # preparation. The lock also keeps this scan-local index current as rows
        # are resolved; another playback thread cannot consume them underneath it.
        with self.catalog.transaction(immediate=False):
            pending_positions = DeferredLegacyPositions(
                self.catalog.list_import_records(action="unresolved")
            )
            for item in items:
                self._bind_catalog_item(item, pending_positions=pending_positions)
        if self.playback.active_asset_id is not None:
            with self.control_lock:
                matches = [
                    item for item in items if item.asset_id == self.playback.active_asset_id
                ]
                if len(matches) == 1:
                    active = matches[0]
                    self.playback.active_item = active
                    self.playback.active_work_id = active.work_id
                    self.playback.active_legacy_key = active.key
                    self.playback.active_title = active.title
                    self.playback.active_rel_path = active.rel_path

    @catalog_degrades(fallback=_degraded_sentinel)
    def _catalog_item_progress(self, item: MediaItem) -> dict[str, Any] | None | object:
        value = self._legacy_progress_for_asset(item.asset_id, item.work_id)
        return value

    def _progress_all(self) -> dict[str, dict[str, Any]]:
        progress = self.store.all()
        if self.catalog is None:
            return progress
        items, _shows = self.library.snapshot()
        for item in items:
            if item.asset_id is None:
                continue
            value = self._catalog_item_progress(item)
            if value is _CATALOG_DEGRADED:
                continue
            if value is not None:
                progress[item.key] = {
                    **value,
                    "media_key": item.key,
                    "title": item.title,
                    "rel_path": item.rel_path,
                }
        return progress

    @catalog_degrades(fallback=_legacy_snapshot_fallback)
    def _record_snapshot_degrading(
        self, snapshot: dict[str, Any], *, force: bool = False
    ) -> MediaItem | None:
        return self._record_snapshot_locked(snapshot, force=force)

    @catalog_degrades(fallback=_status_store_fallback)
    def _status_item_progress(self, item: MediaItem) -> dict[str, Any] | None:
        progress = (
            self._legacy_progress_for_asset(item.asset_id, item.work_id)
            if item.asset_id is not None
            else self.store.get(item.key)
        )
        return progress

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
