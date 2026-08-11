import os
import sqlite3
import stat
import tempfile
import threading
import unittest
from contextlib import closing
from pathlib import Path

from pi.apps.video_library.video_asset_catalog import (
    CatalogConflict,
    CatalogNotFound,
    MediaAssetCatalog,
    ensure_pre_v2_backup,
)


class FakeClock:
    def __init__(self, value=1_700_000_000.0):
        self.value = float(value)

    def __call__(self):
        return self.value

    def advance(self, seconds=1):
        self.value += seconds
        return self.value


def create_v1_database(path, *, with_override=True):
    connection = sqlite3.connect(path)
    override = ", finished_override INTEGER" if with_override else ""
    connection.execute(
        f"""
        CREATE TABLE progress (
            media_key TEXT PRIMARY KEY,
            position REAL NOT NULL DEFAULT 0,
            duration REAL NOT NULL DEFAULT 0,
            updated REAL NOT NULL,
            finished INTEGER NOT NULL DEFAULT 0
            {override},
            play_count INTEGER NOT NULL DEFAULT 0,
            title TEXT,
            rel_path TEXT
        )
        """
    )
    connection.execute(
        "CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
    )
    connection.commit()
    return connection


def insert_v1_row(connection, key="episode:show:s1:e1", **changes):
    values = {
        "media_key": key,
        "position": 125.5,
        "duration": 1800.25,
        "updated": 1_700_000_010.0,
        "finished": 0,
        "finished_override": None,
        "play_count": 2,
        "title": "Show S01E01",
        "rel_path": "/TV/Show/Show.S01E01.mkv",
    }
    values.update(changes)
    columns = {row[1] for row in connection.execute("PRAGMA table_info(progress)")}
    values = {key: value for key, value in values.items() if key in columns}
    names = list(values)
    connection.execute(
        f"INSERT OR REPLACE INTO progress ({', '.join(names)}) "
        f"VALUES ({', '.join('?' for _ in names)})",
        tuple(values[name] for name in names),
    )
    connection.commit()
    return values


