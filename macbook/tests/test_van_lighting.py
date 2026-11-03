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

    def _reply(self, payload, status=200):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
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
        self._reply(self.server.post_reply, self.server.post_status)


class DashboardServer:
    """A fake dashboard; inside `with`, it exposes url, gets and posts."""

    def __init__(self, payload, post_reply=None, post_status=200):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.server.payload = payload
        self.server.post_reply = post_reply or dict(lights_payload("on"), message="Lights turned on")
        self.server.post_status = post_status
        self.server.gets, self.server.posts = [], []
        self.server.url = f"http://127.0.0.1:{self.server.server_address[1]}"

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
                result, stdout, _, notify = self.run_main(server.url, SOLDER)
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
                result, _, stderr, notify = self.run_main(server.url, target)
            self.assertEqual(result, 1)
            self.assertEqual(server.posts, [])
            self.assertIn(message, stderr)
            self.assertIn(message, notify.call_args.args[0])

    def test_dashboard_refusal_is_reported(self):
        refusal = {"ok": False, "message": "could not update lights: timeout"}
        with DashboardServer(lights_payload(), refusal) as server:
            result, _, stderr, notify = self.run_main(server.url, SOLDER)
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

    def run_json(self, url, *args):
        stdout = io.StringIO()
        with mock.patch.dict(os.environ, {van_lighting.DASHBOARD_URL_ENV: url}), \
                mock.patch.object(van_lighting, "notify") as notify, contextlib.redirect_stdout(stdout):
            result = van_lighting.main(list(args))
        notify.assert_not_called()
        return result, json.loads(stdout.getvalue())

    def test_lights_lists_every_light_with_its_group_but_no_power_switches(self):
        with DashboardServer(lights_payload()) as server:
            result, output = self.run_json(server.url, "lights")
        self.assertEqual(result, 0)
        self.assertEqual([(light["entity_id"], light["group"]) for light in output["lights"]],
                         [("light.wiz_front_driver", "cab"), ("light.solder_led", "solder")])
        self.assertEqual(set(output["lights"][0]), set(van_lighting.LIGHT_FIELDS) | {"group", "group_label"})
        self.assertEqual(server.posts, [])

    def test_set_applies_power_colour_then_brightness_to_each_light(self):
        lights = ("light.wiz_dresser", "light.wiz_werkbench")
        with DashboardServer(lights_payload()) as server:
            result, output = self.run_json(server.url, "set", *lights, "--on", "--hue", "30",
                                           "--saturation", "60", "--brightness", "70")
        self.assertEqual(result, 0)
        self.assertEqual(server.gets, [])
        self.assertEqual(server.posts, [call for light in lights for call in (
            ("/api/lights/power", {"target": [light], "value": ["true"]}),
            ("/api/lights/hue", {"entity": [light], "hue": ["30"], "saturation": ["60"]}),
            ("/api/lights/brightness", {"entity": [light], "brightness": ["70"]}))])
        self.assertEqual(output["lights"][1]["entity_id"], "light.solder_led")

    def test_set_sends_white_temperature_and_omits_unset_saturation(self):
        with DashboardServer(lights_payload()) as server:
            self.run_json(server.url, "set", "light.wiz_dresser", "--kelvin", "2700")
            self.run_json(server.url, "set", "light.wiz_dresser", "--hue", "200")
        self.assertEqual(server.posts, [
            ("/api/lights/color-temperature", {"entity": ["light.wiz_dresser"], "kelvin": ["2700"]}),
            ("/api/lights/hue", {"entity": ["light.wiz_dresser"], "hue": ["200"]})])

    def test_set_rejects_incomplete_or_conflicting_arguments_before_any_request(self):
        for args in (["light.a"], ["light.a", "--saturation", "50"], ["light.a", "--hue", "1", "--kelvin", "3000"],
                     ["light.a", "--brightness", "0"], ["light.a", "--on", "--off"]):
            with self.subTest(args=args), contextlib.redirect_stderr(io.StringIO()), \
                    self.assertRaises(SystemExit) as exit:
                van_lighting.main(["set", *args])
            self.assertEqual(exit.exception.code, 2)

    def test_a_dashboard_refusal_reports_its_own_error_text(self):
        refusal = {"ok": False, "message": "saturation must be from 0 to 100"}
        with DashboardServer(lights_payload(), refusal, post_status=400) as server:
            result, output = self.run_json(server.url, "set", "light.wiz_dresser", "--hue", "10")
        self.assertEqual(result, 1)
        self.assertEqual(output, {"ok": False, "error": "DashboardError: saturation must be from 0 to 100"})

    def test_set_failure_prints_a_json_error_without_notifying(self):
        refusal = {"ok": False, "message": "could not set light hue: timeout"}
        with DashboardServer(lights_payload(), refusal) as server:
            result, output = self.run_json(server.url, "set", "light.wiz_dresser", "--hue", "10")
        self.assertEqual(result, 1)
        self.assertEqual(output, {"ok": False, "error": "DashboardError: could not set light hue: timeout"})



if __name__ == "__main__":
    unittest.main()
