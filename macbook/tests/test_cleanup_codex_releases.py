"""Exercise release cleanup against disposable fixtures only."""

from contextlib import redirect_stderr, redirect_stdout
import errno
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import unittest
import sys
from unittest.mock import patch


SCRIPTS = Path(__file__).resolve().parents[2] / "shared" / "sh"
# The suite runs on both hosts the script supports; fixtures use this host's target.
TARGET = "aarch64-apple-darwin" if sys.platform == "darwin" else "aarch64-unknown-linux-musl"


def load_script(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


cleaner = load_script("cleanup_codex_releases")


class CleanupFixture:
    package = "standalone"

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name).resolve()
        self.root = self.home / ".codex/packages" / self.package / "releases"
        self.root.mkdir(parents=True)
        (self.root.parent / "install.lock").touch()
        self.versions = [self.release(n) for n in (7, 8, 9, 10, 11)]
        (self.root.parent / "current").symlink_to(self.versions[-1])
        active = patch.object(cleaner, "running_releases", return_value=set())
        active.start()
        self.addCleanup(active.stop)

    def release(self, minor, root=None):
        version = f"0.{minor}.0"
        name = f"{version}-{TARGET}"
        path = (root or self.root) / name
        (path / "bin").mkdir(parents=True)
        binary = path / "bin/codex"
        binary.write_text("fixture binary")
        binary.chmod(0o755)
        (path / "codex-package.json").write_text(json.dumps({
            "layoutVersion": 1, "version": version, "variant": "codex",
            "target": TARGET, "entrypoint": "bin/codex",
        }))
        return path

    def run_cleanup(self, apply=False):
        with redirect_stdout(io.StringIO()) as output:
            cleaner.cleanup(self.home, apply=apply, package=self.package)
        return output.getvalue()


