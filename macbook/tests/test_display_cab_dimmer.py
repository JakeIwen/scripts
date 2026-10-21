"""Display plug-in tracking and cab-light writes for display-cab-dimmer."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
import unittest
from unittest import mock
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SOURCE = Path(__file__).resolve().parents[1] / "scripts/display_cab_dimmer.py"
SPEC = importlib.util.spec_from_file_location("display_cab_dimmer", SOURCE)
dimmer = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = dimmer  # dataclasses resolve annotations through sys.modules
SPEC.loader.exec_module(dimmer)

TARGET_ONE = dimmer.DisplayInfo(2, "Q24h-10 (1)", False, 12462, 26280, 1)
TARGET_TWO = dimmer.DisplayInfo(3, "Q24h-10 (2)", False, 12462, 26280, 2)
BUILT_IN = dimmer.DisplayInfo(1, "Built-in Retina Display", True, 1552, 41055, 1)
TV = dimmer.DisplayInfo(4, "SAMSUNG", False, 19501, 1, 1)


class PlugTrackerTests(unittest.TestCase):
    def setUp(self):
        self.empty = dimmer.matching_snapshot([BUILT_IN])
        self.one = dimmer.matching_snapshot([BUILT_IN, TARGET_ONE])
        self.two = dimmer.matching_snapshot([BUILT_IN, TARGET_ONE, TARGET_TWO])

    def test_starting_snapshot_is_only_a_baseline(self):
        tracker = dimmer.PlugTracker(self.one)
        self.assertFalse(tracker.evaluate(self.one, 0, fallback=True))

    def test_a_display_appearing_fires_once(self):
        tracker = dimmer.PlugTracker(self.empty)
        tracker.schedule(0, 3)
        self.assertFalse(tracker.evaluate(self.one, 2.9))
        self.assertTrue(tracker.evaluate(self.one, 3))
        self.assertFalse(tracker.evaluate(self.one, 23, fallback=True))

    def test_two_displays_appearing_within_one_window_fire_once(self):
        tracker = dimmer.PlugTracker(self.empty)
        tracker.schedule(0, 3)
        tracker.schedule(1, 3)
        self.assertFalse(tracker.evaluate(self.two, 3))
        self.assertTrue(tracker.evaluate(self.two, 4))
        self.assertFalse(tracker.evaluate(self.two, 24, fallback=True))

    def test_removal_then_readd_fires_again(self):
        tracker = dimmer.PlugTracker(self.one)
        tracker.schedule(0, 1)
        self.assertFalse(tracker.evaluate(self.empty, 1))
        tracker.schedule(2, 1)
        self.assertTrue(tracker.evaluate(self.one, 3))

    def test_repeated_callbacks_push_the_deadline(self):
        tracker = dimmer.PlugTracker(self.empty)
        tracker.schedule(0, 3)
        tracker.schedule(2.5, 3)
        self.assertFalse(tracker.evaluate(self.one, 3))
        self.assertFalse(tracker.evaluate(self.one, 5.4))
        self.assertTrue(tracker.evaluate(self.one, 5.5))

    def test_sleep_then_wake_with_same_set_does_not_fire(self):
        tracker = dimmer.PlugTracker(self.one)
        tracker.will_sleep()
        self.assertFalse(tracker.evaluate(self.empty, 100, fallback=True))
        tracker.did_wake(100, 3)
        self.assertFalse(tracker.evaluate(self.one, 104.9))
        self.assertFalse(tracker.evaluate(self.one, 105))

    def test_sleep_then_wake_with_extra_display_fires(self):
        tracker = dimmer.PlugTracker(self.one)
        tracker.will_sleep()
        tracker.did_wake(100, 3)
        self.assertTrue(tracker.evaluate(self.two, 105))

    def test_nonmatching_external_display_never_fires(self):
        tracker = dimmer.PlugTracker(self.empty)
        tracker.schedule(0, 1)
        snapshot = dimmer.matching_snapshot([BUILT_IN, TV])
        self.assertFalse(tracker.evaluate(snapshot, 1))


class DisplayNameTests(unittest.TestCase):
    def test_only_exact_name_with_optional_numeric_suffix_matches(self):
        self.assertTrue(dimmer.display_name_matches("Q24h-10"))
        self.assertTrue(dimmer.display_name_matches("Q24h-10 (2)"))
        self.assertFalse(dimmer.display_name_matches("Built-in Retina Display"))
        self.assertFalse(dimmer.display_name_matches("Q24h-10 Pro"))

    def test_builtin_display_with_target_name_does_not_enter_snapshot(self):
        target_named_builtin = dimmer.DisplayInfo(5, "Q24h-10", True, 1, 2, 3)
        self.assertEqual(dimmer.matching_snapshot([target_named_builtin]), frozenset())


class _DashboardHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != "/api/lights":
            self.send_error(404)
            return
        self._write_json(self.server.get_status, self.server.get_payload)

    def do_POST(self):
        if self.path != "/api/lights/brightness":
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length", "0"))
        self.server.posts.append(self.rfile.read(length).decode("ascii"))
        self._write_json(200, {
            "ok": True,
            "message": "Brightness set to 40%",
            "lighting": {},
        })

    def _write_json(self, status, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format, *_args):
        pass


class DashboardServer:
    def __init__(self, payload, status=200):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _DashboardHandler)
        self.server.get_payload = payload
        self.server.get_status = status
        self.server.posts = []
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def url(self):
        host, port = self.server.server_address
        return f"http://{host}:{port}"

    @property
    def posts(self):
        return self.server.posts

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()


def lights_payload(lights):
    return {
        "ok": True,
        "lighting": {
            "state": "on",
            "on_count": 1,
            "available_count": len(lights),
            "total_count": len(lights),
            "groups": [
                {
                    "id": "galley",
                    "label": "Galley",
                    "state": "on",
                    "lights": [{
                        "entity_id": "light.not_cab",
                        "label": "Not cab",
                        "state": "on",
                        "available": True,
                        "brightness": 100,
                    }],
                },
                {
                    "id": "cab",
                    "label": "Cab",
                    "state": "on",
                    "lights": lights,
                    "power_switch": None,
                },
            ],
        },
    }


def light(entity, state="on", brightness=100, available=True):
    return {
        "entity_id": entity,
        "label": entity,
        "state": state,
        "available": available,
        "brightness": brightness,
    }


class DimmingActionTests(unittest.TestCase):
    def run_action(self, server, brightness=40):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            changed = dimmer.dim_cab_lights(
                brightness, dashboard_url=server.url, timeout=0.5
            )
        return changed, output.getvalue()

    def test_only_on_cab_lights_are_posted(self):
        payload = lights_payload([
            light("light.wiz_front_driver"),
            light("light.wiz_front_passenger", state="off"),
        ])
        with DashboardServer(payload) as server:
            changed, output = self.run_action(server)
        self.assertEqual(changed, 1)
        self.assertEqual(
            server.posts,
            ["entity=light.wiz_front_driver&brightness=40"],
        )
        self.assertIn("light.wiz_front_driver: brightness set to 40%", output)
        self.assertIn("cab dimming complete: changed 1 light(s) to 40%", output)

    def test_light_already_at_target_gets_no_post(self):
        payload = lights_payload([light("light.wiz_front_driver", brightness=40)])
        with DashboardServer(payload) as server:
            changed, output = self.run_action(server)
        self.assertEqual(changed, 0)
        self.assertEqual(server.posts, [])
        self.assertIn("cab dimming complete: changed 0 light(s) to 40%", output)

    def test_unavailable_cab_light_is_skipped(self):
        payload = lights_payload([
            light("light.wiz_front_driver", state="unavailable", brightness=None, available=False)
        ])
        with DashboardServer(payload) as server:
            changed, _output = self.run_action(server)
        self.assertEqual(changed, 0)
        self.assertEqual(server.posts, [])

    def test_get_502_is_logged_without_raising_or_posting(self):
        with DashboardServer({"ok": False, "error": "Home Assistant unavailable"}, 502) as server:
            changed, output = self.run_action(server)
        self.assertEqual(changed, 0)
        self.assertEqual(server.posts, [])
        self.assertEqual(output.count("cab dimming skipped:"), 1)

    def test_connection_refused_is_logged_without_raising(self):
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            changed = dimmer.dim_cab_lights(
                40, dashboard_url=f"http://127.0.0.1:{port}", timeout=0.2
            )
        self.assertEqual(changed, 0)
        self.assertEqual(output.getvalue().count("cab dimming skipped:"), 1)


class CliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        declarations = root / "settings"
        config = root / "config"
        declarations.mkdir()
        declaration = {
            "tool": "display-cab-dimmer",
            "title": "Dim cab lights when the monitor is plugged in",
            "description": "Test declaration.",
            "settings": [
                {
                    "name": "brightness",
                    "label": "Cab brightness",
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 100,
                    "default": 40,
                    "unit": "%",
                    "description": "Target brightness.",
                },
                {
                    "name": "settle_seconds",
                    "label": "Settle time",
                    "type": "number",
                    "minimum": 0.5,
                    "maximum": 30,
                    "step": 0.5,
                    "default": 3,
                    "unit": "s",
                    "description": "Display settle time.",
                },
            ],
        }
        (declarations / "display-cab-dimmer.json").write_text(
            json.dumps(declaration), encoding="utf-8"
        )
        self.environment = mock.patch.dict(os.environ, {
            "JACOBR_SETTINGS_DIR": str(declarations),
            "JACOBR_CONFIG_DIR": str(config),
        })
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def test_dim_now_uses_settings_and_returns_zero(self):
        payload = lights_payload([light("light.wiz_front_driver")])
        with DashboardServer(payload) as server, mock.patch.dict(
            os.environ, {dimmer.DASHBOARD_URL_ENV: server.url}
        ):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                result = dimmer.main(["--dim-now"])
        self.assertEqual(result, 0)
        self.assertEqual(server.posts, ["entity=light.wiz_front_driver&brightness=40"])
        self.assertIn("cab dimming complete: changed 1 light(s) to 40%", output.getvalue())

    def test_list_prints_display_details_and_returns_zero(self):
        output = io.StringIO()
        with mock.patch.object(dimmer, "online_displays", return_value=[BUILT_IN, TARGET_TWO]):
            with contextlib.redirect_stdout(output):
                result = dimmer.main(["--list"])
        self.assertEqual(result, 0)
        rendered = output.getvalue()
        self.assertIn("id=1 name='Built-in Retina Display' built-in", rendered)
        self.assertIn("id=3 name='Q24h-10 (2)' external", rendered)
        self.assertIn("vendor=12462 model=26280 unit=2 matches=yes", rendered)


if __name__ == "__main__":
    unittest.main()
