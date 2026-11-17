"""The watcher against the real pi/scripts/media_position.py `serve`, behind a fake ssh,
with fake Van TV and video-library HTTP servers."""
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from media_scrub_fixture import REAL_SERVE, ScrubCase

VLC = {"ok": True, "player": {"available": True, "state": "PLAYING", "title": "A Movie",
                              "position": 30.0, "duration": 200.0, "rate": 1.0, "can_seek": True,
                              "item": {"id": "item9", "title": "A Movie"}}}


class FakePi:
    """Replies are attributes; every POST is recorded."""

    def __init__(self):
        self.vantv_status = (200, {"player": "STOPPED", "video": None})
        self.video_status = (200, VLC)
        self.posts = []
        self.servers = []
        owner = self

        def handler(kind):
            class Handler(BaseHTTPRequestHandler):
                def log_message(self, *args):
                    pass

                def reply(self, status, body):
                    raw = json.dumps(body).encode()
                    self.send_response(status)
                    self.send_header("Content-Length", str(len(raw)))
                    self.end_headers()
                    self.wfile.write(raw)

                def do_GET(self):
                    self.reply(*(owner.vantv_status if kind == "vantv" else owner.video_status))

                def do_POST(self):
                    body = self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode()
                    owner.posts.append((kind, self.path, body))
                    self.reply(200, {"ok": True} if kind == "video" else {"action": "seek", "done": True})
            return Handler

        self.urls = {}
        for kind in ("vantv", "video"):
            server = ThreadingHTTPServer(("127.0.0.1", 0), handler(kind))
            server.daemon_threads = True
            threading.Thread(target=server.serve_forever, daemon=True).start()
            self.servers.append(server)
            self.urls[kind] = "http://127.0.0.1:%d/api" % server.server_address[1]

    def stop(self):
        for server in self.servers:
            server.shutdown()
            server.server_close()


class RealServe(ScrubCase):
    def setUp(self):
        super().setUp()
        self.pi = FakePi()
        self.addCleanup(self.pi.stop)
        self.set_ssh(self.write_ssh("real-ssh", 'exec "/usr/bin/python3" -I "%s" serve --interval 0.2' % REAL_SERVE))

    def env(self, **extra):
        return super().env(MEDIA_POSITION_VANTV_URL=self.pi.urls["vantv"],
                           MEDIA_POSITION_VIDEO_URL=self.pi.urls["video"], **extra)

    def test_status_and_seek_through_the_real_serve(self):
        status = self.status(wait=8)
        self.assertEqual((status["source"], status["media_id"], status["seekable"]),
                         ("vlc", "vlc:item:item9", True))
        code, result = self.seek(position=120.0, media_id="vlc:item:item9")
        self.assertEqual((code, result["ok"]), (0, True), result)
        self.assertEqual([(kind, path) for kind, path, _ in self.pi.posts], [("video", "/api/position")])
        ack = self.wait_for(lambda: self.status(wait=0)["seek_ack"], 5, "ack")
        self.assertEqual((ack["page"], ack["seq"], ack["ok"]), ("p1", 1, True))

    def test_the_pi_refuses_a_media_change_the_mac_could_not_see(self):
        self.start_watcher()
        # The player changes between the Mac's last sample and the Pi's re-read.
        changed = json.loads(json.dumps(VLC))
        changed["player"]["item"]["id"] = "other"
        self.pi.video_status = (200, changed)
        code, result = self.seek(position=10.0, seq=2, media_id="vlc:item:item9")
        self.assertEqual(code, 1)
        self.assertFalse(result["ok"])

    def test_van_tv_failure_is_unknown_and_not_ok(self):
        self.pi.vantv_status = (503, {"error": "busy"})
        status = self.status(wait=3)
        self.assertFalse(status["ok"])
        self.assertEqual(status["source"], "unknown")
        self.assertFalse(status["seekable"])


if __name__ == "__main__":
    unittest.main()
