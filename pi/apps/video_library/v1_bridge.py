"""Compatibility bridge for the v1 video library."""

from __future__ import annotations

import math
import sqlite3
from collections.abc import Mapping
from typing import Any

from .catalog_values import (
    CatalogError,
    CatalogNotFound,
    _digest,
    _finite_nonnegative,
    _json_or_none,
    _required_text,
)


_V1_UNTRUSTED_COVERAGE = "untrusted"


class V1CatalogBridge:
    @staticmethod
    def _v1_json_value(value: Any) -> Any:
        if isinstance(value, bytes):
            return {"sqlite_blob_hex": value.hex()}
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        return str(value)

    def _read_v1_progress(self, db: sqlite3.Connection) -> dict[str, dict[str, Any]] | None:
        exists = db.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'progress'"
        ).fetchone()
        if exists is None:
            return None
        cursor = db.execute("SELECT * FROM progress")
        rows: dict[str, dict[str, Any]] = {}
        for raw_row in cursor.fetchall():
            row = self._row(cursor, raw_row)
            if row is None or "media_key" not in row:
                raise CatalogError("legacy progress table has no media_key column")
            key = str(row["media_key"])
            rows[key] = {
                str(name): self._v1_json_value(value) for name, value in row.items()
            }
        return rows

    def _v1_row_digest(self, row: Mapping[str, Any]) -> str:
        return _digest(dict(row))

    def _v1_covered_state_digest(
        self,
        db: sqlite3.Connection,
        asset_id: str | None,
    ) -> str | None:
        """Fingerprint the exact v2 projections represented by one v1 row."""

        if asset_id is None:
            return None
        asset = self._one(
            db,
            "SELECT asset_id, work_id FROM video_v2_assets WHERE asset_id = ?",
            (asset_id,),
        )
        if asset is None:
            return None
        asset_state = self._one(
            db,
            """
            SELECT asset_id, position, duration, completed, play_count,
                   updated_at, last_session_id, last_event_id
            FROM video_v2_asset_playback_state
            WHERE asset_id = ?
            """,
            (asset_id,),
        )
        work_id = str(asset["work_id"]) if asset["work_id"] is not None else None
        work_state = (
            self._one(
                db,
                """
                SELECT work_id, watched_auto, watched_override, play_count,
                       updated_at, last_asset_id
                FROM video_v2_work_watch_state
                WHERE work_id = ?
                """,
                (work_id,),
            )
            if work_id is not None
            else None
        )
        return _digest(
            {
                "asset_id": asset_id,
                "work_id": work_id,
                "asset_state": {
                    "present": asset_state is not None,
                    "row": asset_state,
                },
                "work_watch_state": {
                    "present": work_state is not None,
                    "row": work_state,
                },
            }
        )

    def _v1_shadow_covers_state(
        self,
        db: sqlite3.Connection,
        *,
        shadow: Mapping[str, Any] | None,
        asset_id: str,
    ) -> bool:
        """Return whether a shadow proves it covered the current v2 state."""

        if (
            shadow is None
            or shadow.get("asset_id") is None
            or str(shadow["asset_id"]) != asset_id
        ):
            return False
        covered_digest = shadow.get("covered_state_digest")
        if covered_digest is not None:
            return covered_digest == self._v1_covered_state_digest(db, asset_id)

        # Migration-2 shadows predate exact coverage.  Retain the old wall-time
        # rule only as a one-time upgrade fallback; the next actual projection,
        # import, or clear records an exact digest.
        if shadow.get("source_updated") is None:
            return False
        state_updates: list[float] = []
        asset = self._one(
            db,
            "SELECT work_id FROM video_v2_assets WHERE asset_id = ?",
            (asset_id,),
        )
        asset_state = self._one(
            db,
            """
            SELECT updated_at FROM video_v2_asset_playback_state
            WHERE asset_id = ?
            """,
            (asset_id,),
        )
        if asset_state is not None:
            state_updates.append(float(asset_state["updated_at"]))
        if asset is not None and asset["work_id"] is not None:
            work_state = self._one(
                db,
                """
                SELECT updated_at FROM video_v2_work_watch_state
                WHERE work_id = ?
                """,
                (str(asset["work_id"]),),
            )
            if work_state is not None:
                state_updates.append(float(work_state["updated_at"]))
        return not state_updates or float(shadow["source_updated"]) >= max(state_updates)

    def _shadow_v1_row(
        self,
        db: sqlite3.Connection,
        *,
        media_key: str,
        present: bool,
        row_digest: str | None,
        source_updated: float | None,
        asset_id: str | None,
        covered_state_digest: str | None,
        raw: Mapping[str, Any] | None,
        observed_at: float,
    ) -> None:
        db.execute(
            """
            INSERT INTO video_v2_v1_shadow
                (media_key, was_present, row_digest, source_updated,
                 asset_id, raw_json, last_seen_at, covered_state_digest)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(media_key) DO UPDATE SET
                was_present = excluded.was_present,
                row_digest = excluded.row_digest,
                source_updated = excluded.source_updated,
                asset_id = COALESCE(excluded.asset_id, video_v2_v1_shadow.asset_id),
                raw_json = excluded.raw_json,
                last_seen_at = excluded.last_seen_at,
                covered_state_digest = excluded.covered_state_digest
            """,
            (
                media_key,
                int(present),
                row_digest,
                source_updated,
                asset_id,
                _json_or_none(dict(raw) if raw is not None else None),
                observed_at,
                covered_state_digest,
            ),
        )

    def _legacy_target(
        self,
        db: sqlite3.Connection,
        *,
        media_key: str,
        row: Mapping[str, Any],
        observed_at: float,
        create_missing: bool,
    ) -> tuple[str | None, str | None]:
        asset_id = self.resolve_legacy_key(media_key, connection=db)
        if asset_id is None and create_missing:
            work_id = self.create_work(
                "legacy",
                title=str(row.get("title") or media_key),
                metadata={"created_from": "v1-progress", "legacy_key": media_key},
                observed_at=observed_at,
                connection=db,
            )
            asset_id = self.create_asset(
                asset_kind="legacy-v1",
                work_id=work_id,
                metadata={"created_from": "v1-progress", "legacy_key": media_key},
                observed_at=observed_at,
                connection=db,
            )
            self.bind_legacy_key(
                asset_id,
                media_key,
                metadata={"rel_path": row.get("rel_path")},
                observed_at=observed_at,
                connection=db,
            )
        if asset_id is None:
            return None, None
        asset = self._assert_asset(db, asset_id)
        return asset_id, str(asset["work_id"]) if asset["work_id"] is not None else None

    def _apply_v1_snapshot(
        self,
        db: sqlite3.Connection,
        *,
        media_key: str,
        row: Mapping[str, Any],
        row_digest: str,
        asset_id: str,
        work_id: str | None,
        observed_at: float,
        authoritative: bool = False,
    ) -> bool:
        try:
            source_updated = float(row.get("updated") or observed_at)
        except (TypeError, ValueError):
            source_updated = observed_at
        if not math.isfinite(source_updated):
            source_updated = observed_at
        position = _finite_nonnegative(row.get("position") or 0, "legacy position") or 0
        duration = _finite_nonnegative(row.get("duration") or 0, "legacy duration") or 0
        completed = bool(row.get("finished") or 0)
        play_count = max(0, int(row.get("play_count") or 0))
        override_raw = row.get("finished_override")
        override = None if override_raw is None else bool(override_raw)
        event_id, _ = self._insert_event(
            db,
            asset_id=asset_id,
            event_type="legacy_v1_snapshot",
            position=position,
            duration=duration,
            completed=completed,
            observed_at=source_updated,
            event_key=f"v1-snapshot:{media_key}:{row_digest}",
            payload={"source": "progress", "media_key": media_key, "observed_at": observed_at},
        )
        current_state = self._one(
            db,
            "SELECT updated_at FROM video_v2_asset_playback_state WHERE asset_id = ?",
            (asset_id,),
        )
        if (
            not authoritative
            and current_state is not None
            and source_updated < float(current_state["updated_at"])
        ):
            return False
        applied = self._put_asset_state(
            db,
            asset_id=asset_id,
            updated_at=source_updated,
            position=position,
            duration=duration,
            completed=completed,
            play_count=play_count,
            last_event_id=event_id,
        )
        if work_id is not None:
            self._put_work_state(
                db,
                work_id=work_id,
                updated_at=source_updated,
                watched_auto=completed,
                watched_override=override,
                play_count=play_count,
                last_asset_id=asset_id,
                allow_auto_reset=True,
            )
        return applied

    def project_v1_progress(
        self,
        media_key: str,
        *,
        position: float,
        duration: float = 0,
        updated: float | None = None,
        finished: bool = False,
        finished_override: bool | None = None,
        play_count: int = 0,
        title: str | None = None,
        rel_path: str | None = None,
        asset_id: str | None = None,
        connection: sqlite3.Connection | None = None,
    ) -> None:
        """UPSERT a rollback-compatible v1 row inside the caller's transaction."""

        key = _required_text(media_key, "media_key")
        timestamp = self._now(updated)
        with self.transaction(connection=connection) as db:
            table = db.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'progress'"
            ).fetchone()
            if table is None:
                raise CatalogNotFound("legacy progress table does not exist")
            columns = {
                str(row[1]) for row in db.execute("PRAGMA table_info(progress)").fetchall()
            }
            values: dict[str, Any] = {
                "media_key": key,
                "position": _finite_nonnegative(position, "position") or 0,
                "duration": _finite_nonnegative(duration, "duration") or 0,
                "updated": timestamp,
                "finished": int(bool(finished)),
                "finished_override": (
                    None if finished_override is None else int(bool(finished_override))
                ),
                "play_count": max(0, int(play_count)),
                "title": title,
                "rel_path": rel_path,
            }
            writable = [name for name in values if name in columns]
            if "media_key" not in writable:
                raise CatalogError("legacy progress table has no media_key column")
            updates = [name for name in writable if name != "media_key"]
            placeholders = ", ".join("?" for _ in writable)
            update_sql = ", ".join(f"{name} = excluded.{name}" for name in updates)
            db.execute(
                f"""
                INSERT INTO progress ({', '.join(writable)}) VALUES ({placeholders})
                ON CONFLICT(media_key) DO UPDATE SET {update_sql}
                """,
                tuple(values[name] for name in writable),
            )
            row = self._read_v1_progress(db)[key]  # type: ignore[index]
            digest = self._v1_row_digest(row)
            if asset_id is None:
                asset_id = self.resolve_legacy_key(key, connection=db)
            self._shadow_v1_row(
                db,
                media_key=key,
                present=True,
                row_digest=digest,
                source_updated=timestamp,
                asset_id=asset_id,
                covered_state_digest=(
                    self._v1_covered_state_digest(db, asset_id)
                    or _V1_UNTRUSTED_COVERAGE
                ),
                raw=row,
                observed_at=timestamp,
            )

    def project_v1_clear(
        self,
        media_key: str,
        *,
        asset_id: str | None = None,
        observed_at: float | None = None,
        connection: sqlite3.Connection | None = None,
    ) -> bool:
        """Delete the v1 projection and mark the absence as already observed."""

        key = _required_text(media_key, "media_key")
        timestamp = self._now(observed_at)
        with self.transaction(connection=connection) as db:
            result = db.execute("DELETE FROM progress WHERE media_key = ?", (key,))
            if asset_id is None:
                asset_id = self.resolve_legacy_key(key, connection=db)
            prior_shadow = self._one(
                db,
                "SELECT source_updated FROM video_v2_v1_shadow WHERE media_key = ?",
                (key,),
            )
            causal_updates = [timestamp]
            if prior_shadow is not None and prior_shadow["source_updated"] is not None:
                causal_updates.append(float(prior_shadow["source_updated"]))
            if asset_id is not None:
                current_state = self._one(
                    db,
                    """
                    SELECT updated_at FROM video_v2_asset_playback_state
                    WHERE asset_id = ?
                    """,
                    (asset_id,),
                )
                if current_state is not None:
                    causal_updates.append(float(current_state["updated_at"]))
            self._shadow_v1_row(
                db,
                media_key=key,
                present=False,
                row_digest=None,
                source_updated=max(causal_updates),
                asset_id=asset_id,
                covered_state_digest=(
                    self._v1_covered_state_digest(db, asset_id)
                    or _V1_UNTRUSTED_COVERAGE
                ),
                raw=None,
                observed_at=timestamp,
            )
            return bool(result.rowcount)

    def _reconcile_v1_present_row(
        self,
        db: sqlite3.Connection,
        *,
        rows: Mapping[str, Mapping[str, Any]],
        row_digests: Mapping[str, str],
        shadows: Mapping[str, Mapping[str, Any]],
        media_key: str,
        timestamp: float,
        create_missing: bool,
        report: dict[str, Any],
        import_id: str,
    ) -> None:
        row = rows[media_key]
        row_digest = row_digests[media_key]
        shadow = shadows.get(media_key)
        changed = (
            shadow is None
            or not bool(shadow["was_present"])
            or shadow["row_digest"] != row_digest
        )
        if not changed:
            report["unchanged"] += 1
            source_updated = float(row.get("updated") or timestamp)
            if shadow["source_updated"] is not None:
                # ``source_updated`` also carries the causal watermark
                # proving which v2 state this projected row covered.
                # Never move that watermark backwards after a Pi clock
                # correction merely because the row itself is unchanged.
                source_updated = max(
                    source_updated, float(shadow["source_updated"])
                )
            self._shadow_v1_row(
                db,
                media_key=media_key,
                present=True,
                row_digest=row_digest,
                source_updated=source_updated,
                asset_id=str(shadow["asset_id"]) if shadow["asset_id"] else None,
                covered_state_digest=shadow.get("covered_state_digest"),
                raw=row,
                observed_at=timestamp,
            )
            return
        report["changed"] += 1
        previously_bound = self.resolve_legacy_key(media_key, connection=db)
        shadow_asset_id = (
            str(shadow["asset_id"])
            if shadow is not None
            and bool(shadow["was_present"])
            and shadow["asset_id"] is not None
            else None
        )
        if shadow_asset_id is not None:
            # The compatibility shadow records which exact asset v2
            # projected into this one-key v1 row.  It is stronger than
            # an older parser-key binding after a symlink retarget.
            shadow_asset = self._assert_asset(db, shadow_asset_id)
            asset_id = shadow_asset_id
            work_id = (
                str(shadow_asset["work_id"])
                if shadow_asset["work_id"] is not None
                else None
            )
        else:
            asset_id, work_id = self._legacy_target(
                db,
                media_key=media_key,
                row=row,
                observed_at=timestamp,
                create_missing=create_missing,
            )
        if asset_id is None:
            report["unresolved"] += 1
            action = "unresolved"
            applied = False
            shadow_covers_state = False
        else:
            if previously_bound is None:
                report["created"] += 1
            shadow_covers_state = self._v1_shadow_covers_state(
                db,
                shadow=shadow,
                asset_id=asset_id,
            )
            if shadow is not None and not shadow_covers_state:
                applied = False
            else:
                applied = self._apply_v1_snapshot(
                    db,
                    media_key=media_key,
                    row=row,
                    row_digest=row_digest,
                    asset_id=asset_id,
                    work_id=work_id,
                    observed_at=timestamp,
                    authoritative=shadow_covers_state,
                )
            action = "applied" if applied else "stale"
            report[action] += 1
        try:
            source_updated = float(row.get("updated") or timestamp)
        except (TypeError, ValueError):
            source_updated = timestamp
        shadow_updated = source_updated
        if (
            applied
            and shadow_covers_state
            and shadow is not None
            and shadow.get("source_updated") is not None
        ):
            # A Pi clock correction can make an authoritative old-server
            # edit carry a lower wall timestamp than the v2 state it
            # replaces.  Retain the prior causal watermark atomically so
            # another rollback edit is still recognized even if the
            # process exits before the library is projected again.
            shadow_updated = max(
                source_updated, float(shadow["source_updated"])
            )
        self.record_import(
            import_id,
            source_key=media_key,
            action=action,
            content_digest=row_digest,
            source_updated=source_updated,
            asset_id=asset_id,
            work_id=work_id,
            raw=row,
            imported_at=timestamp,
            connection=db,
        )
        self._shadow_v1_row(
            db,
            media_key=media_key,
            present=True,
            row_digest=row_digest,
            source_updated=shadow_updated,
            asset_id=asset_id,
            covered_state_digest=(
                self._v1_covered_state_digest(db, asset_id)
                if applied
                else _V1_UNTRUSTED_COVERAGE
            ),
            raw=row,
            observed_at=timestamp,
        )

    def _reconcile_v1_missing_row(
        self,
        db: sqlite3.Connection,
        *,
        shadows: Mapping[str, Mapping[str, Any]],
        media_key: str,
        timestamp: float,
        absence_policy: str,
        report: dict[str, Any],
        import_id: str,
    ) -> None:
        shadow = shadows[media_key]
        prior_digest = str(shadow["row_digest"] or "unknown")
        asset_id = (
            str(shadow["asset_id"])
            if shadow["asset_id"] is not None
            else self.resolve_legacy_key(media_key, connection=db)
        )
        applied = False
        shadow_covers_state = bool(
            asset_id is not None
            and self._v1_shadow_covers_state(
                db,
                shadow=shadow,
                asset_id=asset_id,
            )
        )
        if (
            asset_id is not None
            and absence_policy == "clear"
            and shadow_covers_state
        ):
            self.clear_playhead(
                asset_id,
                reason="legacy_v1_row_absent",
                clear_work_auto=True,
                # The same exact row can be restored and deleted more
                # than once.  Each observed absence is a new causal
                # clear even when its prior row digest is identical.
                event_key=(
                    f"v1-absent:{media_key}:{prior_digest}:{import_id}"
                ),
                observed_at=timestamp,
                connection=db,
            )
            applied = True
            report["cleared"] += 1
        db.execute(
            """
                    INSERT INTO video_v2_v1_tombstones
                        (media_key, prior_digest, asset_id, detected_at,
                         applied_to_state, ambiguity_note)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(media_key, prior_digest) DO UPDATE SET
                        asset_id = COALESCE(
                            excluded.asset_id,
                            video_v2_v1_tombstones.asset_id
                        ),
                        detected_at = excluded.detected_at,
                        applied_to_state = MAX(
                            video_v2_v1_tombstones.applied_to_state,
                            excluded.applied_to_state
                        ),
                        ambiguity_note = excluded.ambiguity_note
                    """,
            (
                media_key,
                prior_digest,
                asset_id,
                timestamp,
                int(applied),
                "A missing v1 row normally means old ProgressStore.clear; "
                "the original row is retained in the shadow/import audit.",
            ),
        )
        report["tombstones"] += 1
        self.record_import(
            import_id,
            source_key=media_key,
            action="cleared" if applied else "absence-audited",
            content_digest=prior_digest,
            asset_id=asset_id,
            raw=None,
            imported_at=timestamp,
            connection=db,
        )
        shadow_updated = shadow["source_updated"]
        if applied and asset_id is not None:
            cleared_state = self._one(
                db,
                """
                        SELECT updated_at FROM video_v2_asset_playback_state
                        WHERE asset_id = ?
                        """,
                (asset_id,),
            )
            causal_updates = [timestamp]
            if shadow_updated is not None:
                causal_updates.append(float(shadow_updated))
            if cleared_state is not None:
                causal_updates.append(float(cleared_state["updated_at"]))
            # A v1 deletion is authoritative but carries no source wall
            # timestamp of its own.  Preserve which v2 state the
            # tombstone cleared so a row recreated after a backwards
            # clock correction is accepted on the next v2 start.
            shadow_updated = max(causal_updates)
        self._shadow_v1_row(
            db,
            media_key=media_key,
            present=False,
            row_digest=None,
            source_updated=shadow_updated,
            asset_id=asset_id,
            covered_state_digest=(
                self._v1_covered_state_digest(db, asset_id)
                if applied
                else _V1_UNTRUSTED_COVERAGE
            ),
            raw=None,
            observed_at=timestamp,
        )

    def reconcile_v1_progress(
        self,
        *,
        source_ref: str = "progress",
        create_missing: bool = True,
        absence_policy: str = "clear",
        observed_at: float | None = None,
        connection: sqlite3.Connection | None = None,
    ) -> dict[str, Any]:
        """Ingest old-server v1 edits and turn missing rows into tombstones.

        ``absence_policy='clear'`` applies a missing-row tombstone to the v2
        playhead.  ``'audit'`` records the ambiguous deletion without changing
        state.  Repeated identical snapshots are no-ops apart from the import
        run summary.
        """

        if absence_policy not in {"clear", "audit"}:
            raise ValueError("absence_policy must be 'clear' or 'audit'")
        timestamp = self._now(observed_at)
        report: dict[str, Any] = {
            "available": False,
            "rows": 0,
            "changed": 0,
            "unchanged": 0,
            "created": 0,
            "applied": 0,
            "stale": 0,
            "unresolved": 0,
            "cleared": 0,
            "tombstones": 0,
        }
        with self.transaction(connection=connection) as db:
            rows = self._read_v1_progress(db)
            if rows is None:
                return report
            report["available"] = True
            report["rows"] = len(rows)
            row_digests = {key: self._v1_row_digest(row) for key, row in rows.items()}
            snapshot_digest = _digest(sorted(row_digests.items()))
            import_id = self.begin_import(
                source_kind="sqlite-v1-progress",
                source_ref=source_ref,
                source_digest=snapshot_digest,
                started_at=timestamp,
                connection=db,
            )
            shadows = {
                str(row["media_key"]): row
                for row in self._all(db, "SELECT * FROM video_v2_v1_shadow")
            }
            for media_key in sorted(rows):
                self._reconcile_v1_present_row(
                    db,
                    rows=rows,
                    row_digests=row_digests,
                    shadows=shadows,
                    media_key=media_key,
                    timestamp=timestamp,
                    create_missing=create_missing,
                    report=report,
                    import_id=import_id,
                )

            missing = sorted(
                key
                for key, shadow in shadows.items()
                if bool(shadow["was_present"]) and key not in rows
            )
            for media_key in missing:
                self._reconcile_v1_missing_row(
                    db,
                    shadows=shadows,
                    media_key=media_key,
                    timestamp=timestamp,
                    absence_policy=absence_policy,
                    report=report,
                    import_id=import_id,
                )

            self.finish_import(
                import_id,
                status="complete",
                summary=report,
                finished_at=timestamp,
                connection=db,
            )
            report["import_id"] = import_id
            report["snapshot_digest"] = snapshot_digest
            return report
