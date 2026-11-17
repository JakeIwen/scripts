"""media_position.py serve: the JSON-lines stream, stdin seeks and its exit conditions."""

import json
import signal
import unittest

from pi.tests.media import media_position_fixture as fx


class ServeTests(unittest.TestCase):
    def setUp(self):
        self.services = fx.FakeServices().start()
        self.addCleanup(self.services.stop)
        self.services.vantv_status = (200, fx.VANTV_PLAYING)

    def serve(self, *args):
        server = fx.Serve(self.services, *args)
        self.addCleanup(server.close)
        return server

    def test_emits_samples_and_exits_on_stdin_eof(self):
        server = self.serve("--interval", "0.1")
        first, second = server.next_of("sample"), server.next_of("sample")
        self.assertEqual((first["source"], first["v"]), ("vantv", 1))
        self.assertGreaterEqual(second["t"], first["t"])
        server.process.stdin.close()
        self.assertEqual(server.process.wait(timeout=10), 0)

    def test_exits_when_stdout_is_closed(self):
        server = self.serve("--interval", "0.05")
        server.next_of("sample")
        server.process.stdout.close()
        self.assertEqual(server.process.wait(timeout=10), 0)

    def test_exits_after_max_seconds(self):
        server = self.serve("--interval", "0.1", "--max-seconds", "0.5")
        self.assertEqual(server.process.wait(timeout=10), 0)

    def test_exits_on_sigterm_and_sighup(self):
        for sig in (signal.SIGTERM, signal.SIGHUP):
            server = self.serve("--interval", "0.1")
            server.next_of("sample")
            server.process.send_signal(sig)
            self.assertEqual(server.process.wait(timeout=10), 0, sig)

    def test_seek_result_then_fresh_sample_and_routing(self):
        server = self.serve("--interval", "5")
        server.next_of("sample")
        server.send(json.dumps({"type": "seek", "req": 7, "position": 40, "source": "vantv",
                                "media_id": "vantv:inst1:abc123"}))
        result, after = server.line(), server.line()
        self.assertEqual(result, {"type": "seek_result", "req": 7, "ok": True, "message": result["message"]})
        self.assertEqual(after["type"], "sample")
        kind, path, _, body = self.services.posts[0]
        self.assertEqual((kind, path, json.loads(body)), ("vantv", "/api/control", {"action": "seek", "position": 40.0}))

    def test_vlc_seek_over_stdin(self):
        self.services.vantv_status = (200, {"player": "STOPPED"})
        server = self.serve("--interval", "5")
        server.next_of("sample")
        server.send(json.dumps({"type": "seek", "req": 1, "position": 60.0, "source": "vlc",
                                "media_id": "vlc:item:item9"}))
        self.assertTrue(server.next_of("seek_result")["ok"])
        self.assertEqual(self.services.posts[0][:2], ("video", "/api/position"))

    def test_mismatch_and_out_of_range_refused_with_sample_after(self):
        server = self.serve("--interval", "5")
        server.next_of("sample")
        bad = [
            {"position": 40, "source": "vlc", "media_id": "vlc:item:item9"},
            {"position": 40, "source": "vantv", "media_id": "vantv:inst1:zzz"},
            {"position": 1000, "source": "vantv", "media_id": "vantv:inst1:abc123"},
            {"position": -5, "source": "vantv", "media_id": "vantv:inst1:abc123"},
            {"position": "40", "source": "vantv", "media_id": "vantv:inst1:abc123"},
            {"position": 40, "source": "vantv", "media_id": 5},
        ]
        for req, fields in enumerate(bad):
            server.send(json.dumps(dict(fields, type="seek", req=req)))
            result = server.next_of("seek_result")
            self.assertEqual((result["req"], result["ok"]), (req, False), fields)
            self.assertEqual(server.line()["type"], "sample")
        server.send('{"type":"seek","req":99,"position":NaN,"source":"vantv","media_id":"vantv:inst1:abc123"}')
        self.assertFalse(server.next_of("seek_result")["ok"])
        self.assertEqual(self.services.posts, [])

    def test_malformed_lines_are_ignored_or_answered(self):
        server = self.serve("--interval", "0.2")
        server.next_of("sample")
        server.send("not json")
        server.send(json.dumps({"type": "seek"}))                 # no req: ignored
        server.send(json.dumps({"type": "bogus", "req": 3}))      # int req: answered
        server.send(json.dumps([1, 2]))
        result = server.next_of("seek_result")
        self.assertEqual((result["req"], result["ok"]), (3, False))
        self.assertEqual(server.next_of("sample")["type"], "sample")  # still serving
        server.process.stdin.close()
        server.process.wait(timeout=10)
        self.assertEqual(self.services.posts, [])
        self.assertEqual(server.process.returncode, 0)


if __name__ == "__main__":
    unittest.main()
