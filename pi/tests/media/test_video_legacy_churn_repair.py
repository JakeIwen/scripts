"""Behavior coverage for the same-transaction legacy device-churn repair."""
from __future__ import annotations

from contextlib import closing
import hashlib
import json
import sqlite3
from types import SimpleNamespace
from unittest.mock import patch
import unittest

from pi.apps.video_library.catalog import MediaAssetCatalog
from pi.apps.video_library.maintenance import legacy_churn_evidence as churn
from pi.apps.video_library.maintenance import identity_repair_evidence as silo
from pi.tests.media import test_video_filesystem_repair as base


class LegacyChurnRepairTests(base.FilesystemIdentityRepairTests):
    def setUp(self):
        super().setUp()
        self.pairs = []
        with MediaAssetCatalog(str(self.db), clock=lambda: 1000.0) as catalog:
            self.pairs.append(self._seed_pair(catalog, "First", 1, prior=True, target_playhead=23))
            self.pairs.append(self._seed_pair(catalog, "Second", 2, prior=True, target_playhead=None))
            self.pairs.append(self._seed_pair(catalog, "Counterpart", 3, prior=False))
        original = {"repair_input_version": 1,
                    "same_file_pairs": [p["prior"] for p in self.pairs if p["prior"]]}
        self.prior.write_text(json.dumps(original))
        for patcher in (
            patch.object(churn, "PRIOR_PLAN_SHA256", hashlib.sha256(self.prior.read_bytes()).hexdigest()),
            patch.object(churn, "EXPECTED_COUNTS", {churn.ProofKind.PRIOR_REPAIR: 2,
                                                   churn.ProofKind.COUNTERPART: 1}),
            patch.object(silo, "EXPECTED_COUNTERPART_COUNTS", {"duplicate_works": 1, "assets": 2}),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.plan = self.root / "churn-plan.json"
        self.assertEqual(self._capture(self.plan), 0)

    def _seed_pair(self, catalog, title, index, *, prior, target_playhead=None):
        path = f"/mnt/movingparts/torrent/New/{title}.mkv"
        key = f"episode:tv:{title.casefold()}:s1:e1"
        work = catalog.create_work("episode", title=title, series=title, season=1, episode=1)
        physical = {"inode": 100 + index, "size": 500 + index, "mtime_ns": 2000 + index}
        source = catalog.resolve_or_create_provisional_file(path, device_id="2161", **physical)
        catalog.bind_work(source, work)
        target = catalog.create_asset(work_id=work, observed_at=1100 if not prior else 100)
        with catalog.transaction() as db:
            base.FilesystemIdentityRepairTests._insert(db, "video_v2_file_identities", {
                "identity_id": f"fid_current_{index}", "asset_id": target,
                "device_id": silo.FILESYSTEM_DEVICE_ID, **physical,
                "fingerprint_algorithm": None, "fingerprint": None, "observed_at": 1200.0})
        catalog.record_location(target, path)
        catalog.bind_legacy_key(source, key, metadata={"reviewed": title})
        if prior:
            if target_playhead is not None:
                catalog.apply_imported_playhead(target, position=target_playhead,
                                                source_updated=10, event_key="before", source="test")
            session = catalog.start_session(source, started_at=100)
            catalog.checkpoint(session, position=60 * index, duration=200,
                               observed_at=150, completed=index == 2)
            catalog.finish_session(session, ended_at=160)
        identity = catalog.connection.execute(
            "SELECT identity_id FROM video_v2_file_identities WHERE asset_id=?", (source,)).fetchone()[0]
        original = {"source_asset": f"ast_original_{index}", "target_asset": source,
                    "work_id": work, "target_path": path, "expected_stat": physical,
                    "identity_id": identity} if prior else None
        self.items.append(SimpleNamespace(key=key, title=title, rel_path=f"/New/{title}",
                                          path=path, real_path=path))
        self.observations[path] = {"path": path, "device_id": silo.FILESYSTEM_DEVICE_ID, **physical}
        return {"source": source, "target": target, "key": key, "path": path, "prior": original}

    def test_rebinds_all_keys_transfers_two_and_projects_shadows(self):
        source_ids = [pair["source"] for pair in self.pairs]
        before = {table: [r for r in self._rows(table) if r["asset_id"] in source_ids]
                  for table in ("video_v2_assets", "video_v2_asset_playback_state",
                                "video_v2_playback_sessions", "video_v2_playback_events")}
        code, report = self._invoke(apply=True)
        self.assertEqual(code, 0, report)
        self.assertEqual(report["legacy_counts"], {"approved": 3})
        self.assertEqual(len(report["playhead_transfers"]), 2)
        self.assertEqual(report["scan_after"]["legacy_mismatch_count"], 0)
        for table, expected in before.items():
            self.assertEqual([r for r in self._rows(table) if r["asset_id"] in source_ids], expected)
        for pair in self.pairs:
            binding = self._rows("video_v2_legacy_keys", "WHERE media_key=?", (pair["key"],))[0]
            self.assertEqual(binding["asset_id"], pair["target"])
            self.assertIn("reviewed", json.loads(binding["metadata_json"]))
            if pair["prior"]:
                shadow = self._rows("video_v2_v1_shadow", "WHERE media_key=?", (pair["key"],))[0]
                self.assertEqual(shadow["asset_id"], pair["target"])
        before_repeat = self._snapshot()
        code, repeat = self._invoke(apply=True)
        self.assertEqual(code, 0, repeat)
        self.assertEqual(repeat["sqlite_changes"], 0)
        self.assertEqual(repeat["legacy_counts"], {"already_repaired": 3})
        self.assertEqual(before_repeat, self._snapshot())

    def test_changed_legacy_row_is_reported_and_whole_transaction_rolls_back(self):
        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute("UPDATE video_v2_legacy_keys SET metadata_json=? WHERE media_key=?",
                       ('{"changed":true}', self.pairs[0]["key"]))
        before = self._snapshot()
        code, report = self._invoke(apply=True)
        self.assertEqual(code, 2, report)
        self.assertEqual(report["legacy_counts"].get("skipped"), 1)
        self.assertFalse(report["committed"])
        self.assertEqual(before, self._snapshot())

    def test_changed_file_stat_is_reported_without_committing_other_pairs(self):
        self.observations[self.pairs[0]["path"]]["size"] += 1
        before = self._snapshot()
        code, report = self._invoke(apply=True)
        self.assertEqual(code, 2, report)
        self.assertEqual(report["legacy_counts"].get("skipped"), 1)
        self.assertEqual(before, self._snapshot())

    def test_reviewed_prior_plan_checksum_is_required(self):
        self.prior.write_text(self.prior.read_text() + " ")
        before = self._snapshot()
        code, report = self._invoke(apply=True)
        self.assertEqual(code, 1)
        self.assertIn("checksum changed", report["error"])
        self.assertEqual(before, self._snapshot())

    def test_old_silo_only_plan_is_refused_before_writes(self):
        plan = json.loads(self.plan.read_text())
        plan["filesystem_identity_repair_version"] = 1
        plan.pop("legacy_churn")
        self.plan.write_text(json.dumps(plan))
        before = self._snapshot()
        code, report = self._invoke(apply=True)
        self.assertEqual(code, 1)
        self.assertIn("unsupported repair plan version", report["error"])
        self.assertEqual(before, self._snapshot())

    def test_timestamp_only_copy_does_not_need_another_transfer(self):
        source = {"position": 5, "duration": 60, "completed": 0,
                  "play_count": 0, "updated_at": 200}
        self.assertFalse(churn.playhead_transfer_needed(source, {**source, "updated_at": 100}))
        self.assertFalse(churn.playhead_transfer_needed(source, {**source, "position": 20,
                                                               "updated_at": 300}))


if __name__ == "__main__":
    unittest.main()
