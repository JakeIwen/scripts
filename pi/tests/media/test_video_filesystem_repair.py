"""Behavior tests for the guarded Silo filesystem-identity repair."""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from pi.apps.video_library.catalog import MediaAssetCatalog
from pi.apps.video_library.legacy_progress import ProgressStore
from pi.apps.video_library.maintenance import filesystem_identity_repair as repair
from pi.apps.video_library.maintenance import identity_repair_evidence as evidence
from pi.apps.video_library.maintenance import legacy_churn_evidence as churn
from pi.apps.video_library.maintenance.same_file_repair import snapshot


class FakeLibrary:
    def __init__(self, items):
        self.items = items
        self.error = None

    def scan(self):
        return True

    def snapshot(self):
        return self.items, []


class FilesystemIdentityRepairTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = self.root / "catalog.sqlite3"
        store = ProgressStore(str(self.db))
        store.connection.close()
        catalog = MediaAssetCatalog(str(self.db))
        catalog.close()
        self._seed_database()
        self.prior = self.root / "prior.json"
        self.prior.write_text(json.dumps({"repair_input_version": 1, "same_file_pairs": []}))
        self.observations = {
            evidence.TARGET_PATH: {"path": evidence.TARGET_PATH,
                "device_id": evidence.FILESYSTEM_DEVICE_ID, "inode": evidence.TARGET_INODE,
                "size": evidence.TARGET_SIZE, "mtime_ns": evidence.TARGET_MTIME_NS},
        }
        self.patchers = [
            patch.object(churn, "PRIOR_PLAN_SHA256", hashlib.sha256(self.prior.read_bytes()).hexdigest()),
            patch.object(churn, "EXPECTED_COUNTS", {}),
            patch.object(evidence, "filesystem_observation", side_effect=self._observation),
            patch.object(evidence, "target_link_resolves", return_value=True),
            patch.object(evidence, "former_audio_path_status", return_value=[
                {"path": evidence.AUDIO_TARGET_PATH, "is_file": False, "lexists": False},
                {"path": evidence.AUDIO_LINK_PATH, "is_file": False, "lexists": True},
            ]),
            patch.object(evidence, "EXPECTED_AUDIO_HISTORY_COUNTS",
                         {"sessions": 1, "events": 1}),
            patch.object(evidence, "EXPECTED_COUNTERPART_COUNTS",
                         {"duplicate_works": 0, "assets": 0}),
        ]
        for patcher in self.patchers:
            patcher.start()
            self.addCleanup(patcher.stop)
        self.items = [SimpleNamespace(key=evidence.MEDIA_KEY, path=evidence.LINK_PATH,
                                      real_path=evidence.TARGET_PATH)]
        self.plan = self.root / "plan.json"
        self.assertEqual(self._capture(self.plan), 0)
        self.sequence = 0

    @staticmethod
    def _insert(db, table, row):
        columns = tuple(row)
        placeholders = ",".join("?" for _ in columns)
        db.execute(f"INSERT INTO {table} ({','.join(columns)}) VALUES ({placeholders})",
                   tuple(row[column] for column in columns))

    def _seed_database(self):
        db = sqlite3.connect(self.db)
        db.execute("PRAGMA foreign_keys=ON")
        for row in evidence.EXPECTED_WORKS:
            self._insert(db, "video_v2_works", row)
        for row in evidence.EXPECTED_ASSETS:
            self._insert(db, "video_v2_assets", row)
        for row in evidence.EXPECTED_FIXED_IDENTITIES.values():
            self._insert(db, "video_v2_file_identities", row)
        for row in [*evidence.EXPECTED_PRIOR_LOCATIONS, *evidence.EXPECTED_SOURCE_LOCATIONS]:
            self._insert(db, "video_v2_locations", row)
        for row in [*evidence.EXPECTED_PRIOR_ALIASES, *evidence.EXPECTED_SOURCE_ALIASES]:
            self._insert(db, "video_v2_aliases", row)
        self._insert(db, "video_v2_legacy_keys", evidence.EXPECTED_LEGACY)
        self._seed_playback(db)
        db.execute("""INSERT INTO progress
            (media_key,position,duration,updated,finished,finished_override,play_count,title,rel_path)
            VALUES (?,?,?,?,?,?,?,?,?)""", (evidence.MEDIA_KEY, 0.0, 0.0, 1787218876.2805045,
                                              0, None, 1, "silo s03e07", "/New/silo_s03e07"))
        db.commit()
        db.close()

    def _seed_playback(self, db):
        state = evidence.EXPECTED_AUDIO_STATE
        self._insert(db, "video_v2_playback_sessions", {
            "session_id": state["last_session_id"], "asset_id": evidence.AUDIO_ASSET,
            "started_at": 1787215869.8900483, "ended_at": state["updated_at"],
            "end_reason": "stopped", "launch_path": evidence.AUDIO_LINK_PATH,
            "player_instance": "/org/videolan/vlc/playlist/23",
            "metadata_json": '{"complete_at_start":true}',
        })
        self._insert(db, "video_v2_playback_events", {
            "event_id": state["last_event_id"], "session_id": state["last_session_id"],
            "asset_id": evidence.AUDIO_ASSET, "event_type": "session_finished",
            "position": 0.0, "duration": 3278.176, "completed": 0,
            "playback_state": None, "event_key": "fixture-audio-finished",
            "observed_at": state["updated_at"], "payload_json": '{"reason":"stopped"}',
        })
        self._insert(db, "video_v2_asset_playback_state", state)

    def _observation(self, path):
        return dict(self.observations[path])

    def _capture(self, path):
        with contextlib.redirect_stdout(io.StringIO()), patch.object(
                repair, "MediaLibrary", return_value=FakeLibrary(self.items)):
            return repair.main(["--db", str(self.db), "--capture-plan", str(path),
                                "--prior-repair-plan", str(self.prior)])

    def _invoke(self, *, apply=False, holder_error=None):
        self.sequence += 1
        report = self.root / f"report-{self.sequence}.json"
        args = ["--db", str(self.db), "--plan", str(self.plan), "--report", str(report),
                "--prior-repair-plan", str(self.prior)]
        if apply:
            args.extend(["--apply", "--i-have-stopped-video-library"])
        holder = patch.object(repair, "ensure_unused", side_effect=holder_error,
                              return_value={"test": "quiescent"})
        with patch.object(repair, "MediaLibrary", return_value=FakeLibrary(self.items)), holder, \
                contextlib.redirect_stdout(io.StringIO()):
            code = repair.main(args)
        return code, json.loads(report.read_text())

    def _rows(self, table, where="", args=()):
        db = sqlite3.connect(self.db)
        db.row_factory = sqlite3.Row
        try:
            return [dict(row) for row in db.execute(f"SELECT * FROM {table} {where}", args)]
        finally:
            db.close()

    def _snapshot(self):
        db = sqlite3.connect(self.db)
        db.row_factory = sqlite3.Row
        try:
            return snapshot(db)
        finally:
            db.close()

    def test_default_dry_run_uses_memory_backup_and_leaves_bytes_unchanged(self):
        before = self.db.read_bytes()
        code, report = self._invoke()
        self.assertEqual(code, 0, report.get("error"))
        self.assertEqual(self.db.read_bytes(), before)
        self.assertEqual(report["status"], "approved")
        self.assertTrue(report["rolled_back"])
        self.assertFalse(report["committed"])
        self.assertEqual(report["scan_after"]["target_resolutions"][0]["asset_id"],
                         evidence.CORRECT_ASSET)

    def test_apply_preserves_history_metadata_and_has_only_intended_deltas(self):
        history_before = {table: self._rows(table, "WHERE asset_id=?", (evidence.AUDIO_ASSET,))
                          for table in ("video_v2_playback_sessions",
                                        "video_v2_playback_events",
                                        "video_v2_asset_playback_state")}
        legacy_before = self._rows("video_v2_legacy_keys")[0]
        code, report = self._invoke(apply=True)
        self.assertEqual(code, 0, report.get("error"))
        self.assertTrue(report["committed"])
        identities = {row["identity_id"]: row for row in self._rows("video_v2_file_identities")}
        self.assertIsNone(identities[evidence.WRONG_IDENTITY]["device_id"])
        self.assertEqual(identities[evidence.OLD_BARBARIANS_IDENTITY]["device_id"], "2113")
        stable = [row for row in identities.values() if row["asset_id"] == evidence.CORRECT_ASSET
                  and row["device_id"] == evidence.FILESYSTEM_DEVICE_ID]
        self.assertEqual(len(stable), 1)
        legacy_after = self._rows("video_v2_legacy_keys")[0]
        self.assertEqual(legacy_after["asset_id"], evidence.CORRECT_ASSET)
        self.assertEqual(legacy_after["metadata_json"], legacy_before["metadata_json"])
        self.assertEqual(legacy_after["first_seen"], legacy_before["first_seen"])
        for table, expected in history_before.items():
            self.assertEqual(self._rows(table, "WHERE asset_id=?", (evidence.AUDIO_ASSET,)), expected)
        self.assertTrue(all(row["deleted"] == 0 for row in report["row_counts"].values()))
        self.assertEqual(report["preserved"]["audio_sessions"], 1)
        self.assertIn("outside the guarded Silo repair",
                      report["counterpart_duplicates"]["action"])

    def test_repeat_with_same_plan_is_noop(self):
        code, first = self._invoke(apply=True)
        self.assertEqual(code, 0, first.get("error"))
        after_first = self._snapshot()
        code, second = self._invoke(apply=True)
        self.assertEqual(code, 0, second.get("error"))
        self.assertEqual(second["status"], "already_repaired")
        self.assertEqual(second["sqlite_changes"], 0)
        self.assertEqual(self._snapshot(), after_first)

    def test_changed_evidence_skips_without_mutation(self):
        db = sqlite3.connect(self.db)
        db.execute("UPDATE video_v2_aliases SET metadata_json='changed' WHERE alias_id=?",
                   (evidence.EXPECTED_SOURCE_ALIASES[0]["alias_id"],))
        db.commit()
        db.close()
        before = self._snapshot()
        code, report = self._invoke(apply=True)
        self.assertEqual(code, 2)
        self.assertEqual(report["summary"]["skipped"], 1)
        self.assertIn("source alias changed", report["skipped_reason"])
        self.assertFalse(report["committed"])
        self.assertEqual(self._snapshot(), before)

    def test_unreviewed_source_version_skips_without_mutation(self):
        db = sqlite3.connect(self.db)
        old = evidence.EXPECTED_SOURCE_LOCATIONS[0]
        db.execute("UPDATE video_v2_locations SET valid_to=last_seen WHERE location_id=?",
                   (old["location_id"],))
        self._insert(db, "video_v2_locations", {**old, "location_id": "loc_unreviewed"})
        db.commit()
        db.close()
        before = self._snapshot()
        code, report = self._invoke(apply=True)
        self.assertEqual(code, 2)
        self.assertIn("active source versions changed", report["skipped_reason"])
        self.assertEqual(before, self._snapshot())

    def test_postscan_conflict_rolls_back_entire_repair(self):
        before = self._snapshot()
        from pi.apps.video_library.catalog_values import CatalogConflict
        with patch.object(repair, "_scan_item", side_effect=CatalogConflict("conflict")):
            code, report = self._invoke(apply=True)
        self.assertEqual(code, 1)
        self.assertIn("post-repair file conflicts", report["error"])
        self.assertFalse(report["committed"])
        self.assertEqual(before, self._snapshot())

    def test_wrong_uuid_attachment_is_also_invalidated(self):
        extra = {"identity_id": "fid_wrong_uuid_fixture", "asset_id": evidence.WRONG_ASSET,
                 "device_id": evidence.FILESYSTEM_DEVICE_ID, "inode": evidence.TARGET_INODE,
                 "size": evidence.TARGET_SIZE, "mtime_ns": evidence.TARGET_MTIME_NS,
                 "fingerprint_algorithm": None, "fingerprint": None,
                 "observed_at": 1791441000.0}
        db = sqlite3.connect(self.db)
        self._insert(db, "video_v2_file_identities", extra)
        db.commit()
        db.close()
        code, report = self._invoke(apply=True)
        self.assertEqual(code, 0, report.get("error"))
        identities = {row["identity_id"]: row for row in self._rows("video_v2_file_identities")}
        self.assertIsNone(identities[extra["identity_id"]]["device_id"])
        self.assertEqual(identities[evidence.OLD_BARBARIANS_IDENTITY]["size"], 1409266220)

    def test_apply_requires_acknowledgement_and_holder_check(self):
        report_path = self.root / "missing-ack.json"
        args = ["--db", str(self.db), "--plan", str(self.plan), "--report", str(report_path),
                "--apply"]
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            repair.main(args)
        self.assertFalse(report_path.exists())
        before = self._snapshot()
        code, report = self._invoke(apply=True, holder_error=RuntimeError("database held"))
        self.assertEqual(code, 1)
        self.assertIn("database held", report["error"])
        self.assertEqual(self._snapshot(), before)

    def test_capture_refuses_changed_work_instead_of_rubber_stamping_it(self):
        db = sqlite3.connect(self.db)
        db.execute("UPDATE video_v2_assets SET work_id=? WHERE asset_id=?",
                   (evidence.BARBARIANS_WORK, evidence.CORRECT_ASSET))
        db.commit()
        db.close()
        changed_plan = self.root / "changed-plan.json"
        self.assertEqual(self._capture(changed_plan), 1)
        self.assertFalse(changed_plan.exists())

    def test_postscan_rejects_unplanned_legacy_mismatches(self):
        other_path = "/mnt/movingparts/torrent/New/Vietnam.fixture.mkv"
        other_key = "episode:tv:vietnam:s1:e1"
        other_work = "wrk_vietnam_fixture"
        other_asset = "ast_vietnam_fixture"
        self.observations[other_path] = {"path": other_path,
            "device_id": evidence.FILESYSTEM_DEVICE_ID, "inode": 999,
            "size": 10, "mtime_ns": 20}
        self.items.append(SimpleNamespace(key=other_key, path=other_path, real_path=other_path))
        db = sqlite3.connect(self.db)
        self._insert(db, "video_v2_works", {
            "work_id": other_work, "kind": "episode", "title": "Vietnam", "year": None,
            "series": "Vietnam", "season": 1, "episode": 1, "external_ids_json": None,
            "metadata_json": None, "created_at": 100.0, "updated_at": 100.0})
        self._insert(db, "video_v2_assets", {
            "asset_id": other_asset, "work_id": other_work, "asset_kind": "provisional-file",
            "expected_size": 10, "fingerprint_algorithm": None, "fingerprint": None,
            "metadata_json": None, "created_at": 100.0, "updated_at": 100.0})
        self._insert(db, "video_v2_file_identities", {
            "identity_id": "fid_vietnam_fixture", "asset_id": other_asset,
            "device_id": evidence.FILESYSTEM_DEVICE_ID, "inode": 999, "size": 10,
            "mtime_ns": 20, "fingerprint_algorithm": None, "fingerprint": None,
            "observed_at": 100.0})
        self._insert(db, "video_v2_locations", {
            "location_id": "loc_vietnam_fixture", "asset_id": other_asset,
            "path": other_path, "location_kind": "library-target", "source": "movingparts",
            "valid_from": 100.0, "valid_to": None, "last_seen": 100.0,
            "metadata_json": None})
        self._insert(db, "video_v2_legacy_keys", {
            "source": "v1-progress", "media_key": other_key, "asset_id": evidence.WRONG_ASSET,
            "first_seen": 100.0, "last_seen": 100.0, "metadata_json": None})
        db.commit()
        db.close()
        code, report = self._invoke()
        self.assertEqual(code, 1)
        self.assertIn("legacy binding mismatches remain", report["error"])
        self.assertTrue(report["rolled_back"])
        scan = report["scan_after"]
        self.assertEqual(scan["catalog_status"], "remaining mismatches or scan errors")
        self.assertEqual(scan["legacy_binding_mismatches"], [{
            "media_key": other_key, "path": other_path, "real_path": other_path,
            "bound_asset": evidence.WRONG_ASSET, "current_asset": other_asset}])


if __name__ == "__main__":
    unittest.main()
