"""The lighting toggle reads the dashboard's state and sends the opposite."""
import contextlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
import socket
import threading
import unittest
from unittest import mock
from urllib import parse

import van_lighting

SOLDER = "switch.solder_flood"


def lights_payload(state="off", available=True):
    return {"ok": True, "lighting": {"groups": [
        {"id": "cab", "lights": [{"entity_id": "light.wiz_front_driver", "state": "on",
                                  "available": True, "label": "Front"}]},
        {"id": "solder", "lights": [{"entity_id": "light.solder_led", "state": "unavailable",
                                     "available": False, "label": "Solder LED"}],
         "power_switch": {"entity_id": SOLDER, "state": state, "available": available,
                          "label": "Solder power"}},
    ]}}


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _reply(self, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self.server.gets.append(self.path)
        self._reply(self.server.payload)

    def do_POST(self):
        length = int(self.headers["Content-Length"])
        form = parse.parse_qs(self.rfile.read(length).decode("ascii"))
        self.server.posts.append((self.path, form))
        self._reply(self.server.post_reply)


class DashboardServer:
    def __init__(self, payload, post_reply=None):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.server.payload = payload
        self.server.post_reply = post_reply or {"ok": True, "message": "Lights turned on"}
        self.server.gets, self.server.posts = [], []

    def __enter__(self):
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return self.server

    def __exit__(self, *args):
        self.server.shutdown()
        self.server.server_close()


class VanLightingTests(unittest.TestCase):
    def run_main(self, url, *args):
        stdout, stderr = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, {van_lighting.DASHBOARD_URL_ENV: url}), \
                mock.patch.object(van_lighting, "notify") as notify, \
                contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            result = van_lighting.main(["toggle", *args])
        return result, stdout.getvalue(), stderr.getvalue(), notify

    def test_toggles_power_switch_to_the_opposite_state(self):
        for state, wanted, value in (("off", "on", "true"), ("on", "off", "false")):
            with self.subTest(state=state), DashboardServer(lights_payload(state)) as server:
                result, stdout, _, notify = self.run_main(self.dashboard_url(server), SOLDER)
            self.assertEqual(result, 0)
            self.assertEqual(server.gets, ["/api/lights"])
            self.assertEqual(server.posts, [("/api/lights/power", {"target": [SOLDER], "value": [value]})])
            self.assertEqual(stdout, f"{SOLDER}: turned {wanted}\n")
            notify.assert_not_called()

    def test_refuses_unavailable_or_unknown_targets_without_posting(self):
        cases = ((lights_payload(available=False), SOLDER, "Solder power is unavailable"),
                 (lights_payload(), "light.solder_led", "Solder LED is unavailable"),
                 (lights_payload(), "switch.missing", "switch.missing is not in the dashboard"))
        for payload, target, message in cases:
            with self.subTest(target=target), DashboardServer(payload) as server:
                result, _, stderr, notify = self.run_main(self.dashboard_url(server), target)
            self.assertEqual(result, 1)
            self.assertEqual(server.posts, [])
            self.assertIn(message, stderr)
            self.assertIn(message, notify.call_args.args[0])

    def test_dashboard_refusal_is_reported(self):
        refusal = {"ok": False, "error": "could not update lights: timeout"}
        with DashboardServer(lights_payload(), refusal) as server:
            result, _, stderr, notify = self.run_main(self.dashboard_url(server), SOLDER)
        self.assertEqual(result, 1)
        self.assertIn("could not update lights: timeout", stderr)
        notify.assert_called_once()

    def test_unreachable_dashboard_is_reported(self):
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        with mock.patch.object(van_lighting, "HTTP_TIMEOUT_SECONDS", 0.5):
            result, _, stderr, notify = self.run_main(f"http://127.0.0.1:{port}", SOLDER)
        self.assertEqual(result, 1)
        self.assertIn("URLError", stderr)
        notify.assert_called_once()

    @staticmethod
    def dashboard_url(server):
        return f"http://127.0.0.1:{server.server_address[1]}"


if __name__ == "__main__":
    unittest.main()
