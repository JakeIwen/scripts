"""rootfs_tidy_archive.py through its CLI, against fixture trees and a fake volume.

Races are injected through ROOTFS_TIDY_ARCHIVE_TEST_HOOK, which the module runs
at fixed points of each file's move.
"""
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from .rootfs_archive_fixtures import write_volume

MODULE = Path(__file__).resolve().parents[2] / "scripts" / "rootfs_tidy_archive.py"
OLD = 1577836800  # 2020-01-01


class ArchiveFixture(unittest.TestCase):
    def setUp(self):
        self.root = Path(os.path.realpath(tempfile.mkdtemp()))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.home = self.root / "home"
        self.codex = self.home / ".codex"
        self.mount = self.root / "mnt"
        self.mount.mkdir()
        self.bin = self.root / "bin"
        self.findmnt, self.mountinfo = write_volume(str(self.bin), str(self.mount))
        self.archive = self.mount / "vanpi-root-archive" / "codex"
        self.sessions = self.codex / "archived_sessions"

    def write(self, rel, content, old=True):
        path = self.codex / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        if old:
            os.utime(path, (OLD, OLD))
        return path

    def hook(self, body):
        path = self.root / "hook.sh"
        path.write_text("#!/bin/bash\nset -eu\nstage=$1 rel=$2\n" + body + "\n")
        path.chmod(0o755)
        return str(path)

    def run_archive(self, *args, hook=None, env=None):
        environment = {
            "PATH": "/usr/bin:/bin",
            "ROOTFS_TIDY_ARCHIVE_MOUNT": str(self.mount),
            "ROOTFS_TIDY_FINDMNT": self.findmnt,
            "ROOTFS_TIDY_MOUNTINFO": self.mountinfo,
        }
        if hook:
            environment["ROOTFS_TIDY_ARCHIVE_TEST_HOOK"] = hook
        environment.update(env or {})
        return subprocess.run(
            [sys.executable, "-I", str(MODULE), *args, "--home", str(self.home)],
            capture_output=True, text=True, env=environment, timeout=120)

    def assert_status(self, result, status):
        self.assertEqual(result.returncode, status, result.stdout + result.stderr)

    def temps(self):
        tmp = self.mount / "vanpi-root-archive" / ".tidy-tmp"
        return sorted(os.listdir(tmp)) if tmp.exists() else []


class ArchiveMoveTests(ArchiveFixture):
    def test_moves_old_files_and_leaves_recent_live_and_symlinked_ones(self):
        self.write("archived_sessions/old.jsonl", "old")
        self.write("archived_sessions/sub/nested.jsonl", "nested")
        self.write("migration-backups/b1/state.sqlite", "mig")
        self.write("archived_sessions/new.jsonl", "new", old=False)
        live = self.write("sessions/live.jsonl", "live")
        (self.sessions / "link.jsonl").symlink_to("../sessions/live.jsonl")

        result = self.run_archive("--apply")

        self.assert_status(result, 0)
        self.assertIn("3 file(s) older than 30 days, 3 moved", result.stdout)
        self.assertIn("skipped symlink archived_sessions/link.jsonl", result.stdout)
        self.assertEqual((self.archive / "archived_sessions/old.jsonl").read_text(), "old")
        self.assertEqual((self.archive / "archived_sessions/sub/nested.jsonl").read_text(), "nested")
        self.assertEqual((self.archive / "migration-backups/b1/state.sqlite").read_text(), "mig")
        self.assertEqual(int((self.archive / "archived_sessions/old.jsonl").stat().st_mtime), OLD)
        self.assertFalse((self.sessions / "old.jsonl").exists())
        self.assertFalse((self.sessions / "sub/nested.jsonl").exists())
        self.assertTrue((self.sessions / "new.jsonl").exists())
        self.assertTrue((self.sessions / "link.jsonl").is_symlink())
        self.assertEqual(live.read_text(), "live")
        self.assertFalse((self.archive / "sessions").exists())
        self.assertEqual(self.temps(), [])

    def test_dry_run_lists_and_writes_nothing(self):
        self.write("archived_sessions/old.jsonl", "old")
        before = sorted(str(p) for p in self.root.rglob("*"))

        result = self.run_archive()

        self.assert_status(result, 0)
        self.assertIn("would move archived_sessions/old.jsonl", result.stdout)
        self.assertEqual(sorted(str(p) for p in self.root.rglob("*")), before)

    def test_names_exfat_refuses_are_encoded_and_still_verify(self):
        names = {"new\nline.jsonl": "new%0Aline.jsonl", "back\\slash.jsonl": "back%5Cslash.jsonl",
                 "per%cent": "per%25cent", "colon:x": "colon%3Ax", "dot.": "dot%2E"}
        for name in names:
            self.write(f"archived_sessions/{name}", name)

        result = self.run_archive("--apply")

        self.assert_status(result, 0)
        for name, encoded in names.items():
            self.assertEqual((self.archive / "archived_sessions" / encoded).read_text(), name)
            self.assertFalse((self.sessions / name).exists(), name)

    def test_existing_destination_is_never_clobbered(self):
        source = self.write("archived_sessions/old.jsonl", "old")
        (self.archive / "archived_sessions").mkdir(parents=True)
        (self.archive / "archived_sessions/old.jsonl").write_text("other")

        result = self.run_archive("--apply")

        self.assert_status(result, 1)
        self.assertIn("already holds a different file", result.stderr)
        self.assertEqual((self.archive / "archived_sessions/old.jsonl").read_text(), "other")
        self.assertEqual(source.read_text(), "old")

    def test_identical_destination_from_an_interrupted_run_completes_the_move(self):
        source = self.write("archived_sessions/old.jsonl", "old")
        (self.archive / "archived_sessions").mkdir(parents=True)
        (self.archive / "archived_sessions/old.jsonl").write_text("old")

        self.assert_status(self.run_archive("--apply"), 0)
        self.assertFalse(source.exists())

    def test_leftover_quarantine_is_put_back_under_its_name(self):
        self.write("archived_sessions/.tidy-moving-old.jsonl", "old")

        result = self.run_archive("--apply")

        self.assert_status(result, 0)
        self.assertEqual((self.sessions / "old.jsonl").read_text(), "old")
        self.assertFalse((self.sessions / ".tidy-moving-old.jsonl").exists())

    def test_symlinked_subtree_or_home_is_never_followed(self):
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "old.jsonl").write_text("o")
        os.utime(outside / "old.jsonl", (OLD, OLD))
        self.codex.mkdir(parents=True)
        self.sessions.symlink_to(outside)

        result = self.run_archive("--apply")

        self.assert_status(result, 0)
        self.assertIn("skipped archived_sessions", result.stdout)
        self.assertTrue((outside / "old.jsonl").exists())
        self.assertFalse((self.archive / "archived_sessions").exists())

        linked_home = self.root / "linked-home"
        linked_home.symlink_to(self.home)
        self.home = linked_home
        result = self.run_archive("--apply")
        self.assert_status(result, 0)
        self.assertIn("skipped (", result.stdout)


