import sqlite3
import stat
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from pi.apps.video_library.catalog import MediaAssetCatalog, ensure_pre_v2_backup
from pi.apps.video_library.catalog_values import CatalogConflict


class FakeClock:
    def __init__(self, value=1_700_000_000.0):
        self.value = float(value)

    def __call__(self):
        return self.value

    def advance(self, seconds=1):
        self.value += seconds
        return self.value


def create_v1_database(path):
    connection = sqlite3.connect(path)
    connection.execute(
        """
        CREATE TABLE progress (
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
    def test_backup_is_verified_private_atomic_and_never_refreshed(self):
        connection = create_v1_database(self.db_path)
        insert_v1_row(connection)
        connection.close()

        backup = Path(ensure_pre_v2_backup(self.db_path))
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

class V1ReconciliationTests(CatalogFixture):
    def setUp(self):
        super().setUp()
        connection = create_v1_database(self.db_path)
        insert_v1_row(connection)
        connection.close()

    def test_reconcile_transaction_ownership_success_rollback_and_caller_reuse(self):
        catalog = self.catalog()
        first = catalog.reconcile_v1_progress(observed_at=self.clock())
        self.assertEqual(first["applied"], 1)
        asset = catalog.resolve_legacy_key("episode:show:s1:e1")
        import_count = catalog.connection.execute(
            "SELECT COUNT(*) FROM video_v2_import_runs"
        ).fetchone()[0]

        catalog.connection.execute(
            "UPDATE progress SET position = -1, title = 'invalid' WHERE media_key = ?",
            ("episode:show:s1:e1",),
        )
        catalog.connection.commit()
        with self.assertRaises(ValueError):
            catalog.reconcile_v1_progress(observed_at=self.clock.advance())
        self.assertEqual(
            catalog.connection.execute(
                "SELECT COUNT(*) FROM video_v2_import_runs"
            ).fetchone()[0],
            import_count,
        )
        self.assertEqual(catalog.get_asset_state(asset)["position"], 125.5)

        catalog.connection.execute(
            "UPDATE progress SET position = 777, title = 'caller owned' "
            "WHERE media_key = ?",
            ("episode:show:s1:e1",),
        )
        catalog.connection.commit()
        with catalog.transaction() as db:
            reused = catalog.reconcile_v1_progress(
                observed_at=self.clock.advance(), connection=db
            )
            self.assertEqual(reused["applied"], 1)
            self.assertTrue(db.in_transaction)
        self.assertEqual(catalog.get_asset_state(asset)["position"], 777)

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

    def test_identical_row_can_be_deleted_restored_and_deleted_again(self):
        catalog = self.catalog()
        catalog.reconcile_v1_progress(observed_at=self.clock())
        asset = catalog.resolve_legacy_key("episode:show:s1:e1")

        old_server = sqlite3.connect(self.db_path)
        old_server.execute(
            "DELETE FROM progress WHERE media_key = ?",
            ("episode:show:s1:e1",),
        )
        old_server.commit()
        old_server.close()
        first_delete = catalog.reconcile_v1_progress(
            observed_at=self.clock.advance()
        )
        self.assertEqual(first_delete["cleared"], 1)
        self.assertEqual(catalog.get_asset_state(asset)["position"], 0)

        old_server = sqlite3.connect(self.db_path)
        insert_v1_row(old_server)
        old_server.close()
        restored = catalog.reconcile_v1_progress(observed_at=self.clock.advance())
        self.assertEqual(restored["applied"], 1)
        self.assertEqual(catalog.get_asset_state(asset)["position"], 125.5)

        old_server = sqlite3.connect(self.db_path)
        old_server.execute(
            "DELETE FROM progress WHERE media_key = ?",
            ("episode:show:s1:e1",),
        )
        old_server.commit()
        old_server.close()
        second_delete = catalog.reconcile_v1_progress(
            observed_at=self.clock.advance()
        )

        self.assertEqual(second_delete["cleared"], 1)
        self.assertEqual(catalog.get_asset_state(asset)["position"], 0)
        self.assertEqual(
            len(
                [
                    event
                    for event in catalog.list_events(asset)
                    if event["event_type"] == "playhead_cleared"
                ]
            ),
            2,
        )

    def test_rejected_identical_tombstone_records_later_applied_clear(self):
        catalog = self.catalog()
        catalog.reconcile_v1_progress(observed_at=self.clock())
        media_key = "episode:show:s1:e1"
        asset = catalog.resolve_legacy_key(media_key)
        work = catalog.lookup_asset(asset)["work_id"]

        # Project an open session, then recover it behind the wall-clock
        # watermark.  Recovery changes the exact last-event pointer without
        # changing any field representable by the v1 row.
        catalog.start_session(asset, position=125.5, started_at=self.clock())
        state = catalog.get_asset_state(asset)
        work_state = catalog.get_work_watch_state(work)
        projected_updated = max(state["updated_at"], work_state["updated_at"])
        catalog.project_v1_progress(
            media_key,
            position=state["position"],
            duration=state["duration"],
            updated=projected_updated,
            finished=work_state["watched"],
            finished_override=work_state["watched_override"],
            play_count=state["play_count"],
            title="Show S01E01",
            rel_path="/TV/Show/Show.S01E01.mkv",
            asset_id=asset,
        )
        prior_digest = catalog.connection.execute(
            "SELECT row_digest FROM video_v2_v1_shadow WHERE media_key = ?",
            (media_key,),
        ).fetchone()[0]
        self.assertEqual(
            catalog.recover_open_sessions(observed_at=self.clock() - 100),
            1,
        )

        old_server = sqlite3.connect(self.db_path)
        old_server.execute("DELETE FROM progress WHERE media_key = ?", (media_key,))
        old_server.commit()
        old_server.close()
        rejected = catalog.reconcile_v1_progress(observed_at=self.clock() - 99)
        self.assertEqual(rejected["cleared"], 0)
        first_tombstone = catalog.connection.execute(
            "SELECT applied_to_state, detected_at FROM video_v2_v1_tombstones "
            "WHERE media_key = ? AND prior_digest = ?",
            (media_key, prior_digest),
        ).fetchone()
        self.assertEqual(first_tombstone[0], 0)

        # The normal post-recovery projection recreates the exact same v1 row
        # while recording coverage of the recovered v2 event state.
        state = catalog.get_asset_state(asset)
        work_state = catalog.get_work_watch_state(work)
        catalog.project_v1_progress(
            media_key,
            position=state["position"],
            duration=state["duration"],
            updated=max(state["updated_at"], work_state["updated_at"]),
            finished=work_state["watched"],
            finished_override=work_state["watched_override"],
            play_count=state["play_count"],
            title="Show S01E01",
            rel_path="/TV/Show/Show.S01E01.mkv",
            asset_id=asset,
        )
        self.assertEqual(
            catalog.connection.execute(
                "SELECT row_digest FROM video_v2_v1_shadow WHERE media_key = ?",
                (media_key,),
            ).fetchone()[0],
            prior_digest,
        )

        old_server = sqlite3.connect(self.db_path)
        old_server.execute("DELETE FROM progress WHERE media_key = ?", (media_key,))
        old_server.commit()
        old_server.close()
        applied = catalog.reconcile_v1_progress(observed_at=self.clock() + 1)

        self.assertEqual(applied["cleared"], 1)
        self.assertEqual(catalog.get_asset_state(asset)["position"], 0)
        updated_tombstone = catalog.connection.execute(
            "SELECT applied_to_state, detected_at FROM video_v2_v1_tombstones "
            "WHERE media_key = ? AND prior_digest = ?",
            (media_key, prior_digest),
        ).fetchone()
        self.assertEqual(updated_tombstone[0], 1)
        self.assertGreater(updated_tombstone[1], first_tombstone[1])

if __name__ == "__main__":
    unittest.main()