class CatalogFixture(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.root = Path(self.tempdir.name)
        self.db_path = self.root / "progress.sqlite3"
        self.clock = FakeClock()

    def catalog(self, *, path=None, connection=None, lock=None):
        catalog = MediaAssetCatalog(
            path or (None if connection is not None else self.db_path),
            connection=connection,
            lock=lock,
            clock=self.clock,
        )
        self.addCleanup(catalog.close)
        return catalog


class AdditiveSchemaAndBackupTests(CatalogFixture):
    def test_schema_is_additive_idempotent_and_keeps_v1_rows_and_sql_exact(self):
        connection = create_v1_database(self.db_path)
        insert_v1_row(connection)
        connection.execute("INSERT INTO metadata VALUES ('legacy_mtime', '123.25')")
        connection.commit()
        before_schema = connection.execute(
            """
            SELECT name, sql FROM sqlite_master
            WHERE type = 'table' AND name IN ('progress', 'metadata') ORDER BY name
            """
        ).fetchall()
        before_rows = connection.execute(
            """
            SELECT media_key, quote(position), typeof(position), quote(duration),
                   typeof(duration), quote(updated), typeof(updated), finished,
                   finished_override, play_count, quote(title), quote(rel_path)
            FROM progress
            """
        ).fetchall()
        before_metadata = connection.execute("SELECT * FROM metadata").fetchall()
        journal_before = connection.execute("PRAGMA journal_mode").fetchone()[0]
        connection.close()

        catalog = self.catalog()
        self.assertEqual(
            [row[0] for row in catalog.connection.execute(
                "SELECT version FROM video_v2_schema_migrations ORDER BY version"
            )],
            [1, 2],
        )
        self.assertEqual(catalog.connection.execute("PRAGMA foreign_keys").fetchone()[0], 1)
        self.assertGreaterEqual(
            catalog.connection.execute("PRAGMA busy_timeout").fetchone()[0], 5000
        )
        catalog.close()

        second = MediaAssetCatalog(self.db_path, clock=self.clock)
        second.close()
        check = sqlite3.connect(self.db_path)
        self.assertEqual(
            check.execute(
                """
                SELECT name, sql FROM sqlite_master
                WHERE type = 'table' AND name IN ('progress', 'metadata') ORDER BY name
                """
            ).fetchall(),
            before_schema,
        )
        self.assertEqual(
            check.execute(
                """
                SELECT media_key, quote(position), typeof(position), quote(duration),
                       typeof(duration), quote(updated), typeof(updated), finished,
                       finished_override, play_count, quote(title), quote(rel_path)
                FROM progress
                """
            ).fetchall(),
            before_rows,
        )
        self.assertEqual(check.execute("SELECT * FROM metadata").fetchall(), before_metadata)
        self.assertEqual(check.execute("PRAGMA journal_mode").fetchone()[0], journal_before)
        self.assertFalse(
            any(
                name.startswith("video_v2_") is False
                and name not in {"progress", "metadata"}
                for (name,) in check.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
                )
            )
        )
        check.close()

    def test_backup_is_verified_private_atomic_and_never_refreshed(self):
        connection = create_v1_database(self.db_path)
        insert_v1_row(connection)
        connection.close()

        backup = Path(ensure_pre_v2_backup(self.db_path))
        self.assertEqual(backup.name, "progress.pre-v2.sqlite3")
        self.assertEqual(stat.S_IMODE(backup.stat().st_mode), 0o600)
        with closing(sqlite3.connect(backup)) as snapshot:
            self.assertEqual(snapshot.execute("PRAGMA quick_check").fetchone()[0], "ok")
            self.assertEqual(snapshot.execute("SELECT COUNT(*) FROM progress").fetchone()[0], 1)

        source = sqlite3.connect(self.db_path)
        insert_v1_row(source, key="feature:new:2026")
        source.close()
        self.assertEqual(ensure_pre_v2_backup(self.db_path), str(backup))
        with closing(sqlite3.connect(backup)) as snapshot:
            self.assertEqual(snapshot.execute("SELECT COUNT(*) FROM progress").fetchone()[0], 1)
        self.assertIsNone(ensure_pre_v2_backup(":memory:"))


