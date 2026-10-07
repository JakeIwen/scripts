"""Behavioral tests for the standalone, fail-closed maintenance entrypoint."""
from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from pi.apps.video_library.catalog import MediaAssetCatalog
from pi.apps.video_library.legacy_progress import ProgressStore
from pi.apps.video_library.library import MediaLibrary
from pi.apps.video_library.media_models import LibrarySource
from pi.apps.video_library.maintenance import same_file_repair as repair


class SameFileRepairTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.db = self.root / "progress.sqlite3"
        store = ProgressStore(str(self.db))
        store.connection.close()
        self.catalog = MediaAssetCatalog(str(self.db))
        self.catalog.connection.row_factory = sqlite3.Row
        self.addCleanup(self.catalog.close)
        self.media = self.root / "Movie.2024.mkv"
        self.media.write_bytes(b"test fixture")
        self.index = self.root / "links"
        (self.index / "Movies").mkdir(parents=True)
        self.link = self.index / "Movies" / "Movie_2024"
        self.link.symlink_to(self.media)
        self.library = MediaLibrary([LibrarySource("fixture", str(self.root), str(self.index))],
                                    require_mount=False)
        self.assertTrue(self.library.scan())
        self.item = self.library.snapshot()[0][0]
        self.work = self.catalog.create_work("movie", title="Movie")
        self.target = self.catalog.resolve_or_create_provisional_file(str(self.media))
        self.catalog.bind_work(self.target, self.work)
        self.source = self.catalog.create_asset(work_id=self.work, asset_kind="provisional-file")
        for path in (self.media, self.link):
            self.catalog.record_location(self.source, str(path))
        self.catalog.record_alias(self.source, self.item.key, namespace="parser-key")
        for alias in self.item.aliases:
            self.catalog.record_alias(self.source, alias, namespace="library-relative-path")
        self.catalog.bind_legacy_key(self.source, self.item.key)
        for asset, position, observed in ((self.target, 25, 100), (self.source, 75, 200)):
            session = self.catalog.start_session(asset, position=position, started_at=observed)
            self.catalog.finish_session(session, ended_at=observed + 1)
        self.pair = self.make_pair()
        self.plan = dict(repair_input_version=1, expected_library_items=1,
                         same_file_pairs=[self.pair], reused_inode_exceptions=[])
        self.plan_path = self.root / "plan.json"
        self.sequence = 0

    def make_pair(self):
        db = self.catalog.connection
        ids = repair.rows(db, "video_v2_file_identities", "WHERE asset_id=?", (self.target,))
        return dict(title=self.item.title, source_asset=self.source, target_asset=self.target,
                    work_id=self.work, target_path=str(self.media), expected_stat=repair.stat_value(self.media),
                    identity_id=ids[0]["identity_id"], expected_identities=ids,
                    expected_assets=[self.catalog.lookup_asset(a) for a in (self.source, self.target)],
                    expected_legacy_assets={self.source: self.catalog.lookup_asset(self.source)},
                    expected_legacy_identities={}, expected_legacy_locations={},
                    expected_progress={self.item.key: []},
                    locations_to_version=self.catalog.list_locations(self.source, active_only=True),
                    aliases_to_version=repair.rows(db, "video_v2_aliases", "WHERE asset_id=? AND valid_to IS NULL", (self.source,)),
                    legacy_bindings_to_review=repair.rows(db, "video_v2_legacy_keys"),
                    playback_states=[self.catalog.get_asset_state(a) for a in (self.source, self.target)],
                    transfer_newer_playhead=True)

    def invoke(self, *, apply=False, extra=()):
        self.sequence += 1
        report_path = self.root / f"report-{self.sequence}.json"
        self.plan_path.write_text(json.dumps(self.plan))
        args = ["--db", str(self.db), "--plan", str(self.plan_path), "--report", str(report_path), *extra]
        if apply:
            args.extend(["--apply", "--i-have-stopped-video-library"])
        with patch.object(repair, "MediaLibrary", return_value=self.library), \
                patch.object(repair, "ensure_unused", return_value={"test": "quiescent"}), \
                contextlib.redirect_stdout(io.StringIO()):
            code = repair.main(args)
        return code, json.loads(report_path.read_text())

    def snapshot(self):
        return repair.snapshot(self.catalog.connection)

    def test_default_dry_run_never_changes_file_and_reports_transfer(self):
        before = self.db.read_bytes()
        code, report = self.invoke()
        self.assertEqual(code, 0, report.get("error"))
        self.assertEqual(self.db.read_bytes(), before)
        self.assertTrue(report["rolled_back"])
        self.assertEqual(report["scan_before"]["conflict_count"], 1)
        self.assertEqual(report["scan_after"]["conflict_count"], 0)
        self.assertEqual(report["pairs"][0]["playhead"]["after"]["position"], 75)
        self.assertFalse(report["committed"])

    def test_apply_preserves_history_and_second_apply_is_noop(self):
        source = self.catalog.get_asset_state(self.source)
        sessions = self.catalog.connection.execute("SELECT COUNT(*) FROM video_v2_playback_sessions").fetchone()[0]
        code, report = self.invoke(apply=True)
        self.assertEqual(code, 0, report.get("error"))
        self.assertEqual(self.catalog.get_asset_state(self.source), source)
        self.assertEqual(self.catalog.get_asset_state(self.target)["position"], 75)
        self.assertEqual(self.catalog.resolve_path(str(self.media)), self.target)
        self.assertEqual(self.catalog.resolve_legacy_key(self.item.key), self.target)
        self.assertEqual(report["row_counts"]["video_v2_playback_sessions"]["after"], sessions)
        self.assertEqual(report["row_counts"]["video_v2_playback_events"]["delta"], 1)
        first = self.snapshot()
        code, second = self.invoke(apply=True)
        self.assertEqual(code, 0, second.get("error"))
        self.assertEqual(second["sqlite_changes"], 0)
        self.assertEqual(second["pair_counts"], {"already_repaired": 1})
        self.assertEqual(self.snapshot(), first)

    def test_changed_stat_skips_pair_and_rolls_back_entire_transaction(self):
        self.media.write_bytes(b"replacement encode")
        before = self.snapshot()
        code, report = self.invoke(apply=True)
        self.assertEqual(code, 1)
        self.assertEqual(report["pair_counts"], {"skipped": 1})
        self.assertIn("stat changed", report["pairs"][0]["reason"])
        self.assertEqual(self.snapshot(), before)

    def test_changed_playback_is_not_overwritten(self):
        self.catalog.clear_playhead(self.source)
        before = self.snapshot()
        code, report = self.invoke(apply=True)
        self.assertEqual(code, 1)
        self.assertIn("source playback changed", report["pairs"][0]["reason"])
        self.assertEqual(self.snapshot(), before)

    def test_changed_work_is_not_merged(self):
        self.catalog.bind_work(self.source, self.catalog.create_work("movie", title="Other"))
        before = self.snapshot()
        code, report = self.invoke(apply=True)
        self.assertEqual(code, 1)
        self.assertEqual(report["pairs"][0]["status"], "skipped")
        self.assertEqual(self.snapshot(), before)

    def test_newer_target_playback_is_not_overwritten(self):
        self.catalog.clear_playhead(self.target)
        before = self.snapshot()
        code, report = self.invoke(apply=True)
        self.assertEqual(code, 1)
        self.assertIn("playback evidence changed", report["pairs"][0]["reason"])
        self.assertEqual(self.snapshot(), before)

    def test_unrelated_encode_binding_is_retained(self):
        other = self.catalog.create_asset(work_id=self.work, asset_kind="torrent")
        self.catalog.bind_legacy_key(other, self.item.key, replace=True)
        self.pair["legacy_bindings_to_review"] = repair.rows(self.catalog.connection, "video_v2_legacy_keys")
        self.pair["expected_legacy_assets"] = {other: self.catalog.lookup_asset(other)}
        code, report = self.invoke(apply=True)
        self.assertEqual(code, 0, report.get("error"))
        self.assertEqual(self.catalog.resolve_legacy_key(self.item.key), other)
        self.assertEqual(report["pairs"][0]["legacy"][0]["skipped"], "unrelated exact encode retained")

    def test_older_device_same_file_binding_is_rebound_without_merging_asset(self):
        st = repair.stat_value(self.media)
        other = self.catalog.resolve_or_create_provisional_file(
            str(self.root / "old-mount.mkv"), device_id="previous-device",
            inode=st["inode"], size=st["size"], mtime_ns=st["mtime_ns"])
        self.catalog.bind_work(other, self.work)
        self.catalog.record_location(other, str(self.media))
        self.catalog.record_location(self.source, str(self.media))
        self.catalog.bind_legacy_key(other, self.item.key, replace=True)
        self.pair = self.make_pair()
        self.plan["same_file_pairs"] = [self.pair]
        self.pair["expected_legacy_assets"] = {other: self.catalog.lookup_asset(other)}
        self.pair["expected_legacy_identities"] = {other: repair.rows(
            self.catalog.connection, "video_v2_file_identities", "WHERE asset_id=?", (other,))}
        self.pair["expected_legacy_locations"] = {other: self.catalog.list_locations(other)}
        prior = self.catalog.lookup_asset(other)
        code, report = self.invoke(apply=True)
        self.assertEqual(code, 0, report.get("error"))
        self.assertTrue(report["pairs"][0]["legacy"][0]["rebound"])
        self.assertEqual(self.catalog.lookup_asset(other), prior)
        self.assertIsNone(self.catalog.get_asset_state(other))

    def test_changed_legacy_binding_skips_entire_pair(self):
        other = self.catalog.create_asset(work_id=self.work)
        self.catalog.bind_legacy_key(other, self.item.key, replace=True)
        before = self.snapshot()
        code, report = self.invoke(apply=True)
        self.assertEqual(code, 1)
        self.assertIn("legacy binding evidence changed", report["pairs"][0]["reason"])
        self.assertEqual(self.snapshot(), before)

    def test_unexpected_api_failure_rolls_back_earlier_transfer(self):
        before = self.snapshot()
        with patch.object(MediaAssetCatalog, "record_alias", side_effect=RuntimeError("injected")):
            code, report = self.invoke(apply=True)
        self.assertEqual(code, 1)
        self.assertIn("injected", report["error"])
        self.assertTrue(report["rolled_back"])
        self.assertEqual(self.snapshot(), before)

    def test_postcheck_failure_rolls_back_everything(self):
        before = self.snapshot()
        with patch.object(repair, "postcheck", side_effect=RuntimeError("postcheck")):
            code, report = self.invoke(apply=True)
        self.assertEqual(code, 1)
        self.assertEqual(self.snapshot(), before)

    def add_inode_exception(self):
        db = self.catalog.connection
        # Reuse the physical fixture as Silo, replacing the same-file plan.
        old_work = self.catalog.create_work("episode", title="Barbarians")
        self.catalog.bind_work(self.target, old_work)
        old_path = self.root / "missing-barbarians.mkv"
        self.catalog.record_location(self.target, str(old_path))
        with self.catalog.transaction():
            db.execute("UPDATE video_v2_file_identities SET size=1, mtime_ns=2 WHERE asset_id=?", (self.target,))
        old = repair.rows(db, "video_v2_file_identities", "WHERE asset_id=?", (self.target,))
        exception = dict(location=self.catalog.list_locations(self.source, active_only=True)[0],
                         stat=repair.stat_value(self.media), inode_identities=old, path_identities=[],
                         assets=[self.catalog.lookup_asset(a) for a in (self.source, self.target)],
                         asset_locations=self.catalog.list_locations(self.source, active_only=True),
                         playback_states=[self.catalog.get_asset_state(a) for a in (self.source, self.target)],
                         retired_asset_locations=self.catalog.list_locations(self.target))
        # The real old physical path is absent, not Silo's present path.
        with self.catalog.transaction():
            db.execute("UPDATE video_v2_locations SET path=? WHERE asset_id=?", (str(old_path), self.target))
        exception["retired_asset_locations"] = self.catalog.list_locations(self.target)
        exception["location"] = next(loc for loc in exception["asset_locations"] if loc["path"] == str(self.media))
        self.plan["same_file_pairs"] = []
        self.plan["reused_inode_exceptions"] = [exception]
        return old_path

    def test_inode_exception_only_invalidates_identity_without_playback_transfer(self):
        self.add_inode_exception()
        before = [self.catalog.get_asset_state(a) for a in (self.source, self.target)]
        code, report = self.invoke(apply=True)
        self.assertEqual(code, 0, report.get("error"))
        self.assertFalse(report["inode_exceptions"][0]["playback_transferred"])
        self.assertEqual(report["row_counts"]["video_v2_playback_events"]["delta"], 0)
        self.assertEqual([self.catalog.get_asset_state(a) for a in (self.source, self.target)], before)
        code, second = self.invoke(apply=True)
        self.assertEqual(code, 0, second.get("error"))
        self.assertEqual(second["sqlite_changes"], 0)

    def test_inode_exception_refuses_if_old_file_reappears(self):
        old_path = self.add_inode_exception()
        old_path.write_bytes(b"returned file")
        before = self.snapshot()
        code, report = self.invoke(apply=True)
        self.assertEqual(code, 1)
        self.assertIn("Barbarians path still exists", report["inode_exceptions"][0]["reason"])
        self.assertEqual(self.snapshot(), before)

    def test_apply_requires_explicit_stopped_acknowledgement(self):
        self.plan_path.write_text(json.dumps(self.plan))
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            repair.main(["--db", str(self.db), "--plan", str(self.plan_path), "--report",
                         str(self.root / "no-report.json"), "--apply"])
        self.assertFalse((self.root / "no-report.json").exists())

    def test_holder_check_rejects_active_holder_or_diagnostic_errors(self):
        import subprocess
        for result in (subprocess.CompletedProcess([], 0, "1234", ""),
                       subprocess.CompletedProcess([], 1, "", "permission denied")):
            with patch.object(repair.shutil, "which", return_value="/usr/bin/fuser"), \
                    patch.object(repair.subprocess, "run", return_value=result), \
                    self.assertRaisesRegex(RuntimeError, "in use or holder check inconclusive"):
                repair.ensure_unused(self.db)

    def test_no_holder_tools_requires_confirmed_inactive_service(self):
        import subprocess
        with patch.object(repair.shutil, "which", side_effect=[None, None, "/usr/bin/systemctl"]), \
                patch.object(repair.subprocess, "run", return_value=subprocess.CompletedProcess([], 3, "inactive\n", "")):
            self.assertEqual(repair.ensure_unused(self.db)["result"], "inactive")


if __name__ == "__main__":
    unittest.main()
