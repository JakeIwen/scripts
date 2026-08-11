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


if __name__ == "__main__":
    unittest.main()
