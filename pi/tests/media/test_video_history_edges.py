#!/usr/bin/env python3
"""Failure-boundary contracts for durable video playback history.

These tests use only temporary files, in-memory player/qBittorrent fakes, and a
loopback test HTTP server.  They deliberately cover the seams where the legacy
tracker was most likely to lose or misattribute progress.
"""

from __future__ import annotations

import json
import os
import socket
import sqlite3
import subprocess
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs
from unittest import mock

from pi.apps.video_library import video_library_server as video
from pi.tests.media.test_video_identity_integration import (
    TORRENT_ID,
    FakePlayer,
    FakeQbittorrent,
    MediaFixture,
    torrent_result,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
BASHRC_PATH = REPOSITORY_ROOT / "pi" / ".bashrc"


class _RecordingHandler(BaseHTTPRequestHandler):
    """Return a configured response and retain the decoded POST form."""

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length).decode("utf-8")
        self.server.requests.append(  # type: ignore[attr-defined]
            {"path": self.path, "form": parse_qs(body), "headers": dict(self.headers)}
        )
        payload = self.server.response_body.encode("utf-8")  # type: ignore[attr-defined]
        self.send_response(self.server.response_status)  # type: ignore[attr-defined]
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        delay = float(getattr(self.server, "response_delay", 0))
        if delay:
            time.sleep(delay)
        try:
            self.wfile.write(payload)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, _format: str, *_args: Any) -> None:
        return


