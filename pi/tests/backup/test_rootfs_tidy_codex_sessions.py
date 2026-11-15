"""rootfs_tidy_codex_sessions.py through its CLI, against fixture homes.

A race between selection and unlink is injected through
ROOTFS_TIDY_CODEX_TEST_HOOK, which the module runs at exactly that point.
"""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

MODULE = Path(__file__).resolve().parents[2] / "scripts" / "rootfs_tidy_codex_sessions.py"
OLD = 1577836800  # 2020-01-01


class CodexSessionSweepTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(os.path.realpath(tempfile.mkdtemp()))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.home = self.root / "home"
        self.codex = self.home / ".codex"
        self.sessions = self.codex / "archived_sessions"
        self.sessions.mkdir(parents=True)

    def write(self, rel, content, old=True):
        path = self.codex / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        if old:
            os.utime(path, (OLD, OLD))
        return path

    def hook(self, body):
        path = self.root / "hook.sh"
        path.write_text("#!/bin/bash\nset -eu\nrel=$1\n" + body + "\n")
        path.chmod(0o755)
        return str(path)

    def sweep(self, *args, hook=None):
        environment = {"PATH": "/usr/bin:/bin"}
        if hook:
            environment["ROOTFS_TIDY_CODEX_TEST_HOOK"] = hook
        return subprocess.run(
            [sys.executable, "-I", str(MODULE), *args, "--home", str(self.home)],
            capture_output=True, text=True, env=environment, timeout=60)

    def assert_status(self, result, status):
        self.assertEqual(result.returncode, status, result.stdout + result.stderr)

    def test_deletes_old_files_and_counts_their_bytes(self):
        old = self.write("archived_sessions/old.jsonl", "12345")
        nested = self.write("archived_sessions/2026/01/nested.jsonl", "abc")
        recent = self.write("archived_sessions/new.jsonl", "new", old=False)
        live = self.write("sessions/live.jsonl", "live")

        result = self.sweep("--apply")

        self.assert_status(result, 0)
        self.assertFalse(old.exists())
        self.assertFalse(nested.exists())
        self.assertTrue(recent.exists())
        self.assertTrue(live.exists(), "live sessions/ must never be touched")
        self.assertIn("deleted archived_sessions/old.jsonl (5 bytes)", result.stdout)
        self.assertIn("2 file(s) older than 30 days, deleted 2, 8 bytes", result.stdout)

    def test_dry_run_deletes_nothing_and_reports_what_it_would(self):
        old = self.write("archived_sessions/old.jsonl", "12345")

        result = self.sweep()

        self.assert_status(result, 0)
        self.assertTrue(old.exists())
        self.assertIn("would delete archived_sessions/old.jsonl (5 bytes)", result.stdout)
        self.assertIn("1 file(s) older than 30 days, would delete 1, 5 bytes", result.stdout)

    def test_symlinks_and_their_targets_are_left_alone(self):
        target = self.write("sessions/live.jsonl", "live")
        link = self.sessions / "link.jsonl"
        link.symlink_to(target)
        os.utime(link, (OLD, OLD), follow_symlinks=False)
        outside = self.root / "outside"
        outside.mkdir()
        victim = outside / "victim.jsonl"
        victim.write_text("x")
        os.utime(victim, (OLD, OLD))
        (self.sessions / "linkdir").symlink_to(outside)

        result = self.sweep("--apply")

        self.assert_status(result, 0)
        self.assertTrue(link.is_symlink())
        self.assertTrue(target.exists())
        self.assertTrue(victim.exists(), "followed a symlinked directory")
        self.assertIn("skipped symlink archived_sessions/link.jsonl", result.stdout)

    def test_directories_and_other_non_regular_files_are_not_deleted(self):
        subdir = self.sessions / "empty-old"
        subdir.mkdir()
        os.utime(subdir, (OLD, OLD))
        fifo = self.sessions / "pipe"
        os.mkfifo(fifo)
        os.utime(fifo, (OLD, OLD))

        result = self.sweep("--apply")

        self.assert_status(result, 0)
        self.assertTrue(subdir.is_dir())
        self.assertTrue(fifo.exists())
        self.assertIn("skipped archived_sessions/pipe (not a regular file)", result.stdout)

    def test_a_file_written_after_selection_is_kept(self):
        path = self.write("archived_sessions/racy.jsonl", "old")
        hook = self.hook(f'echo more >> "{self.codex}/$rel"; touch -t 202001010000 "{self.codex}/$rel"')

        result = self.sweep("--apply", hook=hook)

        self.assert_status(result, 0)
        self.assertEqual(path.read_text(), "oldmore\n")
        self.assertIn("kept archived_sessions/racy.jsonl: it changed after it was selected",
                      result.stdout)
        self.assertIn("deleted 0, 0 bytes", result.stdout)

    def test_a_file_replaced_after_selection_is_kept(self):
        self.write("archived_sessions/swap.jsonl", "old")
        # Same size and an old mtime: only the inode tells it apart.
        hook = self.hook(f'p="{self.codex}/$rel"; printf new > "$p.n"; touch -t 202001010000 "$p.n";'
                         f' mv "$p.n" "$p"')

        result = self.sweep("--apply", hook=hook)

        self.assert_status(result, 0)
        self.assertEqual((self.sessions / "swap.jsonl").read_text(), "new")
        self.assertIn("it changed after it was selected", result.stdout)

    def test_a_file_touched_recent_after_selection_is_kept(self):
        path = self.write("archived_sessions/touched.jsonl", "old")
        hook = self.hook(f'touch "{self.codex}/$rel"')

        result = self.sweep("--apply", hook=hook)

        self.assert_status(result, 0)
        self.assertTrue(path.exists())

    def test_a_symlinked_codex_directory_is_skipped(self):
        shutil.rmtree(self.codex)
        real = self.root / "real-codex"
        victim = real / "archived_sessions" / "victim.jsonl"
        victim.parent.mkdir(parents=True)
        victim.write_text("x")
        os.utime(victim, (OLD, OLD))
        self.codex.symlink_to(real)

        result = self.sweep("--apply")

        self.assert_status(result, 0)
        self.assertTrue(victim.exists())
        self.assertIn("skipped", result.stdout)

    def test_missing_archive_directory_is_a_quiet_skip(self):
        shutil.rmtree(self.sessions)

        result = self.sweep("--apply")

        self.assert_status(result, 0)
        self.assertIn("does not exist", result.stdout)

    @unittest.skipIf(os.geteuid() == 0, "root reads a mode-000 directory anyway")
    def test_an_unreadable_directory_fails_the_run(self):
        locked = self.sessions / "locked"
        locked.mkdir()
        self.write("archived_sessions/old.jsonl", "x")
        locked.chmod(0)
        self.addCleanup(locked.chmod, 0o700)

        result = self.sweep("--apply")

        self.assert_status(result, 1)
        self.assertFalse((self.sessions / "old.jsonl").exists())


if __name__ == "__main__":
    unittest.main()
