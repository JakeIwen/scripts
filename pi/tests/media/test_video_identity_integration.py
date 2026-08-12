"""Integration contracts for durable video identity and playback history v2.

These tests deliberately exercise the video service at its public boundary and
inspect durable results through ``MediaAssetCatalog``.  qBittorrent and VLC are
in-memory fakes; no player, network service, mount, or live media file is used.
"""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from typing import Any
from unittest import mock

from pi.apps.video_library import video_asset_catalog as identity
from pi.apps.video_library import video_library_server as video
from pi.apps.video_library import video_qbittorrent as qb


TORRENT_ID = "a" * 40
INFOHASH_V2 = "b" * 64


class FakeClock:
    def __init__(self, value: float = 1_700_000_000.0):
        self.value = float(value)

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += float(seconds)


class FakePlayer:
    """Small VLC stand-in that preserves the initially launched track path."""

    def __init__(self):
        self.launch_calls: list[dict[str, Any]] = []
        self.action_calls: list[str] = []
        self.quit_calls = 0
        self.snapshot_value: dict[str, Any] = {
            "available": False,
            "state": "OFFLINE",
            "position": 0.0,
            "duration": 0.0,
        }

    def snapshot(self) -> dict[str, Any]:
        return dict(self.snapshot_value)

    def launch(
        self,
        paths: list[str],
        *,
        position: float = 0,
        subtitles: str = "auto",
    ) -> dict[str, Any]:
        if not paths:
            raise ValueError("no media paths supplied")
        if subtitles not in {"auto", "off"}:
            raise ValueError("subtitles must be auto or off")
        call = {
            "paths": list(paths),
            "position": float(position),
            "subtitles": subtitles,
        }
        self.launch_calls.append(call)
        self.snapshot_value = {
            "available": True,
            "state": "PLAYING",
            "path": paths[0],
            "url": Path(paths[0]).as_uri(),
            "title": Path(paths[0]).name,
            "position": float(position),
            "duration": 1_800.0,
            "track_id": f"/fake/track/{len(self.launch_calls)}",
            "volume": 1.0,
            "rate": 1.0,
            "fullscreen": True,
            "can_fullscreen": True,
            "can_seek": True,
        }
        return self.snapshot()

    def action(self, action: str) -> None:
        self.action_calls.append(action)

    def prepare_room(self, *, wait: bool = False) -> None:
        del wait

    def quit(self) -> None:
        self.quit_calls += 1


class FakeQbittorrent:
    """Path resolver plus completion feed using real qB result dataclasses."""

    client_id = "vanpi"

    def __init__(self):
        self.path_results: dict[str, qb.ResolvedTorrentFile | Exception | None] = {}
        self.completed_results: dict[str, tuple[qb.ResolvedTorrentFile, ...]] = {}
        self.resolve_calls: list[str] = []
        self.completed_calls: list[str] = []
        self.reconcile_file_calls: list[qb.TorrentFileIdentity] = []

    @staticmethod
    def key(path: str | os.PathLike[str]) -> str:
        return os.path.normpath(os.path.abspath(os.fspath(path)))

    def set_path(
        self,
        path: str | os.PathLike[str],
        result: qb.ResolvedTorrentFile | Exception | None,
    ) -> None:
        self.path_results[self.key(path)] = result

    def resolve_path(self, path: str | os.PathLike[str]):
        normalized = self.key(path)
        self.resolve_calls.append(normalized)
        result = self.path_results.get(normalized)
        if isinstance(result, Exception):
            raise result
        return result

    def reconcile_completed_torrent(
        self, torrent_id: str
    ) -> tuple[qb.ResolvedTorrentFile, ...]:
        self.completed_calls.append(torrent_id)
        return self.completed_results.get(torrent_id, ())

    def reconcile_file(
        self, torrent_identity: qb.TorrentFileIdentity
    ) -> qb.ResolvedTorrentFile:
        self.reconcile_file_calls.append(torrent_identity)
        for result in self.path_results.values():
            if (
                isinstance(result, qb.ResolvedTorrentFile)
                and result.identity == torrent_identity
            ):
                return result
        raise qb.QbittorrentNotFound("torrent file is no longer available")


