"""rootfs_tidy_sweeps.py through its CLI, against fixture homes.

Races are injected through ROOTFS_TIDY_TEST_HOOK, which the module runs at the
last point before each delete. Mounts are described by a fixture mountinfo
(ROOTFS_TIDY_MOUNTINFO), since the suite cannot mount filesystems.
"""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

MODULE = Path(__file__).resolve().parents[2] / "scripts" / "rootfs_tidy_sweeps.py"
OLD = 1577836800  # 2020-01-01


def age(path):
    os.utime(path, (OLD, OLD), follow_symlinks=False)


class SweepFixture(unittest.TestCase):
    def setUp(self):
        self.root = Path(os.path.realpath(tempfile.mkdtemp()))
        self.addCleanup(self.cleanup)
        self.home = self.root / "home"
        self.codex = self.home / ".codex"
        self.sessions = self.codex / "archived_sessions"
        self.sessions.mkdir(parents=True)
        self.tmp = self.home / "dev" / "obd-things" / "tmp"
        self.tmp.mkdir(parents=True)
        self.mountinfo = self.root / "mountinfo"
        self.mountinfo.write_text("22 1 179:2 / / rw,noatime shared:1 - ext4 /dev/root rw\n")

    def cleanup(self):
        subprocess.run(["chmod", "-R", "u+rwx", str(self.root)], check=False)
        shutil.rmtree(self.root, ignore_errors=True)

    def write(self, path, content="x", old=True):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        if old:
            age(path)
        return path

    def mount_at(self, path):
        escaped = str(path).replace("\\", "\\134").replace(" ", "\\040")
        with self.mountinfo.open("a") as handle:
            handle.write(f"404 26 0:50 / {escaped} rw,relatime - tmpfs tmpfs rw\n")

    def hook(self, body):
        path = self.root / "hook.sh"
        path.write_text("#!/bin/bash\nset -eu\nstage=$1 rel=$2\n" + body + "\n")
        path.chmod(0o755)
        return str(path)

    def sweep(self, *args, hook=None):
        environment = {"PATH": "/usr/bin:/bin", "ROOTFS_TIDY_MOUNTINFO": str(self.mountinfo)}
        if hook:
            environment["ROOTFS_TIDY_TEST_HOOK"] = hook
        return subprocess.run(
            [sys.executable, "-I", str(MODULE), *args, "--home", str(self.home)],
            capture_output=True, text=True, env=environment, timeout=60)

    def assert_status(self, result, status):
        self.assertEqual(result.returncode, status, result.stdout + result.stderr)


