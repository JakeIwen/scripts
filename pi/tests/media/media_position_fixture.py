"""Fake Van TV and video-library servers plus a subprocess runner for media_position.py."""

import json
import os
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "media_position.py"

VANTV_PLAYING = {
    "ok": True, "instance": "inst1", "player": "PLAYING",
    "video": {"id": "abc123", "title": "A Cast"}, "position": 12.5,
    "duration": 100, "speed": 1.5,
}
VLC_PLAYING = {
    "ok": True,
    "player": {
        "available": True, "state": "PLAYING", "title": "A Movie", "position": 30.0,
        "duration": 200.0, "rate": 1.0, "can_seek": True, "item": {"id": "item9", "title": "A Movie"},
    },
}


def closed_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class FakeServices:
    """Two HTTP servers whose replies are plain attributes tests set.

    A reply is (status, body-dict-or-raw-bytes); `delay` sleeps before replying.
    """

    def __init__(self):
        self.vantv_status = (200, {"player": "STOPPED", "video": None})
        self.video_status = (200, VLC_PLAYING)
        self.vantv_control = (200, {"action": "seek", "done": True})
        self.video_position = (200, {"ok": True})
        self.vantv_delay = 0.0
        self.posts = []
        self.gets = []
        self.lock = threading.Lock()
        self.servers = []

    def _handler(self, kind):
        services = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def reply(self, spec):
                status, body = spec
                raw = body if isinstance(body, bytes) else json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self):
                with services.lock:
                    services.gets.append(kind)
                if kind == "vantv" and services.vantv_delay:
                    time.sleep(services.vantv_delay)
                self.reply(services.vantv_status if kind == "vantv" else services.video_status)

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length).decode()
                with services.lock:
                    services.posts.append((kind, self.path, self.headers.get("Content-Type"), body))
                self.reply(services.vantv_control if kind == "vantv" else services.video_position)

        return Handler

    def start(self):
        urls = {}
        for kind in ("vantv", "video"):
            server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler(kind))
            server.daemon_threads = True
            threading.Thread(target=server.serve_forever, daemon=True).start()
            self.servers.append(server)
            urls[kind] = "http://127.0.0.1:%d/api" % server.server_address[1]
        self.urls = urls
        return self

    def stop(self):
        for server in self.servers:
            server.shutdown()
            server.server_close()

    def env(self):
        env = dict(os.environ)
        env["MEDIA_POSITION_VANTV_URL"] = self.urls["vantv"]
        env["MEDIA_POSITION_VIDEO_URL"] = self.urls["video"]
        return env


def command(*args):
    return [sys.executable, "-I", str(SCRIPT)] + list(args)


def run_cli(services, *args, timeout=15):
    return subprocess.run(command(*args), env=services.env(), capture_output=True, text=True, timeout=timeout)


def sample_of(services):
    result = run_cli(services, "status")
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


class Serve:
    """A running `serve` process with line helpers."""

    def __init__(self, services, *args):
        self.process = subprocess.Popen(
            command("serve", *args), env=services.env(), stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)

    def line(self):
        return json.loads(self.process.stdout.readline())

    def next_of(self, kind):
        for _ in range(50):
            record = self.line()
            if record["type"] == kind:
                return record
        raise AssertionError("no %s line" % kind)

    def send(self, text):
        self.process.stdin.write(text if text.endswith("\n") else text + "\n")
        self.process.stdin.flush()

    def close(self):
        if self.process.poll() is None:
            self.process.kill()
        self.process.wait()
        for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
            try:
                stream.close()
            except (OSError, ValueError):
                pass
