"""Behavior tests for phone_drop: run with
python3 -m unittest discover -s pi/projects/phone-drop
"""

from __future__ import annotations

import http.client
import os
import tempfile
import threading
import unittest
import uuid
from pathlib import Path
from unittest import mock

from werkzeug.serving import make_server

import phone_drop

TOKEN = "t" * 40


def multipart(parts):
    """Encode (field, filename, content_type, data) tuples as multipart/form-data."""
    boundary = uuid.uuid4().hex
    body = bytearray()
    for field, filename, content_type, data in parts:
        body += f"--{boundary}\r\n".encode()
        disposition = f'form-data; name="{field}"'
        if filename is not None:
            disposition += f'; filename="{filename}"'
        body += f"Content-Disposition: {disposition}\r\n".encode()
        if content_type:
            body += f"Content-Type: {content_type}\r\n".encode()
        body += b"\r\n" + data + b"\r\n"
    body += f"--{boundary}--\r\n".encode()
    return bytes(body), f"multipart/form-data; boundary={boundary}"


class PhoneDropServerTest(unittest.TestCase):
    quota_bytes = 1 << 20
    min_free_bytes = 0

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.inbox = Path(self.tmp.name) / "from-phone"
        self.inbox.mkdir()
        settings = phone_drop.Settings(
            inbox=self.inbox,
            token=TOKEN,
            quota_bytes=self.quota_bytes,
            min_free_bytes=self.min_free_bytes,
        )
        self.server = make_server(
            "127.0.0.1", 0, phone_drop.PhoneDrop(settings, clock=lambda: 0.0), threaded=True
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(5)
        self.tmp.cleanup()

    def request(self, method, path, body=b"", headers=None, token=TOKEN):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=10)
        headers = dict(headers or {})
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        text = response.read().decode()
        connection.close()
        return response.status, text

    def upload(self, parts, **kwargs):
        body, content_type = multipart(parts)
        return self.request("POST", "/upload", body, {"Content-Type": content_type}, **kwargs)

    def inbox_files(self):
        return sorted(p.name for p in self.inbox.iterdir() if p.is_file())

    def assert_no_partials(self):
        partial_dir = self.inbox / phone_drop.PARTIAL_DIR_NAME
        leftovers = list(partial_dir.iterdir()) if partial_dir.exists() else []
        self.assertEqual(leftovers, [])


