"""Video identity schema boundary."""

from __future__ import annotations

if __package__:
    pass
else:
    pass


SCHEMA_VERSION = 3

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
    (
        3,
        "exact legacy v1 projection coverage",
        (
            """
            ALTER TABLE video_v2_v1_shadow
            ADD COLUMN covered_state_digest TEXT
            """,
        ),
    ),
)
