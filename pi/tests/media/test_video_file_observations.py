"""Regression coverage for interrupted filesystem observations."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from pi.apps.video_library.catalog import CatalogConflict, MediaAssetCatalog


class FileObservationFailureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = MediaAssetCatalog(":memory:")
        self.addCleanup(self.catalog.close)

    def test_unavailable_existing_file_does_not_replace_its_asset(self) -> None:
        path = "/media/Legion.S02E08.mkv"
        observation = {
            "device_id": "2161",
            "inode": 151006451,
            "size": 1076709049,
            "mtime_ns": 1639853797260709133,
        }
        original = self.catalog.resolve_or_create_provisional_file(path, **observation)
        session = self.catalog.start_session(original, position=120)
        before = self.catalog.list_locations(original)
        with patch("pi.apps.video_library.catalog.os.stat", side_effect=FileNotFoundError(path)):
            with self.assertRaises(FileNotFoundError):
                self.catalog.resolve_or_create_provisional_file(path)
        self.assertEqual(self.catalog.resolve_path(path), original)
        self.assertEqual(self.catalog.list_locations(original), before)
        recovered = self.catalog.resolve_or_create_provisional_file(path, **observation)
        self.assertEqual(recovered, original)
        self.assertEqual(self.catalog.get_session(session)["asset_id"], original)
        self.assertEqual(self.catalog.get_asset_state(recovered)["position"], 120)

    def test_stat_loss_then_restore_does_not_conflict(self) -> None:
        path = "/media/The.Endgame.mkv"
        observation = {
            "device_id": "2161", "inode": 151008515,
            "size": 3716894545, "mtime_ns": 1766047424554473073,
        }
        original = self.catalog.resolve_or_create_provisional_file(path, **observation)
        with patch("pi.apps.video_library.catalog.os.stat", side_effect=OSError):
            try:
                self.catalog.resolve_or_create_provisional_file(path)
            except OSError:
                pass
        recovered = self.catalog.resolve_or_create_provisional_file(path, **observation)
        self.assertEqual(recovered, original)

    def test_unavailable_new_file_does_not_create_path_only_identity(self) -> None:
        path = "/media/unavailable.mkv"
        with patch("pi.apps.video_library.catalog.os.stat", side_effect=OSError("drive unavailable")):
            with self.assertRaisesRegex(OSError, "drive unavailable"):
                self.catalog.resolve_or_create_provisional_file(path)
        self.assertIsNone(self.catalog.resolve_path(path))

    def test_conflict_inside_scan_transaction_does_not_retire_path(self) -> None:
        path = "/media/current.mkv"
        previous = self.catalog.resolve_or_create_provisional_file(
            path, device_id="2081", inode=10, size=100, mtime_ns=1000
        )
        self.catalog.resolve_or_create_provisional_file(
            "/media/inode.mkv", device_id="2161", inode=20, size=200, mtime_ns=2000
        )
        self.catalog.resolve_or_create_provisional_file(
            "/media/fingerprint.mkv", device_id="2161", inode=30, size=200,
            mtime_ns=2000, fingerprint="different-asset",
        )
        before = self.catalog.list_locations(previous)
        # The scanner catches per-item failures inside its batch transaction.
        with self.catalog.transaction():
            with self.assertRaises(CatalogConflict):
                self.catalog.resolve_or_create_provisional_file(
                    path, device_id="2161", inode=20, size=200, mtime_ns=2000,
                    fingerprint="different-asset",
                )
        self.assertEqual(self.catalog.resolve_path(path), previous)
        self.assertEqual(self.catalog.list_locations(previous), before)

    def test_existing_path_only_collision_still_requires_review(self) -> None:
        path = "/media/Legion.S02E08.mkv"
        observation = {
            "device_id": "2161",
            "inode": 151006451,
            "size": 1076709049,
            "mtime_ns": 1639853797260709133,
        }
        original = self.catalog.resolve_or_create_provisional_file(path, **observation)
        unproven = self.catalog.create_asset(asset_kind="provisional-file")
        self.catalog.record_location(unproven, path)
        session = self.catalog.start_session(unproven, position=300)
        with self.assertRaises(CatalogConflict):
            self.catalog.resolve_or_create_provisional_file(path, **observation)
        self.assertEqual(self.catalog.resolve_path(path), unproven)
        self.assertEqual(self.catalog.get_session(session)["asset_id"], unproven)
        self.assertEqual(self.catalog.get_asset_state(unproven)["position"], 300)
        self.assertIsNone(self.catalog.get_asset_state(original))


if __name__ == "__main__":
    unittest.main()
