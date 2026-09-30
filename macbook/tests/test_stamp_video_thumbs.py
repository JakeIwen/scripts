"""macOS resource-fork regression tests; synthetic files, no GUI or real videos."""

import os
from pathlib import Path
import shlex
import shutil
import signal
import struct
import subprocess
import sys
import tempfile
import time
import unittest
import zlib


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/stamp-video-thumbs.zsh"
def png_chunk(kind, data):
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))


def solid_png(rgba=(60, 150, 200, 255), side=16, width=None, height=None):
    width = width or side
    height = height or side
    return (
        b"\x89PNG\r\n\x1a\n"
        + png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
        + png_chunk(b"IDAT", zlib.compress((b"\0" + bytes(rgba) * width) * height))
        + png_chunk(b"IEND", b"")
    )


PNG = solid_png()


def resource_fork(resource_id=-16455, data_prefix=b"", png=PNG):
    """One custom icns resource in a classic Resource Manager file."""
    icon = b"icns" + struct.pack(">I", 16 + len(png)) + b"icp4" + struct.pack(">I", 8 + len(png)) + png
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
        # Exercise the actual comparison functions with image fixtures, without
        # relying on the test runner's access to the logged-in icon service.
        compare_source = cls.build / "compare.swift"
        compare_source.write_text(cls.swift.split("// Command dispatch", 1)[0] + '''
if CommandLine.arguments[1] == "--open-retry" {
    var calls = 0
    let denied = CommandLine.arguments[2] == "denied"
    let result = openResourceFork("unused", opener: { _ in
        calls += 1
        if denied { errno = EACCES; return -1 }
        if calls < 3 { errno = ENOENT; return -1 }
        return 77
    }, pause: {})
    print(calls, result.0, result.1); exit(0)
}
if CommandLine.arguments[1] == "--resize" {
    let input = NSImage(contentsOfFile: CommandLine.arguments[2])!
    let side = Int(CommandLine.arguments[3])!
    let resized = try sizedIcon(input, side)
    let cg = resized.cgImage(forProposedRect:nil, context:nil, hints:nil)!
    let ctx = CGContext(data:nil, width:side, height:side, bitsPerComponent:8,
        bytesPerRow:side*4, space:CGColorSpaceCreateDeviceRGB(),
        bitmapInfo:CGBitmapInfo.byteOrder32Big.rawValue | CGImageAlphaInfo.premultipliedLast.rawValue)!
    ctx.draw(cg, in:CGRect(x:0,y:0,width:side,height:side))
    let rgba = ctx.data!.bindMemory(to:UInt8.self,capacity:side*side*4)
    print(cg.width, cg.height, rgba[(side/2)*4+3], rgba[((side/2)*side)*4+3], rgba[((side/2)*side+side/2)*4+3])
    exit(0)
}
guard let stored = NSImage(contentsOfFile: CommandLine.arguments[1]),
      let actual = NSImage(contentsOfFile: CommandLine.arguments[2]) else { exit(5) }
do {
    let expected = try iconSamples(stored), shown = try iconSamples(actual)
    var generic: [Double]? = nil
    if CommandLine.arguments.count > 3, CommandLine.arguments[3] == "--check-detail", !hasIconDetail(shown) {
        print("featureless placeholder"); exit(5)
    }
    if CommandLine.arguments.count > 3, let image = NSImage(contentsOfFile: CommandLine.arguments[3]) {
        generic = try iconSamples(image)
    }
    print(iconDifference(expected, shown))
    exit(needsIconRefresh(expected, shown, generic: generic) ? 6 : 0)
} catch { print(error); exit(5) }
''')
        cls.comparator = cls.build / "compare"
        subprocess.run([
            "/usr/bin/swiftc", "-O", "-module-cache-path", str(cls.build / "modules"),
            "-o", str(cls.comparator), str(compare_source),
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

    def test_stored_image_decodes_for_refresh(self):
        file = self.fixture(fork=resource_fork())
        result = subprocess.run([str(self.helper), "--check-image", str(file)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_refresh_scan_selects_valid_stored_icons_and_generates_missing(self):
        self.fixture("valid.mp4", resource_fork())
        self.fixture("empty.mp4", empty_map_with_stale_data())
        self.fixture("missing.mp4")
        result = self.scan("--refresh-existing")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("2 would stamp, 0 skipped, 0 failed (of 3)", result.stdout)
        self.assertIn("1 stored icons would refresh", result.stdout)

    def test_refresh_scan_detects_undecodable_image_with_valid_metadata(self):
        self.fixture(fork=resource_fork(png=bytes(100)))
        result = self.scan("--refresh-existing")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("stored icon image cannot be decoded", result.stdout)
        self.assertIn("1 would stamp, 0 skipped", result.stdout)

    def test_individual_file_argument_and_duplicate_are_processed_once(self):
        file = self.fixture("clip [1].MP4", resource_fork())
        result = subprocess.run([
            "/bin/zsh", str(self.script), "--dry-run", "--refresh-existing", str(file), str(file)
        ], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("(of 1)", result.stdout)
        self.assertIn("1 stored icons would refresh", result.stdout)

    def test_explicit_sidecar_is_rejected(self):
        file = self.fixture("._video.mp4", resource_fork())
        result = subprocess.run([
            "/bin/zsh", str(self.script), "--dry-run", str(file)
        ], capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn("not a supported video file", result.stderr)

    def test_refresh_uses_stored_icon_without_quicklook(self):
        file = self.fixture(fork=resource_fork())
        calls = self.root / "refresh-calls"
        ql_calls = self.root / "quicklook-calls"
        helper = self.cache / "stampicon"
        helper.write_text(
            '#!/bin/sh\nif [ "$1" = --refresh ]; then\n'
            + '  printf "%s\\n" "$2" "$3" >> ' + shlex.quote(str(calls)) + '\n  exit 0\nfi\n'
            + 'exec ' + shlex.quote(str(self.helper)) + ' "$@"\n'
        )
        helper.chmod(0o755)
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        ql = bin_dir / "qlmanage"
        ql.write_text('#!/bin/sh\ntouch ' + shlex.quote(str(ql_calls)) + '\nexit 99\n')
        ql.chmod(0o755)
        env = dict(os.environ, PATH=str(bin_dir) + os.pathsep + os.environ["PATH"])
        result = subprocess.run([
            "/bin/zsh", str(self.script), "--refresh-existing", str(file)
        ], env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(calls.read_text().splitlines(), [str(file), "256"])
        self.assertFalse(ql_calls.exists())
        self.assertIn("1 stored icons refreshed", result.stdout)

    def test_refresh_failure_preserves_resource_metadata(self):
        file = self.fixture(fork=resource_fork())
        before = Path(str(file) + "/..namedfork/rsrc").read_bytes()
        helper = self.cache / "stampicon"
        helper.write_text(
            '#!/bin/sh\nif [ "$1" = --refresh ]; then echo "no GUI"; exit 1; fi\n'
            + 'exec ' + shlex.quote(str(self.helper)) + ' "$@"\n'
        )
        helper.chmod(0o755)
        result = subprocess.run([
            "/bin/zsh", str(self.script), "--refresh-existing", str(file)
        ], capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn("icon refresh failed: no GUI", result.stdout)
        self.assertEqual(Path(str(file) + "/..namedfork/rsrc").read_bytes(), before)

    def compare_images(self, actual):
        expected_file = self.root / "expected.png"
        actual_file = self.root / "actual.png"
        expected_file.write_bytes(PNG)
        actual_file.write_bytes(actual)
        return subprocess.run([
            str(self.comparator), str(expected_file), str(actual_file)
        ], capture_output=True, text=True)

    def test_rendered_comparison_matches_same_image(self):
        self.assertEqual(self.compare_images(PNG).returncode, 0)

    def test_rendered_comparison_tolerates_resolution_change(self):
        self.assertEqual(self.compare_images(solid_png(side=256)).returncode, 0)

    def test_rendered_comparison_detects_generic_white_icon(self):
        self.assertEqual(self.compare_images(solid_png((245, 245, 245, 255))).returncode, 6)

    def test_rendered_comparison_blank_is_unknown_not_mismatch(self):
        result = self.compare_images(solid_png((0, 0, 0, 0)))
        self.assertEqual(result.returncode, 5)
        self.assertIn("blank/unavailable", result.stdout)

    def test_generic_match_catches_even_a_light_stored_thumbnail(self):
        expected = self.root / "light.png"
        generic = self.root / "generic.png"
        expected.write_bytes(solid_png((245, 240, 230, 255)))
        generic.write_bytes(solid_png((245, 245, 245, 255)))
        result = subprocess.run([
            str(self.comparator), str(expected), str(generic), str(generic)
        ], capture_output=True, text=True)
        self.assertEqual(result.returncode, 6, result.stdout + result.stderr)

    def test_featureless_service_placeholder_is_unknown(self):
        expected = self.root / "stored.png"
        actual = self.root / "placeholder.png"
        expected.write_bytes(PNG)
        actual.write_bytes(solid_png((249, 249, 249, 255)))
        result = subprocess.run([
            str(self.comparator), str(expected), str(actual), "--check-detail"
        ], capture_output=True, text=True)
        self.assertEqual(result.returncode, 5)
        self.assertIn("featureless placeholder", result.stdout)

    def install_rendered_service_stub(self):
        helper = self.cache / "stampicon"
        calls = self.root / "refreshed"
        helper.write_text(
            '#!/bin/sh\n'
            'if [ "$1" = --refresh ]; then\n'
            '  printf "%s\\n" "$2" >> ' + shlex.quote(str(calls)) + '\n  exit 0\nfi\n'
            'if [ "$1" = --check-rendered ]; then\n'
            '  ' + shlex.quote(str(self.helper)) + ' --check-image "$2" || exit $?\n'
            '  case "$2" in */mismatch.mp4) echo "macOS returned the generic mp4 icon"; exit 6;; esac\n'
            '  exit 0\nfi\n'
            'exec ' + shlex.quote(str(self.helper)) + ' "$@"\n'
        )
        helper.chmod(0o755)
        return calls

    def test_rendered_scan_distinguishes_refresh_skip_and_generate(self):
        self.fixture("matching.mp4", resource_fork())
        self.fixture("mismatch.mp4", resource_fork())
        self.fixture("missing.mp4")
        calls = self.install_rendered_service_stub()
        result = self.scan("--verify-rendered")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("1 would stamp, 1 skipped, 0 failed (of 3)", result.stdout)
        self.assertIn("1 stored icons would refresh", result.stdout)
        self.assertIn("macOS returned the generic mp4 icon", result.stdout)
        self.assertFalse(calls.exists())

    def test_rendered_scan_only_refreshes_mismatch(self):
        self.fixture("matching.mp4", resource_fork())
        mismatch = self.fixture("mismatch.mp4", resource_fork())
        calls = self.install_rendered_service_stub()
        result = subprocess.run([
            "/bin/zsh", str(self.script), "--verify-rendered", str(self.media)
        ], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(calls.read_text().splitlines(), [str(mismatch)])
        self.assertIn("0 stamped, 1 skipped, 0 failed (of 2)", result.stdout)

    def test_rendered_service_failure_does_not_repair(self):
        file = self.fixture(fork=resource_fork())
        before = Path(str(file) + "/..namedfork/rsrc").read_bytes()
        helper = self.cache / "stampicon"
        helper.write_text('#!/bin/sh\necho "icon service unavailable"\nexit 5\n')
        helper.chmod(0o755)
        result = self.scan("--verify-rendered")
        self.assertEqual(result.returncode, 1)
        self.assertIn("0 would stamp, 0 skipped, 1 failed", result.stdout)
        self.assertIn("0 stored icons would refresh", result.stdout)
        self.assertEqual(Path(str(file) + "/..namedfork/rsrc").read_bytes(), before)

    def test_resizing_preserves_aspect_and_adds_transparent_padding(self):
        for width, height, edges in [(512, 256, [0, 255, 255]), (256, 512, [255, 0, 255])]:
            with self.subTest(width=width, height=height):
                source = self.root / "wide-or-tall.png"
                source.write_bytes(solid_png(width=width, height=height))
                result = subprocess.run([
                    str(self.comparator), "--resize", str(source), "256"
                ], capture_output=True, text=True, check=True)
                self.assertEqual([int(x) for x in result.stdout.split()], [256, 256, *edges])

    def test_explicit_size_reaches_refresh_helper(self):
        file = self.fixture(fork=resource_fork())
        calls = self.root / "size-calls"
        helper = self.cache / "stampicon"
        helper.write_text(
            '#!/bin/sh\nif [ "$1" = --refresh ]; then\n'
            '  printf "%s\\n" "$3" >> ' + shlex.quote(str(calls)) + '\n  exit 0\nfi\n'
            'exec ' + shlex.quote(str(self.helper)) + ' "$@"\n'
        )
        helper.chmod(0o755)
        result = subprocess.run([
            "/bin/zsh", str(self.script), "--refresh-existing", "--size", "512", str(file)
        ], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(calls.read_text().strip(), "512")

    def test_invalid_size_is_rejected_before_processing(self):
        for size in ["bad", "0", "-1", "99999"]:
            with self.subTest(size=size):
                result = self.scan("--size", size)
                self.assertEqual(result.returncode, 2)
                self.assertIn("--size must be an integer", result.stderr)

    def test_resource_open_retries_transient_missing(self):
        r = subprocess.run([str(self.comparator), "--open-retry", "transient"], capture_output=True, text=True, check=True)
        self.assertEqual(r.stdout.strip(), "3 77 0")

    def test_resource_open_does_not_retry_permission_error(self):
        r = subprocess.run([str(self.comparator), "--open-retry", "denied"], capture_output=True, text=True, check=True)
        calls, fd, error = [int(x) for x in r.stdout.split()]
        self.assertEqual((calls, fd), (1, -1))
        self.assertNotEqual(error, 0)

    def test_watchdog_preserves_child_exit_status(self):
        r = subprocess.run([str(self.helper), "--run-timeout", "2", "/bin/sh", "-c", "exit 7"])
        self.assertEqual(r.returncode, 7)

    def test_watchdog_kills_stuck_process_group(self):
        marker = self.root / "child-survived"
        command = "trap '' TERM; (sleep 0.8; touch " + shlex.quote(str(marker)) + ") & wait"
        start = time.monotonic()
        r = subprocess.run([str(self.helper), "--run-timeout", "0.2", "/bin/sh", "-c", command], capture_output=True, text=True, timeout=4)
        self.assertEqual(r.returncode, 124, r.stderr)
        self.assertLess(time.monotonic() - start, 3)
        time.sleep(0.5)
        self.assertFalse(marker.exists(), "descendant survived the watchdog timeout")

    def test_watchdog_forwards_interrupt_to_owned_group(self):
        started = self.root / "started"
        survived = self.root / "survived"
        command = "touch " + shlex.quote(str(started)) + "; sleep 0.8; touch " + shlex.quote(str(survived))
        p = subprocess.Popen([str(self.helper), "--run-timeout", "5", "/bin/sh", "-c", command])
        self.addCleanup(lambda: p.kill() if p.poll() is None else None)
        deadline = time.monotonic() + 3
        while not started.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertTrue(started.exists())
        p.send_signal(signal.SIGINT)
        self.assertEqual(p.wait(timeout=3), 130)
        time.sleep(0.9)
        self.assertFalse(survived.exists())

    def install_generators(self, ql_hangs=False):
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        png = self.root / "generated.png"
        png.write_bytes(PNG)
        ql_calls = self.root / "ql-calls"
        ff_calls = self.root / "ff-calls"
        ql = bin_dir / "qlmanage"
        ql.write_text('#!' + sys.executable + '\n'
            'from pathlib import Path\nimport sys,time\n'
            f'Path({str(ql_calls)!r}).write_text("called")\n'
            'output=Path(sys.argv[sys.argv.index("-o")+1])/ (Path(sys.argv[-1]).name+".png")\n'
            + ('output.write_bytes(b"partial PNG")\ntime.sleep(20)\n' if ql_hangs
               else f'output.write_bytes(Path({str(png)!r}).read_bytes())\n'))
        ql.chmod(0o755)
        ff = bin_dir / "ffmpeg"
        ff.write_text('#!' + sys.executable + '\n'
            'from pathlib import Path\nimport sys\n'
            f'Path({str(ff_calls)!r}).write_text(sys.argv[sys.argv.index("-i")+1])\n'
            f'Path(sys.argv[-1]).write_bytes(Path({str(png)!r}).read_bytes())\n')
        ff.chmod(0o755)
        return dict(os.environ, PATH=str(bin_dir) + os.pathsep + os.environ["PATH"]), ql_calls, ff_calls

    def install_stamping_stub(self, *, timeout=False):
        calls = self.root / "stamp-calls"
        helper = self.cache / "stampicon"
        helper.write_text('#!/bin/sh\n'
            'case "$1" in --run-timeout|--container|--check|--check-image|--check-rendered)\n'
            '  exec ' + shlex.quote(str(self.helper)) + ' "$@";; esac\n'
            'printf "%s\\n" "$1" >> ' + shlex.quote(str(calls)) + '\n'
            + ('sleep 20\n' if timeout else 'exit 0\n'))
        helper.chmod(0o755)
        return calls

    def test_flv_mislabeled_mp4_bypasses_quicklook(self):
        file = self.fixture("mislabeled.mp4")
        file.write_bytes(b"FLV\x01\x05" + bytes(100))
        env, ql, ff = self.install_generators()
        calls = self.install_stamping_stub()
        r = subprocess.run(["/bin/zsh", str(self.script), "--refresh-existing", str(file)], env=env, capture_output=True, text=True, timeout=10)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse(ql.exists())
        self.assertEqual(ff.read_text(), str(file))
        self.assertEqual(calls.read_text().splitlines(), [str(file)])
        self.assertTrue(file.read_bytes().startswith(b"FLV"))

    def test_quicklook_timeout_falls_back_and_batch_continues(self):
        self.fixture("one.mp4")
        self.fixture("two.mp4")
        env, _, ff = self.install_generators(ql_hangs=True)
        calls = self.install_stamping_stub()
        r = subprocess.run(["/bin/zsh", str(self.script), "--timeout", "1", str(self.media)], env=env, capture_output=True, text=True, timeout=12)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(len(calls.read_text().splitlines()), 2)
        self.assertTrue(ff.exists())
        self.assertIn("2 stamped", r.stdout)

    def test_stamping_timeout_preserves_existing_metadata(self):
        file = self.fixture(fork=resource_fork())
        before = Path(str(file) + "/..namedfork/rsrc").read_bytes()
        env, _, _ = self.install_generators()
        calls = self.install_stamping_stub(timeout=True)
        r = subprocess.run(["/bin/zsh", str(self.script), "--force", "--timeout", "1", str(file)], env=env, capture_output=True, text=True, timeout=8)
        self.assertEqual(r.returncode, 1)
        self.assertIn("timed out", r.stdout)
        self.assertEqual(calls.read_text().splitlines(), [str(file)])
        self.assertEqual(Path(str(file) + "/..namedfork/rsrc").read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
