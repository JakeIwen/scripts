"""macOS resource-fork regression tests; synthetic files, no GUI or real videos."""

import base64
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/stamp-video-thumbs.zsh"
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII="
)


def resource_fork(resource_id=-16455, data_prefix=b""):
    """One custom icns resource in a classic Resource Manager file."""
    icon = b"icns" + struct.pack(">I", 16 + len(PNG)) + b"icp4" + struct.pack(">I", 8 + len(PNG)) + PNG
    data = data_prefix + struct.pack(">I", len(icon)) + icon
    header = struct.pack(">IIII", 256, 256 + len(data), len(data), 50)
    resource_map = (
        bytes(24) + struct.pack(">HHH", 28, 50, 0)
        + struct.pack(">4sHH", b"icns", 0, 10)
        + struct.pack(">hHI", resource_id, 0xffff, len(data_prefix)) + bytes(4)
    )
    return header + bytes(240) + data + resource_map


def empty_map_with_stale_data():
    """Regression: nonempty fork whose active directory is the empty map."""
    header = struct.pack(">IIII", 256, 256, 0, 30)
    resource_map = header + bytes(8) + struct.pack(">HHH", 28, 30, 0xffff)
    return header + bytes(240) + resource_map + resource_fork()


@unittest.skipUnless(sys.platform == "darwin", "requires macOS resource forks and Swift")
class StampVideoThumbTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build_tmp = tempfile.TemporaryDirectory(prefix="stamp-icon-tests-")
        cls.addClassCleanup(cls.build_tmp.cleanup)
        cls.build = Path(cls.build_tmp.name)
        cls.source = SCRIPT.read_text()
        cls.swift = cls.source.split("<<'EOF'\n", 1)[1].split("\nEOF\n", 1)[0] + "\n"
        source_path = cls.build / "stampicon.swift"
        source_path.write_text(cls.swift)
        cls.helper = cls.build / "stampicon"
        subprocess.run([
            "/usr/bin/swiftc", "-O", "-module-cache-path", str(cls.build / "modules"),
            "-o", str(cls.helper), str(source_path),
        ], check=True, capture_output=True, text=True)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="stamp-icon-fixtures-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.media = self.root / "media"
        self.media.mkdir()
        self.cache = self.root / "cache"
        self.cache.mkdir()
        shutil.copy2(self.helper, self.cache / "stampicon")
        (self.cache / "stampicon.swift").write_text(self.swift)
        self.script = self.root / "stamp.zsh"
        self.script.write_text(self.source.replace(
            'CACHE="$HOME/.cache/stamp-video-thumbs"', f'CACHE="{self.cache}"'
        ))

    def fixture(self, name="test.mp4", fork=None, flags=0x0400):
        file = self.media / name
        file.write_bytes(b"synthetic video data: must not change")
        if fork is not None:
            Path(str(file) + "/..namedfork/rsrc").write_bytes(fork)
        info = bytes(8) + struct.pack(">H", flags) + bytes(22)
        subprocess.run([
            "/usr/bin/xattr", "-wx", "com.apple.FinderInfo", info.hex(), str(file)
        ], check=True, capture_output=True)
        return file

    def check(self, file):
        return subprocess.run([str(self.helper), "--check", str(file)], capture_output=True, text=True)

    def scan(self, *args):
        return subprocess.run([
            "/bin/zsh", str(self.script), "--dry-run", *args, str(self.media)
        ], capture_output=True, text=True)

    def test_valid_custom_icon(self):
        self.assertEqual(self.check(self.fixture(fork=resource_fork())).returncode, 0)

    def test_valid_icon_at_nonzero_data_offset(self):
        self.assertEqual(self.check(self.fixture(fork=resource_fork(data_prefix=bytes(40)))).returncode, 0)

    def test_empty_map_with_stale_icon_bytes_needs_repair(self):
        result = self.check(self.fixture(fork=empty_map_with_stale_data()))
        self.assertEqual(result.returncode, 4)
        self.assertIn("empty resource data/map", result.stdout)

    def test_missing_custom_icon_flag(self):
        self.assertEqual(self.check(self.fixture(fork=resource_fork(), flags=0)).returncode, 4)

    def test_missing_fork(self):
        self.assertEqual(self.check(self.fixture()).returncode, 4)

    def test_resource_id_is_not_custom_icon(self):
        self.assertEqual(self.check(self.fixture(fork=resource_fork(resource_id=128))).returncode, 4)

    def test_truncated_fork(self):
        self.assertEqual(self.check(self.fixture(fork=resource_fork()[:-10])).returncode, 4)

    def test_invalid_icns_length(self):
        fork = bytearray(resource_fork())
        struct.pack_into(">I", fork, 264, 0xffffffff)
        self.assertEqual(self.check(self.fixture(fork=fork)).returncode, 4)

    def test_out_of_bounds_type_list(self):
        fork = bytearray(resource_fork())
        map_offset = struct.unpack_from(">I", fork, 4)[0]
        struct.pack_into(">H", fork, map_offset + 24, 65535)
        self.assertEqual(self.check(self.fixture(fork=fork)).returncode, 4)

    def test_empty_type_list(self):
        fork = bytearray(resource_fork())
        map_offset = struct.unpack_from(">I", fork, 4)[0]
        struct.pack_into(">H", fork, map_offset + 28, 65535)
        self.assertEqual(self.check(self.fixture(fork=fork)).returncode, 4)

    def test_missing_video_is_io_error(self):
        self.assertEqual(self.check(self.media / "vanished.mp4").returncode, 5)

    def test_dry_run_selects_broken_and_missing_without_changes(self):
        good = self.fixture("good [1].mp4", resource_fork())
        broken = self.fixture("broken ' $file.mp4", empty_map_with_stale_data())
        missing = self.fixture("missing.mp4")
        self.fixture("._sidecar.mp4", resource_fork())
        before = Path(str(broken) + "/..namedfork/rsrc").read_bytes()
        result = self.scan("--verify-existing")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("2 would stamp, 1 skipped, 0 failed (of 3)", result.stdout)
        self.assertIn("empty resource data/map", result.stdout)
        self.assertEqual(Path(str(broken) + "/..namedfork/rsrc").read_bytes(), before)
        for file in (good, broken, missing):
            self.assertEqual(file.read_bytes(), b"synthetic video data: must not change")

    def test_default_keeps_fast_presence_check(self):
        self.fixture(fork=empty_map_with_stale_data())
        result = self.scan()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("0 would stamp, 1 skipped", result.stdout)

    def test_force_overrides_validation(self):
        self.fixture(fork=resource_fork())
        result = self.scan("--force", "--verify-existing")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("1 would stamp, 0 skipped", result.stdout)

    def test_scan_io_error_does_not_schedule_repair(self):
        self.fixture(fork=resource_fork())
        helper = self.cache / "stampicon"
        helper.write_text("#!/bin/sh\necho 'simulated SMB read error'\nexit 5\n")
        helper.chmod(0o755)
        result = self.scan("--verify-existing")
        self.assertEqual(result.returncode, 1)
        self.assertIn("0 would stamp, 0 skipped, 1 failed", result.stdout)
        self.assertIn("simulated SMB read error", result.stdout)


if __name__ == "__main__":
    unittest.main()