class AssetIdentityTests(CatalogFixture):
    def test_random_work_asset_ids_and_work_binding_are_stable(self):
        catalog = self.catalog()
        work = catalog.create_work("episode", title="Pilot", series="Show", season=1, episode=1)
        asset = catalog.create_asset(asset_kind="encode")
        other = catalog.create_asset(asset_kind="encode")
        self.assertRegex(work, r"^wrk_[0-9a-f]{32}$")
        self.assertRegex(asset, r"^ast_[0-9a-f]{32}$")
        self.assertNotEqual(asset, other)
        catalog.bind_work(asset, work)
        self.assertEqual(catalog.lookup_asset(asset)["work_id"], work)
        self.assertEqual(catalog.lookup_work(work)["title"], "Pilot")
        with self.assertRaises(CatalogNotFound):
            catalog.bind_work(asset, "wrk_missing")

    def test_torrent_locator_is_idempotent_and_survives_torrent_id_change(self):
        catalog = self.catalog()
        first = catalog.resolve_or_create_torrent_asset(
            client_id="qb-main",
            torrent_id="ABC123",
            file_index=4,
            info_hash_v1="AAA111",
            info_hash_v2="BBB222",
            expected_size=123456,
            path=self.root / "incomplete" / "raw.mkv",
        )
        repeat = catalog.resolve_or_create_torrent_asset(
            client_id="qb-main",
            torrent_id="abc123",
            file_index=4,
            info_hash_v1="aaa111",
            info_hash_v2="bbb222",
        )
        renamed = catalog.resolve_or_create_torrent_asset(
            client_id="qb-main",
            torrent_id="new-qb-id",
            file_index=4,
            info_hash_v1="aaa111",
            info_hash_v2="bbb222",
        )
        another_file = catalog.resolve_or_create_torrent_asset(
            client_id="qb-main",
            torrent_id="abc123",
            file_index=5,
            info_hash_v1="aaa111",
            info_hash_v2="bbb222",
        )
        self.assertEqual(first, repeat)
        self.assertEqual(first, renamed)
        self.assertNotEqual(first, another_file)
        self.assertEqual(
            catalog.lookup_torrent_asset(
                client_id="qb-main", info_hash_v2="BBB222", file_index=4
            ),
            first,
        )
        self.assertEqual(
            catalog.connection.execute(
                "SELECT COUNT(*) FROM video_v2_torrent_locators WHERE asset_id = ?",
                (first,),
            ).fetchone()[0],
            2,
        )
        with self.assertRaises(CatalogConflict):
            catalog.resolve_or_create_torrent_asset(
                client_id="qb-main",
                torrent_id="abc123",
                file_index=4,
                info_hash_v1="changed-hash",
            )

    def test_torrent_locator_listing_is_filtered_deterministic_and_read_only(self):
        catalog = self.catalog()
        later = catalog.resolve_or_create_torrent_asset(
            client_id="qb-z",
            torrent_id="torrent-b",
            file_index=3,
            info_hash_v1="bbb",
            observed_at=self.clock.advance(10),
        )
        earlier = catalog.resolve_or_create_torrent_asset(
            client_id="qb-a",
            torrent_id="torrent-a",
            file_index=7,
            info_hash_v2="aaa",
            observed_at=self.clock.advance(10),
        )
        catalog.resolve_or_create_torrent_asset(
            client_id="qb-z",
            torrent_id="torrent-a",
            file_index=1,
            info_hash_v1="ccc",
            observed_at=self.clock.advance(10),
        )
        before = catalog.connection.total_changes
        listed = catalog.list_torrent_locators()
        self.assertEqual(
            [(row["client_id"], row["torrent_id"], row["file_index"]) for row in listed],
            [("qb-a", "torrent-a", 7), ("qb-z", "torrent-a", 1), ("qb-z", "torrent-b", 3)],
        )
        self.assertEqual(catalog.connection.total_changes, before)
        self.assertEqual(
            [row["asset_id"] for row in catalog.list_torrent_locators(earlier)],
            [earlier],
        )
        self.assertEqual(
            [row["asset_id"] for row in catalog.list_torrent_locators(later)],
            [later],
        )
        self.assertEqual(catalog.list_torrent_locators("ast_missing"), [])

    def test_arbitrary_file_gets_provisional_identity_and_can_move(self):
        catalog = self.catalog()
        first_path = self.root / "random movie.mkv"
        second_path = self.root / "elsewhere" / "random movie.mkv"
        first_path.write_bytes(b"random non-torrent video")
        first = catalog.resolve_or_create_provisional_file(first_path)
        repeat = catalog.resolve_or_create_provisional_file(first_path)
        second_path.parent.mkdir()
        first_path.rename(second_path)
        moved = catalog.resolve_or_create_provisional_file(second_path)
        self.assertEqual(first, repeat)
        self.assertEqual(first, moved)
        self.assertEqual(catalog.resolve_path(second_path), first)
        self.assertTrue(catalog.retire_location(first_path))
        self.assertIsNone(catalog.resolve_path(first_path))
        self.assertEqual(catalog.resolve_path(first_path, include_historical=True), first)
        identities = catalog.connection.execute(
            "SELECT COUNT(*) FROM video_v2_file_identities WHERE asset_id = ?", (first,)
        ).fetchone()[0]
        self.assertEqual(identities, 1)

    def test_provisional_asset_can_safely_acquire_torrent_identity_later(self):
        catalog = self.catalog()
        path = self.root / "incomplete" / "unknown.mkv"
        path.parent.mkdir()
        path.write_bytes(b"partially downloaded media")
        provisional = catalog.resolve_or_create_provisional_file(path)
        attached = catalog.attach_torrent_locator(
            provisional,
            client_id="qb-main",
            torrent_id="torrent-1",
            file_index=0,
            info_hash_v1="hash-v1",
            expected_size=999,
            path=path,
        )
        self.assertEqual(attached, provisional)
        self.assertEqual(catalog.lookup_asset(provisional)["asset_kind"], "torrent")
        self.assertEqual(catalog.lookup_asset(provisional)["expected_size"], 999)
        self.assertEqual(
            catalog.lookup_torrent_asset(
                client_id="qb-main", torrent_id="torrent-1", file_index=0
            ),
            provisional,
        )
        self.assertEqual(
            catalog.attach_torrent_locator(
                provisional,
                client_id="qb-main",
                torrent_id="torrent-1",
                file_index=0,
                info_hash_v1="hash-v1",
            ),
            provisional,
        )

        other = catalog.create_asset()
        with self.assertRaises(CatalogConflict):
            catalog.attach_torrent_locator(
                other,
                client_id="qb-main",
                torrent_id="torrent-1",
                file_index=0,
                info_hash_v1="hash-v1",
            )
        self.assertEqual(
            catalog.lookup_torrent_asset(
                client_id="qb-main", torrent_id="torrent-1", file_index=0
            ),
            provisional,
        )

    def test_paths_aliases_and_legacy_keys_are_versioned_not_identity(self):
        catalog = self.catalog()
        first = catalog.create_asset()
        second = catalog.create_asset()
        path = self.root / "links" / "Clean.mkv"
        loc1 = catalog.record_location(first, path, observed_at=self.clock())
        self.assertEqual(catalog.record_location(first, path), loc1)
        self.clock.advance()
        loc2 = catalog.record_location(second, path)
        self.assertNotEqual(loc1, loc2)
        self.assertEqual(catalog.resolve_path(path), second)
        self.assertEqual(catalog.list_locations(first)[0]["valid_to"], self.clock())

        alias1 = catalog.record_alias(first, "Show S01E01", namespace="clean-link")
        self.assertEqual(
            catalog.record_alias(first, "Show S01E01", namespace="clean-link"), alias1
        )
        self.clock.advance()
        alias2 = catalog.record_alias(second, "Show S01E01", namespace="clean-link")
        self.assertNotEqual(alias1, alias2)
        self.assertEqual(
            catalog.resolve_alias("Show S01E01", namespace="clean-link"), second
        )

        catalog.bind_legacy_key(first, "episode:old-key")
        catalog.bind_legacy_key(first, "episode:old-key")
        self.assertEqual(catalog.resolve_legacy_key("episode:old-key"), first)
        with self.assertRaises(CatalogConflict):
            catalog.bind_legacy_key(second, "episode:old-key")
        catalog.bind_legacy_key(second, "episode:old-key", replace=True)
        self.assertEqual(catalog.resolve_legacy_key("episode:old-key"), second)


