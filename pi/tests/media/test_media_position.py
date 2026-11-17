"""media_position.py: sample precedence, record shape and the one-shot seek, via its CLI."""

import unittest

from pi.tests.media import media_position_fixture as fx


class SampleTests(unittest.TestCase):
    def setUp(self):
        self.services = fx.FakeServices().start()
        self.addCleanup(self.services.stop)

    def sample(self):
        return fx.sample_of(self.services)

    def test_vantv_active_wins_and_skips_the_video_library(self):
        self.services.vantv_status = (200, fx.VANTV_PLAYING)
        sample = self.sample()
        self.assertEqual(
            (sample["type"], sample["v"], sample["source"], sample["state"], sample["title"]),
            ("sample", 1, "vantv", "PLAYING", "A Cast"))
        self.assertEqual((sample["position"], sample["duration"], sample["rate"]), (12.5, 100.0, 1.5))
        self.assertEqual((sample["media_id"], sample["can_seek"], sample["error"]), ("vantv:inst1:abc123", True, None))
        self.assertEqual(self.services.gets, ["vantv"])

    def test_vlc_when_vantv_inactive(self):
        sample = self.sample()
        self.assertEqual((sample["source"], sample["state"], sample["media_id"]), ("vlc", "PLAYING", "vlc:item:item9"))
        self.assertEqual((sample["rate"], sample["can_seek"], sample["title"]), (1.0, True, "A Movie"))

    def test_vlc_when_vantv_refuses_connections(self):
        env = self.services.env()
        env["MEDIA_POSITION_VANTV_URL"] = "http://127.0.0.1:%d/api" % fx.closed_port()
        self.services.env = lambda: env
        self.assertEqual(self.sample()["source"], "vlc")

    def test_vantv_loading_is_active_but_not_seekable(self):
        self.services.vantv_status = (200, dict(fx.VANTV_PLAYING, player="LOADING"))
        sample = self.sample()
        self.assertEqual((sample["source"], sample["can_seek"]), ("vantv", False))

    def test_unknown_on_503_bad_json_and_timeout_without_vlc_fallback(self):
        for reply in ((503, {"error": "Video store unavailable"}), (200, b"not json"), (200, [1, 2])):
            self.services.vantv_status = reply
            self.services.gets.clear()
            sample = self.sample()
            self.assertEqual((sample["source"], sample["can_seek"], sample["media_id"]), ("unknown", False, None), reply)
            self.assertTrue(sample["error"])
            self.assertEqual(self.services.gets, ["vantv"], reply)

    def test_unknown_on_timeout(self):
        self.services.vantv_status = (200, fx.VANTV_PLAYING)
        self.services.vantv_delay = 2.5
        sample = self.sample()
        self.assertEqual(sample["source"], "unknown")

    def test_nothing_playing(self):
        self.services.video_status = (200, {"player": {"available": False, "state": "OFFLINE"}})
        self.assertEqual(self.sample()["source"], "none")
        self.services.video_status = (200, {"player": dict(fx.VLC_PLAYING["player"], state="STOPPED")})
        self.assertEqual(self.sample()["source"], "none")

    def test_vlc_without_catalog_item_has_no_media_id(self):
        player = dict(fx.VLC_PLAYING["player"], item=None)
        self.services.video_status = (200, {"player": player})
        sample = self.sample()
        self.assertEqual((sample["source"], sample["media_id"]), ("vlc", None))

    def test_vlc_rate_and_paused(self):
        player = dict(fx.VLC_PLAYING["player"], rate=2.0, state="PAUSED")
        self.services.video_status = (200, {"player": player})
        sample = self.sample()
        self.assertEqual((sample["rate"], sample["state"]), (2.0, "PAUSED"))


class OneShotSeekTests(unittest.TestCase):
    def setUp(self):
        self.services = fx.FakeServices().start()
        self.addCleanup(self.services.stop)

    def seek(self, position, source, media_id):
        return fx.run_cli(self.services, "seek", position, "--source", source, "--media-id", media_id)

    def test_vantv_seek_posts_control_json(self):
        self.services.vantv_status = (200, fx.VANTV_PLAYING)
        result = self.seek("40", "vantv", "vantv:inst1:abc123")
        self.assertEqual(result.returncode, 0, result.stdout)
        kind, path, content_type, body = self.services.posts[0]
        self.assertEqual((kind, path, content_type), ("vantv", "/api/control", "application/json"))
        self.assertEqual(__import__("json").loads(body), {"action": "seek", "position": 40.0})
        self.assertEqual(len(self.services.posts), 1)

    def test_vlc_seek_posts_form_position(self):
        result = self.seek("45.5", "vlc", "vlc:item:item9")
        self.assertEqual(result.returncode, 0, result.stdout)
        kind, path, content_type, body = self.services.posts[0]
        self.assertEqual((kind, path, body), ("video", "/api/position", "position=45.5"))
        self.assertIn("x-www-form-urlencoded", content_type)

    def test_refusals_send_no_post(self):
        self.services.vantv_status = (200, fx.VANTV_PLAYING)
        cases = [
            ("40", "vlc", "vlc:item:item9"),            # source mismatch
            ("40", "vantv", "vantv:inst1:other"),       # media changed
            ("40", "vantv", "vantv:inst2:abc123"),      # receiver restarted
            ("101", "vantv", "vantv:inst1:abc123"),     # beyond duration
            ("-1", "vantv", "vantv:inst1:abc123"),      # negative
            ("nan", "vantv", "vantv:inst1:abc123"),     # non-finite
            ("inf", "vantv", "vantv:inst1:abc123"),
        ]
        for position, source, media_id in cases:
            result = self.seek(position, source, media_id)
            self.assertNotEqual(result.returncode, 0, (position, source, media_id, result.stdout, result.stderr))
        self.assertEqual(self.services.posts, [])

    def test_unseekable_vlc_without_catalog_item_is_refused(self):
        player = dict(fx.VLC_PLAYING["player"], item=None)
        self.services.video_status = (200, {"player": player})
        self.assertEqual(self.seek("10", "vlc", "vlc:item:item9").returncode, 1)
        self.assertEqual(self.services.posts, [])

    def test_failure_bodies_are_failures(self):
        self.services.vantv_status = (200, fx.VANTV_PLAYING)
        for reply in ((200, {"action": "seek", "done": False}), (200, {"done": True}),
                      (200, {"ok": True}), (400, {"error": "Seek needs one of position"}),
                      (200, b"garbage")):
            self.services.vantv_control = reply
            result = self.seek("40", "vantv", "vantv:inst1:abc123")
            self.assertEqual(result.returncode, 1, reply)
        self.services.vantv_status = (200, {"player": "STOPPED"})
        self.services.video_position = (409, {"ok": False, "message": "the current VLC track is not seekable"})
        result = self.seek("40", "vlc", "vlc:item:item9")
        self.assertEqual(result.returncode, 1)
        self.assertIn("not seekable", result.stdout)
        self.services.video_position = (200, {"ok": False})
        self.assertEqual(self.seek("40", "vlc", "vlc:item:item9").returncode, 1)


if __name__ == "__main__":
    unittest.main()