class CleanupTests(CleanupFixture, unittest.TestCase):
    def test_preview_never_deletes(self):
        self.assertIn("Would delete 2 directories", self.run_cleanup())
        self.assertTrue(all(path.exists() for path in self.versions))
        self.assertFalse((self.root.parent / "install.lock.d").exists())

    def test_apply_uses_numeric_versions_not_mtime_and_is_idempotent(self):
        os.utime(self.versions[0], (2000000000, 2000000000))
        self.run_cleanup(apply=True)
        self.assertEqual(set(self.root.iterdir()), set(self.versions[-3:]))
        self.assertIn("Deleted 0 directories", self.run_cleanup(apply=True))

    def test_current_and_running_old_versions_are_kept(self):
        link = self.root.parent / "current"
        link.unlink()
        link.symlink_to(self.versions[0])
        with patch.object(cleaner, "running_releases", return_value={self.versions[1]}):
            self.run_cleanup(apply=True)
        self.assertTrue(all(path.exists() for path in self.versions))

    def test_newly_running_candidate_is_kept(self):
        with patch.object(cleaner, "running_releases",
                          side_effect=[set(), {self.versions[1]}, set()]):
            self.run_cleanup(apply=True)
        self.assertTrue(self.versions[1].exists())
        self.assertFalse(self.versions[0].exists())

    def test_unknown_entries_and_symlink_targets_survive(self):
        outside = self.home / "outside"
        outside.mkdir()
        (outside / "important").write_text("preserve")
        (self.versions[0] / "external").symlink_to(outside)
        alias = self.root / f"0.1.0-{TARGET}"
        alias.symlink_to(outside)
        unknown = self.root / "future-layout"
        unknown.mkdir()
        self.run_cleanup(apply=True)
        self.assertEqual((outside / "important").read_text(), "preserve")
        self.assertTrue(alias.is_symlink())
        self.assertTrue(unknown.is_dir())

    def test_malformed_candidate_aborts_before_any_deletion(self):
        (self.versions[0] / "codex-package.json").write_text("{}")
        with self.assertRaises(RuntimeError):
            self.run_cleanup(apply=True)
        self.assertTrue(all(path.exists() for path in self.versions))

    def test_mount_or_probe_failure_aborts_before_deletion(self):
        with patch.object(Path, "is_mount", side_effect=lambda: True):
            with self.assertRaises(RuntimeError):
                self.run_cleanup(apply=True)
        with patch.object(cleaner, "running_releases", side_effect=RuntimeError("probe failed")):
            with self.assertRaises(RuntimeError):
                self.run_cleanup(apply=True)
        self.assertTrue(all(path.exists() for path in self.versions))

    def test_nested_mount_aborts_before_any_deletion(self):
        mounted = self.versions[0] / "nested"
        mounted.mkdir()
        original = Path.is_mount
        with patch.object(Path, "is_mount", lambda p: p == mounted or original(p)):
            with self.assertRaises(RuntimeError):
                self.run_cleanup(apply=True)
        self.assertTrue(all(path.exists() for path in self.versions))

    def test_missing_or_external_current_aborts(self):
        link = self.root.parent / "current"
        link.unlink()
        link.symlink_to(self.home)
        with self.assertRaises(RuntimeError):
            self.run_cleanup(apply=True)
        self.assertTrue(all(path.exists() for path in self.versions))

    def test_symlinked_root_refused(self):
        moved = self.root.with_name("moved")
        self.root.rename(moved)
        self.root.symlink_to(moved)
        with self.assertRaises(RuntimeError):
            self.run_cleanup(apply=True)
        self.assertEqual(len(list(moved.iterdir())), 5)

    def test_installer_directory_lock_is_never_removed(self):
        lock = self.root.parent / "install.lock.d"
        lock.mkdir()
        with self.assertRaises(FileExistsError):
            self.run_cleanup(apply=True)
        self.assertTrue(lock.is_dir())
        self.assertTrue(all(path.exists() for path in self.versions))

    def test_both_installer_file_lock_variants_block_cleanup(self):
        for method in ("flock", "lockf"):
            code = ("import fcntl, sys; f=open(sys.argv[1], 'r+'); "
                    f"fcntl.{method}(f, fcntl.LOCK_EX); "
                    "print('locked', flush=True); sys.stdin.read()")
            process = subprocess.Popen(
                ["/usr/bin/python3", "-c", code, str(self.root.parent / "install.lock")],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
            )
            try:
                self.assertEqual(process.stdout.readline().strip(), "locked")
                with self.assertRaises(BlockingIOError):
                    self.run_cleanup(apply=True)
            finally:
                process.communicate(timeout=5)
        self.assertTrue(all(path.exists() for path in self.versions))


class DaemonCleanupTests(CleanupTests):
    """The entire deletion/lock/protection contract also holds for the daemon."""
    package = "app-server-daemon"

    @unittest.skipUnless(sys.platform == "linux" and os.getuid() == 0,
                         "Native /proc protection check requires Linux root to inspect every process.")
    def test_executing_daemon_release_survives_until_its_process_exits(self):
        binary = self.versions[0] / "bin/codex"
        shutil.copyfile("/bin/sleep", binary)
        process = subprocess.Popen([str(binary), "60"])
        try:
            with patch.object(cleaner, "running_releases", cleaner.linux_running_releases):
                self.run_cleanup(apply=True)
                self.assertTrue(self.versions[0].exists())
                self.assertFalse(self.versions[1].exists())
                self.assertIsNone(process.poll())
        finally:
            process.terminate()
            process.wait(timeout=5)
        with patch.object(cleaner, "running_releases", cleaner.linux_running_releases):
            self.run_cleanup(apply=True)
        self.assertEqual(set(self.root.iterdir()), set(self.versions[-3:]))


