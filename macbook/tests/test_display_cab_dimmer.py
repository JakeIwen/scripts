"""Display plug-in tracking, state, status, and cab-light writes."""
import contextlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import io
import json
import os
from pathlib import Path
import socket
import stat
import sys
import tempfile
import threading
import unittest
from unittest import mock

import tool_automation

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


class DisplayMatchingTests(unittest.TestCase):
    def test_monitors_match_by_edid_identity_not_screen_name(self):
        renamed = dimmer.DisplayInfo(5, "Some other name", False, 12462, 26280, 3)
        lookalike = dimmer.DisplayInfo(6, "Q24h-10", False, 19501, 1, 4)
        self.assertEqual(dimmer.matching_snapshot([renamed, lookalike]), frozenset({renamed.key}))

    def test_builtin_display_never_enters_snapshot(self):
        builtin_with_target_ids = dimmer.DisplayInfo(5, "Q24h-10", True, 12462, 26280, 3)
        self.assertEqual(dimmer.matching_snapshot([builtin_with_target_ids]), frozenset())

    def test_turning_mirroring_off_is_not_a_plug_in(self):
        # A mirrored monitor has no NSScreen name; when mirroring stops it gains one.
        mirrored = dimmer.DisplayInfo(2, dimmer.MIRROR_NAME, False, 12462, 26280, 1)
        tracker = dimmer.PlugTracker(dimmer.matching_snapshot([BUILT_IN, mirrored, TARGET_TWO]))
        tracker.schedule(0, 3)
        self.assertFalse(tracker.evaluate(dimmer.matching_snapshot([BUILT_IN, TARGET_ONE, TARGET_TWO]), 3))

    def test_mirror_is_labelled_with_the_monitor_name(self):
        mirrored = dimmer.DisplayInfo(2, dimmer.MIRROR_NAME, False, 12462, 26280, 1)
        self.assertEqual(dimmer.matching_monitor_names([TARGET_TWO, mirrored]), ["Q24h-10 (2)", "Q24h-10 (mirror)"])

    def test_matching_monitor_names_are_sorted(self):
        self.assertEqual(
            dimmer.matching_monitor_names([TARGET_TWO, BUILT_IN, TARGET_ONE]),
            ["Q24h-10 (1)", "Q24h-10 (2)"],
        )


class _DashboardHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != "/api/lights":
            self.send_error(404)
            return
        self.server.gets += 1
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
        self.server.gets = 0
        self.server.posts = []
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def url(self):
        host, port = self.server.server_address
        return f"http://{host}:{port}"

    @property
    def gets(self):
        return self.server.gets

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
            result = dimmer.dim_cab_lights(
                brightness, dashboard_url=server.url, timeout=0.5
            )
        return result, output.getvalue()

    def test_only_on_cab_lights_are_posted(self):
        payload = lights_payload([
            light("light.wiz_front_driver"),
            light("light.wiz_front_passenger", state="off"),
        ])
        with DashboardServer(payload) as server:
            result, output = self.run_action(server)
        self.assertEqual(result.outcome, dimmer.DimOutcome.DIMMED)
        self.assertEqual(result.changed, 1)
        self.assertEqual(
            server.posts,
            ["entity=light.wiz_front_driver&brightness=40"],
        )
        self.assertIn("light.wiz_front_driver: brightness set to 40%", output)
        self.assertIn("cab dimming complete: changed 1 light(s) to 40%", output)

    def test_light_already_at_target_gets_no_post(self):
        payload = lights_payload([light("light.wiz_front_driver", brightness=40)])
        with DashboardServer(payload) as server:
            result, output = self.run_action(server)
        self.assertEqual(result.outcome, dimmer.DimOutcome.NOTHING_TO_DO)
        self.assertEqual(result.changed, 0)
        self.assertEqual(server.posts, [])
        self.assertIn("cab dimming complete: changed 0 light(s) to 40%", output)

    def test_unavailable_cab_light_is_skipped(self):
        payload = lights_payload([
            light("light.wiz_front_driver", state="unavailable", brightness=None, available=False)
        ])
        with DashboardServer(payload) as server:
            result, _output = self.run_action(server)
        self.assertEqual(result.outcome, dimmer.DimOutcome.NOTHING_TO_DO)
        self.assertEqual(server.posts, [])

    def test_get_502_is_logged_without_raising_or_posting(self):
        with DashboardServer({"ok": False, "error": "Home Assistant unavailable"}, 502) as server:
            result, output = self.run_action(server)
        self.assertEqual(result.outcome, dimmer.DimOutcome.FAILED)
        self.assertEqual(result.changed, 0)
        self.assertEqual(server.posts, [])
        self.assertEqual(output.count("cab dimming skipped:"), 1)

    def test_connection_refused_is_logged_without_raising(self):
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = dimmer.dim_cab_lights(
                40, dashboard_url=f"http://127.0.0.1:{port}", timeout=0.2
            )
        self.assertEqual(result.outcome, dimmer.DimOutcome.FAILED)
        self.assertEqual(output.getvalue().count("cab dimming skipped:"), 1)


class ToolEnvironmentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.declarations = self.root / "settings"
        self.config = self.root / "config"
        self.state_dir = self.root / "state"
        self.declarations.mkdir()
        declaration = {
            "tool": "display-cab-dimmer",
            "title": "Dim cab lights when the monitor is plugged in",
            "description": "Test declaration.",
            "settings": [
                {
                    "name": "enabled",
                    "label": "Enabled",
                    "type": "boolean",
                    "default": True,
                    "description": "Enable automatic dimming.",
                },
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
        (self.declarations / "display-cab-dimmer.json").write_text(
            json.dumps(declaration), encoding="utf-8"
        )
        self.environment = mock.patch.dict(os.environ, {
            "JACOBR_SETTINGS_DIR": str(self.declarations),
            "JACOBR_CONFIG_DIR": str(self.config),
            dimmer.STATE_DIR_ENV: str(self.state_dir),
        })
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def save_enabled(self, enabled):
        self.config.mkdir(exist_ok=True)
        (self.config / "display-cab-dimmer.json").write_text(
            json.dumps({"enabled": enabled}), encoding="utf-8"
        )

    def write_running_state(self, *, monitors=None, last_run=None):
        changes = {
            "agent": {"pid": 12345, "started_at": 1000.0},
            "monitors": list(monitors or []),
            "monitors_at": 1001.0,
        }
        if last_run is not None:
            changes["last_run"] = last_run
        self.assertTrue(dimmer.update_state(changes))

    def validated_status(self):
        status_record = dimmer.automation_status()
        self.assertEqual(tool_automation.validate_status(status_record), status_record)
        return status_record

    def test_plug_in_enabled_dims_and_records_run(self):
        payload = lights_payload([light("light.wiz_front_driver")])
        settings = {"enabled": True, "brightness": 40, "settle_seconds": 3}
        with DashboardServer(payload) as server:
            result = dimmer.handle_plug_in(
                settings, dashboard_url=server.url, timeout=0.5, now=1760000000.0
            )
        self.assertEqual(result.outcome, dimmer.DimOutcome.DIMMED)
        self.assertEqual(server.posts, ["entity=light.wiz_front_driver&brightness=40"])
        state = dimmer.read_state()
        self.assertEqual(state["last_plug_in"], 1760000000.0)
        self.assertEqual(state["last_run"], {
            "trigger": "plug-in",
            "at": 1760000000.0,
            "outcome": "dimmed",
            "changed": 1,
            "detail": "changed 1 light(s) to 40%",
        })

    def test_plug_in_disabled_touches_no_lights_and_records_skip(self):
        payload = lights_payload([light("light.wiz_front_driver")])
        settings = {"enabled": False, "brightness": 40, "settle_seconds": 3}
        with DashboardServer(payload) as server:
            result = dimmer.handle_plug_in(
                settings, dashboard_url=server.url, timeout=0.5, now=1760000001.0
            )
        self.assertEqual(result.outcome, dimmer.DimOutcome.DISABLED)
        self.assertEqual(server.gets, 0)
        self.assertEqual(server.posts, [])
        state = dimmer.read_state()
        self.assertEqual(state["last_plug_in"], 1760000001.0)
        self.assertEqual(state["last_run"]["outcome"], "disabled")
        self.assertEqual(state["last_run"]["trigger"], "plug-in")

    def test_dim_now_failure_returns_one_and_records_failed_manual_run(self):
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()
        with mock.patch.dict(
            os.environ, {dimmer.DASHBOARD_URL_ENV: f"http://127.0.0.1:{port}"}
        ), mock.patch.object(dimmer, "HTTP_TIMEOUT_SECONDS", 0.1):
            result = dimmer.main(["--dim-now"])
        self.assertEqual(result, 1)
        last_run = dimmer.read_state()["last_run"]
        self.assertEqual(last_run["trigger"], "manual")
        self.assertEqual(last_run["outcome"], "failed")
        self.assertIn("URLError", last_run["detail"])

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
        self.assertEqual(dimmer.read_state()["last_run"]["trigger"], "manual")

    def test_state_update_recovers_from_corrupt_file_and_sets_private_modes(self):
        self.state_dir.mkdir(mode=0o755)
        (self.state_dir / "state.json").write_text("not json", encoding="utf-8")
        self.assertTrue(dimmer.update_state({"last_plug_in": 123.0}))
        self.assertEqual(dimmer.read_state()["last_plug_in"], 123.0)
        self.assertEqual(stat.S_IMODE(self.state_dir.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE((self.state_dir / "state.json").stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE((self.state_dir / "state.lock").stat().st_mode), 0o600)

    def test_status_without_state_is_unknown(self):
        status_record = self.validated_status()
        self.assertEqual(status_record["level"], "unknown")
        self.assertEqual(status_record["summary"], "No activity recorded yet")

    def test_status_dead_agent_is_warn(self):
        self.write_running_state(monitors=["Q24h-10 (1)"])
        with mock.patch.object(os, "kill", side_effect=ProcessLookupError):
            status_record = self.validated_status()
        self.assertEqual(status_record["level"], "warn")
        self.assertEqual(status_record["summary"], "Agent is not running")
        self.assertEqual(status_record["fields"][-1]["value"], "Not running")

    def test_status_disabled_is_idle(self):
        self.save_enabled(False)
        self.write_running_state(monitors=["Q24h-10 (1)"])
        with mock.patch.object(os, "kill", return_value=None):
            status_record = self.validated_status()
        self.assertEqual(status_record["level"], "idle")
        self.assertEqual(
            status_record["summary"],
            "Disabled; plug-ins are recorded but the lights are left alone",
        )

    def test_status_running_is_ok_with_ordered_fields(self):
        self.write_running_state(monitors=["Q24h-10 (1)", "Q24h-10 (2)"])
        with mock.patch.object(os, "kill", return_value=None):
            status_record = self.validated_status()
        self.assertEqual(status_record["level"], "ok")
        self.assertEqual(status_record["summary"], "Watching 2 monitors")
        self.assertEqual(
            [field["label"] for field in status_record["fields"]],
            ["Enabled", "Brightness", "Monitors", "Last plug-in", "Last run", "Agent"],
        )
        self.assertEqual(status_record["fields"][2]["value"], "Q24h-10 (1), Q24h-10 (2)")

    def test_failed_last_run_warns_only_its_field(self):
        self.write_running_state(
            monitors=["Q24h-10 (1)"],
            last_run={
                "trigger": "manual",
                "at": 1760000000.0,
                "outcome": "failed",
                "changed": 0,
                "detail": "URLError: offline",
            },
        )
        with mock.patch.object(os, "kill", return_value=None):
            status_record = self.validated_status()
        self.assertEqual(status_record["level"], "ok")
        last_run = status_record["fields"][4]
        self.assertEqual(last_run["value"], "Failed: URLError: offline (manual)")
        self.assertEqual(last_run["level"], "warn")
        self.assertEqual(last_run["time"], 1760000000.0)

    def test_status_json_cli_prints_a_valid_record(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = dimmer.main(["--status", "--json"])
        self.assertEqual(result, 0)
        tool_automation.validate_status(json.loads(output.getvalue()))

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


class AutomationDeclarationTests(unittest.TestCase):
    def test_real_declaration_loads_and_references_existing_script(self):
        declaration = tool_automation.load_declaration("display-cab-dimmer")
        self.assertEqual(declaration.tool, "display-cab-dimmer")
        self.assertEqual(declaration.action("dim-now").confirm, True)
        self.assertEqual(Path(declaration.status.argv[4]), SOURCE)
        self.assertTrue(Path(declaration.status.argv[4]).is_file())
        self.assertTrue(Path(declaration.action("dim-now").command.argv[4]).is_file())


if __name__ == "__main__":
    unittest.main()