class CodexSessionTests(SweepFixture):
    def test_deletes_old_files_and_counts_their_bytes(self):
        old = self.write(self.sessions / "old.jsonl", "12345")
        nested = self.write(self.sessions / "2026" / "01" / "nested.jsonl", "abc")
        recent = self.write(self.sessions / "new.jsonl", "new", old=False)
        live = self.write(self.codex / "sessions" / "live.jsonl")
        backup = self.write(self.codex / "migration-backups" / "b1" / "state.sqlite")

        result = self.sweep("--apply")

        self.assert_status(result, 0)
        self.assertFalse(old.exists())
        self.assertFalse(nested.exists())
        self.assertTrue(recent.exists())
        self.assertTrue(live.exists(), "live sessions/ must never be touched")
        self.assertTrue(backup.exists(), "migration-backups/ is not swept")
        self.assertIn("deleted archived_sessions/old.jsonl (5 bytes)", result.stdout)
        self.assertIn("Codex sessions: 2 file(s) older than 30 days, deleted 2, 8 bytes",
                      result.stdout)

    def test_dry_run_deletes_nothing_and_reports_what_it_would(self):
        old = self.write(self.sessions / "old.jsonl", "12345")

        result = self.sweep()

        self.assert_status(result, 0)
        self.assertTrue(old.exists())
        self.assertIn("would delete archived_sessions/old.jsonl (5 bytes)", result.stdout)
        self.assertIn("1 file(s) older than 30 days, would delete 1, 5 bytes", result.stdout)

    def test_symlinks_and_their_targets_are_left_alone(self):
        target = self.write(self.codex / "sessions" / "live.jsonl")
        link = self.sessions / "link.jsonl"
        link.symlink_to(target)
        age(link)
        victim = self.write(self.root / "outside" / "victim.jsonl")
        (self.sessions / "linkdir").symlink_to(victim.parent)

        result = self.sweep("--apply")

        self.assert_status(result, 0)
        self.assertTrue(link.is_symlink())
        self.assertTrue(target.exists())
        self.assertTrue(victim.exists(), "followed a symlinked directory")
        self.assertIn("skipped archived_sessions/link.jsonl (symlink)", result.stdout)

    def test_directories_and_other_non_regular_files_are_not_deleted(self):
        subdir = self.sessions / "empty-old"
        subdir.mkdir()
        age(subdir)
        fifo = self.sessions / "pipe"
        os.mkfifo(fifo)
        age(fifo)

        result = self.sweep("--apply")

        self.assert_status(result, 0)
        self.assertTrue(subdir.is_dir())
        self.assertTrue(fifo.exists())
        self.assertIn("skipped archived_sessions/pipe (not a regular file)", result.stdout)

    def test_a_file_written_after_selection_is_kept(self):
        path = self.write(self.sessions / "racy.jsonl", "old")
        hook = self.hook(f'echo more >> "{self.codex}/$rel"; touch -t 202001010000 "{self.codex}/$rel"')

        result = self.sweep("--apply", hook=hook)

        self.assert_status(result, 0)
        self.assertEqual(path.read_text(), "oldmore\n")
        self.assertIn("kept archived_sessions/racy.jsonl: it changed after it was selected",
                      result.stdout)
        self.assertIn("deleted 0, 0 bytes", result.stdout)

    def test_a_file_replaced_after_selection_is_kept(self):
        self.write(self.sessions / "swap.jsonl", "old")
        # Same size and an old mtime: only the inode tells it apart.
        hook = self.hook(f'p="{self.codex}/$rel"; printf new > "$p.n"; touch -t 202001010000 "$p.n";'
                         f' mv "$p.n" "$p"')

        result = self.sweep("--apply", hook=hook)

        self.assert_status(result, 0)
        self.assertEqual((self.sessions / "swap.jsonl").read_text(), "new")
        self.assertIn("it changed after it was selected", result.stdout)

    def test_a_symlinked_codex_directory_is_skipped(self):
        shutil.rmtree(self.codex)
        victim = self.write(self.root / "real-codex" / "archived_sessions" / "victim.jsonl")
        self.codex.symlink_to(self.root / "real-codex")

        result = self.sweep("--apply")

        self.assert_status(result, 0)
        self.assertTrue(victim.exists())
        self.assertIn("Codex sessions: skipped", result.stdout)

    def test_a_mount_inside_the_archive_is_not_entered(self):
        inside = self.write(self.sessions / "mnt" / "old.jsonl")
        self.mount_at(self.sessions / "mnt")

        result = self.sweep("--apply")

        self.assert_status(result, 0)
        self.assertTrue(inside.exists())
        self.assertIn("another filesystem is mounted there", result.stdout)

    @unittest.skipIf(os.geteuid() == 0, "root opens a mode-000 directory anyway")
    def test_an_unreadable_archive_directory_fails_the_run(self):
        self.sessions.chmod(0)

        result = self.sweep("--apply")

        self.assert_status(result, 1)
        self.assertIn("Codex sessions: failed: cannot open", result.stderr)