class UploadTests(PhoneDropServerTest):
    def test_health_needs_no_token(self):
        status, text = self.request("GET", "/health", token=None)
        self.assertEqual(status, 200)

    def test_missing_and_wrong_tokens_are_refused(self):
        part = [("file", "a.txt", "text/plain", b"hello")]
        for token in (None, "wrong-token"):
            status, text = self.upload(part, token=token)
            self.assertEqual(status, 401)
        self.assertEqual(self.inbox_files(), [])

    def test_saves_file_with_exact_bytes(self):
        data = os.urandom(200_000)
        status, text = self.upload([("file", "IMG_3454.HEIC", "image/heic", data)])
        self.assertEqual(status, 200, text)
        self.assertEqual((self.inbox / "IMG_3454.HEIC").read_bytes(), data)
        self.assert_no_partials()

    def test_never_overwrites_existing_names(self):
        (self.inbox / "note.txt").write_text("original")
        for _ in range(2):
            status, _text = self.upload([("file", "note.txt", "text/plain", b"new")])
            self.assertEqual(status, 200)
        self.assertEqual(self.inbox_files(), ["note (2).txt", "note (3).txt", "note.txt"])
        self.assertEqual((self.inbox / "note.txt").read_text(), "original")

    def test_client_names_cannot_escape_or_hide(self):
        parts = [
            ("file", "../../escape.txt", "text/plain", b"1"),
            ("file", ".bashrc", "text/plain", b"2"),
            ("file", "a:b?.txt", "text/plain", b"3"),
        ]
        status, text = self.upload(parts)
        self.assertEqual(status, 200, text)
        self.assertEqual(self.inbox_files(), ["ab.txt", "bashrc", "escape.txt"])
        self.assertFalse((Path(self.tmp.name) / "escape.txt").exists())

    def test_saves_every_file_part_in_one_request(self):
        parts = [
            ("file", "one.jpg", "image/jpeg", b"a" * 10),
            ("other", "two.mov", "video/quicktime", b"b" * 20),
            ("caption", None, None, b"form text is ignored"),
        ]
        status, text = self.upload(parts)
        self.assertEqual(status, 200, text)
        self.assertEqual(self.inbox_files(), ["one.jpg", "two.mov"])

    def test_raw_body_uses_the_filename_header(self):
        status, text = self.request(
            "POST",
            "/upload",
            b"%PDF-1.7 body",
            {"Content-Type": "application/pdf", "X-Filename": "Trip%20Receipt.pdf"},
        )
        self.assertEqual(status, 200, text)
        self.assertEqual(self.inbox_files(), ["Trip Receipt.pdf"])

    def test_unnamed_raw_body_gets_a_dated_name_from_its_type(self):
        status, text = self.request("POST", "/upload", b"\xff\xd8", {"Content-Type": "image/jpeg"})
        self.assertEqual(status, 200, text)
        [name] = self.inbox_files()
        self.assertRegex(name, r"^upload-\d{8}-\d{6}\.jpg$")

    def test_request_without_a_file_is_rejected(self):
        status, text = self.upload([("caption", None, None, b"just text")])
        self.assertEqual(status, 400)
        status, _text = self.request("POST", "/upload", b"", {"Content-Type": "image/jpeg"})
        self.assertEqual(status, 400)
        self.assertEqual(self.inbox_files(), [])
        self.assert_no_partials()

    def test_quota_refuses_up_front_and_keeps_inbox_unchanged(self):
        (self.inbox / "existing.bin").write_bytes(b"x" * (self.quota_bytes - 10))
        status, text = self.upload([("file", "big.bin", None, b"y" * (2 << 20))])
        self.assertEqual(status, 413)
        self.assertEqual(self.inbox_files(), ["existing.bin"])
        self.assert_no_partials()

    def test_quota_is_enforced_while_streaming(self):
        # The body fits inside the up-front framing slack, so only the streaming
        # budget can catch it; the second part must not survive either.
        (self.inbox / "existing.bin").write_bytes(b"x" * (self.quota_bytes - 100))
        parts = [("file", "a.bin", None, b"a" * 50), ("file", "b.bin", None, b"b" * 500)]
        status, text = self.upload(parts)
        self.assertEqual(status, 413, text)
        self.assertEqual(self.inbox_files(), ["existing.bin"])
        self.assert_no_partials()


class FreeSpaceFloorTests(PhoneDropServerTest):
    quota_bytes = 1 << 40
    min_free_bytes = 1 << 30

    def test_free_space_floor_refuses_upload(self):
        usage = mock.Mock(free=(1 << 30) + 5)
        with mock.patch.object(phone_drop.shutil, "disk_usage", return_value=usage):
            status, text = self.upload([("file", "a.bin", None, b"z" * 100)])
        self.assertEqual(status, 413)
        self.assertEqual(self.inbox_files(), [])


class FilenameTests(unittest.TestCase):
    def test_long_names_keep_their_extension_within_the_byte_limit(self):
        name = phone_drop.safe_filename("é" * 300 + ".jpeg", None, 0)
        self.assertTrue(name.endswith(".jpeg"))
        self.assertLessEqual(len(name.encode("utf-8")), phone_drop.MAX_NAME_BYTES)

    def test_dot_only_names_fall_back_to_a_dated_name(self):
        for raw in (None, "", ".", "..", "  "):
            self.assertTrue(phone_drop.safe_filename(raw, "video/quicktime", 0).endswith(".mov"))


class TokenFileTests(unittest.TestCase):
    def test_rejects_readable_or_short_tokens(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "token"
            path.write_text(TOKEN)
            os.chmod(path, 0o644)
            with self.assertRaises(SystemExit):
                phone_drop.load_token(path)
            os.chmod(path, 0o600)
            self.assertEqual(phone_drop.load_token(path), TOKEN)
            path.write_text("short")
            with self.assertRaises(SystemExit):
                phone_drop.load_token(path)


if __name__ == "__main__":
    unittest.main()
