"""Playback state and behavior for the video library."""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

if __package__:
    from .catalog import CatalogError
    from .config import RESUME_REWIND, VLC_FIXED_VOLUME, WATCHED_FRACTION
    from .media_models import MediaItem, seconds_text
    from .naming import clean_name
    from .players.sonos_volume import AudioPreparingError
    from .players.vlc_player import RoomPreparationError
else:
    from catalog import CatalogError  # type: ignore[no-redef]
    from config import (  # type: ignore[no-redef]
        RESUME_REWIND,
        VLC_FIXED_VOLUME,
        WATCHED_FRACTION,
    )
    from media_models import MediaItem, seconds_text  # type: ignore[no-redef]
    from naming import clean_name  # type: ignore[no-redef]
    from sonos_volume import AudioPreparingError  # type: ignore[no-redef]
    from vlc_player import RoomPreparationError  # type: ignore[no-redef]


@dataclass
class PlaybackState:
    sleep_deadline: float | None = None
    last_saved_key: str | None = None
    last_saved_position: float | None = None
    last_saved_duration: float | None = None
    last_saved_at: float = 0.0
    last_saved_state: str | None = None
    audio_was_preparing: bool = False
    last_audio_volume: int | None = None
    active_session_id: str | None = None
    active_asset_id: str | None = None
    active_work_id: str | None = None
    active_track_id: str | None = None
    active_item: MediaItem | None = None
    active_legacy_key: str | None = None
    active_title: str | None = None
    active_rel_path: str | None = None
    active_complete: bool = True
    last_snapshot: dict[str, Any] | None = None
    pending_explicit_launch: dict[str, Any] | None = None


