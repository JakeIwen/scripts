"""Filesystem UUID promotion and recycled-inode regression contracts."""
from __future__ import annotations

import os
from pathlib import Path
import sqlite3
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from pi.apps.video_library.catalog import CatalogConflict, MediaAssetCatalog
from pi.apps.video_library import filesystem_identity as fs


UUID = "fsuuid:23652e98-ced8-456a-8174-50aa9c86f889"


class FilesystemIdentityTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "file.mkv"
        self.path.write_bytes(b"file")
        self.catalog = MediaAssetCatalog(":memory:")
        self.addCleanup(self.catalog.close)

    def observation(self, device="2161", **changes):
        value = os.stat(self.path)
        return dict(device_id=device, **{
            "inode": value.st_ino, "size": value.st_size,
            "mtime_ns": value.st_mtime_ns, **changes,
        })

    def stable(self):
        return patch.object(fs, "filesystem_device_id", return_value=UUID)

    def test_uuid_resolves_same_filesystem_after_kernel_device_change(self):
        entry = Mock(name="device-link")
        entry.name = UUID.removeprefix(fs.UUID_PREFIX)
        directory = Mock()
        directory.iterdir.return_value = [entry]
        with patch.object(fs, "UUID_DIRECTORY", directory), patch.object(fs.sys, "platform", "linux"):
            for device in (2049, 2081, 2113, 2129, 2161):
                entry.stat.return_value = SimpleNamespace(st_mode=stat.S_IFBLK, st_rdev=device)
                observed = SimpleNamespace(st_dev=device)
                with patch.object(fs.os, "stat", return_value=observed):
                    self.assertEqual(fs.filesystem_device_id(str(self.path), observed), UUID)

    def test_missing_or_ambiguous_uuid_fails_closed(self):
        directory = Mock()
        with patch.object(fs, "UUID_DIRECTORY", directory), patch.object(fs.sys, "platform", "linux"):
            for count in (0, 2):
                entry = Mock()
                entry.stat.return_value = SimpleNamespace(st_mode=stat.S_IFBLK, st_rdev=2113)
                directory.iterdir.return_value = [entry] * count
                with self.assertRaises(OSError):
                    fs.filesystem_device_id(str(self.path), SimpleNamespace(st_dev=2113))

    def test_counterpart_legacy_path_promotes_without_changing_asset(self):
        old = self.catalog.resolve_or_create_provisional_file(self.path, **self.observation())
        with self.stable():
            promoted = self.catalog.resolve_or_create_provisional_file(self.path)
            again = self.catalog.resolve_or_create_provisional_file(self.path)
        self.assertEqual((promoted, again), (old, old))
        devices = [r[0] for r in self.catalog.connection.execute(
            "SELECT device_id FROM video_v2_file_identities WHERE asset_id=?", (old,))]
        self.assertCountEqual(devices, ["2161", UUID])
        self.assertEqual(len(self.catalog.list_locations(old)), 1)

    def test_all_ten_counterpart_files_keep_current_not_retired_duplicate(self):
        for episode in range(1, 11):
            path = self.path.with_name(f"Counterpart.S01E{episode:02}.mkv")
            path.write_bytes(bytes([episode]))
            observed = os.stat(path)
            values = dict(inode=observed.st_ino, size=1, mtime_ns=observed.st_mtime_ns)
            old = self.catalog.resolve_or_create_provisional_file(path, device_id="2161", **values)
            current = self.catalog.resolve_or_create_provisional_file(path, device_id="2113", **values)
            self.assertNotEqual(old, current)
            with self.stable():
                self.assertEqual(self.catalog.resolve_or_create_provisional_file(path), current)

    def test_exact_silo_barbarians_shape_rejects_recycled_inode(self):
        barb = self.catalog.resolve_or_create_provisional_file(
            "/deleted/barbarians.mkv", device_id="2113", inode=151003051,
            size=1409266220, mtime_ns=1703033633739932272)
        silo = self.catalog.resolve_or_create_provisional_file(
            "/media/silo.mkv", device_id="2161", inode=151003051,
            size=4431739775, mtime_ns=1787217266069220939)
        values = SimpleNamespace(st_dev=2113, st_ino=151003051,
                                 st_size=4431739775, st_mtime_ns=1787217266069220939)
        with self.stable(), patch.object(fs.os, "stat", return_value=values):
            actual = self.catalog.resolve_or_create_provisional_file("/media/silo.mkv")
        self.assertEqual(actual, silo)
        self.assertNotEqual(actual, barb)

    def test_same_size_changed_mtime_rejects_both_identity_formats(self):
        for device in ("2113", UUID):
            path = f"/media/{device}.mkv"
            first = self.catalog.resolve_or_create_provisional_file(
                path, device_id=device, inode=33, size=10, mtime_ns=1)
            second = self.catalog.resolve_or_create_provisional_file(
                path, device_id=device, inode=33, size=10, mtime_ns=2)
            self.assertNotEqual(first, second)

    def test_different_size_never_adopts_old_asset_even_same_mtime(self):
        for device in ("2113", UUID):
            first = self.catalog.resolve_or_create_provisional_file(
                f"/media/{device}/old", device_id=device, inode=35, size=10, mtime_ns=1)
            second = self.catalog.resolve_or_create_provisional_file(
                f"/media/{device}/new", device_id=device, inode=35, size=20, mtime_ns=1)
            self.assertNotEqual(first, second)

    def test_retired_path_does_not_promote_old_device_evidence(self):
        old = self.catalog.resolve_or_create_provisional_file(self.path, **self.observation())
        self.catalog.retire_location(self.path)
        with self.stable():
            new = self.catalog.resolve_or_create_provisional_file(self.path, preferred_asset_id=old)
        self.assertNotEqual(old, new)

    def test_explicit_promotion_requires_live_matching_stat(self):
        old = self.catalog.resolve_or_create_provisional_file(self.path, **self.observation())
        expected = self.observation(UUID)
        self.path.write_bytes(b"different")
        with self.stable():
            new = self.catalog.resolve_or_create_provisional_file(self.path, **expected)
        self.assertNotEqual(old, new)

    def test_old_numeric_evidence_cannot_override_different_uuid(self):
        old = self.catalog.resolve_or_create_provisional_file(self.path, **self.observation())
        with self.stable():
            self.assertEqual(self.catalog.resolve_or_create_provisional_file(self.path), old)
        with patch.object(fs, "filesystem_device_id", return_value="fsuuid:other"):
            different = self.catalog.resolve_or_create_provisional_file(self.path)
        self.assertNotEqual(old, different)

    def test_unknown_to_known_mtime_records_stronger_observation(self):
        with self.stable():
            asset = self.catalog.resolve_or_create_provisional_file(self.path)
            self.catalog.connection.execute("UPDATE video_v2_file_identities SET mtime_ns=NULL")
            self.catalog.connection.commit()
            self.assertEqual(self.catalog.resolve_or_create_provisional_file(self.path), asset)
        values = [r[0] for r in self.catalog.connection.execute(
            "SELECT mtime_ns FROM video_v2_file_identities WHERE asset_id=?", (asset,))]
        self.assertCountEqual(values, [None, os.stat(self.path).st_mtime_ns])
        os.utime(self.path, ns=(1, 1))
        with self.stable():
            self.assertNotEqual(self.catalog.resolve_or_create_provisional_file(self.path), asset)

    def test_promotion_conflict_never_leaves_partial_writes(self):
        old = self.catalog.resolve_or_create_provisional_file(self.path, **self.observation())
        self.catalog.resolve_or_create_provisional_file(
            "/other", device_id=UUID, inode=700, size=4, mtime_ns=1, fingerprint="other")
        before = self.catalog.connection.total_changes
        with self.catalog.transaction(), self.stable():
            with self.assertRaises(CatalogConflict):
                self.catalog.resolve_or_create_provisional_file(self.path, fingerprint="other")
        self.assertEqual(self.catalog.connection.total_changes, before)
        self.assertEqual(self.catalog.resolve_path(self.path), old)

    def test_schema_stays_version_three_and_reopen_is_idempotent(self):
        with self.stable():
            self.catalog.resolve_or_create_provisional_file(self.path)
        before = list(self.catalog.connection.execute("SELECT * FROM video_v2_schema_migrations"))
        MediaAssetCatalog(connection=self.catalog.connection)
        self.assertEqual([r[0] for r in before], [1, 2, 3])
        self.assertEqual(before, list(self.catalog.connection.execute("SELECT * FROM video_v2_schema_migrations")))


if __name__ == "__main__":
    unittest.main()