class PlaybackTests(CatalogFixture):
    def test_sessions_checkpoint_dedupe_stale_events_and_watched_override(self):
        catalog = self.catalog()
        work = catalog.create_work("movie", title="A Movie")
        asset = catalog.create_asset(work_id=work)
        session = catalog.start_session(asset, position=10, launch_path=self.root / "movie.mkv")
        self.assertRegex(session, r"^ses_[0-9a-f]{32}$")
        self.clock.advance(10)
        first_event = catalog.checkpoint(
            session,
            position=200,
            duration=1000,
            completed=False,
            playback_state="playing",
            event_key="player-seq:1",
        )
        duplicate = catalog.checkpoint(
            session,
            position=999,
            duration=1000,
            event_key="player-seq:1",
        )
        self.assertEqual(first_event, duplicate)
        self.assertEqual(catalog.get_asset_state(asset)["position"], 200)

        catalog.checkpoint(
            session,
            position=50,
            duration=1000,
            observed_at=self.clock() - 5,
            event_key="late-old-checkpoint",
        )
        self.assertEqual(catalog.get_asset_state(asset)["position"], 200)
        self.clock.advance(10)
        catalog.checkpoint(session, position=950, duration=1000, completed=True)
        self.assertTrue(catalog.get_work_watch_state(work)["watched"])
        catalog.set_work_watched(work, False, manual=True)
        state = catalog.get_work_watch_state(work)
        self.assertTrue(state["watched_auto"])
        self.assertFalse(state["watched"])
        catalog.clear_work_watched_override(work)
        self.assertTrue(catalog.get_work_watch_state(work)["watched"])

        finished = catalog.finish_session(session, reason="stop")
        repeated = catalog.finish_session(session, reason="different ignored")
        self.assertEqual(finished, repeated)
        self.assertEqual(
            len([event for event in catalog.list_events(asset) if event["event_type"] == "session_finished"]),
            1,
        )
        self.assertEqual(catalog.get_asset_state(asset)["play_count"], 1)
        self.assertEqual(catalog.get_work_watch_state(work)["play_count"], 1)

    def test_shared_connection_lock_and_caller_transaction_are_atomic(self):
        connection = create_v1_database(self.db_path)
        self.addCleanup(connection.close)
        shared_lock = threading.RLock()
        catalog = self.catalog(connection=connection, lock=shared_lock)
        work = catalog.create_work("movie")
        asset = catalog.create_asset(work_id=work)
        catalog.bind_legacy_key(asset, "feature:movie:2026")

        with self.assertRaisesRegex(RuntimeError, "rollback"):
            with catalog.transaction() as db:
                catalog.project_v1_progress(
                    "feature:movie:2026",
                    position=25,
                    updated=self.clock(),
                    asset_id=asset,
                    connection=db,
                )
                catalog.start_session(asset, connection=db)
                raise RuntimeError("rollback")
        self.assertIsNone(
            connection.execute(
                "SELECT 1 FROM progress WHERE media_key = 'feature:movie:2026'"
            ).fetchone()
        )
        self.assertEqual(
            connection.execute(
                "SELECT COUNT(*) FROM video_v2_playback_sessions"
            ).fetchone()[0],
            0,
        )

        with catalog.transaction() as db:
            catalog.project_v1_progress(
                "feature:movie:2026",
                position=30,
                duration=90,
                updated=self.clock.advance(),
                asset_id=asset,
                connection=db,
            )
            session = catalog.start_session(asset, position=30, connection=db)
            catalog.checkpoint(
                session,
                position=31,
                duration=90,
                event_key="atomic",
                connection=db,
            )
        self.assertEqual(
            connection.execute(
                "SELECT position FROM progress WHERE media_key = 'feature:movie:2026'"
            ).fetchone()[0],
            30,
        )
        self.assertEqual(catalog.get_asset_state(asset)["position"], 31)


