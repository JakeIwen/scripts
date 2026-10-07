"""Playback persistence while a library scan owns the catalog transaction."""

from __future__ import annotations

import threading
import unittest

from pi.apps.video_library.catalog import MediaAssetCatalog
from pi.apps.video_library.legacy_progress import ProgressStore
from pi.tests.media.test_video_identity_integration import FakePlayer, MediaFixture


class StatusSignalPlayer(FakePlayer):
    def __init__(self) -> None:
        super().__init__()
        self.observe_status = False
        self.status_snapshot = threading.Event()

    def snapshot(self):
        value = super().snapshot()
        if self.observe_status:
            self.status_snapshot.set()
        return value


class VideoScanPlaybackTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = MediaFixture()
        self.addCleanup(self.fixture.cleanup)

    def assert_durable_progress(
        self,
        *,
        media_key: str,
        asset_id: str,
        marker: str,
        position: float,
    ) -> None:
        reopened_store = ProgressStore(str(self.fixture.database))
        self.addCleanup(reopened_store.connection.close)
        reopened_catalog = MediaAssetCatalog(
            connection=reopened_store.connection,
            lock=reopened_store.lock,
            clock=self.fixture.clock,
        )
        self.assertIsNone(
            reopened_catalog.resolve_alias(marker, namespace="scan-rollback-test")
        )
        asset_state = reopened_catalog.get_asset_state(asset_id)
        legacy_progress = reopened_store.get(media_key)
        self.assertIsNotNone(asset_state)
        self.assertIsNotNone(legacy_progress)
        self.assertEqual(asset_state["position"], position)
        self.assertEqual(legacy_progress["position"], position)

    def test_status_save_waits_for_scan_transaction_and_survives_rollback(self) -> None:
        target = self.fixture.payload("scan-playback.mkv")
        self.fixture.link("Movies", "Scan.Playback.2026.mkv", target)
        player = StatusSignalPlayer()
        service, library, store, catalog, _player = self.fixture.stack(player=player)
        items, _shows = library.snapshot()
        self.assertEqual(len(items), 1)
        item = items[0]
        asset_id = item.asset_id
        self.assertIsNotNone(asset_id)
        self.assertIs(catalog.connection, store.connection)
        self.assertIs(catalog.lock, store.lock)

        service.play(item_id=item.id, restart=True)
        player.snapshot_value.update(
            position=321.0,
            duration=1_200.0,
            state="PAUSED",
        )
        player.observe_status = True
        player.status_snapshot.clear()

        status_finished = threading.Event()
        status_results: list[dict] = []
        errors: list[BaseException] = []

        def save_status() -> None:
            try:
                status_results.append(service.status())
            except BaseException as exc:
                errors.append(exc)
            finally:
                status_finished.set()

        status_thread = threading.Thread(
            target=save_status,
            name="video-status-during-scan-test",
            daemon=True,
        )
        marker = "status-save-must-outlive-scan-rollback"

        try:
            with self.assertRaisesRegex(RuntimeError, "abort test scan"):
                with catalog.transaction():
                    catalog.record_alias(
                        asset_id,
                        marker,
                        namespace="scan-rollback-test",
                    )
                    self.assertTrue(service.rescan())
                    status_thread.start()
                    self.assertTrue(
                        player.status_snapshot.wait(1),
                        "status did not read the player snapshot",
                    )
                    self.assertFalse(
                        status_finished.wait(0.1),
                        "status save did not wait for the scan transaction",
                    )
                    raise RuntimeError("abort test scan")
        finally:
            if status_thread.ident is not None:
                status_thread.join(1)

        self.assertFalse(status_thread.is_alive(), "status did not finish after rollback")
        self.assertEqual(errors, [])
        self.assertEqual(len(status_results), 1)
        self.assertEqual(
            status_results[0]["player"]["item"]["progress"]["position"],
            321.0,
        )
        self.assert_durable_progress(
            media_key=item.key,
            asset_id=asset_id,
            marker=marker,
            position=321.0,
        )


if __name__ == "__main__":
    unittest.main()