class ScratchTests(SweepFixture):
    def scratch(self, name, old=True):
        path = self.write(self.tmp / name / "inner" / "f", old=old)
        if old:
            age(path.parent)
            age(path.parent.parent)
        return path.parent.parent

    def test_only_idle_suite_and_playwright_dash_directories_are_removed(self):
        suite_old = self.scratch("suite-old")
        playwright_old = self.scratch("playwright-old")
        kept = [self.scratch(name) for name in
                ("playwright", "playwrightNotes", "playwright_recordings", "compute")]
        recent = self.scratch("suite-new", old=False)
        busy = self.scratch("suite-busy")
        self.write(busy / "inner" / "fresh", old=False)

        result = self.sweep("--apply")

        self.assert_status(result, 0)
        self.assertFalse(suite_old.exists())
        self.assertFalse(playwright_old.exists())
        for path in kept + [recent, busy]:
            self.assertTrue(path.exists(), path.name)
        self.assertIn("obd tmp: removed suite-old", result.stdout)
        self.assertIn("obd tmp: kept suite-busy (recent writes inside)", result.stdout)

    def test_dry_run_removes_nothing(self):
        suite_old = self.scratch("suite-old")

        result = self.sweep()

        self.assert_status(result, 0)
        self.assertTrue((suite_old / "inner" / "f").exists())
        self.assertIn("obd tmp: would remove suite-old", result.stdout)

    def test_an_ancestor_swapped_for_a_symlink_mid_run_redirects_nothing(self):
        self.scratch("suite-probe")
        outside = self.root / "outside"
        victim = self.write(outside / "suite-probe" / "keep.txt")
        age(victim.parent)
        moved = self.root / "moved-tmp"
        hook = self.hook(f'[[ $stage == before-remove ]] || exit 0; mv "{self.tmp}" "{moved}";'
                         f' ln -s "{outside}" "{self.tmp}"')

        result = self.sweep("--apply", hook=hook)

        self.assert_status(result, 0)
        self.assertTrue(victim.exists(), "deleted through the swapped symlink")
        self.assertFalse((moved / "suite-probe").exists(),
                         "the pinned directory is the one that is swept")

    def test_a_symlink_named_like_a_candidate_is_not_followed(self):
        victim = self.write(self.root / "outside" / "keep.txt")
        age(victim.parent)
        (self.tmp / "suite-link").symlink_to(victim.parent)
        age(self.tmp / "suite-link")

        result = self.sweep("--apply")

        self.assert_status(result, 0)
        self.assertTrue(victim.exists())
        self.assertTrue((self.tmp / "suite-link").is_symlink())

    def test_a_candidate_with_a_mount_below_it_is_kept_whole(self):
        candidate = self.scratch("suite-old")
        self.mount_at(candidate / "inner")
        mounted_itself = self.scratch("suite-mount")
        self.mount_at(mounted_itself)

        result = self.sweep("--apply")

        self.assert_status(result, 0)
        self.assertTrue((candidate / "inner" / "f").exists())
        self.assertTrue((mounted_itself / "inner" / "f").exists())
        self.assertIn("kept suite-old (a filesystem is mounted there)", result.stdout)

    def test_a_file_written_after_the_idle_check_keeps_the_directory(self):
        candidate = self.scratch("suite-old")
        hook = self.hook(f'[[ $stage == before-remove ]] || exit 0; echo new > "{candidate}/late"')

        result = self.sweep("--apply", hook=hook)

        self.assert_status(result, 0)
        self.assertEqual((candidate / "late").read_text(), "new\n")
        self.assertIn("kept suite-old (recent writes inside)", result.stdout)

    def test_a_directory_made_after_the_idle_check_is_kept(self):
        candidate = self.scratch("suite-old")
        # An old file in a fresh directory, with the candidate's own mtime made
        # old again: only the child directory's freshness tells.
        hook = self.hook(f'[[ $stage == before-remove ]] || exit 0; c="{candidate}";'
                         ' mkdir "$c/fresh"; echo input > "$c/fresh/old-input.txt";'
                         ' touch -t 202001010000 "$c/fresh/old-input.txt" "$c"')

        result = self.sweep("--apply", hook=hook)

        self.assert_status(result, 0)
        self.assertEqual((candidate / "fresh" / "old-input.txt").read_text(), "input\n")
        self.assertIn("kept suite-old (it changed while being removed)", result.stdout)

    def test_a_mount_made_after_the_idle_check_keeps_the_directory(self):
        candidate = self.scratch("suite-old")
        hook = self.hook(f'[[ $stage == before-remove ]] || exit 0;'
                         f' echo "405 26 0:51 / {candidate}/inner rw - tmpfs tmpfs rw"'
                         f' >> "{self.mountinfo}"')

        result = self.sweep("--apply", hook=hook)

        self.assert_status(result, 0)
        self.assertTrue((candidate / "inner" / "f").exists())
        self.assertIn("kept suite-old (a filesystem is mounted there)", result.stdout)

    @unittest.skipIf(os.geteuid() == 0, "root reads a mode-000 directory anyway")
    def test_a_directory_that_cannot_be_inspected_is_kept_and_fails_the_run(self):
        candidate = self.scratch("suite-old")
        locked = candidate / "locked"
        locked.mkdir()
        age(locked)
        age(candidate)
        locked.chmod(0)

        result = self.sweep("--apply")

        self.assert_status(result, 1)
        self.assertTrue(candidate.exists())
        self.assertIn("could not inspect it completely", result.stderr)


