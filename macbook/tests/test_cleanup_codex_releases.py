"""Exercise release cleanup against disposable fixtures only."""

from contextlib import redirect_stdout
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def load_script(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


cleaner = load_script("cleanup_codex_releases")


class CleanupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name).resolve()
        self.root = self.home / ".codex/packages/standalone/releases"
        self.root.mkdir(parents=True)
        (self.root.parent / "install.lock").touch()
        self.versions = [self.release(n) for n in (7, 8, 9, 10, 11)]
        (self.root.parent / "current").symlink_to(self.versions[-1])
        active = patch.object(cleaner, "running_releases", return_value=set())
        active.start()
        self.addCleanup(active.stop)

    def release(self, minor):
        version = f"0.{minor}.0"
        name = version + "-aarch64-apple-darwin"
        path = self.root / name
        (path / "bin").mkdir(parents=True)
        binary = path / "bin/codex"
        binary.write_text("fixture binary")
        binary.chmod(0o755)
        (path / "codex-package.json").write_text(json.dumps({
            "layoutVersion": 1, "version": version, "variant": "codex",
            "target": "aarch64-apple-darwin", "entrypoint": "bin/codex",
        }))
        return path

    def run_cleanup(self, apply=False):
        with redirect_stdout(io.StringIO()) as output:
            cleaner.cleanup(self.home, apply=apply)
        return output.getvalue()

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
        alias = self.root / "0.1.0-aarch64-apple-darwin"
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


if __name__ == "__main__":
    unittest.main()