class PlaypFallbackTests(unittest.TestCase):
    """Exercise the real shell function without starting VLC."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / ".twilio").mkdir()
        (self.root / ".twilio" / "twilio_creds.sh").write_text("", encoding="utf-8")
        (self.root / "secrets").mkdir()
        (self.root / "secrets" / ".bash_variables").write_text("", encoding="utf-8")
        self.media = self.root / "random local clip with spaces.mkv"
        self.media.write_bytes(b"not a torrent and that is okay")

    def _run_playp(
        self,
        api: str,
        *,
        direct_status: int = 0,
        max_time: float = 2,
    ) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        environment.update(
            {
                "HOME": str(self.root),
                "BASHRC_UNDER_TEST": str(BASHRC_PATH),
                "MEDIA_UNDER_TEST": self.media.name,
                "PLAYP_TEST_API": api,
                "RUN_VLC_TEST_STATUS": str(direct_status),
                "VAN_VIDEO_PLAYP_MAX_TIME": str(max_time),
                "TMPDIR": str(self.root),
            }
        )
        script = r'''
source "$BASHRC_UNDER_TEST"
VIDEOAPI="$PLAYP_TEST_API"
run_vlc() {
  printf 'DIRECT_VLC:%s\n' "$1"
  return "$RUN_VLC_TEST_STATUS"
}
cd "$HOME"
playp "$MEDIA_UNDER_TEST"
'''
        return subprocess.run(
            ["/bin/bash", "--noprofile", "--norc", "-c", script],
            check=False,
            env=environment,
            capture_output=True,
            text=True,
            timeout=10,
        )

    def _server(
        self, status: int, body: str, *, delay: float = 0
    ) -> tuple[ThreadingHTTPServer, threading.Thread]:
        server = ThreadingHTTPServer(("127.0.0.1", 0), _RecordingHandler)
        server.response_status = status  # type: ignore[attr-defined]
        server.response_body = body  # type: ignore[attr-defined]
        server.response_delay = delay  # type: ignore[attr-defined]
        server.requests = []  # type: ignore[attr-defined]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 2)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return server, thread

    def test_manager_success_handles_unrecognized_media_without_direct_fallback(self) -> None:
        server, _thread = self._server(
            200,
            '{"ok":true,"tracked":true,"identity":"catalog"}',
        )
        port = server.server_address[1]

        result = self._run_playp(f"http://127.0.0.1:{port}/api")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('"identity":"catalog"', result.stdout)
        self.assertNotIn("DIRECT_VLC:", result.stdout)
        requests = server.requests  # type: ignore[attr-defined]
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0]["path"], "/api/play-local")
        self.assertEqual(
            requests[0]["form"]["path"],
            [str(self.media.resolve())],
        )
        self.assertEqual(requests[0]["form"]["subtitles"], ["auto"])
        self.assertEqual(requests[0]["headers"].get("X-Van-Video"), "1")

    def test_manager_connection_failure_uses_original_direct_vlc_path_once(self) -> None:
        # Holding a bound-but-not-listening socket removes the usual free-port
        # race while guaranteeing curl receives a connection error.
        guard = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.addCleanup(guard.close)
        guard.bind(("127.0.0.1", 0))
        port = guard.getsockname()[1]

        result = self._run_playp(
            f"http://127.0.0.1:{port}/api", direct_status=23
        )

        self.assertEqual(result.returncode, 23)
        self.assertEqual(result.stdout.count("DIRECT_VLC:"), 1)
        self.assertIn(str(self.media.resolve()), result.stdout)

    def test_reachable_manager_error_does_not_risk_a_second_playback_launch(self) -> None:
        server, _thread = self._server(
            422,
            '{"ok":false,"error":"local media file is unavailable"}',
        )
        port = server.server_address[1]

        result = self._run_playp(f"http://127.0.0.1:{port}/api")

        self.assertEqual(result.returncode, 1)
        self.assertNotIn("DIRECT_VLC:", result.stdout)
        self.assertIn("local media file is unavailable", result.stderr)
        self.assertEqual(len(server.requests), 1)  # type: ignore[attr-defined]

    def test_ambiguous_response_timeout_never_double_launches(self) -> None:
        server, _thread = self._server(
            200,
            '{"ok":true,"tracked":true}',
            delay=0.25,
        )
        port = server.server_address[1]

        result = self._run_playp(
            f"http://127.0.0.1:{port}/api",
            max_time=0.05,
        )

        self.assertEqual(result.returncode, 1)
        self.assertNotIn("DIRECT_VLC:", result.stdout)
        self.assertIn("not launching VLC again", result.stderr)
        self.assertEqual(len(server.requests), 1)  # type: ignore[attr-defined]


class HistoryFailureBoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = MediaFixture()
        self.addCleanup(self.fixture.cleanup)

    @staticmethod
    def _only_item(library: video.MediaLibrary) -> video.MediaItem:
        items, _shows = library.snapshot()
        if len(items) != 1:
            raise AssertionError(f"expected one library item, found {len(items)}")
        return items[0]

    def test_checkpoint_and_v1_projection_rollback_as_one_transaction(self) -> None:
        target = self.fixture.payload("atomic-history-target.mkv")
        self.fixture.link("Movies", "Atomic.History.2024.mkv", target)
        service, library, store, catalog, player = self.fixture.stack()
        item = self._only_item(library)
        service.play(item_id=item.id, restart=True)

        before_v1 = dict(store.get(item.key) or {})
        before_v2 = dict(catalog.get_asset_state(item.asset_id) or {})
        before_events = list(catalog.list_events(item.asset_id))
        player.snapshot_value.update(
            path=str(target),
            position=412.0,
            duration=1_800.0,
            state="PAUSED",
        )
        project = catalog.project_v1_progress

        def fail_after_v1_write(*args: Any, **kwargs: Any) -> None:
            project(*args, **kwargs)
            raise RuntimeError("injected failure after v1 projection")

        with mock.patch.object(
            catalog, "project_v1_progress", side_effect=fail_after_v1_write
        ):
            with self.assertRaisesRegex(RuntimeError, "injected failure"):
                service.bookmark()

        self.assertEqual(store.get(item.key), before_v1)
        self.assertEqual(catalog.get_asset_state(item.asset_id), before_v2)
        self.assertEqual(catalog.list_events(item.asset_id), before_events)
        self.assertFalse(store.connection.in_transaction)

    def test_parser_key_rename_during_v1_rollback_preserves_newer_progress(self) -> None:
        target = self.fixture.payload("rollback-rename-target.mkv")
        old_link = self.fixture.link(
            "Movies", "Before.Rollback.Rename.2021.mkv", target
        )
        service, library, store, catalog, player = self.fixture.stack()
        original = self._only_item(library)
        service.play(item_id=original.id, restart=True)
        player.snapshot_value.update(
            path=str(target),
            position=240.0,
            duration=1_200.0,
            state="PAUSED",
        )
        service.bookmark()
        original_asset = original.asset_id
        original_work = original.work_id
        original_key = original.key
        store.connection.close()

        # While the v1 server is deployed, it can advance the old parser-key
        # row even if a later alias rebuild gives the same payload a new key.
        old_link.unlink()
        self.fixture.link("Movies", "After.Rollback.Rename.2021.mkv", target)
        rollback_db = sqlite3.connect(self.fixture.database)
        try:
            with rollback_db:
                result = rollback_db.execute(
                    "UPDATE progress SET position = 777, duration = 1200, "
                    "updated = ?, finished = 0 WHERE media_key = ?",
                    (self.fixture.clock() + 100, original_key),
                )
                self.assertEqual(result.rowcount, 1)
        finally:
            rollback_db.close()

        self.fixture.clock.advance(101)
        _service2, library2, store2, catalog2, _player2 = self.fixture.stack()
        renamed = self._only_item(library2)

        self.assertNotEqual(renamed.key, original_key)
        self.assertEqual(renamed.asset_id, original_asset)
        self.assertEqual(renamed.work_id, original_work)
        self.assertAlmostEqual(
            catalog2.get_asset_state(original_asset)["position"], 777.0
        )
        self.assertAlmostEqual(store2.get(original_key)["position"], 777.0)
        self.assertAlmostEqual(store2.get(renamed.key)["position"], 777.0)

    def test_generic_sessions_end_on_offline_and_stay_separate_on_track_change(self) -> None:
        paths = [self.fixture.root / f"loose clip {index}.mkv" for index in range(1, 4)]
        for path in paths:
            path.write_bytes(f"clip {path.name}".encode("utf-8"))
        fake_qb = FakeQbittorrent()
        for path in paths:
            fake_qb.set_path(path, None)
        service, _library, _store, catalog, player = self.fixture.stack(
            qbittorrent=fake_qb
        )

        service.play_local(str(paths[0]), restart=True)
        first_asset = catalog.resolve_path(paths[0])
        first_session = service.active_session_id
        player.snapshot_value.update(
            path=str(paths[0]), position=123.0, duration=900.0, state="PAUSED"
        )
        service.bookmark()
        player.snapshot_value = {
            "available": False,
            "state": "OFFLINE",
            "position": 0.0,
            "duration": 0.0,
        }
        service.bookmark()

        self.assertIsNone(service.active_session_id)
        self.assertEqual(catalog.get_session(first_session)["end_reason"], "player_offline")
        self.assertAlmostEqual(catalog.get_asset_state(first_asset)["position"], 123.0)

        # Simulate VLC being launched outside the manager, then switching to a
        # second external track.  Each track must get its own pinned identity.
        player.launch([str(paths[1])])
        service.bookmark()
        second_asset = catalog.resolve_path(paths[1])
        second_session = service.active_session_id
        player.snapshot_value.update(
            path=str(paths[2]),
            url=paths[2].as_uri(),
            track_id="/fake/external/track-3",
            title=paths[2].name,
            position=7.0,
            duration=300.0,
            state="PLAYING",
        )
        service.bookmark()
        third_asset = catalog.resolve_path(paths[2])
        third_session = service.active_session_id

        self.assertEqual(len({first_asset, second_asset, third_asset}), 3)
        self.assertNotEqual(second_session, third_session)
        self.assertEqual(catalog.get_session(second_session)["end_reason"], "track_changed")
        self.assertEqual(catalog.get_session(third_session)["asset_id"], third_asset)
        self.assertEqual(
            {event["session_id"] for event in catalog.list_events(second_asset)},
            {second_session},
        )
        self.assertEqual(
            {event["session_id"] for event in catalog.list_events(third_asset)},
            {third_session},
        )

    def test_concurrent_status_recording_creates_exactly_one_session(self) -> None:
        path = self.fixture.root / "concurrent loose clip.mkv"
        path.write_bytes(b"clip")
        fake_qb = FakeQbittorrent()
        fake_qb.set_path(path, None)
        service, _library, store, catalog, player = self.fixture.stack(
            qbittorrent=fake_qb
        )
        player.launch([str(path)])

        original = service._record_snapshot_locked
        state_lock = threading.Lock()
        ready = threading.Barrier(3)
        concurrent = 0
        maximum = 0
        errors: list[BaseException] = []

        def observed(snapshot: dict[str, Any], *, force: bool = False):
            nonlocal concurrent, maximum
            with state_lock:
                concurrent += 1
                maximum = max(maximum, concurrent)
            try:
                time.sleep(0.04)
                return original(snapshot, force=force)
            finally:
                with state_lock:
                    concurrent -= 1

        def worker() -> None:
            try:
                ready.wait()
                service.bookmark()
            except BaseException as exc:  # pragma: no cover - assertion aid
                errors.append(exc)

        with mock.patch.object(service, "_record_snapshot_locked", side_effect=observed):
            threads = [threading.Thread(target=worker) for _ in range(2)]
            for thread in threads:
                thread.start()
            ready.wait()
            for thread in threads:
                thread.join(2)

        self.assertFalse(errors)
        self.assertEqual(maximum, 1)
        self.assertEqual(
            store.connection.execute(
                "SELECT COUNT(*) FROM video_v2_playback_sessions WHERE ended_at IS NULL"
            ).fetchone()[0],
            1,
        )
        asset_id = catalog.resolve_path(path)
        self.assertEqual(catalog.get_asset_state(asset_id)["play_count"], 1)

    def test_incomplete_asset_never_becomes_auto_watched_near_partial_eof(self) -> None:
        incomplete = self.fixture.payload("still-growing/video.mkv", incomplete=True)
        final = self.fixture.payloads / "still-growing/video.mkv"
        fake_qb = FakeQbittorrent()
        fake_qb.set_path(
            incomplete,
            torrent_result(
                incomplete,
                temporary_path=incomplete,
                final_path=final,
                progress=0.55,
                state="downloading",
            ),
        )
        service, _library, _store, catalog, player = self.fixture.stack(
            qbittorrent=fake_qb
        )

        service.play_local(str(incomplete), restart=True)
        asset_id = catalog.resolve_path(incomplete)
        work_id = catalog.lookup_asset(asset_id)["work_id"]
        player.snapshot_value.update(
            path=str(incomplete),
            position=1_790.0,
            duration=1_800.0,
            state="PAUSED",
        )
        service.bookmark()
        player.snapshot_value = {
            "available": False,
            "state": "OFFLINE",
            "position": 0.0,
            "duration": 0.0,
        }
        service.bookmark()

        self.assertFalse(catalog.get_asset_state(asset_id)["completed"])
        work_state = catalog.get_work_watch_state(work_id)
        self.assertFalse(work_state["watched_auto"])
        self.assertFalse(work_state["watched"])

    def test_offline_qb_still_treats_incomplete_path_as_partial(self) -> None:
        incomplete = self.fixture.payload("offline/video.mkv", incomplete=True)
        fake_qb = FakeQbittorrent()
        fake_qb.set_path(incomplete, video.QbittorrentError("offline"))
        service, _library, _store, catalog, player = self.fixture.stack(
            qbittorrent=fake_qb
        )

        result = service.play_local(str(incomplete), restart=True)
        self.assertTrue(result["ok"])
        asset_id = catalog.resolve_path(incomplete)
        work_id = catalog.lookup_asset(asset_id)["work_id"]
        player.snapshot_value.update(
            path=str(incomplete), position=95.0, duration=100.0, state="PAUSED"
        )
        service.bookmark()

        self.assertFalse(catalog.get_asset_state(asset_id)["completed"])
        self.assertFalse(catalog.get_work_watch_state(work_id)["watched"])

    def test_catalog_database_error_never_blocks_generic_playback(self) -> None:
        path = self.fixture.root / "database failure clip.mkv"
        path.write_bytes(b"clip")
        service, _library, _store, catalog, player = self.fixture.stack()

        with mock.patch.object(
            catalog,
            "resolve_path",
            side_effect=sqlite3.OperationalError("injected catalog outage"),
        ):
            result = service.play_local(str(path), restart=True)

        self.assertTrue(result["ok"])
        self.assertFalse(result["tracked"])
        self.assertEqual(player.launch_calls[-1]["paths"], [str(path.resolve())])
        self.assertIn("degraded", service.identity_error)

    def test_completion_symlink_rename_reuses_incomplete_progress(self) -> None:
        incomplete = self.fixture.payload(
            "Verbose.Upstream.Name-GROUP/video-file.mkv", incomplete=True
        )
        final = self.fixture.payloads / "Verbose.Upstream.Name-GROUP/video-file.mkv"
        fake_qb = FakeQbittorrent()
        fake_qb.set_path(
            incomplete,
            torrent_result(
                incomplete,
                temporary_path=incomplete,
                final_path=final,
            ),
        )
        service, library, store, catalog, player = self.fixture.stack(
            qbittorrent=fake_qb
        )
        service.play_local(str(incomplete), restart=True)
        asset_id = catalog.resolve_path(incomplete)
        player.snapshot_value.update(
            path=str(incomplete),
            position=411.0,
            duration=1_800.0,
            state="PAUSED",
        )
        service.bookmark()

        final.parent.mkdir(parents=True, exist_ok=True)
        os.replace(incomplete, final)
        self.fixture.link("New", "Human.Readable.Movie.2026.mkv", final)
        completed = torrent_result(
            final,
            temporary_path=incomplete,
            final_path=final,
            progress=1.0,
            state="uploading",
        )
        fake_qb.set_path(final, completed)
        fake_qb.completed_results[TORRENT_ID] = (completed,)

        service.reconcile_torrents(TORRENT_ID)
        item = self._only_item(library)

        self.assertEqual(item.asset_id, asset_id)
        self.assertAlmostEqual(catalog.get_asset_state(asset_id)["position"], 411.0)
        self.assertAlmostEqual(store.get(item.key)["position"], 411.0)
        service.play(item_id=item.id)
        self.assertAlmostEqual(
            player.launch_calls[-1]["position"], 411.0 - video.RESUME_REWIND
        )

    def test_active_incomplete_to_final_checkpoints_keep_v1_projection_current(
        self,
    ) -> None:
        incomplete = self.fixture.payload(
            "Active.Transition-GROUP/video-file.mkv", incomplete=True
        )
        final = self.fixture.payloads / "Active.Transition-GROUP/video-file.mkv"
        fake_qb = FakeQbittorrent()
        fake_qb.set_path(
            incomplete,
            torrent_result(
                incomplete,
                temporary_path=incomplete,
                final_path=final,
            ),
        )
        service, library, store, catalog, player = self.fixture.stack(
            qbittorrent=fake_qb
        )
        service.play_local(str(incomplete), restart=True)
        asset_id = service.active_asset_id
        player.snapshot_value.update(
            path=str(incomplete),
            position=411.0,
            duration=1_800.0,
            state="PAUSED",
        )
        service.bookmark()

        final.parent.mkdir(parents=True, exist_ok=True)
        os.replace(incomplete, final)
        self.fixture.link("New", "Active.Transition.2026.mkv", final)
        completed = torrent_result(
            final,
            temporary_path=incomplete,
            final_path=final,
            progress=1.0,
            state="uploading",
        )
        fake_qb.set_path(final, completed)
        fake_qb.completed_results[TORRENT_ID] = (completed,)
        service.reconcile_torrents(TORRENT_ID)
        item = self._only_item(library)
        self.assertEqual(item.asset_id, asset_id)
        self.assertAlmostEqual(store.get(item.key)["position"], 411.0)

        # VLC can continue reporting the now-retired incomplete path for its
        # already-pinned track. New checkpoints still have to update the v1
        # projection that a one-command rollback will read.
        player.snapshot_value.update(
            path=str(incomplete),
            position=733.0,
            duration=1_800.0,
            state="PLAYING",
        )
        service.bookmark()

        self.assertAlmostEqual(catalog.get_asset_state(asset_id)["position"], 733.0)
        self.assertAlmostEqual(store.get(item.key)["position"], 733.0)

    def test_legacy_position_log_audits_raw_resolved_and_unresolved_lines(self) -> None:
        target = self.fixture.payload("legacy-audit-target.mkv")
        self.fixture.link("Movies", "Legacy.Audit.2023.mkv", target)
        service, library, store, _catalog, _player = self.fixture.stack()
        item = self._only_item(library)
        resolved_line = f"{item.rel_path} 123000000 0:02:03"
        unresolved_line = (
            "/incomplete/Verbose.Unlinked.Release-GROUP/video.mkv "
            "456000000 0:07:36"
        )
        self.fixture.legacy.write_text(
            f"{resolved_line}\n{unresolved_line}\n", encoding="utf-8"
        )
        os.utime(self.fixture.legacy, (1_700_000_123, 1_700_000_123))

        self.assertEqual(service.import_legacy_positions(), 1)

        connection = store.connection
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """
            SELECT records.asset_id, records.action, records.raw_json,
                   runs.source_kind, runs.source_ref
            FROM video_v2_import_records AS records
            JOIN video_v2_import_runs AS runs USING (import_id)
            WHERE records.raw_json IS NOT NULL
            ORDER BY records.imported_at, records.source_key
            """
        ).fetchall()

        def json_contains_exact(value: Any, expected: str) -> bool:
            if isinstance(value, str):
                return value == expected
            if isinstance(value, list):
                return any(json_contains_exact(child, expected) for child in value)
            if isinstance(value, dict):
                return any(json_contains_exact(child, expected) for child in value.values())
            return False

        decoded = [(row, json.loads(row["raw_json"])) for row in rows]
        resolved_records = [
            row for row, raw in decoded if json_contains_exact(raw, resolved_line)
        ]
        unresolved_records = [
            row for row, raw in decoded if json_contains_exact(raw, unresolved_line)
        ]
        self.assertEqual(len(resolved_records), 1)
        self.assertEqual(resolved_records[0]["asset_id"], item.asset_id)
        self.assertEqual(len(unresolved_records), 1)
        self.assertIsNone(unresolved_records[0]["asset_id"])
        self.assertIn("legacy", unresolved_records[0]["source_kind"].casefold())

    def test_first_v2_start_attributes_and_migrates_legacy_log_progress(self) -> None:
        target = self.fixture.payload("first-start-target.mkv")
        self.fixture.link("Movies", "First.Start.2022.mkv", target)
        relative = "/Movies/First.Start.2022.mkv"
        self.fixture.legacy.write_text(
            f"{relative} 321000000 0:05:21\n", encoding="utf-8"
        )
        os.utime(self.fixture.legacy, (1_700_000_500, 1_700_000_500))

        _service, library, store, catalog, _player = self.fixture.stack()
        item = self._only_item(library)
        self.assertAlmostEqual(catalog.get_asset_state(item.asset_id)["position"], 321)
        self.assertAlmostEqual(store.get(item.key)["position"], 321)
        row = store.connection.execute(
            """
            SELECT asset_id, work_id, action
            FROM video_v2_import_records
            WHERE action = 'matched' AND asset_id = ?
            """,
            (item.asset_id,),
        ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(tuple(row), (item.asset_id, item.work_id, "matched"))


if __name__ == "__main__":
    unittest.main()