def torrent_result(
    matched_path: Path,
    *,
    temporary_path: Path,
    final_path: Path,
    progress: float = 0.5,
    state: str = "downloading",
) -> qb.ResolvedTorrentFile:
    return qb.ResolvedTorrentFile(
        identity=qb.TorrentFileIdentity("vanpi", TORRENT_ID, 0),
        torrent_name="Verbose.Release.Name.2026-GROUP",
        torrent_state=state,
        infohash_v1=TORRENT_ID,
        infohash_v2=INFOHASH_V2,
        relative_path="Verbose.Release.Name.2026-GROUP/video-file.mkv",
        expected_size=2_000_000_000,
        progress=progress,
        piece_range=(0, 999),
        priority=1,
        availability=None,
        save_path=str(final_path.parent),
        download_path=str(temporary_path.parent),
        content_path=str(final_path),
        temporary_paths=(str(temporary_path),),
        final_paths=(str(final_path),),
        matched_path=str(matched_path),
        location_kind="temporary" if matched_path == temporary_path else "final",
    )


class MediaFixture:
    def __init__(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.mount = self.root / "movingparts"
        self.index = self.mount / "links"
        self.payloads = self.mount / "torrent"
        self.incomplete = self.mount / "incomplete"
        self.index.mkdir(parents=True)
        self.payloads.mkdir(parents=True)
        self.incomplete.mkdir(parents=True)
        self.database = self.root / "progress.sqlite3"
        self.legacy = self.root / "missing-vlc-positions.txt"
        self.clock = FakeClock()
        self._stores: list[video.ProgressStore] = []

    def cleanup(self) -> None:
        for store in self._stores:
            try:
                store.connection.close()
            except sqlite3.Error:
                pass
        self.temporary.cleanup()

    def payload(self, name: str, *, incomplete: bool = False) -> Path:
        parent = self.incomplete if incomplete else self.payloads
        path = parent / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"test video bytes")
        return path

    def link(self, category: str, name: str, target: Path) -> Path:
        link = self.index / category / name
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to(target)
        return link

    def library(self) -> video.MediaLibrary:
        return video.MediaLibrary(
            [video.LibrarySource("movingparts", str(self.mount), str(self.index))],
            require_mount=False,
        )

    def stack(
        self,
        *,
        player: FakePlayer | None = None,
        qbittorrent: FakeQbittorrent | None = None,
    ) -> tuple[
        video.VideoService,
        video.MediaLibrary,
        video.ProgressStore,
        identity.MediaAssetCatalog,
        FakePlayer,
    ]:
        library = self.library()
        store = video.ProgressStore(str(self.database))
        self._stores.append(store)
        catalog = identity.MediaAssetCatalog(
            connection=store.connection,
            lock=store.lock,
            clock=self.clock,
        )
        player = player or FakePlayer()
        service = video.VideoService(
            library,
            store,
            player,
            catalog=catalog,
            qbittorrent=qbittorrent,
            sonos=None,
            legacy_positions=str(self.legacy),
            clock=self.clock,
        )
        self.assert_rescan(service)
        return service, library, store, catalog, player

    @staticmethod
    def assert_rescan(service: video.VideoService) -> None:
        if not service.rescan():
            raise AssertionError(service.library.error)


class OnePollStopEvent:
    """Let ``VideoService._loop`` execute exactly one polling iteration."""

    def __init__(self):
        self.calls = 0

    def wait(self, _timeout: float) -> bool:
        self.calls += 1
        return self.calls > 1


class VideoIdentityIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = MediaFixture()
        self.addCleanup(self.fixture.cleanup)

    @staticmethod
    def only_item(library: video.MediaLibrary) -> video.MediaItem:
        items, _shows = library.snapshot()
        if len(items) != 1:
            raise AssertionError(f"expected one indexed item, found {len(items)}")
        return items[0]

    def test_indexed_item_gets_durable_work_asset_and_legacy_identity(self):
        target = self.fixture.payload("torrent-name-do-not-change.mkv")
        self.fixture.link("Movies", "Arrival.2016.mkv", target)

        _service, library, store, catalog, _player = self.fixture.stack()
        item = self.only_item(library)

        self.assertIsNotNone(item.asset_id)
        self.assertIsNotNone(item.work_id)
        self.assertEqual(catalog.resolve_path(target), item.asset_id)
        self.assertEqual(catalog.resolve_legacy_key(item.key), item.asset_id)
        self.assertEqual(catalog.lookup_asset(item.asset_id)["work_id"], item.work_id)
        public_item = item.as_dict(None)
        self.assertNotIn("asset_id", public_item)
        self.assertNotIn("work_id", public_item)

        first_ids = (item.asset_id, item.work_id)
        store.connection.close()
        _service2, library2, _store2, catalog2, _player2 = self.fixture.stack()
        restarted_item = self.only_item(library2)
        self.assertEqual((restarted_item.asset_id, restarted_item.work_id), first_ids)
        self.assertEqual(catalog2.resolve_legacy_key(restarted_item.key), first_ids[0])

    def test_symlink_parser_rename_keeps_progress_and_binds_both_legacy_keys(self):
        target = self.fixture.payload("upstream-release-name.mkv")
        old_link = self.fixture.link("Movies", "Mystery.Release.2021.mkv", target)
        service, library, store, catalog, player = self.fixture.stack()
        original = self.only_item(library)

        service.play(item_id=original.id, restart=True)
        player.snapshot_value.update(
            path=str(target),
            position=421.0,
            duration=1_500.0,
            state="PAUSED",
        )
        service.bookmark()
        original_ids = (original.asset_id, original.work_id)

        old_link.unlink()
        self.fixture.link("Movies", "Completely.Different.Title.2022.mkv", target)
        self.assertTrue(service.rescan())
        renamed = self.only_item(library)

        self.assertNotEqual(renamed.key, original.key)
        self.assertEqual((renamed.asset_id, renamed.work_id), original_ids)
        self.assertEqual(catalog.resolve_legacy_key(original.key), renamed.asset_id)
        self.assertEqual(catalog.resolve_legacy_key(renamed.key), renamed.asset_id)
        self.assertAlmostEqual(store.get(renamed.key)["position"], 421.0)

        service.play(item_id=renamed.id)
        self.assertAlmostEqual(
            player.launch_calls[-1]["position"],
            421.0 - video.RESUME_REWIND,
        )

    def test_play_local_never_depends_on_qbittorrent_or_a_parsed_identity(self):
        fake_qb = FakeQbittorrent()
        player = FakePlayer()
        service, _library, _store, catalog, _player = self.fixture.stack(
            player=player,
            qbittorrent=fake_qb,
        )
        unavailable = self.fixture.root / "random camera export.mkv"
        unresolved = self.fixture.root / "miscellaneous clip.mp4"
        unavailable.write_bytes(b"video one")
        unresolved.write_bytes(b"video two")
        fake_qb.set_path(unavailable, qb.QbittorrentUnavailable("offline"))
        fake_qb.set_path(unresolved, None)

        for path in (unavailable, unresolved):
            with self.subTest(path=path.name):
                result = service.play_local(str(path), restart=True, subtitles="off")
                self.assertTrue(result["ok"])
                self.assertEqual(player.launch_calls[-1]["paths"], [str(path.resolve())])
                self.assertEqual(player.launch_calls[-1]["subtitles"], "off")
                asset_id = catalog.resolve_path(path)
                self.assertIsNotNone(asset_id)
                self.assertEqual(catalog.get_asset_state(asset_id)["play_count"], 1)
                serialized = json.dumps(result)
                self.assertNotIn(str(path), serialized)
                self.assertNotIn(Path(path).as_uri(), serialized)
                self.assertNotIn(asset_id, serialized)

    def test_qbittorrent_incomplete_to_final_transition_keeps_one_asset(self):
        incomplete = self.fixture.payload(
            "Verbose.Release.Name.2026-GROUP/video-file.mkv", incomplete=True
        )
        final = self.fixture.payloads / "Verbose.Release.Name.2026-GROUP/video-file.mkv"
        fake_qb = FakeQbittorrent()
        initial = torrent_result(
            incomplete,
            temporary_path=incomplete,
            final_path=final,
        )
        fake_qb.set_path(incomplete, initial)
        service, _library, _store, catalog, player = self.fixture.stack(
            qbittorrent=fake_qb
        )

        service.play_local(str(incomplete), restart=True)
        asset_id = catalog.resolve_path(incomplete)
        player.snapshot_value.update(position=360.0, duration=1_800.0, state="PAUSED")
        service.bookmark()

        final.parent.mkdir(parents=True, exist_ok=True)
        os.replace(incomplete, final)
        completed = torrent_result(
            final,
            temporary_path=incomplete,
            final_path=final,
            progress=1.0,
            state="uploading",
        )
        fake_qb.set_path(final, completed)
        fake_qb.completed_results[TORRENT_ID] = (completed,)
        result = service.reconcile_torrents(TORRENT_ID)

        self.assertTrue(result["ok"])
        self.assertNotIn(str(incomplete), json.dumps(result))
        self.assertNotIn(str(final), json.dumps(result))
        self.assertEqual(catalog.resolve_path(final), asset_id)
        self.assertEqual(
            catalog.resolve_path(incomplete, include_historical=True), asset_id
        )
        self.assertEqual(
            catalog.lookup_torrent_asset(
                client_id="vanpi",
                torrent_id=TORRENT_ID,
                file_index=0,
            ),
            asset_id,
        )

        service.play_local(str(final))
        self.assertAlmostEqual(
            player.launch_calls[-1]["position"],
            360.0 - video.RESUME_REWIND,
        )

    def test_active_session_remains_pinned_when_its_launch_path_moves(self):
        incomplete = self.fixture.payload("active/video-file.mkv", incomplete=True)
        final = self.fixture.payloads / "active/video-file.mkv"
        fake_qb = FakeQbittorrent()
        fake_qb.set_path(
            incomplete,
            torrent_result(
                incomplete,
                temporary_path=incomplete,
                final_path=final,
            ),
        )
        service, _library, _store, catalog, player = self.fixture.stack(
            qbittorrent=fake_qb
        )
        service.play_local(str(incomplete), restart=True)
        asset_id = catalog.resolve_path(incomplete)
        initial_resolutions = len(fake_qb.resolve_calls)
        session_id = catalog.get_asset_state(asset_id)["last_session_id"]

        final.parent.mkdir(parents=True, exist_ok=True)
        os.replace(incomplete, final)
        fake_qb.set_path(
            incomplete,
            qb.QbittorrentNotFound("the old temporary path has moved"),
        )
        # VLC may continue reporting its launch URI after qB moves the inode.
        player.snapshot_value.update(
            path=str(incomplete),
            position=512.0,
            duration=1_800.0,
            state="PAUSED",
        )
        service.bookmark()

        state = catalog.get_asset_state(asset_id)
        self.assertEqual(state["last_session_id"], session_id)
        self.assertAlmostEqual(state["position"], 512.0)
        self.assertEqual(len(fake_qb.resolve_calls), initial_resolutions)
        events = catalog.list_events(asset_id)
        self.assertEqual({row["session_id"] for row in events}, {session_id})

    def test_poller_records_state_transitions_and_progress_actions_update_v2_and_v1(self):
        target = self.fixture.payload("polling-contract.mkv")
        self.fixture.link("Movies", "Polling.Contract.2020.mkv", target)
        service, library, store, catalog, player = self.fixture.stack()
        item = self.only_item(library)
        service.play(item_id=item.id, restart=True)
        asset_id = item.asset_id

        player.snapshot_value.update(
            path=str(target),
            position=75.0,
            duration=1_000.0,
            state="PLAYING",
        )
        service.stop_event = OnePollStopEvent()
        service._loop()
        self.fixture.clock.advance(15)
        player.snapshot_value.update(position=125.0, state="PAUSED")
        service.stop_event = OnePollStopEvent()
        service._loop()

        events = catalog.list_events(asset_id)
        playback_states = {
            str(row["playback_state"]).upper()
            for row in events
            if row["playback_state"] is not None
        }
        self.assertIn("PLAYING", playback_states)
        self.assertIn("PAUSED", playback_states)
        self.assertAlmostEqual(catalog.get_asset_state(asset_id)["position"], 125.0)
        self.assertAlmostEqual(store.get(item.key)["position"], 125.0)

        with mock.patch.object(video, "active_service", return_value=service):
            video.app.config.update(TESTING=True)
            client = video.app.test_client()
            watched = client.post(
                "/api/progress", data={"item": item.id, "action": "watched"}
            )
            self.assertEqual(watched.status_code, 200)
            work_state = catalog.get_work_watch_state(item.work_id)
            self.assertTrue(work_state["watched"])
            self.assertEqual(work_state["watched_override"], 1)
            self.assertEqual(store.get(item.key)["finished_override"], 1)

            unwatched = client.post(
                "/api/progress", data={"item": item.id, "action": "unwatched"}
            )
            self.assertEqual(unwatched.status_code, 200)
            work_state = catalog.get_work_watch_state(item.work_id)
            self.assertFalse(work_state["watched"])
            self.assertEqual(work_state["watched_override"], 0)

            cleared = client.post(
                "/api/progress", data={"item": item.id, "action": "clear"}
            )
            self.assertEqual(cleared.status_code, 200)

        self.assertIsNone(store.get(item.key))
        self.assertEqual(catalog.get_asset_state(asset_id)["position"], 0)
        self.assertIn(
            "playhead_cleared",
            {row["event_type"] for row in catalog.list_events(asset_id)},
        )

    def test_v1_projection_is_exactly_rollback_readable_and_reimports_old_edits(self):
        target = self.fixture.payload("rollback-contract.mkv")
        self.fixture.link("Movies", "Rollback.Contract.2019.mkv", target)
        service, library, store, catalog, player = self.fixture.stack()
        item = self.only_item(library)
        service.play(item_id=item.id, restart=True)
        player.snapshot_value.update(
            path=str(target),
            position=240.0,
            duration=1_200.0,
            state="PAUSED",
        )
        service.bookmark()
        asset_id = item.asset_id
        store.connection.close()

        with closing(sqlite3.connect(self.fixture.database)) as rollback_db:
            columns = [
                row[1] for row in rollback_db.execute("PRAGMA table_info(progress)")
            ]
            self.assertEqual(
                columns,
                [
                    "media_key",
                    "position",
                    "duration",
                    "updated",
                    "finished",
                    "finished_override",
                    "play_count",
                    "title",
                    "rel_path",
                ],
            )
            row = rollback_db.execute(
                "SELECT position, duration, finished, title FROM progress "
                "WHERE media_key = ?",
                (item.key,),
            ).fetchone()
            self.assertEqual(row, (240.0, 1_200.0, 0, item.title))
            # This is what a deployment of the old server can do while v2 tables
            # remain present but ignored.
            rollback_db.execute(
                "UPDATE progress SET position = 777, duration = 1200, "
                "updated = ?, finished = 0 WHERE media_key = ?",
                (self.fixture.clock() + 100, item.key),
            )
            rollback_db.commit()

        self.fixture.clock.advance(101)
        _service2, library2, _store2, catalog2, _player2 = self.fixture.stack()
        restarted = self.only_item(library2)
        self.assertEqual(restarted.asset_id, asset_id)
        self.assertAlmostEqual(catalog2.get_asset_state(asset_id)["position"], 777.0)