class CacheAndReportTests(SweepFixture):
    def test_old_cache_files_go_and_other_caches_stay(self):
        npm_old = self.write(self.home / ".npm" / "_cacache" / "a" / "old")
        npm_new = self.write(self.home / ".npm" / "_cacache" / "new", old=False)
        pip_old = self.write(self.home / ".cache" / "pip" / "old")
        mozilla = self.write(self.home / ".cache" / "mozilla" / "old")

        result = self.sweep("--apply")

        self.assert_status(result, 0)
        self.assertFalse(npm_old.exists())
        self.assertFalse(pip_old.exists())
        self.assertTrue(npm_new.exists())
        self.assertTrue(mozilla.exists())
        self.assertIn("cache .cache/pip: 1 file(s) older than 30 days, deleted 1, 1 bytes",
                      result.stdout)

    def test_a_mount_inside_a_cache_is_not_entered(self):
        inside = self.write(self.home / ".cache" / "pip" / "tmpfs" / "old")
        self.mount_at(inside.parent)

        result = self.sweep("--apply")

        self.assert_status(result, 0)
        self.assertTrue(inside.exists())

    def test_a_mount_on_a_cache_ancestor_is_skipped(self):
        old = self.write(self.home / ".cache" / "pip" / "evidence.txt")
        self.mount_at(self.home / ".cache")

        result = self.sweep("--apply")

        self.assert_status(result, 0)
        self.assertTrue(old.exists())
        self.assertIn("cache .cache/pip: skipped", result.stdout)
        self.assertIn("is a mount point on the way to", result.stdout)

    def test_a_symlinked_cache_ancestor_is_skipped(self):
        victim = self.write(self.root / "outside" / "pip" / "victim")
        (self.home / ".cache").symlink_to(self.root / "outside")

        result = self.sweep("--apply")

        self.assert_status(result, 0)
        self.assertTrue(victim.exists())
        self.assertIn("cache .cache/pip: skipped", result.stdout)

    def test_big_old_agent_shared_files_are_listed_never_deleted(self):
        shared = self.home / "Documents" / "agent-shared"
        files = {name: shared / f"{name}.bin" for name in ("bigold", "bignew", "smallold")}
        for name, path in files.items():
            self.write(path, "x" * (2000 if name.startswith("big") else 10), old=name != "bignew")

        result = subprocess.run(
            [sys.executable, "-I", str(MODULE), "--apply", "--home", str(self.home)],
            capture_output=True, text=True, timeout=60,
            env={"PATH": "/usr/bin:/bin", "ROOTFS_TIDY_MOUNTINFO": str(self.mountinfo),
                 "ROOTFS_TIDY_BIG_BYTES": "1000"})

        self.assert_status(result, 0)
        for path in files.values():
            self.assertTrue(path.exists())
        self.assertIn("bigold.bin", result.stdout)
        self.assertNotIn("bignew.bin", result.stdout)
        self.assertNotIn("smallold.bin", result.stdout)


def can_bind_mount():
    if not sys.platform.startswith("linux") or not shutil.which("unshare"):
        return False
    probe = subprocess.run(["unshare", "--user", "--map-root-user", "--mount", "true"],
                           capture_output=True, timeout=30, check=False)
    return probe.returncode == 0


@unittest.skipUnless(can_bind_mount(), "needs Linux with unprivileged user and mount namespaces")
class BindMountTests(SweepFixture):
    """Real same-device bind mounts, with a mountinfo fixture that never lists
    them: only the per-descriptor mount id can catch them."""

    def sweep_in_namespace(self, setup, hook=None):
        script = self.root / "inside.sh"
        script.write_text(f"set -eu\n{setup}\nexec \"$@\"\n")
        environment = {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
                       "ROOTFS_TIDY_MOUNTINFO": str(self.mountinfo)}
        if hook:
            environment["ROOTFS_TIDY_TEST_HOOK"] = hook
        return subprocess.run(
            ["unshare", "--user", "--map-root-user", "--mount", "/bin/bash", str(script),
             sys.executable, "-I", str(MODULE), "--apply", "--home", str(self.home)],
            capture_output=True, text=True, env=environment, timeout=60)

    def test_a_bind_mounted_cache_ancestor_is_not_entered(self):
        evidence = self.write(self.root / "outside" / "pip" / "evidence.txt")
        (self.home / ".cache").mkdir()

        result = self.sweep_in_namespace(f'mount --bind "{self.root}/outside" "{self.home}/.cache"')

        self.assert_status(result, 0)
        self.assertTrue(evidence.exists(), result.stdout + result.stderr)

    def test_a_bind_mount_made_before_removal_is_not_entered(self):
        candidate = self.write(self.tmp / "suite-old" / "inner" / "f").parent.parent
        age(candidate / "inner")
        age(candidate)
        evidence = self.write(self.root / "outside" / "evidence.txt")
        age(evidence.parent)
        hook = self.hook(f'[[ $stage == before-remove ]] || exit 0;'
                         f' mount --bind "{evidence.parent}" "{candidate}/inner"')

        result = self.sweep_in_namespace("", hook=hook)

        self.assertTrue(evidence.exists(), result.stdout + result.stderr)
        self.assertTrue(candidate.exists())


class EntryTests(SweepFixture):
    def test_a_missing_home_is_a_skip(self):
        shutil.rmtree(self.home)

        result = self.sweep("--apply")

        self.assert_status(result, 0)
        self.assertIn("sweeps: skipped", result.stdout)

    def test_an_unreadable_mount_table_deletes_nothing(self):
        old = self.write(self.sessions / "old.jsonl")
        self.mountinfo.unlink()
        self.mountinfo.mkdir()

        result = self.sweep("--apply")

        self.assert_status(result, 1)
        self.assertTrue(old.exists())


if __name__ == "__main__":
    unittest.main()
