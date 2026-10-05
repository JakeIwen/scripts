"""Service-side legacy progress compatibility."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import time
from typing import Any

from .config import LEGACY_LINE_RE, QBITTORRENT_FINAL_ROOTS, QBITTORRENT_TEMP_ROOTS
from .media_models import MediaItem
from .video_qbittorrent import ResolvedTorrentFile


_UNSET = object()


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


class LegacyProgressMixin:
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
            changed_item = item.key != self.playback.last_saved_key
            changed_position = (
                self.playback.last_saved_position is None
                or abs(position - self.playback.last_saved_position) >= 1
            )
            changed_duration = (
                self.playback.last_saved_duration is None
                or abs(duration - self.playback.last_saved_duration) >= 1
            )
            changed_state = str(snapshot.get("state") or "") != self.playback.last_saved_state
            became_finished = finished and not (previous or {}).get("finished")
            due = now < self.playback.last_saved_at or now - self.playback.last_saved_at >= 10
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
                self.playback.last_saved_key = item.key
                self.playback.last_saved_position = position
                self.playback.last_saved_duration = duration
                self.playback.last_saved_state = str(snapshot.get("state") or "")
                self.playback.last_saved_at = now
        return item

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
