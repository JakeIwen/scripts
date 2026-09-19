"""macOS integration tests. Build app first; exiftool is a test-only dependency."""
from concurrent.futures import ThreadPoolExecutor
import base64
import hashlib
import json
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest
import unicodedata
import zlib

ROOT = Path(__file__).resolve().parents[1]
BINARY = ROOT / "build/Strip Metadata.app/Contents/MacOS/StripMetadata"
EXIFTOOL = shutil.which("exiftool") or "/opt/homebrew/bin/exiftool"


def command(*args, check=True):
    return subprocess.run([str(a) for a in args], capture_output=True, text=True, check=check)


def png(path):
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    # Opaque red left half, transparent right half. Non-square for orientation checks.
    raw = b"".join(b"\0" + b"\xff\0\0\xff" * 20 + b"\0\0\xff\0" * 20 for _ in range(24))
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 40, 24, 8, 6, 0, 0, 0))
                     + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def tags(path):
    return json.loads(command(EXIFTOOL, "-j", "-G1", "-n", path).stdout)[0]


def rgba_pixels(path):
    data = path.read_bytes()
    position, compressed = 8, b""
    while position < len(data):
        length = struct.unpack(">I", data[position:position + 4])[0]
        kind = data[position + 4:position + 8]
        chunk = data[position + 8:position + 8 + length]
        if kind == b"IHDR":
            width, height, depth, color, _, _, interlace = struct.unpack(">IIBBBBB", chunk)
            assert (depth, color, interlace) == (8, 6, 0)
        if kind == b"IDAT":
            compressed += chunk
        position += length + 12
    decoded = zlib.decompress(compressed)
    stride, previous, pixels = width * 4, bytearray(width * 4), []
    for y in range(height):
        start = y * (stride + 1)
        method = decoded[start]
        row = bytearray(decoded[start + 1:start + 1 + stride])
        for x in range(stride):
            left = row[x - 4] if x >= 4 else 0
            above = previous[x]
            upper_left = previous[x - 4] if x >= 4 else 0
            p = left + above - upper_left
            nearest = min((left, above, upper_left), key=lambda v: abs(p - v))
            predictor = (0, left, above, (left + above) // 2, nearest)[method]
            row[x] = (row[x] + predictor) % 256
        pixels.extend(tuple(row[x:x + 4]) for x in range(0, stride, 4))
        previous = row
    return width, height, pixels


class StripTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="strip-images-")
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.input = self.folder / "image $(touch NEVER) ' café.png"
        png(self.input)

    def strip(self, path):
        return Path(command(BINARY, path).stdout.strip())

    def test_metadata_and_original_preservation_across_formats(self):
        for fmt, ext in [("png", "png"), ("jpeg", "jpg"), ("tiff", "tiff"), ("gif", "gif"),
                         ("bmp", "bmp")]:
            with self.subTest(fmt=fmt):
                src = self.folder / f"private.{ext}"
                command("/usr/bin/sips", "-s", "format", fmt, self.input, "--out", src)
                if fmt not in ("bmp",):
                    command(EXIFTOOL, "-overwrite_original", "-XMP-dc:Creator=PRIVATE_SENTINEL",
                            "-XMP-dc:Description=PRIVATE_SENTINEL", src)
                    self.assertIn("PRIVATE_SENTINEL", json.dumps(tags(src)))
                if fmt in ("jpeg", "tiff", "heic", "png"):
                    command(EXIFTOOL, "-overwrite_original", "-GPSLatitude=43.5", "-GPSLongitude=-91.2",
                            "-Make=PRIVATE_SENTINEL", "-Model=PRIVATE_SENTINEL",
                            "-DateTimeOriginal=2021:04:05 06:07:08", src)
                    self.assertTrue(any("GPSLatitude" in k for k in tags(src)))
                command("/usr/bin/xattr", "-w", "com.example.private-test", "PRIVATE_SENTINEL", src)
                before = hashlib.sha256(src.read_bytes()).hexdigest()
                out = self.strip(src)
                self.assertEqual(out, src.with_name(f"private_stripped.{ext}"))
                metadata = tags(out)
                self.assertNotIn("PRIVATE_SENTINEL", json.dumps(metadata))
                self.assertNotIn(b"PRIVATE_SENTINEL", out.read_bytes())
                for key in metadata:
                    self.assertFalse(any(word in key for word in ("GPS", "DateTimeOriginal", "SerialNumber", "OwnerName")), key)
                self.assertNotIn("com.example.private-test", command("/usr/bin/xattr", out).stdout)
                self.assertEqual(before, hashlib.sha256(src.read_bytes()).hexdigest())

    def test_orientation_is_baked_into_dimensions(self):
        src = self.folder / "portrait.jpg"
        command("/usr/bin/sips", "-s", "format", "jpeg", self.input, "--out", src)
        command(EXIFTOOL, "-overwrite_original", "-Orientation#=6", src)
        after = tags(self.strip(src))
        self.assertEqual(after["File:ImageWidth"], 24)
        self.assertEqual(after["File:ImageHeight"], 40)
        self.assertIn(after.get("IFD0:Orientation", 1), (1,))

    def test_png_pixels_dimensions_and_transparency(self):
        width, height, pixels = rgba_pixels(self.strip(self.input))
        self.assertEqual((width, height), (40, 24))
        for y in range(height):
            self.assertEqual(pixels[y * width], (255, 0, 0, 255))
            self.assertEqual(pixels[y * width + 39][3], 0)

    def test_read_only_webp_becomes_png(self):
        src = self.folder / "web.webp"
        src.write_bytes(base64.b64decode('UklGRiIAAABXRUJQVlA4IBYAAAAwAQCdASoBAAEADsD+JaQAA3AAAAAA'))
        command(EXIFTOOL, "-overwrite_original", "-XMP-dc:Creator=PRIVATE_SENTINEL", src)
        self.assertIn("PRIVATE_SENTINEL", json.dumps(tags(src)))
        output = self.strip(src)
        self.assertEqual(output.name, "web_stripped.png")
        self.assertNotIn(b"PRIVATE_SENTINEL", output.read_bytes())

    def test_collision_and_special_filename(self):
        first = self.strip(self.input)
        before = first.read_bytes()
        second = self.strip(self.input)
        self.assertEqual(unicodedata.normalize("NFC", first.name), self.input.stem + "_stripped.png")
        self.assertEqual(unicodedata.normalize("NFC", second.name), self.input.stem + "_stripped_2.png")
        self.assertEqual(first.read_bytes(), before)

    def test_concurrent_copies_never_clobber(self):
        with ThreadPoolExecutor(max_workers=3) as pool:
            outputs = list(pool.map(self.strip, [self.input] * 3))
        self.assertEqual(len(set(outputs)), 3)
        self.assertTrue(all(p.is_file() for p in outputs))

    def test_invalid_file_and_symlink_are_rejected(self):
        broken = self.folder / "bad.jpg"
        broken.write_text("not an image")
        link = self.folder / "link.png"
        link.symlink_to(self.input)
        for src in (broken, link, self.folder):
            self.assertNotEqual(command(BINARY, src, check=False).returncode, 0)
        self.assertFalse(list(self.folder.glob("*_stripped*")))
        self.assertFalse(list(self.folder.glob(".strip-*")))

    def test_batch_continues_after_bad_file(self):
        result = command(BINARY, self.folder / "missing.png", self.input, check=False)
        self.assertEqual(result.returncode, 1)
        self.assertTrue(Path(result.stdout.strip()).is_file())

    def test_heic_when_encoder_is_available(self):
        src = self.folder / "phone.heic"
        conversion = command("/usr/bin/sips", "-s", "format", "heic", self.input, "--out", src, check=False)
        if conversion.returncode:
            self.skipTest("macOS HEIC encoder unavailable in this environment: " + conversion.stderr.strip())
        command(EXIFTOOL, "-overwrite_original", "-GPSLatitude=43.5", "-Make=PRIVATE_SENTINEL", src)
        output = self.strip(src)
        self.assertEqual(output.suffix, ".heic")
        metadata = json.dumps(tags(output))
        self.assertNotIn("PRIVATE_SENTINEL", metadata)
        self.assertNotIn("GPSLatitude", metadata)

    def test_animated_gif_keeps_frames_and_timing(self):
        src = self.folder / "animation.gif"
        # Two full-canvas 1x1 frames, delays 0.1/0.2s, indefinite looping.
        header = b"GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff"
        loop = b"\x21\xff\x0bNETSCAPE2.0\x03\x01\x00\x00\x00"
        def frame(delay, pixel):
            return (b"\x21\xf9\x04\x00" + struct.pack("<H", delay) + b"\x00\x00"
                    + b"\x2c\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02"
                    + bytes([0x44 if pixel == 0 else 0x4c, 0x01]) + b"\x00")
        src.write_bytes(header + loop + frame(10, 0) + frame(20, 1) + b"\x3b")
        command(EXIFTOOL, "-overwrite_original", "-Comment=PRIVATE_SENTINEL", src)
        original = tags(src)
        result = tags(self.strip(src))
        self.assertEqual(result["GIF:FrameCount"], 2)
        self.assertEqual(result["GIF:Duration"], original["GIF:Duration"])
        self.assertEqual(result["GIF:AnimationIterations"], original["GIF:AnimationIterations"])
        self.assertNotIn("PRIVATE_SENTINEL", json.dumps(result))


if __name__ == "__main__":
    unittest.main()