class ArchiveRaceTests(ArchiveFixture):
    def test_parent_swapped_for_a_symlink_to_sessions_never_reaches_sessions(self):
        for stage in ("before-quarantine", "after-quarantine", "before-unlink"):
            with self.subTest(stage=stage):
                shutil.rmtree(self.home, ignore_errors=True)
                shutil.rmtree(self.mount / "vanpi-root-archive", ignore_errors=True)
                self.write("archived_sessions/sub/victim.jsonl", "archived")
                live = self.write("sessions/victim.jsonl", "LIVE")
                hook = self.hook(
                    f'[[ $stage == {stage} ]] || exit 0\n'
                    f'cd "{self.sessions}"; mv sub sub.moved; ln -s ../sessions sub')

                result = self.run_archive("--apply", hook=hook)

                self.assert_status(result, 0)
                self.assertEqual(live.read_text(), "LIVE")
                self.assertEqual(
                    (self.archive / "archived_sessions/sub/victim.jsonl").read_text(), "archived")
                self.assertEqual(os.listdir(self.sessions / "sub.moved"), [])

    def test_append_after_the_copy_keeps_the_source(self):
        source = self.write("archived_sessions/old.jsonl", "old\n")
        hook = self.hook(f'[[ $stage == after-copy ]] && echo more >> "{source}"; exit 0')

        result = self.run_archive("--apply", hook=hook)

        self.assert_status(result, 1)
        self.assertIn("changed while being copied", result.stderr)
        self.assertEqual(source.read_text(), "old\nmore\n")
        self.assertFalse((self.archive / "archived_sessions/old.jsonl").exists())
        self.assertEqual(self.temps(), [])

    def test_append_through_an_open_file_after_quarantine_is_put_back(self):
        source = self.write("archived_sessions/old.jsonl", "old\n")
        held = self.sessions / ".tidy-moving-old.jsonl"
        hook = self.hook(f'[[ $stage == after-quarantine ]] && echo more >> "{held}"; exit 0')

        result = self.run_archive("--apply", hook=hook)

        self.assert_status(result, 1)
        self.assertIn("changed after it was copied", result.stderr)
        self.assertEqual(source.read_text(), "old\nmore\n")
        self.assertFalse(held.exists())
        # The stale copy is withdrawn, so next week's run archives the new content.
        self.assertFalse((self.archive / "archived_sessions/old.jsonl").exists())

    def test_writer_reopening_by_name_after_quarantine_creates_a_surviving_file(self):
        source = self.write("archived_sessions/old.jsonl", "old\n")
        hook = self.hook(f'[[ $stage == before-unlink ]] && echo more >> "{source}"; exit 0')

        result = self.run_archive("--apply", hook=hook)

        self.assert_status(result, 0)
        self.assertEqual(source.read_text(), "more\n")
        self.assertEqual((self.archive / "archived_sessions/old.jsonl").read_text(), "old\n")

    def test_destination_appearing_during_the_copy_is_not_replaced(self):
        source = self.write("archived_sessions/old.jsonl", "old")
        dest = self.archive / "archived_sessions/old.jsonl"
        hook = self.hook(f'[[ $stage == after-copy ]] && echo racer > "{dest}"; exit 0')

        result = self.run_archive("--apply", hook=hook)

        self.assert_status(result, 1)
        self.assertEqual(dest.read_text(), "racer\n")
        self.assertEqual(source.read_text(), "old")
        self.assertEqual(self.temps(), [])

    def test_failing_hook_after_quarantine_restores_the_name(self):
        source = self.write("archived_sessions/old.jsonl", "old")
        hook = self.hook('[[ $stage == after-quarantine ]] && exit 3; exit 0')

        self.assert_status(self.run_archive("--apply", hook=hook), 1)
        self.assertEqual(source.read_text(), "old")