class V1ReconciliationTests(CatalogFixture):
    def setUp(self):
        super().setUp()
        connection = create_v1_database(self.db_path)
        insert_v1_row(connection)
        connection.close()

    def test_repeated_snapshots_old_server_updates_and_deletion_round_trip(self):
        catalog = self.catalog()
        first = catalog.reconcile_v1_progress(observed_at=self.clock())
        self.assertEqual((first["rows"], first["changed"], first["created"]), (1, 1, 1))
        asset = catalog.resolve_legacy_key("episode:show:s1:e1")
        work = catalog.lookup_asset(asset)["work_id"]
        self.assertEqual(catalog.get_asset_state(asset)["position"], 125.5)
        initial_events = len(catalog.list_events(asset))

        repeated = catalog.reconcile_v1_progress(observed_at=self.clock.advance())
        self.assertEqual(repeated["unchanged"], 1)
        self.assertEqual(len(catalog.list_events(asset)), initial_events)

        old_server = sqlite3.connect(self.db_path)
        insert_v1_row(
            old_server,
            position=777,
            updated=self.clock.advance(20),
            finished=1,
            finished_override=1,
            play_count=4,
        )
        old_server.close()
        changed = catalog.reconcile_v1_progress(observed_at=self.clock.advance())
        self.assertEqual(changed["applied"], 1)
        self.assertEqual(catalog.get_asset_state(asset)["position"], 777)
        self.assertTrue(catalog.get_work_watch_state(work)["watched"])
        self.assertEqual(catalog.get_work_watch_state(work)["watched_override"], 1)

        old_server = sqlite3.connect(self.db_path)
        old_server.execute("DELETE FROM progress WHERE media_key = ?", ("episode:show:s1:e1",))
        old_server.commit()
        old_server.close()
        deleted = catalog.reconcile_v1_progress(observed_at=self.clock.advance())
        self.assertEqual((deleted["tombstones"], deleted["cleared"]), (1, 1))
        self.assertEqual(catalog.get_asset_state(asset)["position"], 0)
        self.assertFalse(catalog.get_work_watch_state(work)["watched"])
        tombstone = catalog.connection.execute(
            """
            SELECT applied_to_state, ambiguity_note FROM video_v2_v1_tombstones
            WHERE media_key = ?
            """,
            ("episode:show:s1:e1",),
        ).fetchone()
        self.assertEqual(tombstone[0], 1)
        self.assertIn("missing v1 row", tombstone[1])
        again = catalog.reconcile_v1_progress(observed_at=self.clock.advance())
        self.assertEqual(again["tombstones"], 0)

    def test_newer_v2_state_is_not_regressed_by_stale_changed_v1_row(self):
        catalog = self.catalog()
        catalog.reconcile_v1_progress(observed_at=self.clock())
        asset = catalog.resolve_legacy_key("episode:show:s1:e1")
        session = catalog.start_session(asset, started_at=self.clock.advance(100))
        catalog.checkpoint(
            session,
            position=900,
            duration=1800,
            observed_at=self.clock.advance(),
        )

        old_server = sqlite3.connect(self.db_path)
        insert_v1_row(old_server, position=10, updated=1_600_000_000.0, title="changed")
        old_server.close()
        report = catalog.reconcile_v1_progress(observed_at=self.clock.advance())
        self.assertEqual(report["stale"], 1)
        self.assertEqual(catalog.get_asset_state(asset)["position"], 900)

    def test_audit_only_absence_and_projected_rows_are_idempotent(self):
        catalog = self.catalog()
        catalog.reconcile_v1_progress(observed_at=self.clock())
        asset = catalog.resolve_legacy_key("episode:show:s1:e1")
        old_server = sqlite3.connect(self.db_path)
        old_server.execute("DELETE FROM progress")
        old_server.commit()
        old_server.close()
        report = catalog.reconcile_v1_progress(
            absence_policy="audit", observed_at=self.clock.advance()
        )
        self.assertEqual((report["tombstones"], report["cleared"]), (1, 0))
        self.assertEqual(catalog.get_asset_state(asset)["position"], 125.5)

        catalog.project_v1_progress(
            "episode:show:s1:e1",
            position=333,
            duration=1800,
            updated=self.clock.advance(),
            asset_id=asset,
        )
        before = len(catalog.list_events(asset))
        unchanged = catalog.reconcile_v1_progress(observed_at=self.clock.advance())
        self.assertEqual(unchanged["unchanged"], 1)
        self.assertEqual(len(catalog.list_events(asset)), before)

    def test_projection_supports_pre_override_v1_schema(self):
        path = self.root / "old.sqlite3"
        connection = create_v1_database(path, with_override=False)
        connection.close()
        catalog = self.catalog(path=path)
        catalog.project_v1_progress(
            "feature:old:1999",
            position=42,
            duration=100,
            finished_override=True,
            title="Old",
        )
        row = catalog.connection.execute(
            "SELECT media_key, position, duration, title FROM progress"
        ).fetchone()
        self.assertEqual(row, ("feature:old:1999", 42.0, 100.0, "Old"))


if __name__ == "__main__":
    unittest.main()
