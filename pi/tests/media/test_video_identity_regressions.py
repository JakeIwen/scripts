#!/usr/bin/env python3
"""Regression contracts for playback identity and history projections.

These cases isolate failure modes that can silently attach progress to the
wrong file or leave the rollback-compatible/UI projection stale.  They use
only temporary media and in-memory SQLite state.
"""

from __future__ import annotations

import os
import sqlite3
import unittest
from unittest import mock

from pi.apps.video_library import video_library_server as video
from pi.apps.video_library import video_qbittorrent as qb
from pi.apps.video_library.video_asset_catalog import MediaAssetCatalog
from pi.tests.media.test_video_identity_integration import (
    TORRENT_ID,
    FakePlayer,
    FakeQbittorrent,
    MediaFixture,
    torrent_result,
)


class AssetReplacementIdentityTests(unittest.TestCase):
    def test_reused_path_with_contradictory_file_identity_creates_new_asset(self) -> None:
        catalog = MediaAssetCatalog(":memory:")
        self.addCleanup(catalog.close)
        path = "/tmp/reused-video-name.mkv"

        original = catalog.resolve_or_create_provisional_file(
            path,
            size=100,
            device_id="device-a",
            inode=101,
            mtime_ns=1_000,
        )
        replacement = catalog.resolve_or_create_provisional_file(
            path,
            size=250,
            device_id="device-a",
            inode=202,
            mtime_ns=2_000,
        )

        self.assertNotEqual(replacement, original)
        self.assertEqual(catalog.resolve_path(path), replacement)
        historical = catalog.list_locations(original)
        self.assertTrue(any(row["valid_to"] is not None for row in historical))

    def test_same_inode_can_grow_without_changing_asset(self) -> None:
        catalog = MediaAssetCatalog(":memory:")
        self.addCleanup(catalog.close)
        path = "/tmp/growing-incomplete-video.mkv"

        partial = catalog.resolve_or_create_provisional_file(
            path,
            size=100,
            device_id="device-a",
            inode=303,
            mtime_ns=1_000,
        )
        grown = catalog.resolve_or_create_provisional_file(
            path,
            size=500,
            device_id="device-a",
            inode=303,
            mtime_ns=2_000,
        )

        self.assertEqual(grown, partial)
        self.assertEqual(catalog.resolve_path(path), partial)