class PackageSelectionTests(CleanupFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.daemon = self.home / ".codex/packages/app-server-daemon/releases"
        self.daemon.mkdir(parents=True)
        (self.daemon.parent / "install.lock").touch()
        self.daemon_versions = [self.release(n, self.daemon) for n in (1, 2, 3, 4, 5)]
        (self.daemon.parent / "current").symlink_to(self.daemon_versions[-1])

    def selected(self, package="all", apply=True):
        with redirect_stdout(io.StringIO()) as output, redirect_stderr(io.StringIO()):
            result = cleaner.cleanup_packages(self.home, package=package, apply=apply)
        return result, output.getvalue()

    def test_default_call_leaves_daemon_untouched(self):
        with redirect_stdout(io.StringIO()):
            self.assertEqual(cleaner.cleanup_packages(self.home, apply=True), 0)
        self.assertEqual(set(self.root.iterdir()), set(self.versions[-3:]))
        self.assertTrue(all(p.exists() for p in self.daemon_versions))

    def test_daemon_only_leaves_standalone_untouched(self):
        self.assertEqual(self.selected("app-server-daemon")[0], 0)
        self.assertTrue(all(p.exists() for p in self.versions))
        self.assertEqual(set(self.daemon.iterdir()), set(self.daemon_versions[-3:]))

    def test_all_preview_and_apply_keep_three_in_each_tree(self):
        self.assertEqual(self.selected(apply=False)[0], 0)
        self.assertTrue(all(p.exists() for p in self.versions + self.daemon_versions))
        self.assertEqual(self.selected()[0], 0)
        self.assertEqual(set(self.root.iterdir()), set(self.versions[-3:]))
        self.assertEqual(set(self.daemon.iterdir()), set(self.daemon_versions[-3:]))

    def test_busy_standalone_does_not_block_daemon(self):
        lock = self.root.parent / "install.lock.d"
        lock.mkdir()
        self.assertEqual(self.selected()[0], 75)
        self.assertTrue(lock.is_dir())
        self.assertTrue(all(p.exists() for p in self.versions))
        self.assertEqual(set(self.daemon.iterdir()), set(self.daemon_versions[-3:]))

    def test_invalid_package_does_not_hide_peer_lock_or_error(self):
        (self.versions[0] / "codex-package.json").write_text("{}")
        (self.daemon.parent / "install.lock.d").mkdir()
        self.assertEqual(self.selected()[0], 1)
        self.assertTrue(all(p.exists() for p in self.versions + self.daemon_versions))

    def test_missing_optional_package_is_skipped_but_partial_layout_fails(self):
        moved = self.daemon.parent.with_name("saved-daemon")
        self.daemon.parent.rename(moved)
        status, output = self.selected()
        self.assertEqual(status, 0)
        self.assertIn("SKIP app-server-daemon: package is not installed.", output)
        self.daemon.parent.mkdir()
        self.assertEqual(self.selected()[0], 1)

    def test_unknown_package_and_symlinked_daemon_fail_without_deleting_it(self):
        self.assertEqual(self.selected("../elsewhere")[0], 1)
        moved = self.daemon.parent.with_name("saved-daemon")
        self.daemon.parent.rename(moved)
        self.daemon.parent.symlink_to(moved)
        self.assertEqual(self.selected()[0], 1)
        self.assertTrue(all((moved / "releases" / p.name).exists() for p in self.daemon_versions))


class LinuxProbeTests(unittest.TestCase):
    """Linux process and mount probes, against fake /proc trees on any host."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name).resolve()
        self.proc = base / "proc"
        self.proc.mkdir()
        self.root = base / "releases"
        self.old = self.root / "0.1.0-aarch64-unknown-linux-musl"
        self.new = self.root / "0.2.0-aarch64-unknown-linux-musl"

    def process(self, pid, exe, maps="", cmdline=b"", threads=None):
        """A fake /proc/<pid> whose task/<tid>/maps hold each thread's mappings."""
        directory = self.proc / str(pid)
        directory.mkdir()
        if exe is not None:
            (directory / "exe").symlink_to(exe)
        (directory / "cmdline").write_bytes(cmdline)
        for tid, task_maps in (threads or {pid: maps}).items():
            (directory / "task" / str(tid)).mkdir(parents=True)
            (directory / "task" / str(tid) / "maps").write_text(task_maps)
        return directory

    def test_release_names_match_only_this_platforms_target(self):
        linux = cleaner.release_name("linux")
        self.assertTrue(linux.fullmatch("0.161.0-aarch64-unknown-linux-musl"))
        self.assertTrue(linux.fullmatch("0.161.0-x86_64-unknown-linux-gnu"))
        self.assertFalse(linux.fullmatch("0.161.0-aarch64-apple-darwin"))
        self.assertFalse(cleaner.release_name("darwin").fullmatch(
            "0.161.0-aarch64-unknown-linux-musl"))

    def test_executables_and_mappings_protect_their_releases(self):
        self.process(10, self.old / "bin/codex")
        self.process(11, "/usr/bin/bash",
                     maps=f"7f00-7f01 r-xp 00000000 b3:02 42 {self.new}/codex-resources/lib.so\n")
        self.process(12, "/usr/bin/bash", maps="7f00-7f01 r-xp 00000000 00:00 0 [vdso]\n")
        (self.proc / "self").mkdir()
        self.assertEqual(cleaner.linux_running_releases(self.root, self.proc),
                         {self.old, self.new})

    def test_exited_processes_are_skipped(self):
        self.process(10, None)
        self.process(11, self.old / "bin/codex")
        self.assertEqual(cleaner.linux_running_releases(self.root, self.proc), {self.old})

    def test_worker_threads_protect_a_release_after_the_leader_exits(self):
        mapping = f"7f00-7f01 r-xp 00000000 b3:02 42 {self.new}/bin/codex\n"
        self.process(40, None, threads={40: "", 41: mapping})
        self.assertEqual(cleaner.linux_running_releases(self.root, self.proc), {self.new})

    def test_an_uninspectable_process_refuses_unless_running_as_root(self):
        original = os.readlink

        def hidden(path):
            if Path(path).parent.name == "20":
                raise PermissionError(13, "Permission denied", str(path))
            return original(path)

        # A non-dumpable host may map a release without naming Codex anywhere.
        self.process(20, "/usr/sbin/sshd", cmdline=b"sshd: pi@notty\0")
        with patch.object(cleaner.os, "readlink", hidden):
            with self.assertRaises(RuntimeError):
                cleaner.linux_running_releases(self.root, self.proc)

    def test_root_reads_every_process_and_restores_the_owner_euid(self):
        self.process(30, self.old / "bin/codex")
        with patch.object(cleaner.os, "getuid", return_value=0), \
                patch.object(cleaner.os, "geteuid", return_value=1000), \
                patch.object(cleaner.os, "seteuid") as seteuid:
            self.assertEqual(cleaner.linux_running_releases(self.root, self.proc), {self.old})
        self.assertEqual([call.args for call in seteuid.call_args_list], [(0,), (1000,)])

    def test_failing_to_drop_root_exits_before_any_further_work(self):
        with patch.object(cleaner.os, "getuid", return_value=0), \
                patch.object(cleaner.os, "geteuid", return_value=1000), \
                patch.object(cleaner.os, "seteuid", side_effect=[None, PermissionError()]), \
                patch.object(cleaner.os, "_exit", side_effect=SystemExit(70)) as exit_now, \
                redirect_stderr(io.StringIO()) as stderr:
            with self.assertRaises(SystemExit):
                with cleaner.root_effective_uid():
                    pass
        exit_now.assert_called_once_with(70)
        self.assertIn("could not drop root privileges", stderr.getvalue())

    def test_failing_to_drop_root_exits_even_when_stderr_fails(self):
        class FullStderr(io.StringIO):
            def write(self, text):
                raise OSError(errno.ENOSPC, "No space left on device")

        with patch.object(cleaner.os, "getuid", return_value=0), \
                patch.object(cleaner.os, "geteuid", return_value=1000), \
                patch.object(cleaner.os, "seteuid", side_effect=[None, PermissionError()]), \
                patch.object(cleaner.os, "_exit", side_effect=SystemExit(70)) as exit_now, \
                redirect_stderr(FullStderr()):
            with self.assertRaises(SystemExit):
                with cleaner.root_effective_uid():
                    pass
        exit_now.assert_called_once_with(70)

    def test_empty_or_missing_process_table_refuses(self):
        with self.assertRaises(RuntimeError):
            cleaner.linux_running_releases(self.root, self.proc)
        with self.assertRaises(RuntimeError):
            cleaner.linux_running_releases(self.root, self.proc / "missing")

    def test_mount_table_reveals_same_device_bind_mounts_with_escaped_names(self):
        mountinfo = self.proc / "mountinfo"
        release = self.root / "0.1.0 old"
        line = "36 25 179:2 / {} rw,relatime shared:1 - ext4 /dev/root rw\n"
        mountinfo.write_text(line.format("/") + line.format(
            str(release).replace(" ", "\\040") + "/nested"))
        with self.assertRaises(RuntimeError):
            cleaner.refuse_mounts_within(release, mountinfo)
        cleaner.refuse_mounts_within(self.old, mountinfo)
        # A sibling whose name only shares a prefix is not beneath the release.
        cleaner.refuse_mounts_within(self.root / "0.1.0", mountinfo)

    def test_unicode_whitespace_in_a_source_path_does_not_hide_the_mount_point(self):
        mountinfo = self.proc / "mountinfo"
        for space in ("\u00a0", "\u2003"):
            mountinfo.write_bytes(
                b"36 25 179:2 / / rw - ext4 /dev/root rw\n"
                + f"37 36 179:2 /src{space}dir {self.old}/codex-resources rw - ext4 "
                  "/dev/root rw\n".encode())
            with self.assertRaises(RuntimeError):
                cleaner.refuse_mounts_within(self.old, mountinfo)

    def test_unreadable_or_empty_mount_table_refuses(self):
        with self.assertRaises(RuntimeError):
            cleaner.refuse_mounts_within(self.old, self.proc / "missing")
        (self.proc / "mountinfo").write_text("")
        with self.assertRaises(RuntimeError):
            cleaner.refuse_mounts_within(self.old, self.proc / "mountinfo")


class RemoveTreeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.tree = self.base / "release"
        (self.tree / "a/b").mkdir(parents=True)
        (self.tree / "a/b/file").write_text("x")
        self.outside = self.base / "outside"
        self.outside.mkdir()
        (self.outside / "keep").write_text("preserve")
        (self.tree / "a/link").symlink_to(self.outside)

    def test_deletes_the_tree_without_following_symlinks(self):
        cleaner.remove_tree(self.tree, self.tree.stat().st_dev)
        self.assertFalse(self.tree.exists())
        self.assertEqual((self.outside / "keep").read_text(), "preserve")

    def test_another_filesystem_stops_deletion_before_removing_anything(self):
        with self.assertRaises(RuntimeError):
            cleaner.remove_tree(self.tree, self.tree.stat().st_dev + 1)
        self.assertTrue((self.tree / "a/b/file").exists())
        self.assertTrue((self.tree / "a/link").is_symlink())

    def test_a_foreign_ancestor_is_refused_before_its_subtree_is_touched(self):
        # "a" reports another device while "a/b" (say, a bind back onto the
        # release filesystem) does not; nothing under "a" may be removed.
        foreign = (self.tree / "a").stat().st_ino
        original = os.fstat

        def fstat(descriptor):
            info = original(descriptor)
            if info.st_ino != foreign:
                return info
            fields = list(info)
            fields[stat.ST_DEV] += 1
            return os.stat_result(fields)

        with patch.object(cleaner.os, "fstat", fstat):
            with self.assertRaises(RuntimeError):
                cleaner.remove_tree(self.tree, self.tree.stat().st_dev)
        self.assertTrue((self.tree / "a/b/file").exists())


if __name__ == "__main__":
    unittest.main()