class PlaybackMixin:
    @staticmethod
    def _is_finished(position: float, duration: float) -> bool:
        if duration <= 0:
            return False
        return position / duration >= WATCHED_FRACTION or duration - position <= 90

    def _reset_save_throttle(self) -> None:
        self.playback.last_saved_key = None
        self.playback.last_saved_position = None
        self.playback.last_saved_duration = None
        self.playback.last_saved_state = None
        self.playback.last_saved_at = 0.0

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
            self.playback.pending_explicit_launch = None
            return
        path_keys = self._launch_path_keys(path)
        if not path_keys:
            self.playback.pending_explicit_launch = None
            return
        self.playback.pending_explicit_launch = {
            "path_keys": path_keys,
            "track_id": (
                str(snapshot["track_id"])
                if snapshot.get("track_id") is not None
                else None
            ),
        }

    def _pending_explicit_launch_matches(self, snapshot: dict[str, Any]) -> bool:
        pending = self.playback.pending_explicit_launch
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
        self.playback.pending_explicit_launch = None

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
        self.playback.active_session_id = session_id
        self.playback.active_asset_id = asset_id
        self.playback.active_work_id = work_id
        self.playback.active_track_id = (
            str(snapshot["track_id"]) if snapshot.get("track_id") is not None else None
        )
        self.playback.active_item = item
        self.playback.active_legacy_key = item.key if item is not None else None
        self.playback.active_title = item.title if item is not None else clean_name(Path(path).name)
        self.playback.active_rel_path = item.rel_path if item is not None else None
        self.playback.active_complete = bool(complete)
        self.playback.last_snapshot = dict(snapshot)
        self._reset_save_throttle()

    def _finish_active_session(
        self,
        reason: str,
        snapshot: dict[str, Any] | None = None,
    ) -> bool:
        if self.catalog is not None and self.playback.active_session_id is not None:
            try:
                value = snapshot or self.playback.last_snapshot or {}
                position = max(0.0, float(value.get("position") or 0))
                duration = max(0.0, float(value.get("duration") or 0))
                completed = self.playback.active_complete and self._is_finished(
                    position, duration
                )
                with self.catalog.transaction() as db:
                    self.catalog.finish_session(
                        self.playback.active_session_id,
                        reason=reason,
                        position=position,
                        duration=duration,
                        completed=completed,
                        connection=db,
                    )
                    if self.playback.active_item is not None:
                        self._project_item_progress(self.playback.active_item, connection=db)
            except (CatalogError, sqlite3.Error, OSError, ValueError) as exc:
                self.identity_error = f"could not finish playback session: {exc}"
                # Keep the pinned session in memory when the durable finish
                # transaction fails.  A later healthy poll can retry the exact
                # same session instead of orphaning it until process restart.
                return False
        self.playback.active_session_id = None
        self.playback.active_asset_id = None
        self.playback.active_work_id = None
        self.playback.active_track_id = None
        self.playback.active_item = None
        self.playback.active_legacy_key = None
        self.playback.active_title = None
        self.playback.active_rel_path = None
        self.playback.active_complete = True
        self.playback.last_snapshot = None
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
            or self.playback.active_session_id is None
            or self.playback.active_asset_id is None
        ):
            return
        position = max(0.0, float(snapshot.get("position") or 0))
        duration = max(0.0, float(snapshot.get("duration") or 0))
        state = str(snapshot.get("state") or "").upper()
        now = self.clock()
        changed_position = (
            self.playback.last_saved_position is None
            or abs(position - self.playback.last_saved_position) >= 1
        )
        changed_duration = (
            self.playback.last_saved_duration is None
            or abs(duration - self.playback.last_saved_duration) >= 1
        )
        changed_state = state != self.playback.last_saved_state
        due = now < self.playback.last_saved_at or now - self.playback.last_saved_at >= 10
        completed = self.playback.active_complete and self._is_finished(position, duration)
        state_before = self.catalog.get_asset_state(self.playback.active_asset_id) or {}
        became_finished = completed and not bool(state_before.get("completed"))
        if not (
            force
            or changed_state
            or became_finished
            or (due and (changed_position or changed_duration))
        ):
            self.playback.last_snapshot = dict(snapshot)
            return
        with self.catalog.transaction() as db:
            self.catalog.checkpoint(
                self.playback.active_session_id,
                position=position,
                duration=duration,
                completed=completed,
                playback_state=state,
                event_type="state_changed" if changed_state else "checkpoint",
                authoritative_order=True,
                connection=db,
            )
            if self.playback.active_item is not None:
                self._project_item_progress(self.playback.active_item, connection=db)
        self.playback.last_saved_key = self.playback.active_legacy_key or self.playback.active_asset_id
        self.playback.last_saved_position = position
        self.playback.last_saved_duration = duration
        self.playback.last_saved_state = state
        self.playback.last_saved_at = now
        self.playback.last_snapshot = dict(snapshot)


    def _record_snapshot(
        self, snapshot: dict[str, Any], *, force: bool = False
    ) -> MediaItem | None:
        # Browser requests and the background poller can arrive together.  The
        # active session fields are one state machine and must move atomically.
        with self.control_lock:
            return self._record_snapshot_degrading(snapshot, force=force)

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
            item = self.playback.active_item
            self._clear_pending_explicit_launch()
            if self.playback.active_session_id is not None:
                self._finish_active_session("player_offline")
            return item

        if (
            self.playback.active_session_id is not None
            and track_id is not None
            and self.playback.active_track_id is not None
            and track_id != self.playback.active_track_id
        ):
            if not self._finish_active_session("track_changed"):
                # This snapshot belongs to the new track.  Until the old pinned
                # session can be closed, never checkpoint it into the old asset.
                return self.playback.active_item

        if (
            self.playback.active_session_id is None
            and state in ("PLAYING", "PAUSED", "PAUSED_PLAYBACK")
            and path
        ):
            clear_override = self._pending_explicit_launch_matches(snapshot)
            if self.playback.pending_explicit_launch is not None and not clear_override:
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
            self.playback.active_session_id is None
            and state == "STOPPED"
            and self.playback.pending_explicit_launch is not None
        ):
            self._clear_pending_explicit_launch()
        item = self.playback.active_item or self.library.item_for_path(path)
        if self.playback.active_session_id is not None and state in (
            "PLAYING",
            "PAUSED",
            "PAUSED_PLAYBACK",
            "STOPPED",
        ):
            self._checkpoint_active(snapshot, force=force)
            if state == "STOPPED":
                self._finish_active_session("stopped", snapshot)
        elif self.playback.active_session_id is None and item is not None:
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
            progress = self._status_item_progress(item)
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
        sleep_remaining = max(0, int(self.playback.sleep_deadline - self.clock())) if self.playback.sleep_deadline else 0
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
                "session_active": self.playback.active_session_id is not None,
                "degraded": bool(history_error),
                "error": history_error,
            },
            "sleep_timer": {
                "active": bool(self.playback.sleep_deadline),
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
            self.playback.audio_was_preparing = True
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
        if self.playback.audio_was_preparing:
            invalidate = getattr(self.sonos, "invalidate", None)
            if invalidate is not None:
                invalidate()
            self.playback.audio_was_preparing = False
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
                self.playback.last_audio_volume = volume
        return self.playback.last_audio_volume

    def _prepare_audio_for_resume(self) -> None:
        desired_volume = self.playback.last_audio_volume
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

        self.playback.audio_was_preparing = True
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
        self.playback.audio_was_preparing = False

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
                if self.playback.active_session_id is not None:
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
                self.playback.audio_was_preparing = True
                raise AudioPreparingError("rear movie audio is still preparing")
            if self.sonos is None:
                raise RuntimeError("Sonos volume control is unavailable")
            if self.playback.audio_was_preparing:
                invalidate = getattr(self.sonos, "invalidate", None)
                if invalidate is not None:
                    invalidate()
                self.playback.audio_was_preparing = False
            actual = int(self.sonos.set_volume(volume))
            value = dict(self.sonos.snapshot())
            if not value.get("available"):
                raise RuntimeError(value.get("error") or "rear Sonos volume is unavailable")
            value["volume"] = actual
            self.playback.last_audio_volume = actual
            return value

    def bookmark(self) -> None:
        self._record_snapshot(self.player.snapshot(), force=True)

    def _replace_active_playback(self) -> bool:
        self.bookmark()
        if self.playback.active_session_id is not None:
            return self._finish_active_session("replaced", self.playback.last_snapshot)
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
                self.playback.audio_was_preparing = True
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
                self.playback.audio_was_preparing = True
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

    def set_sleep_timer(self, minutes: int) -> dict[str, Any]:
        if minutes < 0 or minutes > 8 * 60:
            raise ValueError("sleep timer must be from 0 to 480 minutes")
        with self.control_lock:
            self.playback.sleep_deadline = self.clock() + minutes * 60 if minutes else None
        return {
            "ok": True,
            "message": f"Sleep timer set for {minutes} minutes" if minutes else "Sleep timer off",
        }

    def _pause_for_expired_sleep_timer(self) -> bool:
        with self.control_lock:
            if self.playback.sleep_deadline is None or self.clock() < self.playback.sleep_deadline:
                return False
            self.player.action("pause")
            self.playback.sleep_deadline = None
            return True