class PlaybackProjectionOrderingTests(unittest.TestCase):
    def test_live_session_survives_wall_clock_rollback(self) -> None:
        catalog = MediaAssetCatalog(":memory:")
        self.addCleanup(catalog.close)
        work_id = catalog.create_work("movie", observed_at=1_000)
        asset_id = catalog.create_asset(work_id=work_id, observed_at=1_000)

        first = catalog.start_session(asset_id, position=10, started_at=1_000)
        catalog.checkpoint(
            first,
            position=400,
            duration=1_000,
            observed_at=1_000,
        )
        catalog.finish_session(first, ended_at=1_000)

        # A Pi clock correction must not make newly observed playback stale.
        second = catalog.start_session(asset_id, position=0, started_at=900)
        catalog.checkpoint(
            second,
            position=500,
            duration=1_000,
            observed_at=901,
        )

        asset_state = catalog.get_asset_state(asset_id)
        work_state = catalog.get_work_watch_state(work_id)
        self.assertEqual(asset_state["position"], 500)
        self.assertEqual(asset_state["last_session_id"], second)
        self.assertEqual(asset_state["play_count"], 2)
        self.assertEqual(work_state["play_count"], 2)

    def test_v1_rollback_edit_survives_wall_clock_correction(self) -> None:
        fixture = MediaFixture()
        self.addCleanup(fixture.cleanup)
        target = fixture.payload("rollback-clock-correction.mkv")
        fixture.link("Movies", "Rollback.Clock.2024.mkv", target)
        service, library, store, _catalog, player = fixture.stack()
        item = next(iter(library.items.values()))
        service.play(item_id=item.id, restart=True)
        player.snapshot_value.update(
            path=str(target), position=240, duration=1_000, state="PAUSED"
        )
        service.bookmark()
        asset_id = item.asset_id
        store.connection.close()

        corrected_time = fixture.clock() - 100
        rollback = sqlite3.connect(fixture.database)
        try:
            with rollback:
                rollback.execute(
                    "UPDATE progress SET position = 777, updated = ? "
                    "WHERE media_key = ?",
                    (corrected_time, item.key),
                )
        finally:
            rollback.close()

        fixture.clock.value = corrected_time + 1
        _service2, _library2, _store2, catalog2, _player2 = fixture.stack()

        self.assertEqual(catalog2.get_asset_state(asset_id)["position"], 777)

    def test_consecutive_low_clock_rollback_edits_survive_before_projection(self) -> None:
        fixture = MediaFixture()
        self.addCleanup(fixture.cleanup)
        target = fixture.payload("rollback-clock-crash-window.mkv")
        fixture.link("Movies", "Rollback.Clock.Crash.2024.mkv", target)
        service, library, store, catalog, player = fixture.stack()
        item = next(iter(library.items.values()))
        service.play(item_id=item.id, restart=True)
        player.snapshot_value.update(
            path=str(target), position=240, duration=1_000, state="PAUSED"
        )
        service.bookmark()

        corrected_time = fixture.clock() - 100
        with store.lock, store.connection:
            store.connection.execute(
                "UPDATE progress SET position = 777, updated = ? WHERE media_key = ?",
                (corrected_time, item.key),
            )
        first = catalog.reconcile_v1_progress(observed_at=corrected_time + 1)
        self.assertEqual(first["applied"], 1)
        self.assertEqual(catalog.get_asset_state(item.asset_id)["position"], 777)

        unchanged = catalog.reconcile_v1_progress(observed_at=corrected_time + 2)
        self.assertEqual(unchanged["unchanged"], 1)

        # Simulate another old-server write before the normal v2 library
        # projection gets a chance to refresh the compatibility row/shadow.
        with store.lock, store.connection:
            store.connection.execute(
                "UPDATE progress SET position = 888, updated = ? WHERE media_key = ?",
                (corrected_time + 3, item.key),
            )
        second = catalog.reconcile_v1_progress(observed_at=corrected_time + 4)

        self.assertEqual(second["applied"], 1)
        self.assertEqual(second["stale"], 0)
        self.assertEqual(catalog.get_asset_state(item.asset_id)["position"], 888)

    def test_low_clock_rollback_delete_then_recreate_is_not_lost(self) -> None:
        fixture = MediaFixture()
        self.addCleanup(fixture.cleanup)
        target = fixture.payload("rollback-clock-delete-recreate.mkv")
        fixture.link("Movies", "Rollback.Clock.Recreate.2024.mkv", target)
        service, library, store, catalog, player = fixture.stack()
        item = next(iter(library.items.values()))
        service.play(item_id=item.id, restart=True)
        player.snapshot_value.update(
            path=str(target), position=240, duration=1_000, state="PAUSED"
        )
        service.bookmark()

        corrected_time = fixture.clock() - 100
        with store.lock, store.connection:
            store.connection.execute(
                "DELETE FROM progress WHERE media_key = ?", (item.key,)
            )
        deleted = catalog.reconcile_v1_progress(observed_at=corrected_time)
        self.assertEqual(deleted["cleared"], 1)
        self.assertEqual(catalog.get_asset_state(item.asset_id)["position"], 0)

        store.record(
            item.key,
            position=555,
            duration=1_000,
            updated=corrected_time + 1,
            title=item.title,
            rel_path=item.rel_path,
        )
        recreated = catalog.reconcile_v1_progress(observed_at=corrected_time + 2)

        self.assertEqual(recreated["applied"], 1)
        self.assertEqual(recreated["stale"], 0)
        self.assertEqual(catalog.get_asset_state(item.asset_id)["position"], 555)

    def test_v2_clear_then_low_clock_v1_replay_is_not_lost(self) -> None:
        fixture = MediaFixture()
        self.addCleanup(fixture.cleanup)
        target = fixture.payload("v2-clear-low-clock-replay.mkv")
        fixture.link("Movies", "V2.Clear.Clock.Replay.2024.mkv", target)
        service, library, store, catalog, player = fixture.stack()
        item = next(iter(library.items.values()))
        service.play(item_id=item.id, restart=True)
        player.snapshot_value.update(
            path=str(target), position=240, duration=1_000, state="PAUSED"
        )
        service.bookmark()

        self.assertTrue(service.update_progress(item, "clear"))
        corrected_time = fixture.clock() - 100
        store.record(
            item.key,
            position=555,
            duration=1_000,
            updated=corrected_time,
            title=item.title,
            rel_path=item.rel_path,
        )
        replayed = catalog.reconcile_v1_progress(observed_at=corrected_time + 1)

        self.assertEqual(replayed["applied"], 1)
        self.assertEqual(replayed["stale"], 0)
        self.assertEqual(catalog.get_asset_state(item.asset_id)["position"], 555)

    def test_pending_v1_edit_cannot_regress_unprojected_former_library_playback(
        self,
    ) -> None:
        fixture = MediaFixture()
        self.addCleanup(fixture.cleanup)
        target = fixture.payload("former-library-causal-edit.mkv")
        link = fixture.link("Movies", "Former.Library.Causal.2025.mkv", target)
        service, library, store, catalog, player = fixture.stack()
        item = next(iter(library.items.values()))

        service.play(item_id=item.id, restart=True)
        player.snapshot_value.update(
            path=str(target), position=240, duration=1_000, state="PAUSED"
        )
        service.bookmark()
        player.snapshot_value.update(state="STOPPED")
        service.bookmark()
        self.assertIsNone(service.active_session_id)

        link.unlink()
        self.assertTrue(service.rescan())
        fixture.clock.value -= 100
        store.record(
            item.key,
            position=333,
            duration=1_000,
            updated=fixture.clock(),
            title=item.title,
            rel_path=item.rel_path,
        )

        launched = service.play_local(str(target), restart=True)
        self.assertTrue(launched["tracked"])
        self.assertEqual(service.active_asset_id, item.asset_id)
        self.assertIsNone(service.active_item)
        self.assertEqual(catalog.get_asset_state(item.asset_id)["position"], 0)

        report = catalog.reconcile_v1_progress(observed_at=fixture.clock())

        self.assertEqual(report["applied"], 0)
        self.assertEqual(report["stale"], 1)
        self.assertEqual(catalog.get_asset_state(item.asset_id)["position"], 0)
        shadow = store.connection.execute(
            "SELECT covered_state_digest FROM video_v2_v1_shadow "
            "WHERE media_key = ?",
            (item.key,),
        ).fetchone()
        self.assertEqual(shadow[0], "untrusted")

    def test_pending_v1_delete_cannot_clear_unprojected_former_library_playback(
        self,
    ) -> None:
        fixture = MediaFixture()
        self.addCleanup(fixture.cleanup)
        target = fixture.payload("former-library-causal-delete.mkv")
        link = fixture.link("Movies", "Former.Library.Delete.2025.mkv", target)
        service, library, store, catalog, player = fixture.stack()
        item = next(iter(library.items.values()))

        service.play(item_id=item.id, restart=True)
        player.snapshot_value.update(
            path=str(target), position=240, duration=1_000, state="PAUSED"
        )
        service.bookmark()
        player.snapshot_value.update(state="STOPPED")
        service.bookmark()

        link.unlink()
        self.assertTrue(service.rescan())
        fixture.clock.value -= 100
        self.assertTrue(store.clear(item.key))

        launched = service.play_local(str(target), restart=True)
        self.assertTrue(launched["tracked"])
        player.snapshot_value.update(
            path=str(target), position=500, duration=1_000, state="PAUSED"
        )
        service.bookmark()
        self.assertEqual(catalog.get_asset_state(item.asset_id)["position"], 500)

        report = catalog.reconcile_v1_progress(observed_at=fixture.clock())

        self.assertEqual(report["cleared"], 0)
        self.assertEqual(report["tombstones"], 1)
        self.assertEqual(catalog.get_asset_state(item.asset_id)["position"], 500)
        tombstone = store.connection.execute(
            "SELECT applied_to_state FROM video_v2_v1_tombstones "
            "WHERE media_key = ?",
            (item.key,),
        ).fetchone()
        self.assertEqual(tombstone[0], 0)
        shadow = store.connection.execute(
            "SELECT covered_state_digest FROM video_v2_v1_shadow "
            "WHERE media_key = ?",
            (item.key,),
        ).fetchone()
        self.assertEqual(shadow[0], "untrusted")


class WorkProjectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = MediaFixture()
        self.addCleanup(self.fixture.cleanup)

    @staticmethod
    def _only_item(library):
        items, _shows = library.snapshot()
        if len(items) != 1:
            raise AssertionError(f"expected one media item, found {len(items)}")
        return items[0]

    def test_explicit_replay_clears_automatic_watched_state(self) -> None:
        target = self.fixture.payload("replay-target.mkv")
        self.fixture.link("Movies", "Replay.Target.2024.mkv", target)
        service, library, store, catalog, player = self.fixture.stack()
        item = self._only_item(library)

        service.play(item_id=item.id, restart=True)
        player.snapshot_value.update(
            path=str(target),
            position=990,
            duration=1_000,
            state="PAUSED",
        )
        service.bookmark()
        self.assertTrue(catalog.get_work_watch_state(item.work_id)["watched"])

        service.play(item_id=item.id, restart=True)
        self.fixture.clock.advance(15)
        player.snapshot_value.update(
            path=str(target),
            position=100,
            duration=1_000,
            state="PAUSED",
        )
        service.bookmark()

        self.assertFalse(catalog.get_work_watch_state(item.work_id)["watched"])
        self.assertFalse(store.get(item.key)["finished"])
        projected = service._progress_all()[item.key]
        self.assertEqual(projected["position"], 100)
        self.assertFalse(projected["finished"])

    def test_active_session_checkpoint_survives_wall_clock_rollback(self) -> None:
        target = self.fixture.payload("clock-rollback-target.mkv")
        self.fixture.link("Movies", "Clock.Rollback.2024.mkv", target)
        service, library, _store, catalog, player = self.fixture.stack()
        item = self._only_item(library)

        service.play(item_id=item.id, restart=True)
        player.snapshot_value.update(
            path=str(target), position=400, duration=1_000, state="PLAYING"
        )
        service.bookmark()

        self.fixture.clock.value -= 100
        player.snapshot_value.update(position=500)
        service.bookmark()

        self.assertEqual(catalog.get_asset_state(item.asset_id)["position"], 500)

    def test_marking_unplayed_item_watched_projects_to_v1_and_ui(self) -> None:
        target = self.fixture.payload("never-played-target.mkv")
        self.fixture.link("Movies", "Never.Played.2025.mkv", target)
        service, library, store, catalog, _player = self.fixture.stack()
        item = self._only_item(library)
        self.assertIsNone(catalog.get_asset_state(item.asset_id))

        service.update_progress(item, "watched")

        legacy = store.get(item.key)
        self.assertIsNotNone(legacy)
        self.assertTrue(legacy["finished"])
        self.assertEqual(legacy["finished_override"], 1)
        progress = service._progress_all()[item.key]
        self.assertTrue(progress["finished"])
        card = next(
            movie
            for movie in service.library_payload()["movies"]
            if movie["id"] == item.id
        )
        self.assertTrue(card["progress"]["finished"])

    def test_active_incomplete_transition_keeps_v1_projection_current(self) -> None:
        incomplete = self.fixture.payload(
            "Verbose.Release-GROUP/video-file.mkv", incomplete=True
        )
        final = self.fixture.payloads / "Verbose.Release-GROUP/video-file.mkv"
        qbittorrent = FakeQbittorrent()
        qbittorrent.set_path(
            incomplete,
            torrent_result(
                incomplete,
                temporary_path=incomplete,
                final_path=final,
            ),
        )
        service, library, store, _catalog, player = self.fixture.stack(
            qbittorrent=qbittorrent
        )

        service.play_local(str(incomplete), restart=True)
        player.snapshot_value.update(position=100, duration=1_000, state="PAUSED")
        service.bookmark()

        final.parent.mkdir(parents=True, exist_ok=True)
        os.replace(incomplete, final)
        self.fixture.link("New", "Transition.Movie.2026.mkv", final)
        completed = torrent_result(
            final,
            temporary_path=incomplete,
            final_path=final,
            progress=1.0,
            state="uploading",
        )
        qbittorrent.set_path(final, completed)
        qbittorrent.completed_results[TORRENT_ID] = (completed,)
        service.reconcile_torrents(TORRENT_ID)
        item = self._only_item(library)

        # VLC can keep reporting its old launch URI after qB moves the inode.
        self.fixture.clock.advance(15)
        player.snapshot_value.update(
            path=str(incomplete),
            position=250,
            duration=1_000,
            state="PAUSED",
        )
        service.bookmark()

        self.assertEqual(store.get(item.key)["position"], 250)


class TransitionFailureRegressionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = MediaFixture()
        self.addCleanup(self.fixture.cleanup)

    @staticmethod
    def _only_item(library):
        items, _shows = library.snapshot()
        if len(items) != 1:
            raise AssertionError(f"expected one media item, found {len(items)}")
        return items[0]

    def test_unresolved_incomplete_legacy_position_applies_on_later_resolution(
        self,
    ) -> None:
        relative = "Deferred.Legacy-GROUP/video-file.mkv"
        incomplete = self.fixture.payload(relative, incomplete=True)
        final = self.fixture.payloads / relative
        self.fixture.legacy.write_text(
            f"/incomplete/{relative} 456000000 0:07:36\n",
            encoding="utf-8",
        )
        os.utime(
            self.fixture.legacy,
            (self.fixture.clock() + 500, self.fixture.clock() + 500),
        )
        qbittorrent = FakeQbittorrent()
        qbittorrent.temp_roots = (str(self.fixture.incomplete),)
        qbittorrent.final_roots = (str(self.fixture.payloads),)
        qbittorrent.set_path(
            incomplete,
            torrent_result(
                incomplete,
                temporary_path=incomplete,
                final_path=final,
            ),
        )
        service, _library, _store, catalog, player = self.fixture.stack(
            qbittorrent=qbittorrent
        )
        self.assertEqual(len(catalog.list_import_records(action="unresolved")), 1)

        service.play_local(str(incomplete))

        self.assertAlmostEqual(
            player.launch_calls[-1]["position"], 456 - video.RESUME_REWIND
        )
        self.assertEqual(len(catalog.list_import_records(action="applied")), 1)
        asset_id = service.active_asset_id
        legacy_events = [
            event
            for event in catalog.list_events(asset_id)
            if event["event_type"] == "legacy_raw_snapshot"
        ]
        self.assertEqual(len(legacy_events), 1)
        self.assertAlmostEqual(legacy_events[0]["position"], 456)

    def test_rescan_reused_symlink_path_binds_replacement_to_a_new_asset(self) -> None:
        original_target = self.fixture.payload("upstream-original.mkv")
        replacement_target = self.fixture.payload("upstream-replacement.mkv")
        link = self.fixture.link(
            "Movies", "Stable.Human.Name.2025.mkv", original_target
        )
        service, library, _store, catalog, _player = self.fixture.stack()
        original = self._only_item(library)
        original_asset = original.asset_id
        original_session = catalog.start_session(original_asset)
        catalog.checkpoint(original_session, position=333, duration=1_000)
        catalog.finish_session(original_session, completed=False)

        link.unlink()
        link.symlink_to(replacement_target)
        self.assertTrue(service.rescan())
        replacement = self._only_item(library)

        self.assertNotEqual(replacement.asset_id, original_asset)
        self.assertEqual(catalog.resolve_path(original_target), original_asset)
        self.assertEqual(catalog.resolve_path(replacement_target), replacement.asset_id)
        self.assertEqual(
            catalog.lookup_asset(replacement.asset_id)["work_id"],
            replacement.work_id,
        )
        # A stable human-facing parser key is work evidence, not proof that a
        # different inode is the same exact playable asset.  Its asset-level
        # playhead must not be copied merely because the symlink name was reused.
        self.assertIsNone(catalog.get_asset_state(replacement.asset_id))

    def test_v1_rollback_edit_after_symlink_retarget_applies_to_current_asset(
        self,
    ) -> None:
        original_target = self.fixture.payload("rollback-old-target.mkv")
        replacement_target = self.fixture.payload("rollback-current-target.mkv")
        link = self.fixture.link(
            "Movies", "Stable.Rollback.Name.2025.mkv", original_target
        )
        service, library, store, _catalog, player = self.fixture.stack()
        original = self._only_item(library)
        service.play(item_id=original.id, restart=True)
        player.snapshot_value.update(
            path=str(original_target), position=333, duration=1_000, state="PAUSED"
        )
        service.bookmark()

        link.unlink()
        link.symlink_to(replacement_target)
        self.assertTrue(service.rescan())
        replacement = self._only_item(library)
        self.assertNotEqual(replacement.asset_id, original.asset_id)
        service.play(item_id=replacement.id, restart=True)
        player.snapshot_value.update(
            path=str(replacement_target),
            position=555,
            duration=1_000,
            state="PAUSED",
        )
        service.bookmark()
        replacement_asset = replacement.asset_id
        replacement_key = replacement.key
        store.connection.close()

        rollback = sqlite3.connect(self.fixture.database)
        try:
            with rollback:
                rollback.execute(
                    "UPDATE progress SET position = 777, updated = ? "
                    "WHERE media_key = ?",
                    (self.fixture.clock() + 100, replacement_key),
                )
        finally:
            rollback.close()

        self.fixture.clock.advance(101)
        _service2, library2, _store2, catalog2, _player2 = self.fixture.stack()
        current = self._only_item(library2)

        self.assertEqual(current.asset_id, replacement_asset)
        self.assertEqual(catalog2.get_asset_state(replacement_asset)["position"], 777)

    def test_qb_lookup_retries_real_target_after_symlink_candidate_error(self) -> None:
        incomplete = self.fixture.payload("Symlink.Retry-GROUP/video-file.mkv", incomplete=True)
        final = self.fixture.payloads / "Symlink.Retry-GROUP/video-file.mkv"
        shortcut = self.fixture.root / "play-this-now.mkv"
        shortcut.symlink_to(incomplete)
        qbittorrent = FakeQbittorrent()
        qbittorrent.set_path(
            shortcut,
            qb.QbittorrentNotFound("symlink path is outside qB roots"),
        )
        qbittorrent.set_path(
            os.path.realpath(shortcut),
            torrent_result(
                incomplete,
                temporary_path=incomplete,
                final_path=final,
            ),
        )
        service, _library, _store, catalog, _player = self.fixture.stack(
            qbittorrent=qbittorrent
        )

        result = service.play_local(str(shortcut), restart=True)

        self.assertEqual(
            qbittorrent.resolve_calls[:2],
            [os.path.abspath(shortcut), os.path.realpath(shortcut)],
        )
        self.assertEqual(result["identity"], "torrent")
        self.assertEqual(
            catalog.lookup_torrent_asset(
                client_id="vanpi", torrent_id=TORRENT_ID, file_index=0
            ),
            service.active_asset_id,
        )

    def test_catalog_failure_during_resume_lookup_still_launches(self) -> None:
        path = self.fixture.root / "resume lookup failure clip.mkv"
        path.write_bytes(b"clip")
        service, _library, _store, catalog, player = self.fixture.stack()

        with mock.patch.object(
            catalog,
            "get_asset_state",
            side_effect=sqlite3.OperationalError("injected resume lookup outage"),
        ):
            result = service.play_local(str(path))

        self.assertTrue(result["ok"])
        self.assertFalse(result["tracked"])
        self.assertEqual(player.launch_calls[-1]["paths"], [str(path.resolve())])
        self.assertIn("degraded", service.identity_error)

    def test_completion_mid_session_does_not_watch_partial_timeline(self) -> None:
        incomplete = self.fixture.payload(
            "Mid.Session.Completion-GROUP/video-file.mkv", incomplete=True
        )
        final = self.fixture.payloads / "Mid.Session.Completion-GROUP/video-file.mkv"
        qbittorrent = FakeQbittorrent()
        qbittorrent.set_path(
            incomplete,
            torrent_result(
                incomplete,
                temporary_path=incomplete,
                final_path=final,
            ),
        )
        service, _library, _store, catalog, player = self.fixture.stack(
            qbittorrent=qbittorrent
        )
        service.play_local(str(incomplete), restart=True)
        asset_id = service.active_asset_id
        work_id = service.active_work_id
        player.snapshot_value.update(
            path=str(incomplete), position=95, duration=100, state="PAUSED"
        )
        service.bookmark()

        final.parent.mkdir(parents=True, exist_ok=True)
        os.replace(incomplete, final)
        self.fixture.link("New", "Mid.Session.Completion.2026.mkv", final)
        completed = torrent_result(
            final,
            temporary_path=incomplete,
            final_path=final,
            progress=1.0,
            state="uploading",
        )
        qbittorrent.set_path(final, completed)
        qbittorrent.completed_results[TORRENT_ID] = (completed,)
        service.reconcile_torrents(TORRENT_ID)

        self.fixture.clock.advance(15)
        player.snapshot_value.update(
            path=str(incomplete), position=99, duration=100, state="PLAYING"
        )
        service.bookmark()

        self.assertFalse(catalog.get_asset_state(asset_id)["completed"])
        self.assertFalse(catalog.get_work_watch_state(work_id)["watched"])

    def test_same_work_assets_keep_independent_playheads_and_targeted_clear(self) -> None:
        service, _library, _store, catalog, _player = self.fixture.stack()
        work_id = catalog.create_work("movie", title="Two exact encodes")
        first_asset = catalog.create_asset(work_id=work_id, asset_kind="encode")
        second_asset = catalog.create_asset(work_id=work_id, asset_kind="encode")
        first_session = catalog.start_session(first_asset)
        catalog.checkpoint(first_session, position=111, duration=1_000)
        catalog.finish_session(first_session, completed=False)
        second_session = catalog.start_session(second_asset)
        catalog.checkpoint(second_session, position=777, duration=1_000)
        catalog.finish_session(second_session, completed=False)

        self.assertEqual(
            service._legacy_progress_for_asset(first_asset, work_id)["position"],
            111,
        )
        self.assertEqual(
            service._legacy_progress_for_asset(second_asset, work_id)["position"],
            777,
        )

        catalog.clear_playhead(first_asset, clear_work_auto=True)

        self.assertEqual(
            service._legacy_progress_for_asset(first_asset, work_id)["position"],
            0,
        )
        self.assertEqual(
            service._legacy_progress_for_asset(second_asset, work_id)["position"],
            777,
        )


class SessionRecoveryRegressionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = MediaFixture()
        self.addCleanup(self.fixture.cleanup)

    def _bootstrap_open_projected_session(
        self,
        *,
        media_key: str,
    ) -> tuple[str, str]:
        store = video.ProgressStore(str(self.fixture.database))
        catalog = MediaAssetCatalog(
            connection=store.connection,
            lock=store.lock,
            clock=self.fixture.clock,
        )
        work_id = catalog.create_work("movie", title="Interrupted rollback movie")
        asset_id = catalog.create_asset(work_id=work_id)
        catalog.bind_legacy_key(asset_id, media_key)
        session_id = catalog.start_session(asset_id, position=0)
        catalog.checkpoint(
            session_id,
            position=240,
            duration=1_000,
            authoritative_order=True,
        )
        catalog.project_v1_progress(
            media_key,
            position=240,
            duration=1_000,
            updated=self.fixture.clock(),
            asset_id=asset_id,
        )
        store.connection.close()
        return asset_id, session_id

    def _start_production_service(self) -> video.VideoService:
        with (
            mock.patch.object(video, "STATE_PATH", str(self.fixture.database)),
            mock.patch.object(video, "QbittorrentClient", return_value=None),
            mock.patch.object(video, "VlcController", return_value=object()),
            mock.patch.object(video, "SonosVolumeController", return_value=None),
            mock.patch.object(video, "_service", None),
        ):
            return video.active_service()

    def test_catalog_restart_closes_session_left_open_by_prior_process(self) -> None:
        first = MediaAssetCatalog(str(self.fixture.database), clock=self.fixture.clock)
        work_id = first.create_work("movie", title="Interrupted movie")
        asset_id = first.create_asset(work_id=work_id)
        session_id = first.start_session(asset_id)
        first.checkpoint(session_id, position=321, duration=1_000)
        first.close()

        restarted = MediaAssetCatalog(
            str(self.fixture.database), clock=self.fixture.clock
        )
        self.addCleanup(restarted.close)
        recovered = restarted.recover_open_sessions()

        self.assertEqual(recovered, 1)
        session = restarted.get_session(session_id)
        self.assertIsNotNone(session["ended_at"])
        self.assertEqual(session["end_reason"], "unclean_shutdown")
        self.assertEqual(restarted.get_asset_state(asset_id)["position"], 321)
        self.assertFalse(restarted.get_asset_state(asset_id)["completed"])

    def test_startup_applies_pending_v1_edit_before_open_session_recovery(self) -> None:
        media_key = "feature:pending-startup-edit:2026"
        asset_id, session_id = self._bootstrap_open_projected_session(
            media_key=media_key
        )
        rollback = video.ProgressStore(str(self.fixture.database))
        rollback.record(
            media_key,
            position=333,
            duration=1_000,
            updated=self.fixture.clock() - 100,
        )
        rollback.connection.close()

        service = self._start_production_service()
        self.addCleanup(service.store.connection.close)

        self.assertEqual(service.catalog.get_asset_state(asset_id)["position"], 333)
        session = service.catalog.get_session(session_id)
        self.assertIsNotNone(session["ended_at"])
        self.assertEqual(session["end_reason"], "unclean_shutdown")

    def test_startup_applies_pending_v1_delete_before_open_session_recovery(self) -> None:
        media_key = "feature:pending-startup-delete:2026"
        asset_id, session_id = self._bootstrap_open_projected_session(
            media_key=media_key
        )
        rollback = video.ProgressStore(str(self.fixture.database))
        self.assertTrue(rollback.clear(media_key))
        rollback.connection.close()

        service = self._start_production_service()
        self.addCleanup(service.store.connection.close)

        self.assertEqual(service.catalog.get_asset_state(asset_id)["position"], 0)
        session = service.catalog.get_session(session_id)
        self.assertIsNotNone(session["ended_at"])
        self.assertEqual(session["end_reason"], "unclean_shutdown")

    def test_startup_defers_open_session_recovery_when_v1_reconcile_fails(
        self,
    ) -> None:
        fake_catalog = mock.Mock()
        fake_catalog.reconcile_v1_progress.side_effect = sqlite3.OperationalError(
            "transient startup failure"
        )
        with (
            mock.patch.object(video, "STATE_PATH", str(self.fixture.database)),
            mock.patch.object(video, "ensure_pre_v2_backup", return_value=None),
            mock.patch.object(video, "MediaAssetCatalog", return_value=fake_catalog),
            mock.patch.object(video, "QbittorrentClient", return_value=None),
            mock.patch.object(video, "VlcController", return_value=object()),
            mock.patch.object(video, "SonosVolumeController", return_value=None),
            mock.patch.object(video, "_service", None),
        ):
            service = video.active_service()
        self.addCleanup(service.store.connection.close)

        fake_catalog.recover_open_sessions.assert_not_called()
        self.assertTrue(service.session_recovery_pending)
        self.assertIn("recovery deferred", service.session_recovery_error)

        service.thread = mock.Mock()
        with mock.patch.object(service, "rescan", return_value=True) as rescan:
            with self.assertRaisesRegex(
                sqlite3.OperationalError, "transient startup failure"
            ):
                service.start()
        rescan.assert_not_called()
        self.assertTrue(service.session_recovery_pending)

    def test_start_retries_fail_once_reconcile_before_rescan(self) -> None:
        fake_catalog = mock.Mock()
        fake_catalog.reconcile_v1_progress.side_effect = [
            sqlite3.OperationalError("one startup failure"),
            {"available": True},
        ]
        with (
            mock.patch.object(video, "STATE_PATH", str(self.fixture.database)),
            mock.patch.object(video, "ensure_pre_v2_backup", return_value=None),
            mock.patch.object(video, "MediaAssetCatalog", return_value=fake_catalog),
            mock.patch.object(video, "QbittorrentClient", return_value=None),
            mock.patch.object(video, "VlcController", return_value=FakePlayer()),
            mock.patch.object(video, "SonosVolumeController", return_value=None),
            mock.patch.object(video, "_service", None),
        ):
            service = video.active_service()
        self.addCleanup(service.store.connection.close)
        self.assertTrue(service.session_recovery_pending)
        self.assertTrue(service.status()["history"]["degraded"])

        service.thread = mock.Mock()
        with mock.patch.object(service, "rescan", return_value=True) as rescan:
            service.start()

        self.assertFalse(service.session_recovery_pending)
        self.assertEqual(fake_catalog.reconcile_v1_progress.call_count, 2)
        fake_catalog.recover_open_sessions.assert_called_once_with()
        rescan.assert_called_once_with()
        self.assertIsNone(service.session_recovery_error)
        self.assertFalse(service.status()["history"]["degraded"])

    def test_start_retries_fail_once_recovery_before_rescan(self) -> None:
        fake_catalog = mock.Mock()
        fake_catalog.reconcile_v1_progress.return_value = {"available": True}
        fake_catalog.recover_open_sessions.side_effect = [
            sqlite3.OperationalError("one recovery failure"),
            1,
        ]
        with (
            mock.patch.object(video, "STATE_PATH", str(self.fixture.database)),
            mock.patch.object(video, "ensure_pre_v2_backup", return_value=None),
            mock.patch.object(video, "MediaAssetCatalog", return_value=fake_catalog),
            mock.patch.object(
                video,
                "QbittorrentClient",
                side_effect=qb.QbittorrentUnavailable("unrelated qB warning"),
            ),
            mock.patch.object(video, "VlcController", return_value=FakePlayer()),
            mock.patch.object(video, "SonosVolumeController", return_value=None),
            mock.patch.object(video, "_service", None),
        ):
            service = video.active_service()
        self.addCleanup(service.store.connection.close)
        self.assertTrue(service.session_recovery_pending)

        service.thread = mock.Mock()
        with mock.patch.object(service, "rescan", return_value=True) as rescan:
            service.start()

        self.assertFalse(service.session_recovery_pending)
        self.assertEqual(fake_catalog.reconcile_v1_progress.call_count, 2)
        self.assertEqual(fake_catalog.recover_open_sessions.call_count, 2)
        rescan.assert_called_once_with()
        self.assertIsNone(service.session_recovery_error)
        history = service.status()["history"]
        self.assertTrue(history["degraded"])
        self.assertIn("qBittorrent identity disabled", history["error"])
        self.assertNotIn("recovery failure", history["error"])

    def test_pending_recovery_blocks_opening_a_second_catalog_session(self) -> None:
        store = video.ProgressStore(str(self.fixture.database))
        self.addCleanup(store.connection.close)
        fake_catalog = mock.Mock()
        fake_catalog.reconcile_v1_progress.side_effect = sqlite3.OperationalError(
            "recovery prerequisite unavailable"
        )
        service = video.VideoService(
            self.fixture.library(),
            store,
            object(),
            catalog=fake_catalog,
            sonos=None,
            clock=self.fixture.clock,
        )
        service.session_recovery_pending = True

        with self.assertRaisesRegex(
            sqlite3.OperationalError, "recovery prerequisite unavailable"
        ):
            service._begin_catalog_session(
                asset_id="asset-new",
                work_id=None,
                path="/tmp/new-track.mkv",
                snapshot={"position": 0, "track_id": "track-new"},
                item=None,
                complete=True,
                clear_override=False,
            )

        fake_catalog.recover_open_sessions.assert_not_called()
        fake_catalog.start_session.assert_not_called()
        self.assertTrue(service.session_recovery_pending)

    def test_transient_finish_failure_keeps_session_available_for_retry(self) -> None:
        path = self.fixture.root / "transient finish failure.mkv"
        path.write_bytes(b"clip")
        service, _library, _store, catalog, player = self.fixture.stack()
        service.play_local(str(path), restart=True)
        session_id = service.active_session_id
        player.snapshot_value = {
            "available": False,
            "state": "OFFLINE",
            "position": 0,
            "duration": 0,
        }

        with mock.patch.object(
            catalog,
            "finish_session",
            side_effect=sqlite3.OperationalError("transient finish failure"),
        ):
            service.bookmark()

        self.assertEqual(service.active_session_id, session_id)
        self.assertIsNone(catalog.get_session(session_id)["ended_at"])

        service.bookmark()

        self.assertIsNone(service.active_session_id)
        self.assertEqual(
            catalog.get_session(session_id)["end_reason"], "player_offline"
        )

    def test_managed_replacement_plays_but_does_not_overwrite_unfinished_session(
        self,
    ) -> None:
        first_path = self.fixture.root / "managed replacement first.mkv"
        second_path = self.fixture.root / "managed replacement second.mkv"
        first_path.write_bytes(b"first")
        second_path.write_bytes(b"second")
        service, _library, store, catalog, player = self.fixture.stack()
        service.play_local(str(first_path), restart=True)
        first_session = service.active_session_id
        first_asset = service.active_asset_id

        with mock.patch.object(
            catalog,
            "finish_session",
            side_effect=sqlite3.OperationalError("transient replacement failure"),
        ):
            result = service.play_local(str(second_path), restart=True)

        self.assertTrue(result["ok"])
        self.assertFalse(result["tracked"])
        self.assertEqual(player.launch_calls[-1]["paths"], [str(second_path.resolve())])
        self.assertEqual(service.active_session_id, first_session)
        self.assertEqual(service.active_asset_id, first_asset)
        self.assertIsNone(catalog.get_session(first_session)["ended_at"])
        self.assertEqual(
            store.connection.execute(
                "SELECT COUNT(*) FROM video_v2_playback_sessions"
            ).fetchone()[0],
            1,
        )

        service.bookmark()

        self.assertIsNotNone(catalog.get_session(first_session)["ended_at"])
        self.assertNotEqual(service.active_session_id, first_session)
        self.assertEqual(service.active_asset_id, catalog.resolve_path(second_path))
        self.assertEqual(
            store.connection.execute(
                "SELECT COUNT(*) FROM video_v2_playback_sessions "
                "WHERE ended_at IS NULL"
            ).fetchone()[0],
            1,
        )

    def test_failed_external_track_change_never_checkpoints_new_track_to_old_asset(
        self,
    ) -> None:
        first_path = self.fixture.root / "external first.mkv"
        second_path = self.fixture.root / "external second.mkv"
        first_path.write_bytes(b"first")
        second_path.write_bytes(b"second")
        service, _library, store, catalog, player = self.fixture.stack()
        service.play_local(str(first_path), restart=True)
        first_session = service.active_session_id
        first_asset = service.active_asset_id
        player.snapshot_value.update(
            path=str(first_path), position=123, duration=900, state="PLAYING"
        )
        service.bookmark()

        player.launch([str(second_path)])
        player.snapshot_value.update(position=700, duration=1_000, state="PLAYING")
        with mock.patch.object(
            catalog,
            "finish_session",
            side_effect=sqlite3.OperationalError("transient track-change failure"),
        ):
            service.bookmark()

        self.assertEqual(service.active_session_id, first_session)
        self.assertEqual(catalog.get_asset_state(first_asset)["position"], 123)
        self.assertEqual(
            store.connection.execute(
                "SELECT COUNT(*) FROM video_v2_playback_sessions"
            ).fetchone()[0],
            1,
        )

        service.bookmark()

        second_asset = catalog.resolve_path(second_path)
        self.assertIsNotNone(catalog.get_session(first_session)["ended_at"])
        self.assertEqual(catalog.get_asset_state(first_asset)["position"], 123)
        self.assertEqual(service.active_asset_id, second_asset)
        self.assertEqual(catalog.get_asset_state(second_asset)["position"], 700)

    def test_retried_explicit_launch_clears_watched_override_on_exact_adoption(
        self,
    ) -> None:
        target = self.fixture.payload("retry-clear-override.mkv")
        self.fixture.link("Movies", "Retry.Clear.Override.2025.mkv", target)
        old_path = self.fixture.root / "old active generic.mkv"
        old_path.write_bytes(b"old")
        service, library, _store, catalog, _player = self.fixture.stack()
        item = next(iter(library.items.values()))
        service.update_progress(item, "watched")
        service.play_local(str(old_path), restart=True)

        with mock.patch.object(
            catalog,
            "finish_session",
            side_effect=sqlite3.OperationalError("transient replacement failure"),
        ):
            service.play(item_id=item.id, restart=True)

        before = catalog.get_work_watch_state(item.work_id)
        self.assertTrue(before["watched"])
        self.assertEqual(before["watched_override"], 1)
        self.assertIsNotNone(service.pending_explicit_launch)

        service.bookmark()

        after = catalog.get_work_watch_state(item.work_id)
        self.assertEqual(service.active_asset_id, item.asset_id)
        self.assertFalse(after["watched"])
        self.assertIsNone(after["watched_override"])
        self.assertIsNone(service.pending_explicit_launch)

    def test_mismatched_track_discards_pending_replay_without_clearing_override(
        self,
    ) -> None:
        first_target = self.fixture.payload("pending-intent-first.mkv")
        second_target = self.fixture.payload("pending-intent-second.mkv")
        self.fixture.link("Movies", "Pending.Intent.First.2025.mkv", first_target)
        self.fixture.link("Movies", "Pending.Intent.Second.2025.mkv", second_target)
        old_path = self.fixture.root / "pending intent old active.mkv"
        old_path.write_bytes(b"old")
        service, library, _store, catalog, player = self.fixture.stack()
        items, _shows = library.snapshot()
        first = next(item for item in items if "First" in item.title)
        second = next(item for item in items if "Second" in item.title)
        service.update_progress(first, "watched")
        service.update_progress(second, "watched")
        service.play_local(str(old_path), restart=True)

        with mock.patch.object(
            catalog,
            "finish_session",
            side_effect=sqlite3.OperationalError("transient replacement failure"),
        ):
            service.play(item_id=first.id, restart=True)
        self.assertIsNotNone(service.pending_explicit_launch)

        player.launch([str(second_target)])
        service.bookmark()

        self.assertEqual(service.active_asset_id, second.asset_id)
        self.assertIsNone(service.pending_explicit_launch)
        self.assertTrue(catalog.get_work_watch_state(first.work_id)["watched"])
        self.assertEqual(
            catalog.get_work_watch_state(first.work_id)["watched_override"], 1
        )
        self.assertTrue(catalog.get_work_watch_state(second.work_id)["watched"])
        self.assertEqual(
            catalog.get_work_watch_state(second.work_id)["watched_override"], 1
        )


if __name__ == "__main__":
    unittest.main()