class ArchiveVolumeTests(ArchiveFixture):
    def test_fake_findmnt_reproduces_the_live_padded_format(self):
        aligned = subprocess.run(
            [self.findmnt, "-n", "-o", "TARGET,FSTYPE,UUID", "--mountpoint", str(self.mount)],
            capture_output=True, text=True, check=True).stdout
        self.assertEqual(aligned, f"{self.mount} exfat  122C-A219\n")
        self.write("archived_sessions/old.jsonl", "old")

        self.assert_status(self.run_archive("--apply"), 0)
        self.assertTrue((self.archive / "archived_sessions/old.jsonl").exists())

    def test_absent_or_unexpected_volume_skips_and_writes_nothing(self):
        cases = {
            "other uuid": {"uuid": "OTHER-UUID"},
            "other fstype": {"fstype": "vfat"},
            "other device": {"device": "250:99"},
        }
        source = self.write("archived_sessions/old.jsonl", "old")
        for label, volume in cases.items():
            with self.subTest(label):
                findmnt, mountinfo = write_volume(str(self.root / label), str(self.mount), **volume)
                result = self.run_archive("--apply", env={
                    "ROOTFS_TIDY_FINDMNT": findmnt, "ROOTFS_TIDY_MOUNTINFO": mountinfo})
                self.assert_volume_refused(result, source)
        for label, env in {
            "findmnt fails": {"ROOTFS_TIDY_FINDMNT": "/usr/bin/false"},
            "findmnt missing": {"ROOTFS_TIDY_FINDMNT": str(self.root / "none")},
            "no mountinfo": {"ROOTFS_TIDY_MOUNTINFO": str(self.root / "none")},
            "unmounted": {"ROOTFS_TIDY_MOUNTINFO": os.devnull},
            "mount path missing": {"ROOTFS_TIDY_ARCHIVE_MOUNT": str(self.root / "gone")},
        }.items():
            with self.subTest(label):
                self.assert_volume_refused(self.run_archive("--apply", env=env), source)

    def test_symlinked_mount_point_is_refused(self):
        source = self.write("archived_sessions/old.jsonl", "old")
        link = self.root / "mnt-link"
        link.symlink_to(self.mount)
        findmnt, mountinfo = write_volume(str(self.root / "link"), str(link),
                                          device=f"{os.major(self.mount.stat().st_dev)}"
                                                 f":{os.minor(self.mount.stat().st_dev)}")
        result = self.run_archive("--apply", env={
            "ROOTFS_TIDY_ARCHIVE_MOUNT": str(link), "ROOTFS_TIDY_FINDMNT": findmnt,
            "ROOTFS_TIDY_MOUNTINFO": mountinfo})
        self.assert_volume_refused(result, source)

    def assert_volume_refused(self, result, source):
        self.assert_status(result, 0)
        self.assertIn("Codex archive: skipped", result.stdout)
        self.assertEqual(source.read_text(), "old")
        self.assertEqual(os.listdir(self.mount), [])


@unittest.skipUnless(sys.platform.startswith("linux") and shutil.which("findmnt"),
                     "real findmnt and /proc/self/mountinfo exist only on Linux")
class RealFindmntTests(unittest.TestCase):
    def test_raw_output_for_root_matches_mountinfo(self):
        spec = importlib.util.spec_from_file_location("rootfs_tidy_archive", MODULE)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        found = module.findmnt_entry(shutil.which("findmnt"), "/")
        info = module.mountinfo_entry("/proc/self/mountinfo", "/")
        self.assertIsNotNone(found)
        self.assertEqual(found[:2], info)


if __name__ == "__main__":
    unittest.main()
