"""Behavioral contracts for the audiobook service."""
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest import mock

fake_soco = types.ModuleType("soco")
fake_soco.SoCo = object
fake_soco.discovery = types.ModuleType("soco.discovery")
fake_soco.discovery.discover = lambda timeout=5: set()
sys.modules.setdefault("soco", fake_soco)
sys.modules.setdefault("soco.discovery", fake_soco.discovery)

from pi.apps.audiobooks import audiobook_server as server


class AudiobookServerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.library = self.root / "library"
        self.library.mkdir()
        (self.library / "Solo Book.mp3").write_bytes(b"ID3fixture-audio")
        nested = self.library / "Series" / "Book 2"
        nested.mkdir(parents=True)
        (nested / "01 - Start.m4b").write_bytes(b"fixture-chapter-one")
        (nested / "02 - End.m4b").write_bytes(b"fixture-chapter-two")
        disc = self.library / "Series" / "Disc 1"
        disc.mkdir()
        (disc / "03 - Disc.mp3").write_bytes(b"fixture-disc")
        self.state_path = self.root / "state.json"
        for patch in (mock.patch.object(server, "LIBRARY", str(self.library)),
                      mock.patch.object(server, "STATE_PATH", str(self.state_path))):
            patch.start(); self.addCleanup(patch.stop)
        server.books = {}
        server.last_scan = 0
        server.state = {"books": {}, "device": None}
        server.zones = {}
        server.zones_ts = 0
        self.client = server.app.test_client()

    def test_scan_search_page_and_audio_range(self):
        refreshed = self.client.post("/api/rescan")
        self.assertEqual(refreshed.status_code, 200)
        self.assertEqual(refreshed.get_json()["count"], 3)
        books = self.client.get("/api/books").get_json()["books"]
        self.assertEqual(sorted(book["name"] for book in books),
                         ["Series", "Series - Book 2", "Solo Book"])
        matches = self.client.get("/api/search?q=seres+book+two").get_json()["matches"]
        self.assertEqual(matches[0]["name"], "Series - Book 2")
        page = self.client.get("/")
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"<title>Audiobooks</title>", page.data)
        manifest = self.client.get("/manifest.webmanifest")
        self.assertEqual(manifest.content_type, "application/manifest+json")
        book = next(book for book in server.books.values() if book.name == "Solo Book")
        response = self.client.get(f"/audio/{book.bid}/0.mp3", headers={"Range": "bytes=3-8"})
        self.assertEqual((response.status_code, response.data), (206, b"fixtur"))
        response.close()

    def test_missing_sonos_is_a_json_503_for_every_control_route(self):
        cases = [
            ("/api/toggle", {}),
            ("/api/pause", {}),
            ("/api/skip", {"s": "-30"}),
            ("/api/chapter", {"d": "1"}),
            ("/api/volume", {"d": "1"}),
        ]
        for path, data in cases:
            with self.subTest(path=path):
                response = self.client.post(path, data=data)
                self.assertEqual(response.status_code, 503)
                self.assertEqual(response.content_type, "application/json")
                self.assertEqual(response.get_json(),
                                 {"ok": False, "message": "no Sonos speakers found"})


if __name__ == "__main__":
    unittest.main()
