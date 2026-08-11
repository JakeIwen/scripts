#!/usr/bin/env python3
"""Persistent media identity and playback history for the video library.

This module is deliberately additive.  Its tables use the ``video_v2_``
prefix, and it never creates, alters, replaces, or deletes the legacy
``progress`` and ``metadata`` tables.  The old video server can therefore use
the same database after this schema has been installed.

The catalog separates an exact playable asset from the logical work it
contains.  Paths, torrent names, and cleaned aliases are observations about an
asset rather than its identity.  Playback events are append-only, while the
asset playhead and work watched state are small projections for fast reads.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import math
import os
import sqlite3
import tempfile
import threading
import time
import uuid
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any, Callable


SCHEMA_VERSION = 2
DEFAULT_BUSY_TIMEOUT_MS = 5_000


class CatalogError(RuntimeError):
    """Base class for catalog failures."""


class CatalogConflict(CatalogError):
    """Raised when two durable identities contradict one another."""


class CatalogNotFound(CatalogError):
    """Raised when a requested catalog object does not exist."""


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _json_or_none(value: Any) -> str | None:
    return None if value is None else _json(value)


def _digest(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _finite_nonnegative(value: float | int | None, name: str) -> float | None:
    if value is None:
        return None
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise ValueError(f"{name} must be a finite non-negative number")
    return number


def _required_text(value: Any, name: str) -> str:
    text = str(value).strip()
    if not text:
        raise ValueError(f"{name} must not be empty")
    return text


def _optional_locator(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip().casefold()
    return text or None


def _path_key(path: str | os.PathLike[str]) -> str:
    value = os.path.expanduser(os.fspath(path))
    if not value:
        raise ValueError("path must not be empty")
    return os.path.normpath(os.path.abspath(value))


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _backup_path(path: Path) -> Path:
    stem = path.name[: -len(path.suffix)] if path.suffix else path.name
    return path.with_name(f"{stem}.pre-v2.sqlite3")


def _quick_check(connection: sqlite3.Connection) -> None:
    rows = connection.execute("PRAGMA quick_check").fetchall()
    if len(rows) != 1 or str(rows[0][0]).casefold() != "ok":
        messages = "; ".join(str(row[0]) for row in rows) or "no result"
        raise CatalogError(f"SQLite quick_check failed: {messages}")


def ensure_pre_v2_backup(db_path: str | os.PathLike[str]) -> str | None:
    """Create and verify the immutable sibling ``*.pre-v2.sqlite3`` backup.

    The snapshot uses SQLite's online backup API, is installed with an atomic
    rename, and is never refreshed once present.  This function intentionally
    is *not* called by :class:`MediaAssetCatalog`; deployment code must call it
    before the first v2 schema creation.  ``:memory:`` databases are a no-op.
    """

    if os.fspath(db_path) == ":memory:":
        return None
    source_path = Path(db_path).expanduser().resolve()
    if not source_path.is_file():
        raise FileNotFoundError(source_path)
    destination = _backup_path(source_path)
    if destination.exists():
        with contextlib.closing(sqlite3.connect(str(destination))) as existing:
            _quick_check(existing)
        os.chmod(destination, 0o600)
        return str(destination)

    source = sqlite3.connect(f"{source_path.as_uri()}?mode=ro", uri=True)
    temporary_name: str | None = None
    try:
        _quick_check(source)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{destination.name}.", suffix=".tmp", dir=str(destination.parent)
        )
        os.close(descriptor)
        target = sqlite3.connect(temporary_name)
        try:
            source.backup(target)
            _quick_check(target)
        finally:
            target.close()
        os.chmod(temporary_name, 0o600)
        with open(temporary_name, "rb") as snapshot:
            os.fsync(snapshot.fileno())
        os.replace(temporary_name, destination)
        temporary_name = None
        try:
            directory_fd = os.open(destination.parent, os.O_RDONLY)
        except OSError:
            directory_fd = None
        if directory_fd is not None:
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        return str(destination)
    finally:
        source.close()
        if temporary_name is not None:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(temporary_name)


_MIGRATIONS: tuple[tuple[int, str, tuple[str, ...]], ...] = (
    (
        1,
        "asset catalog and playback event model",
        (
            """
            CREATE TABLE video_v2_works (
                work_id TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                title TEXT,
                year INTEGER,
                series TEXT,
                season INTEGER,
                episode INTEGER,
                external_ids_json TEXT,
                metadata_json TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )
            """,
            """
            CREATE TABLE video_v2_assets (
                asset_id TEXT PRIMARY KEY,
                work_id TEXT REFERENCES video_v2_works(work_id),
                asset_kind TEXT NOT NULL,
                expected_size INTEGER,
                fingerprint_algorithm TEXT,
                fingerprint TEXT,
                metadata_json TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )
            """,
            """
            CREATE UNIQUE INDEX video_v2_assets_fingerprint
            ON video_v2_assets(fingerprint_algorithm, fingerprint, expected_size)
            WHERE fingerprint_algorithm IS NOT NULL AND fingerprint IS NOT NULL
            """,
            """
            CREATE TABLE video_v2_torrent_locators (
                locator_id TEXT PRIMARY KEY,
                asset_id TEXT NOT NULL REFERENCES video_v2_assets(asset_id),
                client_id TEXT NOT NULL,
                torrent_id TEXT NOT NULL,
                file_index INTEGER NOT NULL,
                info_hash_v1 TEXT,
                info_hash_v2 TEXT,
                first_seen REAL NOT NULL,
                last_seen REAL NOT NULL,
                metadata_json TEXT,
                UNIQUE(client_id, torrent_id, file_index)
            )
            """,
            """
            CREATE INDEX video_v2_torrent_v1
            ON video_v2_torrent_locators(client_id, info_hash_v1, file_index)
            """,
            """
            CREATE INDEX video_v2_torrent_v2
            ON video_v2_torrent_locators(client_id, info_hash_v2, file_index)
            """,
            """
            CREATE TABLE video_v2_file_identities (
                identity_id TEXT PRIMARY KEY,
                asset_id TEXT NOT NULL REFERENCES video_v2_assets(asset_id),
                device_id TEXT,
                inode INTEGER,
                size INTEGER,
                mtime_ns INTEGER,
                fingerprint_algorithm TEXT,
                fingerprint TEXT,
                observed_at REAL NOT NULL
            )
            """,
            """
            CREATE INDEX video_v2_file_device_inode
            ON video_v2_file_identities(device_id, inode)
            """,
            """
            CREATE INDEX video_v2_file_fingerprint
            ON video_v2_file_identities(fingerprint_algorithm, fingerprint, size)
            """,
            """
            CREATE TABLE video_v2_locations (
                location_id TEXT PRIMARY KEY,
                asset_id TEXT NOT NULL REFERENCES video_v2_assets(asset_id),
                path TEXT NOT NULL,
                location_kind TEXT NOT NULL,
                source TEXT,
                valid_from REAL NOT NULL,
                valid_to REAL,
                last_seen REAL NOT NULL,
                metadata_json TEXT
            )
            """,
            """
            CREATE UNIQUE INDEX video_v2_active_location
            ON video_v2_locations(path) WHERE valid_to IS NULL
            """,
            """
            CREATE INDEX video_v2_locations_asset
            ON video_v2_locations(asset_id, valid_to)
            """,
            """
            CREATE TABLE video_v2_aliases (
                alias_id TEXT PRIMARY KEY,
                asset_id TEXT NOT NULL REFERENCES video_v2_assets(asset_id),
                namespace TEXT NOT NULL,
                alias TEXT NOT NULL,
                provenance TEXT,
                valid_from REAL NOT NULL,
                valid_to REAL,
                last_seen REAL NOT NULL,
                metadata_json TEXT
            )
            """,
            """
            CREATE UNIQUE INDEX video_v2_active_alias
            ON video_v2_aliases(namespace, alias) WHERE valid_to IS NULL
            """,
            """
            CREATE INDEX video_v2_aliases_asset
            ON video_v2_aliases(asset_id, valid_to)
            """,
            """
            CREATE TABLE video_v2_legacy_keys (
                source TEXT NOT NULL,
                media_key TEXT NOT NULL,
                asset_id TEXT NOT NULL REFERENCES video_v2_assets(asset_id),
                first_seen REAL NOT NULL,
                last_seen REAL NOT NULL,
                metadata_json TEXT,
                PRIMARY KEY(source, media_key)
            )
            """,
            """
            CREATE TABLE video_v2_playback_sessions (
                session_id TEXT PRIMARY KEY,
                asset_id TEXT NOT NULL REFERENCES video_v2_assets(asset_id),
                started_at REAL NOT NULL,
                ended_at REAL,
                end_reason TEXT,
                launch_path TEXT,
                player_instance TEXT,
                metadata_json TEXT
            )
            """,
            """
            CREATE INDEX video_v2_sessions_asset
            ON video_v2_playback_sessions(asset_id, started_at)
            """,
            """
            CREATE TABLE video_v2_playback_events (
                event_id TEXT PRIMARY KEY,
                session_id TEXT REFERENCES video_v2_playback_sessions(session_id),
                asset_id TEXT NOT NULL REFERENCES video_v2_assets(asset_id),
                event_type TEXT NOT NULL,
                position REAL,
                duration REAL,
                completed INTEGER,
                playback_state TEXT,
                event_key TEXT,
                observed_at REAL NOT NULL,
                payload_json TEXT
            )
            """,
            """
            CREATE UNIQUE INDEX video_v2_event_dedupe
            ON video_v2_playback_events(asset_id, event_key)
            WHERE event_key IS NOT NULL
            """,
            """
            CREATE INDEX video_v2_events_asset_time
            ON video_v2_playback_events(asset_id, observed_at)
            """,
            """
            CREATE TABLE video_v2_asset_playback_state (
                asset_id TEXT PRIMARY KEY REFERENCES video_v2_assets(asset_id),
                position REAL NOT NULL DEFAULT 0,
                duration REAL NOT NULL DEFAULT 0,
                completed INTEGER NOT NULL DEFAULT 0,
                play_count INTEGER NOT NULL DEFAULT 0,
                updated_at REAL NOT NULL,
                last_session_id TEXT REFERENCES video_v2_playback_sessions(session_id),
                last_event_id TEXT REFERENCES video_v2_playback_events(event_id)
            )
            """,
            """
            CREATE TABLE video_v2_work_watch_state (
                work_id TEXT PRIMARY KEY REFERENCES video_v2_works(work_id),
                watched_auto INTEGER NOT NULL DEFAULT 0,
                watched_override INTEGER,
                play_count INTEGER NOT NULL DEFAULT 0,
                updated_at REAL NOT NULL,
                last_asset_id TEXT REFERENCES video_v2_assets(asset_id)
            )
            """,
            """
            CREATE TABLE video_v2_import_runs (
                import_id TEXT PRIMARY KEY,
                source_kind TEXT NOT NULL,
                source_ref TEXT NOT NULL,
                source_digest TEXT NOT NULL,
                started_at REAL NOT NULL,
                finished_at REAL,
                status TEXT NOT NULL,
                summary_json TEXT
            )
            """,
            """
            CREATE TABLE video_v2_import_records (
                import_record_id TEXT PRIMARY KEY,
                import_id TEXT NOT NULL REFERENCES video_v2_import_runs(import_id),
                source_key TEXT NOT NULL,
                content_digest TEXT,
                source_updated REAL,
                asset_id TEXT REFERENCES video_v2_assets(asset_id),
                work_id TEXT REFERENCES video_v2_works(work_id),
                action TEXT NOT NULL,
                raw_json TEXT,
                imported_at REAL NOT NULL,
                UNIQUE(import_id, source_key)
            )
            """,
        ),
    ),
    (
        2,
        "legacy v1 compatibility shadow and tombstones",
        (
            """
            CREATE TABLE video_v2_v1_shadow (
                media_key TEXT PRIMARY KEY,
                was_present INTEGER NOT NULL,
                row_digest TEXT,
                source_updated REAL,
                asset_id TEXT REFERENCES video_v2_assets(asset_id),
                raw_json TEXT,
                last_seen_at REAL NOT NULL
            )
            """,
            """
            CREATE TABLE video_v2_v1_tombstones (
                media_key TEXT NOT NULL,
                prior_digest TEXT NOT NULL,
                asset_id TEXT REFERENCES video_v2_assets(asset_id),
                detected_at REAL NOT NULL,
                applied_to_state INTEGER NOT NULL,
                ambiguity_note TEXT NOT NULL,
                PRIMARY KEY(media_key, prior_digest)
            )
            """,
        ),
    ),
)


class MediaAssetCatalog:
    """SQLite-backed asset catalog that can share the legacy DB connection."""

    def __init__(
        self,
        path: str | os.PathLike[str] | None = None,
        *,
        connection: sqlite3.Connection | None = None,
        lock: threading.RLock | None = None,
        clock: Callable[[], float] = time.time,
        busy_timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS,
    ):
        if connection is None and path is None:
            raise ValueError("path or connection is required")
        if connection is not None and path is not None:
            raise ValueError("pass path or connection, not both")
        if busy_timeout_ms < 0:
            raise ValueError("busy_timeout_ms must be non-negative")
        self.clock = clock
        self.lock = lock or threading.RLock()
        self._owns_connection = connection is None
        if connection is None:
            db_path = os.fspath(path)
            if db_path != ":memory:":
                os.makedirs(os.path.dirname(os.path.abspath(db_path)) or ".", exist_ok=True)
            connection = sqlite3.connect(db_path, check_same_thread=False)
        self.connection = connection
        self.connection.execute(f"PRAGMA busy_timeout = {int(busy_timeout_ms)}")
        self.connection.execute("PRAGMA foreign_keys = ON")
        if int(self.connection.execute("PRAGMA foreign_keys").fetchone()[0]) != 1:
            raise CatalogError(
                "foreign keys could not be enabled; initialize the catalog outside a transaction"
            )
        self._migrate()

    def close(self) -> None:
        if self._owns_connection:
            self.connection.close()

    def __enter__(self) -> "MediaAssetCatalog":
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.close()

    @contextlib.contextmanager
    def transaction(
        self,
        *,
        connection: sqlite3.Connection | None = None,
        immediate: bool = True,
    ) -> Iterator[sqlite3.Connection]:
        """Yield a transaction usable for atomic legacy-v1 and v2 writes.

        If the supplied connection is already in a transaction, ownership stays
        with its caller.  Otherwise this context begins and commits (or rolls
        back) the transaction.  Deployment code can therefore write a v1
        ``progress`` row and call :meth:`checkpoint` with the yielded connection
        as one atomic operation.
        """

        db = connection or self.connection
        with self.lock:
            owns_transaction = not db.in_transaction
            if owns_transaction:
                db.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
            try:
                yield db
            except BaseException:
                if owns_transaction:
                    db.rollback()
                raise
            else:
                if owns_transaction:
                    db.commit()

    def _migrate(self) -> None:
        with self.transaction(immediate=True) as db:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS video_v2_schema_migrations (
                    version INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    applied_at REAL NOT NULL
                )
                """
            )
            applied = {
                int(row[0])
                for row in db.execute(
                    "SELECT version FROM video_v2_schema_migrations"
                ).fetchall()
            }
            unknown = [version for version in applied if version > SCHEMA_VERSION]
            if unknown:
                raise CatalogError(
                    f"database schema {max(unknown)} is newer than supported {SCHEMA_VERSION}"
                )
            for version, name, statements in _MIGRATIONS:
                if version in applied:
                    continue
                for statement in statements:
                    db.execute(statement)
                db.execute(
                    """
                    INSERT INTO video_v2_schema_migrations(version, name, applied_at)
                    VALUES (?, ?, ?)
                    """,
                    (version, name, float(self.clock())),
                )

    @staticmethod
    def _row(cursor: sqlite3.Cursor, row: Any) -> dict[str, Any] | None:
        if row is None:
            return None
        if isinstance(row, sqlite3.Row):
            return dict(row)
        return {column[0]: row[index] for index, column in enumerate(cursor.description)}

    def _one(
        self,
        db: sqlite3.Connection,
        sql: str,
        parameters: tuple[Any, ...] = (),
    ) -> dict[str, Any] | None:
        cursor = db.execute(sql, parameters)
        return self._row(cursor, cursor.fetchone())

    def _all(
        self,
        db: sqlite3.Connection,
        sql: str,
        parameters: tuple[Any, ...] = (),
    ) -> list[dict[str, Any]]:
        cursor = db.execute(sql, parameters)
        return [self._row(cursor, row) for row in cursor.fetchall()]  # type: ignore[misc]

    def _now(self, value: float | None = None) -> float:
        timestamp = float(self.clock() if value is None else value)
        if not math.isfinite(timestamp):
            raise ValueError("timestamp must be finite")
        return timestamp

    def _assert_asset(self, db: sqlite3.Connection, asset_id: str) -> dict[str, Any]:
        asset = self._one(db, "SELECT * FROM video_v2_assets WHERE asset_id = ?", (asset_id,))
        if asset is None:
            raise CatalogNotFound(f"unknown asset {asset_id}")
        return asset

    def _assert_work(self, db: sqlite3.Connection, work_id: str) -> dict[str, Any]:
        work = self._one(db, "SELECT * FROM video_v2_works WHERE work_id = ?", (work_id,))
        if work is None:
            raise CatalogNotFound(f"unknown work {work_id}")
        return work

    # -- Logical works and exact assets ---------------------------------

    def create_work(
        self,
        kind: str,
        *,
        title: str | None = None,
        year: int | None = None,
        series: str | None = None,
        season: int | None = None,
        episode: int | None = None,
        external_ids: Mapping[str, Any] | None = None,
        metadata: Mapping[str, Any] | None = None,
        observed_at: float | None = None,
        connection: sqlite3.Connection | None = None,
    ) -> str:
        timestamp = self._now(observed_at)
        work_id = _new_id("wrk")
        with self.transaction(connection=connection) as db:
            db.execute(
                """
                INSERT INTO video_v2_works
                    (work_id, kind, title, year, series, season, episode,
                     external_ids_json, metadata_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    work_id,
                    _required_text(kind, "kind"),
                    title,
                    year,
                    series,
                    season,
                    episode,
                    _json_or_none(dict(external_ids) if external_ids is not None else None),
                    _json_or_none(dict(metadata) if metadata is not None else None),
                    timestamp,
                    timestamp,
                ),
            )
        return work_id

    def create_asset(
        self,
        *,
        asset_kind: str = "generic",
        work_id: str | None = None,
        expected_size: int | None = None,
        fingerprint: str | None = None,
        fingerprint_algorithm: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        observed_at: float | None = None,
        connection: sqlite3.Connection | None = None,
    ) -> str:
        timestamp = self._now(observed_at)
        if expected_size is not None and int(expected_size) < 0:
            raise ValueError("expected_size must be non-negative")
        fingerprint = _optional_locator(fingerprint)
        fingerprint_algorithm = _optional_locator(fingerprint_algorithm)
        if bool(fingerprint) != bool(fingerprint_algorithm):
            raise ValueError("fingerprint and fingerprint_algorithm must be supplied together")
        asset_id = _new_id("ast")
        with self.transaction(connection=connection) as db:
            if work_id is not None:
                self._assert_work(db, work_id)
            db.execute(
                """
                INSERT INTO video_v2_assets
                    (asset_id, work_id, asset_kind, expected_size,
                     fingerprint_algorithm, fingerprint, metadata_json,
                     created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    asset_id,
                    work_id,
                    _required_text(asset_kind, "asset_kind"),
                    int(expected_size) if expected_size is not None else None,
                    fingerprint_algorithm,
                    fingerprint,
                    _json_or_none(dict(metadata) if metadata is not None else None),
                    timestamp,
                    timestamp,
                ),
            )
        return asset_id

    def lookup_asset(
        self,
        asset_id: str,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> dict[str, Any] | None:
        db = connection or self.connection
        with self.lock:
            return self._one(db, "SELECT * FROM video_v2_assets WHERE asset_id = ?", (asset_id,))

    def lookup_work(
        self,
        work_id: str,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> dict[str, Any] | None:
        db = connection or self.connection
        with self.lock:
            return self._one(db, "SELECT * FROM video_v2_works WHERE work_id = ?", (work_id,))

    def bind_work(
        self,
        asset_id: str,
        work_id: str,
        *,
        observed_at: float | None = None,
        connection: sqlite3.Connection | None = None,
    ) -> None:
        timestamp = self._now(observed_at)
        with self.transaction(connection=connection) as db:
            self._assert_asset(db, asset_id)
            self._assert_work(db, work_id)
            db.execute(
                "UPDATE video_v2_assets SET work_id = ?, updated_at = ? WHERE asset_id = ?",
                (work_id, timestamp, asset_id),
            )

    # -- Torrent/file identity ------------------------------------------

    def resolve_or_create_torrent_asset(
        self,
        *,
        client_id: str,
        torrent_id: str,
        file_index: int,
        info_hash_v1: str | None = None,
        info_hash_v2: str | None = None,
        expected_size: int | None = None,
        path: str | os.PathLike[str] | None = None,
        metadata: Mapping[str, Any] | None = None,
        observed_at: float | None = None,
        preferred_asset_id: str | None = None,
        connection: sqlite3.Connection | None = None,
    ) -> str:
        """Resolve a qBittorrent file, retaining both v1 and v2 hash evidence."""

        client = _required_text(client_id, "client_id")
        torrent = _required_text(torrent_id, "torrent_id").casefold()
        index = int(file_index)
        if index < 0:
            raise ValueError("file_index must be non-negative")
        hash_v1 = _optional_locator(info_hash_v1)
        hash_v2 = _optional_locator(info_hash_v2)
        if expected_size is not None and int(expected_size) < 0:
            raise ValueError("expected_size must be non-negative")
        timestamp = self._now(observed_at)

        with self.transaction(connection=connection) as db:
            clauses = ["(client_id = ? AND torrent_id = ? AND file_index = ?)"]
            parameters: list[Any] = [client, torrent, index]
            if hash_v1 is not None:
                clauses.append("(client_id = ? AND info_hash_v1 = ? AND file_index = ?)")
                parameters.extend((client, hash_v1, index))
            if hash_v2 is not None:
                clauses.append("(client_id = ? AND info_hash_v2 = ? AND file_index = ?)")
                parameters.extend((client, hash_v2, index))
            matches = self._all(
                db,
                "SELECT * FROM video_v2_torrent_locators WHERE " + " OR ".join(clauses),
                tuple(parameters),
            )
            asset_ids = {str(row["asset_id"]) for row in matches}
            if len(asset_ids) > 1:
                raise CatalogConflict(
                    "torrent locator fields resolve to multiple assets; manual repair is required"
                )
            if preferred_asset_id is not None:
                self._assert_asset(db, preferred_asset_id)
                if asset_ids and asset_ids != {preferred_asset_id}:
                    raise CatalogConflict(
                        "torrent locator already belongs to a different asset; "
                        "assets are never merged implicitly"
                    )
            exact = next(
                (
                    row
                    for row in matches
                    if row["client_id"] == client
                    and row["torrent_id"] == torrent
                    and int(row["file_index"]) == index
                ),
                None,
            )
            if exact is not None:
                if hash_v1 and exact["info_hash_v1"] not in (None, hash_v1):
                    raise CatalogConflict("torrent v1 hash changed for an existing locator")
                if hash_v2 and exact["info_hash_v2"] not in (None, hash_v2):
                    raise CatalogConflict("torrent v2 hash changed for an existing locator")

            if asset_ids:
                asset_id = asset_ids.pop()
            elif preferred_asset_id is not None:
                asset_id = preferred_asset_id
            else:
                asset_id = self.create_asset(
                    asset_kind="torrent",
                    expected_size=expected_size,
                    metadata=metadata,
                    observed_at=timestamp,
                    connection=db,
                )
            existing_asset = self._assert_asset(db, asset_id)
            if (
                expected_size is not None
                and existing_asset["expected_size"] is not None
                and int(existing_asset["expected_size"]) != int(expected_size)
                and existing_asset["asset_kind"] != "provisional-file"
            ):
                raise CatalogConflict("torrent expected size contradicts the existing asset")
            db.execute(
                """
                INSERT INTO video_v2_torrent_locators
                    (locator_id, asset_id, client_id, torrent_id, file_index,
                     info_hash_v1, info_hash_v2, first_seen, last_seen, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(client_id, torrent_id, file_index) DO UPDATE SET
                    last_seen = excluded.last_seen,
                    info_hash_v1 = COALESCE(video_v2_torrent_locators.info_hash_v1,
                                            excluded.info_hash_v1),
                    info_hash_v2 = COALESCE(video_v2_torrent_locators.info_hash_v2,
                                            excluded.info_hash_v2),
                    metadata_json = COALESCE(excluded.metadata_json,
                                             video_v2_torrent_locators.metadata_json)
                """,
                (
                    _new_id("tor"),
                    asset_id,
                    client,
                    torrent,
                    index,
                    hash_v1,
                    hash_v2,
                    timestamp,
                    timestamp,
                    _json_or_none(dict(metadata) if metadata is not None else None),
                ),
            )
            db.execute(
                """
                UPDATE video_v2_assets
                SET expected_size = CASE
                        WHEN asset_kind = 'provisional-file' AND ? IS NOT NULL THEN ?
                        ELSE COALESCE(expected_size, ?)
                    END,
                    asset_kind = CASE WHEN asset_kind = 'provisional-file'
                                      THEN 'torrent' ELSE asset_kind END,
                    updated_at = ?
                WHERE asset_id = ?
                """,
                (
                    int(expected_size) if expected_size is not None else None,
                    int(expected_size) if expected_size is not None else None,
                    int(expected_size) if expected_size is not None else None,
                    timestamp,
                    asset_id,
                ),
            )
            if path is not None:
                self.record_location(
                    asset_id,
                    path,
                    location_kind="torrent",
                    source=client,
                    observed_at=timestamp,
                    connection=db,
                )
            return asset_id

    def attach_torrent_locator(
        self,
        asset_id: str,
        *,
        client_id: str,
        torrent_id: str,
        file_index: int,
        info_hash_v1: str | None = None,
        info_hash_v2: str | None = None,
        expected_size: int | None = None,
        path: str | os.PathLike[str] | None = None,
        metadata: Mapping[str, Any] | None = None,
        observed_at: float | None = None,
        connection: sqlite3.Connection | None = None,
    ) -> str:
        """Attach qB identity to a provisional asset without implicit merging."""

        return self.resolve_or_create_torrent_asset(
            client_id=client_id,
            torrent_id=torrent_id,
            file_index=file_index,
            info_hash_v1=info_hash_v1,
            info_hash_v2=info_hash_v2,
            expected_size=expected_size,
            path=path,
            metadata=metadata,
            observed_at=observed_at,
            preferred_asset_id=asset_id,
            connection=connection,
        )

    def lookup_torrent_asset(
        self,
        *,
        client_id: str,
        torrent_id: str | None = None,
        file_index: int,
        info_hash_v1: str | None = None,
        info_hash_v2: str | None = None,
        connection: sqlite3.Connection | None = None,
    ) -> str | None:
        client = _required_text(client_id, "client_id")
        index = int(file_index)
        clauses: list[str] = []
        parameters: list[Any] = []
        if torrent_id is not None:
            clauses.append("(client_id = ? AND torrent_id = ? AND file_index = ?)")
            parameters.extend((client, _required_text(torrent_id, "torrent_id").casefold(), index))
        if info_hash_v1 is not None:
            clauses.append("(client_id = ? AND info_hash_v1 = ? AND file_index = ?)")
            parameters.extend((client, _optional_locator(info_hash_v1), index))
        if info_hash_v2 is not None:
            clauses.append("(client_id = ? AND info_hash_v2 = ? AND file_index = ?)")
            parameters.extend((client, _optional_locator(info_hash_v2), index))
        if not clauses:
            raise ValueError("one torrent identifier is required")
        db = connection or self.connection
        with self.lock:
            rows = self._all(
                db,
                "SELECT DISTINCT asset_id FROM video_v2_torrent_locators WHERE "
                + " OR ".join(clauses),
                tuple(parameters),
            )
        asset_ids = {str(row["asset_id"]) for row in rows}
        if len(asset_ids) > 1:
            raise CatalogConflict("torrent lookup resolves to multiple assets")
        return next(iter(asset_ids), None)

    def list_torrent_locators(
        self,
        asset_id: str | None = None,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> list[dict[str, Any]]:
        """Return known qB file identities without changing observation state."""

        db = connection or self.connection
        parameters: tuple[Any, ...] = () if asset_id is None else (asset_id,)
        filter_sql = "" if asset_id is None else "WHERE asset_id = ?"
        with self.lock:
            return self._all(
                db,
                f"""
                SELECT * FROM video_v2_torrent_locators
                {filter_sql}
                ORDER BY client_id, torrent_id, file_index, first_seen, locator_id
                """,
                parameters,
            )

    def resolve_or_create_provisional_file(
        self,
        path: str | os.PathLike[str],
        *,
        size: int | None = None,
        device_id: str | None = None,
        inode: int | None = None,
        mtime_ns: int | None = None,
        fingerprint: str | None = None,
        fingerprint_algorithm: str = "blake2b-partial",
        metadata: Mapping[str, Any] | None = None,
        observed_at: float | None = None,
        preferred_asset_id: str | None = None,
        connection: sqlite3.Connection | None = None,
    ) -> str:
        """Create a trackable asset for any ordinary file without blocking play."""

        normalized_path = _path_key(path)
        timestamp = self._now(observed_at)
        if size is None or device_id is None or inode is None or mtime_ns is None:
            try:
                stat = os.stat(normalized_path)
            except OSError:
                stat = None
            if stat is not None:
                size = stat.st_size if size is None else size
                device_id = str(stat.st_dev) if device_id is None else str(device_id)
                inode = stat.st_ino if inode is None else inode
                mtime_ns = stat.st_mtime_ns if mtime_ns is None else mtime_ns
        if size is not None and int(size) < 0:
            raise ValueError("size must be non-negative")
        fingerprint_value = _optional_locator(fingerprint)
        fingerprint_algo = (
            _optional_locator(fingerprint_algorithm) if fingerprint_value is not None else None
        )

        with self.transaction(connection=connection) as db:
            asset_ids: set[str] = set()
            path_row = self._one(
                db,
                "SELECT asset_id FROM video_v2_locations WHERE path = ? AND valid_to IS NULL",
                (normalized_path,),
            )
            if path_row is not None:
                path_asset_id = str(path_row["asset_id"])
                prior_identities = self._all(
                    db,
                    """
                    SELECT device_id, inode, size
                    FROM video_v2_file_identities
                    WHERE asset_id = ? AND device_id IS NOT NULL AND inode IS NOT NULL
                    """,
                    (path_asset_id,),
                )
                identity_matches = any(
                    str(row["device_id"]) == str(device_id)
                    and int(row["inode"]) == int(inode)
                    for row in prior_identities
                ) if device_id is not None and inode is not None else False
                if not prior_identities or identity_matches:
                    asset_ids.add(path_asset_id)
                else:
                    # A pathname is observation evidence, not permanent
                    # identity.  Reuse after replacement must not inherit the
                    # prior file's playhead.
                    self.retire_location(
                        normalized_path,
                        observed_at=timestamp,
                        connection=db,
                    )
            if fingerprint_value is not None:
                rows = self._all(
                    db,
                    """
                    SELECT DISTINCT asset_id FROM video_v2_file_identities
                    WHERE fingerprint_algorithm = ? AND fingerprint = ?
                      AND (size = ? OR size IS NULL OR ? IS NULL)
                    """,
                    (fingerprint_algo, fingerprint_value, size, size),
                )
                asset_ids.update(str(row["asset_id"]) for row in rows)
            if device_id is not None and inode is not None:
                rows = self._all(
                    db,
                    """
                    SELECT DISTINCT asset_id FROM video_v2_file_identities
                    WHERE device_id = ? AND inode = ?
                    """,
                    (str(device_id), int(inode)),
                )
                asset_ids.update(str(row["asset_id"]) for row in rows)
            if not asset_ids and preferred_asset_id is not None:
                self._assert_asset(db, preferred_asset_id)
                preferred_identities = self._all(
                    db,
                    """
                    SELECT device_id, inode, size
                    FROM video_v2_file_identities
                    WHERE asset_id = ? AND device_id IS NOT NULL AND inode IS NOT NULL
                    """,
                    (preferred_asset_id,),
                )
                preferred_matches = any(
                    str(row["device_id"]) == str(device_id)
                    and int(row["inode"]) == int(inode)
                    for row in preferred_identities
                ) if device_id is not None and inode is not None else False
                if not preferred_identities or preferred_matches:
                    asset_ids.add(preferred_asset_id)
            if len(asset_ids) > 1:
                raise CatalogConflict("file observations resolve to multiple assets")
            if asset_ids:
                asset_id = asset_ids.pop()
            else:
                asset_id = self.create_asset(
                    asset_kind="provisional-file",
                    expected_size=size,
                    fingerprint=fingerprint_value,
                    fingerprint_algorithm=fingerprint_algo,
                    metadata=metadata,
                    observed_at=timestamp,
                    connection=db,
                )
            identity_match = self._one(
                db,
                """
                SELECT identity_id FROM video_v2_file_identities
                WHERE asset_id = ?
                  AND device_id IS ? AND inode IS ? AND size IS ?
                  AND fingerprint_algorithm IS ? AND fingerprint IS ?
                ORDER BY observed_at DESC LIMIT 1
                """,
                (
                    asset_id,
                    str(device_id) if device_id is not None else None,
                    int(inode) if inode is not None else None,
                    int(size) if size is not None else None,
                    fingerprint_algo,
                    fingerprint_value,
                ),
            )
            if identity_match is None:
                db.execute(
                    """
                    INSERT INTO video_v2_file_identities
                        (identity_id, asset_id, device_id, inode, size, mtime_ns,
                         fingerprint_algorithm, fingerprint, observed_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        _new_id("fid"),
                        asset_id,
                        str(device_id) if device_id is not None else None,
                        int(inode) if inode is not None else None,
                        int(size) if size is not None else None,
                        int(mtime_ns) if mtime_ns is not None else None,
                        fingerprint_algo,
                        fingerprint_value,
                        timestamp,
                    ),
                )
            self.record_location(
                asset_id,
                normalized_path,
                location_kind="file",
                observed_at=timestamp,
                connection=db,
            )
            return asset_id

    # -- Time-versioned paths and aliases -------------------------------

    def record_location(
        self,
        asset_id: str,
        path: str | os.PathLike[str],
        *,
        location_kind: str = "file",
        source: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        observed_at: float | None = None,
        connection: sqlite3.Connection | None = None,
    ) -> str:
        normalized_path = _path_key(path)
        timestamp = self._now(observed_at)
        kind = _required_text(location_kind, "location_kind")
        metadata_json = _json_or_none(dict(metadata) if metadata is not None else None)
        with self.transaction(connection=connection) as db:
            self._assert_asset(db, asset_id)
            current = self._one(
                db,
                "SELECT * FROM video_v2_locations WHERE path = ? AND valid_to IS NULL",
                (normalized_path,),
            )
            if current is not None and current["asset_id"] == asset_id:
                db.execute(
                    """
                    UPDATE video_v2_locations
                    SET last_seen = MAX(last_seen, ?), location_kind = ?,
                        source = COALESCE(?, source),
                        metadata_json = COALESCE(?, metadata_json)
                    WHERE location_id = ?
                    """,
                    (timestamp, kind, source, metadata_json, current["location_id"]),
                )
                return str(current["location_id"])
            if current is not None:
                db.execute(
                    """
                    UPDATE video_v2_locations
                    SET valid_to = MAX(valid_from, ?), last_seen = MAX(last_seen, ?)
                    WHERE location_id = ?
                    """,
                    (timestamp, timestamp, current["location_id"]),
                )
            location_id = _new_id("loc")
            db.execute(
                """
                INSERT INTO video_v2_locations
                    (location_id, asset_id, path, location_kind, source,
                     valid_from, valid_to, last_seen, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?)
                """,
                (
                    location_id,
                    asset_id,
                    normalized_path,
                    kind,
                    source,
                    timestamp,
                    timestamp,
                    metadata_json,
                ),
            )
            return location_id

    def retire_location(
        self,
        path: str | os.PathLike[str],
        *,
        observed_at: float | None = None,
        connection: sqlite3.Connection | None = None,
    ) -> bool:
        normalized_path = _path_key(path)
        timestamp = self._now(observed_at)
        with self.transaction(connection=connection) as db:
            result = db.execute(
                """
                UPDATE video_v2_locations
                SET valid_to = MAX(valid_from, ?), last_seen = MAX(last_seen, ?)
                WHERE path = ? AND valid_to IS NULL
                """,
                (timestamp, timestamp, normalized_path),
            )
            return bool(result.rowcount)

    def resolve_path(
        self,
        path: str | os.PathLike[str],
        *,
        include_historical: bool = False,
        connection: sqlite3.Connection | None = None,
    ) -> str | None:
        normalized_path = _path_key(path)
        canonical_path = os.path.realpath(normalized_path)
        candidates = (
            (normalized_path, canonical_path)
            if canonical_path != normalized_path
            else (normalized_path,)
        )
        db = connection or self.connection
        suffix = "" if include_historical else "AND valid_to IS NULL"
        with self.lock:
            rows = self._all(
                db,
                f"""
                SELECT asset_id, path FROM video_v2_locations
                WHERE path IN ({', '.join('?' for _ in candidates)}) {suffix}
                ORDER BY (path = ?) DESC, (valid_to IS NULL) DESC, last_seen DESC
                """,
                (*candidates, normalized_path),
            )
        return str(rows[0]["asset_id"]) if rows else None

    def list_locations(
        self,
        asset_id: str,
        *,
        active_only: bool = False,
        connection: sqlite3.Connection | None = None,
    ) -> list[dict[str, Any]]:
        db = connection or self.connection
        suffix = "AND valid_to IS NULL" if active_only else ""
        with self.lock:
            return self._all(
                db,
                f"""
                SELECT * FROM video_v2_locations
                WHERE asset_id = ? {suffix}
                ORDER BY valid_from, location_id
                """,
                (asset_id,),
            )

    def record_alias(
        self,
        asset_id: str,
        alias: str,
        *,
        namespace: str = "display",
        provenance: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        observed_at: float | None = None,
        connection: sqlite3.Connection | None = None,
    ) -> str:
        alias_text = _required_text(alias, "alias")
        namespace_text = _required_text(namespace, "namespace")
        timestamp = self._now(observed_at)
        metadata_json = _json_or_none(dict(metadata) if metadata is not None else None)
        with self.transaction(connection=connection) as db:
            self._assert_asset(db, asset_id)
            current = self._one(
                db,
                """
                SELECT * FROM video_v2_aliases
                WHERE namespace = ? AND alias = ? AND valid_to IS NULL
                """,
                (namespace_text, alias_text),
            )
            if current is not None and current["asset_id"] == asset_id:
                db.execute(
                    """
                    UPDATE video_v2_aliases
                    SET last_seen = MAX(last_seen, ?),
                        provenance = COALESCE(?, provenance),
                        metadata_json = COALESCE(?, metadata_json)
                    WHERE alias_id = ?
                    """,
                    (timestamp, provenance, metadata_json, current["alias_id"]),
                )
                return str(current["alias_id"])
            if current is not None:
                db.execute(
                    """
                    UPDATE video_v2_aliases
                    SET valid_to = MAX(valid_from, ?), last_seen = MAX(last_seen, ?)
                    WHERE alias_id = ?
                    """,
                    (timestamp, timestamp, current["alias_id"]),
                )
            alias_id = _new_id("als")
            db.execute(
                """
                INSERT INTO video_v2_aliases
                    (alias_id, asset_id, namespace, alias, provenance,
                     valid_from, valid_to, last_seen, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?)
                """,
                (
                    alias_id,
                    asset_id,
                    namespace_text,
                    alias_text,
                    provenance,
                    timestamp,
                    timestamp,
                    metadata_json,
                ),
            )
            return alias_id

    def retire_alias(
        self,
        alias: str,
        *,
        namespace: str = "display",
        observed_at: float | None = None,
        connection: sqlite3.Connection | None = None,
    ) -> bool:
        timestamp = self._now(observed_at)
        with self.transaction(connection=connection) as db:
            result = db.execute(
                """
                UPDATE video_v2_aliases
                SET valid_to = MAX(valid_from, ?), last_seen = MAX(last_seen, ?)
                WHERE namespace = ? AND alias = ? AND valid_to IS NULL
                """,
                (
                    timestamp,
                    timestamp,
                    _required_text(namespace, "namespace"),
                    _required_text(alias, "alias"),
                ),
            )
            return bool(result.rowcount)

    def resolve_alias(
        self,
        alias: str,
        *,
        namespace: str = "display",
        include_historical: bool = False,
        connection: sqlite3.Connection | None = None,
    ) -> str | None:
        db = connection or self.connection
        suffix = "" if include_historical else "AND valid_to IS NULL"
        with self.lock:
            rows = self._all(
                db,
                f"""
                SELECT asset_id FROM video_v2_aliases
                WHERE namespace = ? AND alias = ? {suffix}
                ORDER BY (valid_to IS NULL) DESC, last_seen DESC
                """,
                (_required_text(namespace, "namespace"), _required_text(alias, "alias")),
            )
        return str(rows[0]["asset_id"]) if rows else None

    # -- Legacy parser keys ---------------------------------------------

    def bind_legacy_key(
        self,
        asset_id: str,
        media_key: str,
        *,
        source: str = "v1-progress",
        metadata: Mapping[str, Any] | None = None,
        observed_at: float | None = None,
        replace: bool = False,
        connection: sqlite3.Connection | None = None,
    ) -> None:
        timestamp = self._now(observed_at)
        source_text = _required_text(source, "source")
        key = _required_text(media_key, "media_key")
        metadata_json = _json_or_none(dict(metadata) if metadata is not None else None)
        with self.transaction(connection=connection) as db:
            self._assert_asset(db, asset_id)
            current = self._one(
                db,
                "SELECT * FROM video_v2_legacy_keys WHERE source = ? AND media_key = ?",
                (source_text, key),
            )
            if current is not None and current["asset_id"] != asset_id and not replace:
                raise CatalogConflict(
                    f"legacy key {source_text}:{key} is already bound to another asset"
                )
            db.execute(
                """
                INSERT INTO video_v2_legacy_keys
                    (source, media_key, asset_id, first_seen, last_seen, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(source, media_key) DO UPDATE SET
                    asset_id = CASE WHEN ? THEN excluded.asset_id
                                    ELSE video_v2_legacy_keys.asset_id END,
                    last_seen = MAX(video_v2_legacy_keys.last_seen, excluded.last_seen),
                    metadata_json = COALESCE(excluded.metadata_json,
                                             video_v2_legacy_keys.metadata_json)
                """,
                (
                    source_text,
                    key,
                    asset_id,
                    timestamp,
                    timestamp,
                    metadata_json,
                    int(bool(replace)),
                ),
            )

    def resolve_legacy_key(
        self,
        media_key: str,
        *,
        source: str = "v1-progress",
        connection: sqlite3.Connection | None = None,
    ) -> str | None:
        db = connection or self.connection
        with self.lock:
            row = self._one(
                db,
                """
                SELECT asset_id FROM video_v2_legacy_keys
                WHERE source = ? AND media_key = ?
                """,
                (_required_text(source, "source"), _required_text(media_key, "media_key")),
            )
        return str(row["asset_id"]) if row is not None else None

    # -- Playback sessions, append-only events, and projections ---------

    def _insert_event(
        self,
        db: sqlite3.Connection,
        *,
        asset_id: str,
        event_type: str,
        observed_at: float,
        session_id: str | None = None,
        position: float | None = None,
        duration: float | None = None,
        completed: bool | None = None,
        playback_state: str | None = None,
        event_key: str | None = None,
        payload: Mapping[str, Any] | None = None,
    ) -> tuple[str, bool]:
        event_id = _new_id("evt")
        result = db.execute(
            """
            INSERT INTO video_v2_playback_events
                (event_id, session_id, asset_id, event_type, position, duration,
                 completed, playback_state, event_key, observed_at, payload_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT DO NOTHING
            """,
            (
                event_id,
                session_id,
                asset_id,
                _required_text(event_type, "event_type"),
                position,
                duration,
                None if completed is None else int(bool(completed)),
                playback_state,
                event_key,
                observed_at,
                _json_or_none(dict(payload) if payload is not None else None),
            ),
        )
        if result.rowcount:
            return event_id, True
        if event_key is None:
            raise CatalogConflict("playback event collided without a dedupe key")
        existing = self._one(
            db,
            """
            SELECT event_id FROM video_v2_playback_events
            WHERE asset_id = ? AND event_key = ?
            """,
            (asset_id, event_key),
        )
        if existing is None:
            raise CatalogConflict("playback event was not inserted and could not be found")
        return str(existing["event_id"]), False

    def _put_asset_state(
        self,
        db: sqlite3.Connection,
        *,
        asset_id: str,
        updated_at: float,
        position: float | None = None,
        duration: float | None = None,
        completed: bool | None = None,
        play_count: int | None = None,
        increment_play: bool = False,
        last_session_id: str | None = None,
        last_event_id: str | None = None,
    ) -> bool:
        current = self._one(
            db,
            "SELECT * FROM video_v2_asset_playback_state WHERE asset_id = ?",
            (asset_id,),
        ) or {}
        value = {
            "asset_id": asset_id,
            "position": float(current.get("position", 0) if position is None else position),
            "duration": float(current.get("duration", 0) if duration is None else duration),
            "completed": int(
                current.get("completed", 0) if completed is None else bool(completed)
            ),
            "play_count": (
                int(play_count)
                if play_count is not None
                else int(current.get("play_count", 0)) + int(bool(increment_play))
            ),
            # Wall time can move backwards on an intermittently connected Pi.
            # Calls are serialized by the catalog lock, so live mutation order
            # is authoritative while this timestamp remains monotonic metadata.
            "updated_at": max(float(current.get("updated_at", updated_at)), updated_at),
            "last_session_id": (
                last_session_id
                if last_session_id is not None
                else current.get("last_session_id")
            ),
            "last_event_id": (
                last_event_id if last_event_id is not None else current.get("last_event_id")
            ),
        }
        result = db.execute(
            """
            INSERT INTO video_v2_asset_playback_state
                (asset_id, position, duration, completed, play_count, updated_at,
                 last_session_id, last_event_id)
            VALUES (:asset_id, :position, :duration, :completed, :play_count,
                    :updated_at, :last_session_id, :last_event_id)
            ON CONFLICT(asset_id) DO UPDATE SET
                position = excluded.position,
                duration = excluded.duration,
                completed = excluded.completed,
                play_count = excluded.play_count,
                updated_at = excluded.updated_at,
                last_session_id = excluded.last_session_id,
                last_event_id = excluded.last_event_id
            """,
            value,
        )
        return bool(result.rowcount)

    def _put_work_state(
        self,
        db: sqlite3.Connection,
        *,
        work_id: str,
        updated_at: float,
        watched_auto: bool | None = None,
        watched_override: bool | None | object = ...,
        play_count: int | None = None,
        increment_play: bool = False,
        last_asset_id: str | None = None,
        allow_auto_reset: bool = False,
    ) -> bool:
        current = self._one(
            db,
            "SELECT * FROM video_v2_work_watch_state WHERE work_id = ?",
            (work_id,),
        ) or {}
        current_auto = bool(current.get("watched_auto", 0))
        if watched_auto is None:
            next_auto = current_auto
        elif watched_auto or allow_auto_reset:
            next_auto = bool(watched_auto)
        else:
            next_auto = current_auto
        next_override = (
            current.get("watched_override")
            if watched_override is ...
            else (None if watched_override is None else int(bool(watched_override)))
        )
        value = {
            "work_id": work_id,
            "watched_auto": int(next_auto),
            "watched_override": next_override,
            "play_count": (
                int(play_count)
                if play_count is not None
                else int(current.get("play_count", 0)) + int(bool(increment_play))
            ),
            "updated_at": max(float(current.get("updated_at", updated_at)), updated_at),
            "last_asset_id": last_asset_id or current.get("last_asset_id"),
        }
        result = db.execute(
            """
            INSERT INTO video_v2_work_watch_state
                (work_id, watched_auto, watched_override, play_count,
                 updated_at, last_asset_id)
            VALUES (:work_id, :watched_auto, :watched_override, :play_count,
                    :updated_at, :last_asset_id)
            ON CONFLICT(work_id) DO UPDATE SET
                watched_auto = excluded.watched_auto,
                watched_override = excluded.watched_override,
                play_count = excluded.play_count,
                updated_at = excluded.updated_at,
                last_asset_id = excluded.last_asset_id
            """,
            value,
        )
        return bool(result.rowcount)

    def start_session(
        self,
        asset_id: str,
        *,
        position: float | None = None,
        reset_completed: bool = False,
        launch_path: str | os.PathLike[str] | None = None,
        player_instance: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        started_at: float | None = None,
        connection: sqlite3.Connection | None = None,
    ) -> str:
        timestamp = self._now(started_at)
        start_position = _finite_nonnegative(position, "position")
        normalized_path = _path_key(launch_path) if launch_path is not None else None
        session_id = _new_id("ses")
        with self.transaction(connection=connection) as db:
            asset = self._assert_asset(db, asset_id)
            db.execute(
                """
                INSERT INTO video_v2_playback_sessions
                    (session_id, asset_id, started_at, launch_path,
                     player_instance, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    asset_id,
                    timestamp,
                    normalized_path,
                    player_instance,
                    _json_or_none(dict(metadata) if metadata is not None else None),
                ),
            )
            event_id, _ = self._insert_event(
                db,
                asset_id=asset_id,
                session_id=session_id,
                event_type="session_started",
                position=start_position,
                observed_at=timestamp,
                event_key=f"session-start:{session_id}",
            )
            self._put_asset_state(
                db,
                asset_id=asset_id,
                updated_at=timestamp,
                position=start_position,
                completed=False if reset_completed else None,
                increment_play=True,
                last_session_id=session_id,
                last_event_id=event_id,
            )
            if asset["work_id"] is not None:
                self._put_work_state(
                    db,
                    work_id=str(asset["work_id"]),
                    updated_at=timestamp,
                    increment_play=True,
                    last_asset_id=asset_id,
                )
        return session_id

    def checkpoint(
        self,
        session_id: str,
        *,
        position: float,
        duration: float | None = None,
        completed: bool | None = None,
        playback_state: str | None = None,
        event_type: str = "checkpoint",
        event_key: str | None = None,
        payload: Mapping[str, Any] | None = None,
        observed_at: float | None = None,
        authoritative_order: bool = False,
        connection: sqlite3.Connection | None = None,
    ) -> str:
        timestamp = self._now(observed_at)
        playhead = _finite_nonnegative(position, "position")
        total = _finite_nonnegative(duration, "duration")
        with self.transaction(connection=connection) as db:
            session = self._one(
                db,
                "SELECT * FROM video_v2_playback_sessions WHERE session_id = ?",
                (session_id,),
            )
            if session is None:
                raise CatalogNotFound(f"unknown session {session_id}")
            asset_id = str(session["asset_id"])
            asset = self._assert_asset(db, asset_id)
            prior_state = self._one(
                db,
                "SELECT * FROM video_v2_asset_playback_state WHERE asset_id = ?",
                (asset_id,),
            )
            prior_event = (
                self._one(
                    db,
                    "SELECT observed_at FROM video_v2_playback_events WHERE event_id = ?",
                    (str(prior_state["last_event_id"]),),
                )
                if prior_state is not None and prior_state.get("last_event_id")
                else None
            )
            event_id, inserted = self._insert_event(
                db,
                asset_id=asset_id,
                session_id=session_id,
                event_type=event_type,
                position=playhead,
                duration=total,
                completed=completed,
                playback_state=playback_state,
                event_key=event_key,
                observed_at=timestamp,
                payload=payload,
            )
            ordered_for_session = authoritative_order or not (
                prior_state is not None
                and prior_state.get("last_session_id") == session_id
                and prior_event is not None
                and timestamp < float(prior_event["observed_at"])
            )
            if inserted and ordered_for_session:
                self._put_asset_state(
                    db,
                    asset_id=asset_id,
                    updated_at=timestamp,
                    position=playhead,
                    duration=total,
                    completed=completed,
                    last_session_id=session_id,
                    last_event_id=event_id,
                )
                if completed is not None and asset["work_id"] is not None:
                    self._put_work_state(
                        db,
                        work_id=str(asset["work_id"]),
                        updated_at=timestamp,
                        watched_auto=completed,
                        last_asset_id=asset_id,
                    )
            return event_id

    def finish_session(
        self,
        session_id: str,
        *,
        reason: str = "stopped",
        position: float | None = None,
        duration: float | None = None,
        completed: bool | None = None,
        ended_at: float | None = None,
        connection: sqlite3.Connection | None = None,
    ) -> str:
        timestamp = self._now(ended_at)
        with self.transaction(connection=connection) as db:
            session = self._one(
                db,
                "SELECT * FROM video_v2_playback_sessions WHERE session_id = ?",
                (session_id,),
            )
            if session is None:
                raise CatalogNotFound(f"unknown session {session_id}")
            asset_id = str(session["asset_id"])
            asset = self._assert_asset(db, asset_id)
            state = self._one(
                db,
                "SELECT * FROM video_v2_asset_playback_state WHERE asset_id = ?",
                (asset_id,),
            ) or {}
            event_id, inserted = self._insert_event(
                db,
                asset_id=asset_id,
                session_id=session_id,
                event_type="session_finished",
                position=_finite_nonnegative(
                    state.get("position", 0) if position is None else position,
                    "position",
                ),
                duration=_finite_nonnegative(
                    state.get("duration", 0) if duration is None else duration,
                    "duration",
                ),
                completed=(bool(state.get("completed")) if completed is None else completed),
                observed_at=timestamp,
                event_key=f"session-finished:{session_id}",
                payload={"reason": _required_text(reason, "reason")},
            )
            if inserted:
                completed_value = (
                    bool(state.get("completed"))
                    if completed is None
                    else bool(completed)
                )
                self._put_asset_state(
                    db,
                    asset_id=asset_id,
                    updated_at=timestamp,
                    position=state.get("position", 0) if position is None else position,
                    duration=state.get("duration", 0) if duration is None else duration,
                    completed=completed_value,
                    last_session_id=session_id,
                    last_event_id=event_id,
                )
                if asset["work_id"] is not None:
                    self._put_work_state(
                        db,
                        work_id=str(asset["work_id"]),
                        updated_at=timestamp,
                        watched_auto=completed_value,
                        last_asset_id=asset_id,
                    )
            db.execute(
                """
                UPDATE video_v2_playback_sessions
                SET ended_at = COALESCE(ended_at, ?),
                    end_reason = COALESCE(end_reason, ?)
                WHERE session_id = ?
                """,
                (timestamp, _required_text(reason, "reason"), session_id),
            )
            return event_id

    def clear_playhead(
        self,
        asset_id: str,
        *,
        reason: str = "user_clear",
        clear_work_auto: bool = False,
        event_key: str | None = None,
        observed_at: float | None = None,
        connection: sqlite3.Connection | None = None,
    ) -> str:
        timestamp = self._now(observed_at)
        with self.transaction(connection=connection) as db:
            asset = self._assert_asset(db, asset_id)
            event_id, inserted = self._insert_event(
                db,
                asset_id=asset_id,
                event_type="playhead_cleared",
                position=0,
                completed=False,
                observed_at=timestamp,
                event_key=event_key,
                payload={"reason": _required_text(reason, "reason")},
            )
            if inserted:
                self._put_asset_state(
                    db,
                    asset_id=asset_id,
                    updated_at=timestamp,
                    position=0,
                    duration=0,
                    completed=False,
                    last_event_id=event_id,
                )
                if clear_work_auto and asset["work_id"] is not None:
                    self._put_work_state(
                        db,
                        work_id=str(asset["work_id"]),
                        updated_at=timestamp,
                        watched_auto=False,
                        watched_override=None,
                        last_asset_id=asset_id,
                        allow_auto_reset=True,
                    )
            return event_id

    def set_work_watched(
        self,
        work_id: str,
        watched: bool,
        *,
        manual: bool = True,
        asset_id: str | None = None,
        observed_at: float | None = None,
        connection: sqlite3.Connection | None = None,
    ) -> None:
        timestamp = self._now(observed_at)
        with self.transaction(connection=connection) as db:
            self._assert_work(db, work_id)
            if asset_id is not None:
                self._assert_asset(db, asset_id)
            self._put_work_state(
                db,
                work_id=work_id,
                updated_at=timestamp,
                watched_auto=None if manual else bool(watched),
                watched_override=bool(watched) if manual else ...,
                last_asset_id=asset_id,
                allow_auto_reset=not manual,
            )

    def clear_work_watched_override(
        self,
        work_id: str,
        *,
        observed_at: float | None = None,
        connection: sqlite3.Connection | None = None,
    ) -> None:
        timestamp = self._now(observed_at)
        with self.transaction(connection=connection) as db:
            self._assert_work(db, work_id)
            self._put_work_state(
                db,
                work_id=work_id,
                updated_at=timestamp,
                watched_override=None,
            )

    def get_asset_state(
        self,
        asset_id: str,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> dict[str, Any] | None:
        db = connection or self.connection
        with self.lock:
            return self._one(
                db,
                "SELECT * FROM video_v2_asset_playback_state WHERE asset_id = ?",
                (asset_id,),
            )

    def transfer_playhead(
        self,
        source_asset_id: str,
        target_asset_id: str,
        *,
        reason: str,
        observed_at: float | None = None,
        connection: sqlite3.Connection | None = None,
    ) -> bool:
        """Copy a newer durable playhead across an explicitly proven transition."""

        if source_asset_id == target_asset_id:
            return False
        timestamp = self._now(observed_at)
        with self.transaction(connection=connection) as db:
            source_asset = self._assert_asset(db, source_asset_id)
            target_asset = self._assert_asset(db, target_asset_id)
            source = self._one(
                db,
                "SELECT * FROM video_v2_asset_playback_state WHERE asset_id = ?",
                (source_asset_id,),
            )
            target = self._one(
                db,
                "SELECT * FROM video_v2_asset_playback_state WHERE asset_id = ?",
                (target_asset_id,),
            )
            if source is None or (
                target is not None
                and float(source["updated_at"]) <= float(target["updated_at"])
            ):
                return False
            event_time = max(timestamp, float(source["updated_at"]))
            event_id, inserted = self._insert_event(
                db,
                asset_id=target_asset_id,
                event_type="playhead_transferred",
                position=float(source["position"]),
                duration=float(source["duration"]),
                completed=bool(source["completed"]),
                observed_at=event_time,
                event_key=(
                    f"playhead-transfer:{source_asset_id}:"
                    f"{source.get('last_event_id') or source['updated_at']}"
                ),
                payload={"source_asset_id": source_asset_id, "reason": reason},
            )
            if not inserted:
                return False
            self._put_asset_state(
                db,
                asset_id=target_asset_id,
                updated_at=event_time,
                position=float(source["position"]),
                duration=float(source["duration"]),
                completed=bool(source["completed"]),
                play_count=max(
                    int(source.get("play_count") or 0),
                    int((target or {}).get("play_count") or 0),
                ),
                last_event_id=event_id,
            )
            source_work = (
                self._one(
                    db,
                    "SELECT * FROM video_v2_work_watch_state WHERE work_id = ?",
                    (str(source_asset["work_id"]),),
                )
                if source_asset["work_id"] is not None
                else None
            )
            if target_asset["work_id"] is not None and source_work is not None:
                target_work = self._one(
                    db,
                    "SELECT * FROM video_v2_work_watch_state WHERE work_id = ?",
                    (str(target_asset["work_id"]),),
                )
                if target_work is None or float(source_work["updated_at"]) > float(
                    target_work["updated_at"]
                ):
                    self._put_work_state(
                        db,
                        work_id=str(target_asset["work_id"]),
                        updated_at=max(event_time, float(source_work["updated_at"])),
                        watched_auto=bool(source_work["watched_auto"]),
                        watched_override=(
                            None
                            if source_work["watched_override"] is None
                            else bool(source_work["watched_override"])
                        ),
                        play_count=max(
                            int(source_work.get("play_count") or 0),
                            int((target_work or {}).get("play_count") or 0),
                        ),
                        last_asset_id=target_asset_id,
                        allow_auto_reset=True,
                    )
            return True

    def recover_open_sessions(
        self,
        *,
        reason: str = "unclean_shutdown",
        observed_at: float | None = None,
        connection: sqlite3.Connection | None = None,
    ) -> int:
        """Close sessions left open by a prior process before adopting VLC."""

        timestamp = self._now(observed_at)
        with self.transaction(connection=connection) as db:
            rows = self._all(
                db,
                """
                SELECT session_id FROM video_v2_playback_sessions
                WHERE ended_at IS NULL ORDER BY started_at, session_id
                """,
            )
            for row in rows:
                self.finish_session(
                    str(row["session_id"]),
                    reason=reason,
                    ended_at=timestamp,
                    connection=db,
                )
            return len(rows)

    def get_work_watch_state(
        self,
        work_id: str,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> dict[str, Any] | None:
        db = connection or self.connection
        with self.lock:
            state = self._one(
                db,
                "SELECT * FROM video_v2_work_watch_state WHERE work_id = ?",
                (work_id,),
            )
        if state is not None:
            override = state.get("watched_override")
            state["watched"] = bool(
                state.get("watched_auto") if override is None else override
            )
        return state

    def get_session(
        self,
        session_id: str,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> dict[str, Any] | None:
        db = connection or self.connection
        with self.lock:
            return self._one(
                db,
                "SELECT * FROM video_v2_playback_sessions WHERE session_id = ?",
                (session_id,),
            )

    def list_events(
        self,
        asset_id: str,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> list[dict[str, Any]]:
        db = connection or self.connection
        with self.lock:
            return self._all(
                db,
                """
                SELECT * FROM video_v2_playback_events
                WHERE asset_id = ? ORDER BY observed_at, event_id
                """,
                (asset_id,),
            )

    # -- Import audit and old-server round trips ------------------------

    def list_import_records(
        self,
        *,
        action: str | None = None,
        connection: sqlite3.Connection | None = None,
    ) -> list[dict[str, Any]]:
        db = connection or self.connection
        where = "" if action is None else "WHERE records.action = ?"
        parameters: tuple[Any, ...] = () if action is None else (action,)
        with self.lock:
            return self._all(
                db,
                f"""
                SELECT records.*, runs.source_kind, runs.source_ref
                FROM video_v2_import_records AS records
                JOIN video_v2_import_runs AS runs USING (import_id)
                {where}
                ORDER BY records.source_updated, records.imported_at,
                         records.import_record_id
                """,
                parameters,
            )

    def apply_imported_playhead(
        self,
        asset_id: str,
        *,
        position: float,
        source_updated: float,
        event_key: str,
        source: str,
        connection: sqlite3.Connection | None = None,
    ) -> bool:
        """Apply a conservatively matched raw-history checkpoint once."""

        playhead = _finite_nonnegative(position, "position") or 0
        timestamp = self._now(source_updated)
        with self.transaction(connection=connection) as db:
            self._assert_asset(db, asset_id)
            current = self._one(
                db,
                "SELECT * FROM video_v2_asset_playback_state WHERE asset_id = ?",
                (asset_id,),
            )
            event_id, inserted = self._insert_event(
                db,
                asset_id=asset_id,
                event_type="legacy_raw_snapshot",
                position=playhead,
                completed=False,
                observed_at=timestamp,
                event_key=_required_text(event_key, "event_key"),
                payload={"source": _required_text(source, "source")},
            )
            if not inserted:
                return False
            if current is not None and timestamp < float(current["updated_at"]):
                return False
            self._put_asset_state(
                db,
                asset_id=asset_id,
                updated_at=timestamp,
                position=playhead,
                last_event_id=event_id,
            )
            return True

    def begin_import(
        self,
        *,
        source_kind: str,
        source_ref: str,
        source_digest: str,
        started_at: float | None = None,
        connection: sqlite3.Connection | None = None,
    ) -> str:
        import_id = _new_id("imp")
        with self.transaction(connection=connection) as db:
            db.execute(
                """
                INSERT INTO video_v2_import_runs
                    (import_id, source_kind, source_ref, source_digest,
                     started_at, status)
                VALUES (?, ?, ?, ?, ?, 'running')
                """,
                (
                    import_id,
                    _required_text(source_kind, "source_kind"),
                    _required_text(source_ref, "source_ref"),
                    _required_text(source_digest, "source_digest"),
                    self._now(started_at),
                ),
            )
        return import_id

    def record_import(
        self,
        import_id: str,
        *,
        source_key: str,
        action: str,
        content_digest: str | None = None,
        source_updated: float | None = None,
        asset_id: str | None = None,
        work_id: str | None = None,
        raw: Mapping[str, Any] | None = None,
        imported_at: float | None = None,
        connection: sqlite3.Connection | None = None,
    ) -> str:
        record_id = _new_id("imr")
        with self.transaction(connection=connection) as db:
            if self._one(
                db,
                "SELECT import_id FROM video_v2_import_runs WHERE import_id = ?",
                (import_id,),
            ) is None:
                raise CatalogNotFound(f"unknown import {import_id}")
            db.execute(
                """
                INSERT INTO video_v2_import_records
                    (import_record_id, import_id, source_key, content_digest,
                     source_updated, asset_id, work_id, action, raw_json, imported_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(import_id, source_key) DO UPDATE SET
                    content_digest = excluded.content_digest,
                    source_updated = excluded.source_updated,
                    asset_id = excluded.asset_id,
                    work_id = excluded.work_id,
                    action = excluded.action,
                    raw_json = excluded.raw_json,
                    imported_at = excluded.imported_at
                """,
                (
                    record_id,
                    import_id,
                    _required_text(source_key, "source_key"),
                    content_digest,
                    source_updated,
                    asset_id,
                    work_id,
                    _required_text(action, "action"),
                    _json_or_none(dict(raw) if raw is not None else None),
                    self._now(imported_at),
                ),
            )
            row = self._one(
                db,
                """
                SELECT import_record_id FROM video_v2_import_records
                WHERE import_id = ? AND source_key = ?
                """,
                (import_id, source_key),
            )
            return str(row["import_record_id"])

    def finish_import(
        self,
        import_id: str,
        *,
        status: str = "complete",
        summary: Mapping[str, Any] | None = None,
        finished_at: float | None = None,
        connection: sqlite3.Connection | None = None,
    ) -> None:
        with self.transaction(connection=connection) as db:
            result = db.execute(
                """
                UPDATE video_v2_import_runs
                SET finished_at = ?, status = ?, summary_json = ?
                WHERE import_id = ?
                """,
                (
                    self._now(finished_at),
                    _required_text(status, "status"),
                    _json_or_none(dict(summary) if summary is not None else None),
                    import_id,
                ),
            )
            if not result.rowcount:
                raise CatalogNotFound(f"unknown import {import_id}")

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

    def _shadow_v1_row(
        self,
        db: sqlite3.Connection,
        *,
        media_key: str,
        present: bool,
        row_digest: str | None,
        source_updated: float | None,
        asset_id: str | None,
        raw: Mapping[str, Any] | None,
        observed_at: float,
    ) -> None:
        db.execute(
            """
            INSERT INTO video_v2_v1_shadow
                (media_key, was_present, row_digest, source_updated,
                 asset_id, raw_json, last_seen_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(media_key) DO UPDATE SET
                was_present = excluded.was_present,
                row_digest = excluded.row_digest,
                source_updated = excluded.source_updated,
                asset_id = COALESCE(excluded.asset_id, video_v2_v1_shadow.asset_id),
                raw_json = excluded.raw_json,
                last_seen_at = excluded.last_seen_at
            """,
            (
                media_key,
                int(present),
                row_digest,
                source_updated,
                asset_id,
                _json_or_none(dict(raw) if raw is not None else None),
                observed_at,
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
            self._shadow_v1_row(
                db,
                media_key=key,
                present=False,
                row_digest=None,
                source_updated=None,
                asset_id=asset_id,
                raw=None,
                observed_at=timestamp,
            )
            return bool(result.rowcount)

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
                    self._shadow_v1_row(
                        db,
                        media_key=media_key,
                        present=True,
                        row_digest=row_digest,
                        source_updated=float(row.get("updated") or timestamp),
                        asset_id=str(shadow["asset_id"]) if shadow["asset_id"] else None,
                        raw=row,
                        observed_at=timestamp,
                    )
                    continue
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
                    current_state = self._one(
                        db,
                        """
                        SELECT updated_at FROM video_v2_asset_playback_state
                        WHERE asset_id = ?
                        """,
                        (asset_id,),
                    )
                    shadow_covers_state = bool(
                        shadow is not None
                        and shadow.get("asset_id") == asset_id
                        and shadow.get("source_updated") is not None
                        and (
                            current_state is None
                            or float(shadow["source_updated"])
                            >= float(current_state["updated_at"])
                        )
                    )
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
                    raw=row,
                    observed_at=timestamp,
                )

            missing = sorted(
                key
                for key, shadow in shadows.items()
                if bool(shadow["was_present"]) and key not in rows
            )
            for media_key in missing:
                shadow = shadows[media_key]
                prior_digest = str(shadow["row_digest"] or "unknown")
                asset_id = (
                    str(shadow["asset_id"])
                    if shadow["asset_id"] is not None
                    else self.resolve_legacy_key(media_key, connection=db)
                )
                applied = False
                if asset_id is not None and absence_policy == "clear":
                    self.clear_playhead(
                        asset_id,
                        reason="legacy_v1_row_absent",
                        clear_work_auto=True,
                        event_key=f"v1-absent:{media_key}:{prior_digest}",
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
                    ON CONFLICT(media_key, prior_digest) DO NOTHING
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
                self._shadow_v1_row(
                    db,
                    media_key=media_key,
                    present=False,
                    row_digest=None,
                    source_updated=None,
                    asset_id=asset_id,
                    raw=None,
                    observed_at=timestamp,
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


__all__ = (
    "CatalogConflict",
    "CatalogError",
    "CatalogNotFound",
    "DEFAULT_BUSY_TIMEOUT_MS",
    "MediaAssetCatalog",
    "SCHEMA_VERSION",
    "ensure_pre_v2_backup",
)