class LocalOnlyIdentityRouteTests(unittest.TestCase):
    def setUp(self):
        self.fixture = MediaFixture()
        self.addCleanup(self.fixture.cleanup)
        self.fake_qb = FakeQbittorrent()
        self.player = FakePlayer()
        self.service, _library, _store, _catalog, _player = self.fixture.stack(
            player=self.player,
            qbittorrent=self.fake_qb,
        )
        self.local_file = self.fixture.root / "private local filename.mkv"
        self.local_file.write_bytes(b"private video")
        self.fake_qb.set_path(self.local_file, None)
        self.service_patch = mock.patch.object(
            video, "active_service", return_value=self.service
        )
        self.service_patch.start()
        self.addCleanup(self.service_patch.stop)
        video.app.config.update(TESTING=True)
        self.client = video.app.test_client()

    def post(
        self,
        path: str,
        data: dict[str, str],
        *,
        remote_addr: str,
        base_url: str,
    ):
        return self.client.post(
            path,
            data=data,
            base_url=base_url,
            environ_overrides={"REMOTE_ADDR": remote_addr},
            headers={"X-Van-Video": "1"},
        )

    def test_play_local_and_reconcile_are_loopback_only_and_never_return_paths(self):
        remote_play = self.post(
            "/api/play-local",
            {"path": str(self.local_file)},
            remote_addr="192.168.6.55",
            base_url="http://vanpi.lan:8789",
        )
        self.assertEqual(remote_play.status_code, 403)
        self.assertEqual(self.player.launch_calls, [])

        # A loopback-looking Host header must not override the actual peer.
        spoofed_host = self.post(
            "/api/play-local",
            {"path": str(self.local_file)},
            remote_addr="192.168.6.55",
            base_url="http://127.0.0.1:8789",
        )
        self.assertEqual(spoofed_host.status_code, 403)
        self.assertEqual(self.player.launch_calls, [])

        local_play = self.post(
            "/api/play-local",
            {"path": str(self.local_file), "subtitles": "auto"},
            remote_addr="127.0.0.1",
            base_url="http://127.0.0.1:8789",
        )
        self.assertEqual(local_play.status_code, 200)
        serialized_play = json.dumps(local_play.get_json())
        self.assertNotIn(str(self.local_file), serialized_play)
        self.assertNotIn(self.local_file.as_uri(), serialized_play)

        before_reconcile = len(self.fake_qb.completed_calls) + len(
            self.fake_qb.reconcile_file_calls
        )
        remote_reconcile = self.post(
            "/api/torrents/reconcile",
            {},
            remote_addr="192.168.6.55",
            base_url="http://vanpi.lan:8789",
        )
        self.assertEqual(remote_reconcile.status_code, 403)
        self.assertEqual(
            len(self.fake_qb.completed_calls)
            + len(self.fake_qb.reconcile_file_calls),
            before_reconcile,
        )

        local_reconcile = self.post(
            "/api/torrents/reconcile",
            {},
            remote_addr="::1",
            base_url="http://localhost:8789",
        )
        self.assertEqual(local_reconcile.status_code, 200)
        serialized_reconcile = json.dumps(local_reconcile.get_json())
        self.assertNotIn(str(self.fixture.root), serialized_reconcile)
        self.assertNotIn("matched_path", serialized_reconcile)


if __name__ == "__main__":
    unittest.main()
