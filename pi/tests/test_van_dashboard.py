import json
import os
import subprocess
import tempfile
import threading
import unittest
from email.message import Message
from types import SimpleNamespace

from pi.apps.van_dashboard import van_dashboard as dashboard
from pi.scripts import usb_watch


class FakeClock:
    def __init__(self, value=100.0):
        self.value = value

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


class FakeEngine:
    def __init__(self, running=False, rpm=0.0):
        self.running = running
        self.rpm = rpm

    def snapshot(self):
        return {
            "running": self.running,
            "rpm": self.rpm,
            "evidence_age_seconds": 0.0 if self.running else None,
            "frame_age_seconds": 0.0,
            "source": "test",
            "error": None,
        }

    def start(self):
        pass

    def stop(self):
        pass


class EngineMonitorTests(unittest.TestCase):
    @staticmethod
    def observe_running_pair(monitor):
        monitor.observe(0x0F4, bytes.fromhex("17 80"))
        monitor.observe(0x0FC, bytes.fromhex("0b c0"))

    def test_rpm_decoder_matches_verified_capture_samples(self):
        self.assertEqual(dashboard.rpm_from_engine_frame(0x0F4, bytes.fromhex("17 80")), 752.0)
        self.assertEqual(dashboard.rpm_from_engine_frame(0x0FC, bytes.fromhex("0b c0")), 752.0)
        self.assertEqual(dashboard.rpm_from_engine_frame(0x0F4, bytes.fromhex("00 00")), 0.0)
        self.assertIsNone(dashboard.rpm_from_engine_frame(0x101, bytes.fromhex("17 80")))
        self.assertIsNone(dashboard.rpm_from_engine_frame(0x0F4, b"\x17"))

    def test_requires_repeated_fresh_running_frames(self):
        clock = FakeClock()
        monitor = dashboard.EngineMonitor(clock=clock)
        for _ in range(dashboard.ENGINE_CONFIRM_FRAMES - 1):
            self.observe_running_pair(monitor)
        self.assertFalse(monitor.snapshot()["running"])

        self.observe_running_pair(monitor)
        self.assertTrue(monitor.snapshot()["running"])
        self.assertEqual(monitor.snapshot()["rpm"], 752.0)

        clock.advance(dashboard.ENGINE_EVIDENCE_MAX_AGE + 0.1)
        self.assertFalse(monitor.snapshot()["running"])

    def test_zero_rpm_immediately_revokes_running_evidence(self):
        clock = FakeClock()
        monitor = dashboard.EngineMonitor(clock=clock)
        for _ in range(dashboard.ENGINE_CONFIRM_FRAMES):
            self.observe_running_pair(monitor)
        self.assertTrue(monitor.snapshot()["running"])
        monitor.observe(0x0F4, bytes.fromhex("00 00"))
        self.assertFalse(monitor.snapshot()["running"])

    def test_implausibly_high_value_does_not_count_as_running(self):
        clock = FakeClock()
        monitor = dashboard.EngineMonitor(clock=clock)
        for _ in range(dashboard.ENGINE_CONFIRM_FRAMES):
            monitor.observe(0x0F4, bytes.fromhex("ff ff"))
            monitor.observe(0x0FC, bytes.fromhex("ff ff"))
        self.assertFalse(monitor.snapshot()["running"])

    def test_one_rpm_source_alone_is_not_running_evidence(self):
        clock = FakeClock()
        monitor = dashboard.EngineMonitor(clock=clock)
        for _ in range(dashboard.ENGINE_CONFIRM_FRAMES):
            monitor.observe(0x0FC, bytes.fromhex("0b c0"))
        self.assertFalse(monitor.snapshot()["running"])


class CanLinkSafetyTests(unittest.TestCase):
    @staticmethod
    def command_with(output, returncode=0):
        def command(_args, timeout):
            return SimpleNamespace(stdout=output, stderr="", returncode=returncode)

        return command

    def test_accepts_existing_armed_c_can_without_mutation(self):
        output = "4: can0: <NOARP,UP,LOWER_UP> state UP\n    can state ERROR-ACTIVE bitrate 500000"
        ok, _ = dashboard.c_can_link_status(self.command_with(output))
        self.assertTrue(ok)

    def test_rejects_b_can_speed_and_listen_only(self):
        bcan = "4: can0: <UP,LOWER_UP> state UP\n    can state ERROR-ACTIVE bitrate 125000"
        listen = "4: can0: <UP,LOWER_UP> state UP\n    can <LISTEN-ONLY> bitrate 500000"
        self.assertFalse(dashboard.c_can_link_status(self.command_with(bcan))[0])
        self.assertFalse(dashboard.c_can_link_status(self.command_with(listen))[0])


class FakeGroup:
    def __init__(self):
        self.coordinator = None
        self.members = []
        self.volume = 61
        self.mute = False


class FakeSpeaker:
    def __init__(self, name, volume, transport="STOPPED"):
        self.player_name = name
        self.volume = volume
        self.mute = False
        self.transport = transport
        self.track_info = {
            "title": "Orange Juice",
            "artist": "Stanley Brinks and The Wave Pictures",
            "album": "",
            "position": "0:01:23",
            "duration": "0:03:45",
        }
        self.transport_calls = []
        self.ip_address = "192.168.6.189"
        self.track_info["album_art"] = (
            "http://192.168.6.189:1400/getaa?s=1&u=test-track"
        )
        self.is_visible = True
        self.group = FakeGroup()
        self.group.coordinator = self
        self.group.members = [self]

    def get_current_transport_info(self):
        return {"current_transport_state": self.transport}

    def get_current_track_info(self):
        return self.track_info

    def pause(self):
        self.transport_calls.append("pause")
        self.transport = "PAUSED_PLAYBACK"

    def play(self):
        self.transport_calls.append("play")
        self.transport = "PLAYING"

    def previous(self):
        self.transport_calls.append("previous")

    def next(self):
        self.transport_calls.append("next")


class FakeArtResponse:
    def __init__(self, content=b"\xff\xd8\xfffake-jpeg"):
        self.content = content
        self.headers = Message()
        self.headers["Content-Type"] = "image/jpeg"

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def read(self, size):
        return self.content[:size]


class SonosControllerTests(unittest.TestCase):
    def test_snapshot_selection_and_volume_match_audiobook_controls(self):
        front = FakeSpeaker("Front", 28, "PLAYING")
        rear = FakeSpeaker("Rear", 34)
        front.group.members = [front, rear]
        rear.group = front.group
        solo = FakeSpeaker("Solo", 19)
        zones = {front, rear, solo}

        with tempfile.TemporaryDirectory() as tempdir:
            store = dashboard.StateStore(os.path.join(tempdir, "state.json"))
            store.set("sonos_device", "Front")
            controller = dashboard.SonosController(store, discover_func=lambda timeout: zones)
            snapshot = controller.snapshot()
            self.assertEqual(snapshot["coordinator"], "Front")
            self.assertEqual(snapshot["group"], {"volume": 61, "muted": False})
            self.assertEqual(snapshot["now_playing"]["title"], "Orange Juice")
            self.assertEqual(
                snapshot["now_playing"]["artist"],
                "Stanley Brinks and The Wave Pictures",
            )
            self.assertRegex(
                snapshot["now_playing"]["album_art"],
                r"^/api/speakers/art/[0-9a-f]{16}$",
            )
            grouped = {item["name"] for item in snapshot["speakers"] if item["grouped"]}
            self.assertEqual(grouped, {"Front", "Rear"})
            self.assertEqual(controller.set_volume("Rear", 42), 42)
            self.assertEqual(rear.volume, 42)
            self.assertTrue(controller.set_mute("Rear", True))
            self.assertTrue(rear.mute)
            self.assertEqual(controller.set_group_volume(73), 73)
            self.assertEqual(front.group.volume, 73)
            self.assertTrue(controller.set_group_mute(True))
            self.assertTrue(front.group.mute)
            self.assertEqual(controller.transport("play_pause"), "Sonos paused")
            self.assertEqual(controller.transport("play_pause"), "Sonos playing")
            self.assertEqual(controller.transport("previous"), "Previous Sonos track")
            self.assertEqual(controller.transport("next"), "Next Sonos track")
            self.assertEqual(front.transport_calls, ["pause", "play", "previous", "next"])
            self.assertEqual(controller.select("Solo"), "Solo")
            self.assertEqual(store.get("sonos_device"), "Solo")

    def test_invalid_transport_action_fails_before_discovery(self):
        controller = dashboard.SonosController(
            dashboard.StateStore("/dev/null"),
            discover_func=lambda timeout: self.fail("discovery should not run"),
        )
        with self.assertRaisesRegex(ValueError, "unknown Sonos transport action"):
            controller.transport("shuffle")


class ConnectivityMonitorTests(unittest.TestCase):
    def test_refreshes_reusable_collector_into_cache(self):
        payload = {
            "checked_at": 1_700_000_100,
            "internet": {"online": True},
            "router": {
                "reachable": True,
                "mode": "clientwan",
                "online": ["clientwan"],
                "interfaces": [],
                "error": None,
            },
            "ubnt": {
                "reachable": True,
                "connected": False,
                "ssid": "denlink",
                "error": None,
            },
        }

        def command(args, timeout):
            self.assertEqual(args, ["/test/connectivity"])
            self.assertEqual(timeout, 25)
            return SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr="")

        monitor = dashboard.ConnectivityMonitor(
            collector="/test/connectivity",
            command=command,
            wall_clock=lambda: 1_700_000_110,
        )
        status = monitor.refresh()
        self.assertTrue(status["internet"]["online"])
        self.assertEqual(status["router"]["mode"], "clientwan")
        self.assertEqual(status["ubnt"]["ssid"], "denlink")
        self.assertFalse(status["stale"])
        monitor.request_refresh()
        self.assertTrue(monitor.refresh_event.is_set())


class PriceCheckControllerTests(unittest.TestCase):
    PAYLOAD = {
        "ok": True,
        "items": [
            {
                "id": 7,
                "display_title": "Protein shakes",
                "threshold": "55.00",
                "last_price": "63.15",
            }
        ],
        "summary": {"count": 1, "checked": 1, "below_threshold": 0, "errors": 0},
    }

    def test_uses_fixed_cli_and_parses_json(self):
        calls = []

        def command(args, timeout):
            calls.append((list(args), timeout))
            return SimpleNamespace(
                returncode=0, stdout=json.dumps(self.PAYLOAD), stderr=""
            )

        controller = dashboard.PriceCheckController(
            tool="/test/price/main.py",
            database="/private/prices.sqlite3",
            command=command,
            timeout=91,
        )
        self.assertEqual(controller.status(), self.PAYLOAD)
        controller.add("amazon", "55", "https://example.com/item", "Example")
        controller.edit(7, "amazon", "45", "https://example.com/updated", "Updated")
        controller.remove(7)
        controller.check(7)
        prefix = [
            dashboard.sys.executable,
            "/test/price/main.py",
            "--db",
            "/private/prices.sqlite3",
            "--json",
        ]
        self.assertEqual(calls[0], (prefix + ["list"], 20))
        self.assertEqual(
            calls[1],
            (prefix + ["add", "amazon", "55", "https://example.com/item", "Example"], 20),
        )
        self.assertEqual(
            calls[2],
            (
                prefix
                + [
                    "edit",
                    "7",
                    "amazon",
                    "45",
                    "https://example.com/updated",
                    "Updated",
                ],
                20,
            ),
        )
        self.assertEqual(calls[3], (prefix + ["remove", "7"], 20))
        self.assertEqual(calls[4], (prefix + ["check", "7"], 91))

    def test_rejects_failed_or_non_json_cli_output(self):
        def failed(_args, timeout):
            return SimpleNamespace(
                returncode=1,
                stdout=json.dumps({"ok": False, "message": "duplicate URL"}),
                stderr="",
            )

        with self.assertRaisesRegex(dashboard.PriceCheckCommandError, "duplicate URL"):
            dashboard.PriceCheckController(command=failed).status()

        def malformed(_args, timeout):
            return SimpleNamespace(returncode=0, stdout="not-json", stderr="broken")

        with self.assertRaisesRegex(dashboard.PriceCheckCommandError, "invalid output"):
            dashboard.PriceCheckController(command=malformed).status()


class SystemMonitorClientTests(unittest.TestCase):
    PAYLOAD = {
        "ok": True,
        "status": {"available": True, "stale": False, "current": {}},
        "diagnosis": {"level": "good", "headline": "No faults"},
        "peaks": {},
        "events": [],
    }

    def test_uses_fixed_report_command_and_parses_json(self):
        calls = []

        def command(args, timeout):
            calls.append((list(args), timeout))
            return SimpleNamespace(
                returncode=0, stdout=json.dumps(self.PAYLOAD), stderr=""
            )

        client = dashboard.SystemMonitorClient(
            tool="/test/system_event_monitor.py",
            database="/test/events.sqlite3",
            command=command,
            timeout=12,
        )
        self.assertEqual(client.report(168), self.PAYLOAD)
        self.assertEqual(
            calls,
            [
                (
                    [
                        dashboard.sys.executable,
                        "/test/system_event_monitor.py",
                        "--database",
                        "/test/events.sqlite3",
                        "report",
                        "--hours",
                        "168",
                        "--limit",
                        "100",
                        "--json",
                    ],
                    12,
                )
            ],
        )

    def test_rejects_failed_or_malformed_report(self):
        failed = dashboard.SystemMonitorClient(
            command=lambda args, timeout: SimpleNamespace(
                returncode=1, stdout="", stderr="database unavailable"
            )
        )
        with self.assertRaisesRegex(
            dashboard.SystemMonitorCommandError, "database unavailable"
        ):
            failed.report()

        malformed = dashboard.SystemMonitorClient(
            command=lambda args, timeout: SimpleNamespace(
                returncode=0, stdout="not-json", stderr="broken"
            )
        )
        with self.assertRaisesRegex(dashboard.SystemMonitorCommandError, "invalid output"):
            malformed.report()

    def test_uses_fixed_crash_analysis_and_full_history_commands(self):
        calls = []

        def command(args, timeout):
            calls.append(list(args))
            return SimpleNamespace(returncode=0, stdout='{"ok": true}', stderr="")

        client = dashboard.SystemMonitorClient(
            tool="/test/monitor.py",
            database="/test/events.sqlite3",
            command=command,
        )
        client.crash_analysis()
        client.crash_history(20)
        prefix = [
            dashboard.sys.executable,
            "/test/monitor.py",
            "--database",
            "/test/events.sqlite3",
        ]
        self.assertEqual(calls[0], prefix + ["crash-report", "--save", "--json"])
        self.assertEqual(
            calls[1],
            prefix + ["crash-history", "--limit", "20", "--full", "--json"],
        )


class PriceCheckApiTests(unittest.TestCase):
    def setUp(self):
        self.original = dashboard.price_checks
        self.client = dashboard.app.test_client()

    def tearDown(self):
        dashboard.price_checks = self.original

    def test_list_add_check_and_remove(self):
        calls = []
        payload = {
            "ok": True,
            "items": [],
            "summary": {"count": 0, "checked": 0, "below_threshold": 0, "errors": 0},
        }

        class FakePriceChecks:
            def status(self):
                calls.append(("status",))
                return dict(payload)

            def add(self, *args):
                calls.append(("add", *args))
                return {**payload, "item": {"display_title": "Example"}}

            def edit(self, *args):
                calls.append(("edit", *args))
                return {**payload, "item": {"display_title": "Updated"}}

            def check(self, target):
                calls.append(("check", target))
                return {**payload, "checked": [{"id": 7}]}

            def remove(self, item_id):
                calls.append(("remove", item_id))
                return {**payload, "removed": {"display_title": "Example"}}

        dashboard.price_checks = FakePriceChecks()
        self.assertEqual(self.client.get("/api/price-checks").status_code, 200)
        add = self.client.post(
            "/api/price-checks/add",
            data={
                "parser": "amazon",
                "threshold": "55",
                "url": "https://example.com/item",
                "title": "Example",
            },
        )
        self.assertEqual(add.status_code, 200)
        self.assertEqual(add.get_json()["message"], "Watching Example")
        edit = self.client.post(
            "/api/price-checks/edit",
            data={
                "id": "7",
                "parser": "amazon",
                "threshold": "45",
                "url": "https://example.com/updated",
                "title": "Updated",
            },
        )
        self.assertEqual(edit.status_code, 200)
        self.assertEqual(edit.get_json()["message"], "Updated Updated")
        self.assertEqual(
            self.client.post("/api/price-checks/check", data={"target": "7"}).status_code,
            200,
        )
        self.assertEqual(
            self.client.post("/api/price-checks/remove", data={"id": "7"}).status_code,
            200,
        )
        self.assertEqual(
            calls,
            [
                ("status",),
                ("add", "amazon", "55", "https://example.com/item", "Example"),
                (
                    "edit",
                    "7",
                    "amazon",
                    "45",
                    "https://example.com/updated",
                    "Updated",
                ),
                ("check", "7"),
                ("remove", "7"),
            ],
        )

    def test_rejects_bad_forms_before_running_cli(self):
        class NeverCalled:
            def __getattr__(self, _name):
                self.fail("controller should not be called")

        response = self.client.post("/api/price-checks/check", data={"target": "bad"})
        self.assertEqual(response.status_code, 400)


class UbntWifiControllerTests(unittest.TestCase):
    WIFI = {
        "version": 1,
        "reachable": True,
        "checked_at": 123,
        "state": {
            "configured_ssid": "denlink",
            "associated_ssid": "denlink",
            "ccq_percent": 99.1,
            "automatic_paused": False,
            "selector_running": False,
        },
        "profiles": [{"name": "denlink", "ssid": "denlink", "security": "wpa"}],
        "networks": [],
    }

    def test_provision_uses_fixed_tool_argv_and_does_not_retain_password(self):
        calls = []
        changes = []

        def command(args, timeout, input_text=None):
            calls.append((list(args), timeout, input_text))
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(
                    {"ok": True, "message": "saved", "wifi": self.WIFI}
                ),
                stderr="",
            )

        manager = dashboard.UbntWifiController(
            tool="/test/ubnt_wifi.py",
            command=command,
            wall_clock=FakeClock(200),
            on_change=lambda: changes.append("refresh"),
        )
        payload = {
            "ssid": "Camp",
            "security": "wpa",
            "bssid": "00:11:22:33:44:55",
            "password": "test-password",
        }
        manager._run("provision", payload)

        self.assertEqual(calls[0][0], ["/test/ubnt_wifi.py", "--json", "provision"])
        self.assertNotIn("test-password", " ".join(calls[0][0]))
        self.assertIn('"password":"test-password"', calls[0][2])
        self.assertEqual(payload["password"], "")
        self.assertEqual(manager.snapshot()["operation"]["status"], "complete")
        self.assertEqual(changes, ["refresh"])

    def test_failure_refreshes_authoritative_status(self):
        calls = []

        def command(args, timeout, input_text=None):
            calls.append(list(args))
            if args[-1] == "connect":
                return SimpleNamespace(
                    returncode=1,
                    stdout=json.dumps({"ok": False, "message": "switch failed"}),
                    stderr="",
                )
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps({"ok": True, "wifi": self.WIFI}),
                stderr="",
            )

        manager = dashboard.UbntWifiController(
            tool="/test/ubnt_wifi.py", command=command, wall_clock=FakeClock(200)
        )
        manager._run("connect", {"profile": "denlink"})
        snapshot = manager.snapshot()

        self.assertEqual(snapshot["operation"]["status"], "error")
        self.assertEqual(snapshot["operation"]["error"], "switch failed")
        self.assertTrue(snapshot["wifi"]["reachable"])
        self.assertEqual(
            calls,
            [
                ["/test/ubnt_wifi.py", "--json", "connect"],
                ["/test/ubnt_wifi.py", "--json", "status"],
            ],
        )

    def test_failed_status_refresh_is_not_retried_on_every_poll(self):
        clock = FakeClock(200)
        manager = dashboard.UbntWifiController(wall_clock=clock)
        manager.operation.update(
            {"status": "error", "kind": "status", "completed_at": 200}
        )
        starts = []
        manager.start = lambda kind: starts.append(kind)

        manager.request_refresh(max_age=20)
        self.assertEqual(starts, [])
        clock.advance(21)
        manager.request_refresh(max_age=20)
        self.assertEqual(starts, ["status"])


class TuyaSwitchManagerTests(unittest.TestCase):
    def test_reads_and_toggles_confirmed_starlink_state(self):
        state = "on"
        calls = []

        def command(args, timeout):
            nonlocal state
            calls.append(tuple(args))
            if args[0] == dashboard.TUYA_STATUS:
                return SimpleNamespace(returncode=0, stdout=state + "\n", stderr="")
            if args[0] == dashboard.TUYA_TOGGLE:
                state = args[2]
                return SimpleNamespace(returncode=0, stdout=state + "\n", stderr="")
            raise AssertionError(args)

        switch = dashboard.TuyaSwitchManager(
            "starlink", command=command, wall_clock=lambda: 1_700_000_000
        )
        self.assertEqual(switch.refresh()["state"], "on")
        toggled = switch.toggle()
        self.assertEqual(toggled["state"], "off")
        self.assertTrue(toggled["available"])
        self.assertEqual(calls[1], (dashboard.TUYA_TOGGLE, "starlink", "off"))
        self.assertEqual(calls[2], (dashboard.TUYA_STATUS, "starlink"))

    def test_failed_status_is_neutral_and_cannot_guess_toggle_direction(self):
        def command(args, timeout):
            return SimpleNamespace(returncode=1, stdout="unavailable\n", stderr="")

        switch = dashboard.TuyaSwitchManager("starlink", command=command)
        status = switch.refresh()
        self.assertEqual(status["state"], "unknown")
        self.assertFalse(status["available"])
        with self.assertRaises(ValueError):
            switch.toggle()


class LightingControllerTests(unittest.TestCase):
    @staticmethod
    def light_values(default_state="off"):
        return [
            {
                "entity_id": entity,
                "state": default_state,
                "brightness": 128 if default_state == "on" else None,
            }
            for _group_id, _group_label, lights in dashboard.LIGHT_GROUPS
            for entity, _label in lights
        ]

    def test_status_preserves_configured_groups_and_reports_percent(self):
        values = self.light_values()
        values[0].update(state="on", brightness=128)

        def command(args, timeout):
            self.assertEqual(args, [dashboard.TUYA_LIGHT, "list"])
            return SimpleNamespace(returncode=0, stdout=json.dumps(values), stderr="")

        status = dashboard.LightingController(command=command).status()
        self.assertEqual([group["label"] for group in status["groups"]], [
            "Cab", "Rear", "Kitchen", "Exterior", "Solder", "Extra"
        ])
        self.assertEqual(status["state"], "mixed")
        self.assertEqual(status["on_count"], 1)
        self.assertEqual(status["available_count"], 9)
        self.assertEqual(status["groups"][0]["lights"][0]["brightness"], 50)

    def test_power_and_brightness_use_only_fixed_commands_then_refresh(self):
        values = self.light_values()
        calls = []

        def command(args, timeout):
            calls.append(list(args))
            if args[:2] == [dashboard.TUYA_TOGGLE, "light.wiz_kitchen"]:
                values[4]["state"] = args[2]
                values[4]["brightness"] = 255 if args[2] == "on" else None
            elif args[:3] == [dashboard.TUYA_LIGHT, "set", "light.wiz_kitchen"]:
                values[4]["state"] = "on"
                values[4]["brightness"] = int(args[3])
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(values) if args == [dashboard.TUYA_LIGHT, "list"] else "",
                stderr="",
            )

        controller = dashboard.LightingController(command=command)
        powered = controller.set_power("light.wiz_kitchen", True)
        dimmed = controller.set_brightness("light.wiz_kitchen", 40)
        self.assertEqual(powered["on_count"], 1)
        self.assertEqual(dimmed["groups"][2]["lights"][0]["brightness"], 40)
        self.assertEqual(calls[0], [dashboard.TUYA_TOGGLE, "light.wiz_kitchen", "on"])
        self.assertEqual(calls[1], [dashboard.TUYA_LIGHT, "list"])
        self.assertEqual(calls[2], [dashboard.TUYA_LIGHT, "set", "light.wiz_kitchen", "102"])
        self.assertEqual(calls[3], [dashboard.TUYA_LIGHT, "list"])
        with self.assertRaisesRegex(ValueError, "unknown lighting target"):
            controller.set_power("switch.starlink", True)
        with self.assertRaisesRegex(ValueError, "unknown light entity"):
            controller.set_brightness("light.not_configured", 50)

    def test_rejects_bad_schema_and_reports_timeout(self):
        with self.assertRaises(dashboard.LightingCommandError):
            dashboard.LightingController.parse_status('{"not":"a list"}')

        def timeout(_args, timeout):
            raise subprocess.TimeoutExpired("lights", timeout)

        with self.assertRaisesRegex(dashboard.LightingCommandError, "timed out"):
            dashboard.LightingController(command=timeout).status()

    def test_room_and_all_targets_expand_only_to_configured_entities(self):
        calls = []
        values = self.light_values()

        def command(args, timeout):
            calls.append(list(args))
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(values) if args == [dashboard.TUYA_LIGHT, "list"] else "",
                stderr="",
            )

        controller = dashboard.LightingController(command=command)
        controller.set_power("group:cab", False)
        self.assertEqual(
            calls,
            [
                [dashboard.TUYA_TOGGLE, "light.wiz_front_driver", "off"],
                [dashboard.TUYA_TOGGLE, "light.wiz_front_passenger", "off"],
                [dashboard.TUYA_LIGHT, "list"],
            ],
        )
        calls.clear()
        controller.set_power("all", True)
        self.assertEqual(len(calls), 10)
        self.assertEqual(calls[-1], [dashboard.TUYA_LIGHT, "list"])
        self.assertEqual(
            {call[1] for call in calls[:-1]},
            controller.entities,
        )

    def test_old_helper_falls_back_to_fixed_individual_status_queries(self):
        calls = []

        def command(args, timeout):
            calls.append(list(args))
            if args == [dashboard.TUYA_LIGHT, "list"]:
                return SimpleNamespace(
                    returncode=2,
                    stdout="",
                    stderr="usage: tuya_light.sh <status|set> <light.entity>",
                )
            self.assertEqual(args[:2], [dashboard.TUYA_LIGHT, "status"])
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(
                    {
                        "state": "on" if args[2] == "light.wiz_dresser" else "off",
                        "color_mode": None,
                        "brightness": 128 if args[2] == "light.wiz_dresser" else None,
                        "color_temp_kelvin": None,
                    }
                ),
                stderr="",
            )

        controller = dashboard.LightingController(command=command)
        status = controller.status()
        self.assertEqual(status["on_count"], 1)
        self.assertEqual(status["available_count"], 9)
        self.assertEqual(len(calls), 10)
        self.assertEqual(
            [call[2] for call in calls[1:]],
            list(controller.ordered_entities),
        )


class StoragePolicyManagerTests(unittest.TestCase):
    POLICY = {
        "version": 1,
        "disks_enabled": True,
        "torrents_enabled": True,
        "allow_starlink_torrents": False,
        "runtime": {
            "disks_mounted": True,
            "mounted_disk_labels": ["movingparts", "mbp2tbkup"],
            "qbittorrent_running": False,
        },
    }

    def test_parses_only_the_exact_v1_boolean_schema(self):
        self.assertEqual(
            dashboard.StoragePolicyManager.parse_status(json.dumps(self.POLICY)),
            self.POLICY,
        )
        invalid_values = (
            "not-json",
            json.dumps({**self.POLICY, "version": 2}),
            json.dumps({**self.POLICY, "disks_enabled": 1}),
            json.dumps({**self.POLICY, "unexpected": False}),
            json.dumps(
                {
                    **self.POLICY,
                    "runtime": {
                        **self.POLICY["runtime"],
                        "qbittorrent_running": 0,
                    },
                }
            ),
            json.dumps(
                {
                    **self.POLICY,
                    "runtime": {
                        **self.POLICY["runtime"],
                        "disks_mounted": False,
                    },
                }
            ),
        )
        for value in invalid_values:
            with self.subTest(value=value):
                with self.assertRaises(dashboard.PolicyCommandError):
                    dashboard.StoragePolicyManager.parse_status(value)

    def test_each_update_uses_fixed_argv_and_refreshes_status(self):
        cases = (
            ("disks_enabled", "disks", False),
            ("torrents_enabled", "torrents", False),
            ("allow_starlink_torrents", "starlink-torrents", True),
        )
        for field, target, enabled in cases:
            with self.subTest(field=field):
                calls = []
                policy = dict(self.POLICY)

                def command(args, timeout):
                    calls.append((list(args), timeout))
                    if args[1:3] == ["--json", target]:
                        policy[field] = args[3] == "on"
                    return SimpleNamespace(
                        returncode=0, stdout=json.dumps(policy), stderr=""
                    )

                manager = dashboard.StoragePolicyManager(command=command, timeout=7)
                self.assertEqual(manager.update(field, enabled), policy)
                self.assertEqual(
                    calls,
                    [
                        (
                            [
                                dashboard.POLICYCTL,
                                "--json",
                                target,
                                "on" if enabled else "off",
                            ],
                            7,
                        ),
                        ([dashboard.POLICYCTL, "--json", "status"], 7),
                    ],
                )

    def test_rejects_unknown_fields_and_non_boolean_values(self):
        manager = dashboard.StoragePolicyManager(
            command=lambda args, timeout: self.fail("policyctl must not run")
        )
        with self.assertRaises(ValueError):
            manager.update("shell_command", True)
        with self.assertRaises(ValueError):
            manager.update("disks_enabled", "true")

    def test_subprocess_failure_and_timeout_are_bounded_errors(self):
        failed = dashboard.StoragePolicyManager(
            command=lambda args, timeout: SimpleNamespace(
                returncode=1, stdout="", stderr="policy unavailable"
            )
        )
        with self.assertRaisesRegex(dashboard.PolicyCommandError, "policy unavailable"):
            failed.status()

        def timeout(args, timeout):
            raise subprocess.TimeoutExpired(args, timeout)

        timed_out = dashboard.StoragePolicyManager(command=timeout, timeout=4)
        with self.assertRaisesRegex(dashboard.PolicyCommandError, "timed out after 4"):
            timed_out.status()


class UsbWatchScriptTests(unittest.TestCase):
    def test_json_snapshot_reuses_usb_and_filesystem_label_discovery(self):
        current = {
            ("001", "ID 1d6b:0002 Linux Foundation 2.0 root hub"): [1],
            ("002", "ID abcd:1234 Example Storage Device"): [7],
        }
        labels = {("002", 7): ["movingparts"]}
        payload = usb_watch.json_snapshot(current=current, labelmap=labels)
        self.assertEqual(payload["version"], 1)
        self.assertEqual(payload["devices"][0]["device_id"], "1d6b:0002")
        self.assertTrue(payload["devices"][0]["root_hub"])
        self.assertEqual(payload["devices"][1]["description"], "Example Storage Device")
        self.assertEqual(payload["devices"][1]["labels"], ["movingparts"])


class UsbDeviceMonitorTests(unittest.TestCase):
    @staticmethod
    def payload(*devices):
        return json.dumps({"version": 1, "devices": list(devices)})

    @staticmethod
    def device(device_id, description, count=1, labels=None, bus="002", root=False):
        return {
            "bus": bus,
            "device_id": device_id,
            "description": description,
            "present_count": count,
            "labels": list(labels or []),
            "root_hub": root,
        }

    def test_tracks_unplugged_replugged_and_new_devices_from_usb_watch(self):
        clock = FakeClock(1_000)
        samples = [
            self.payload(
                self.device("1d6b:0002", "Linux root hub", bus="001", root=True),
                self.device(
                    "abcd:1234", "Example Storage", count=2, labels=["movingparts"]
                ),
            ),
            self.payload(
                self.device("1d6b:0002", "Linux root hub", bus="001", root=True),
                self.device("beef:0001", "New Keyboard"),
            ),
            self.payload(
                self.device("1d6b:0002", "Linux root hub", bus="001", root=True),
                self.device(
                    "abcd:1234", "Example Storage", count=1, labels=["movingparts"]
                ),
                self.device("beef:0001", "New Keyboard"),
            ),
        ]
        calls = []

        def command(args, timeout):
            calls.append((list(args), timeout))
            return SimpleNamespace(returncode=0, stdout=samples.pop(0), stderr="")

        monitor = dashboard.UsbDeviceMonitor(
            tool="/test/usb_watch.py", command=command, timeout=4, wall_clock=clock
        )
        baseline = monitor.refresh()
        self.assertEqual(baseline["present_device_count"], 2)
        self.assertEqual(baseline["storage_labels"], ["movingparts"])
        self.assertTrue(all(device["event"] is None for device in baseline["devices"]))

        clock.advance(5)
        changed = monitor.refresh()
        storage = next(
            device for device in changed["devices"] if device["device_id"] == "abcd:1234"
        )
        keyboard = next(
            device for device in changed["devices"] if device["device_id"] == "beef:0001"
        )
        self.assertEqual(storage["status"], "unplugged")
        self.assertEqual(storage["event"], {"kind": "unplugged", "at": 1005})
        self.assertEqual(keyboard["event"], {"kind": "plugged", "at": 1005})
        self.assertEqual(changed["unplugged_device_count"], 2)

        clock.advance(5)
        replugged = monitor.refresh()
        storage = next(
            device for device in replugged["devices"] if device["device_id"] == "abcd:1234"
        )
        self.assertEqual(storage["status"], "partial")
        self.assertEqual(storage["event"], {"kind": "replugged", "at": 1010})
        self.assertEqual(
            calls[0],
            ([dashboard.sys.executable, "/test/usb_watch.py", "--json"], 4),
        )

    def test_failure_is_bounded_and_keeps_last_good_snapshot(self):
        responses = [
            SimpleNamespace(
                returncode=0,
                stdout=self.payload(self.device("abcd:1234", "Example Device")),
                stderr="",
            ),
            subprocess.TimeoutExpired("usb_watch", 3),
        ]

        def command(args, timeout):
            response = responses.pop(0)
            if isinstance(response, Exception):
                raise response
            return response

        monitor = dashboard.UsbDeviceMonitor(command=command, timeout=3)
        self.assertEqual(monitor.refresh()["present_device_count"], 1)
        stale = monitor.refresh()
        self.assertEqual(stale["present_device_count"], 1)
        self.assertIn("timed out after 3", stale["last_error"])

    def test_parser_rejects_unexpected_schema(self):
        invalid = (
            "not-json",
            json.dumps({"version": 2, "devices": []}),
            self.payload(self.device("not-an-id", "Bad device")),
            self.payload({**self.device("abcd:1234", "Bad labels"), "labels": "disk"}),
        )
        for payload in invalid:
            with self.subTest(payload=payload):
                with self.assertRaises(ValueError):
                    dashboard.UsbDeviceMonitor.parse_current(payload)


class SpeedTestManagerTests(unittest.TestCase):
    def test_parser_accepts_existing_speedtest_script_output(self):
        output = "Download Speed: 42.75 Mbps\nUpload Speed:   8.5 Mbps\nLatency:        37.2 ms\n"
        self.assertEqual(
            dashboard.parse_speedtest_output(output),
            {"download_mbps": 42.75, "upload_mbps": 8.5, "latency_ms": 37.2},
        )

    def test_speedtest_is_nonblocking_and_single_flight(self):
        entered = threading.Event()
        release = threading.Event()

        def command(args, timeout):
            self.assertEqual(args, ["/test/speedtest.sh"])
            self.assertEqual(timeout, 180)
            entered.set()
            release.wait(2)
            return SimpleNamespace(
                returncode=0,
                stdout="Download Speed: 50 Mbps\nUpload Speed: 10 Mbps\nLatency: 25 ms\n",
                stderr="",
            )

        manager = dashboard.SpeedTestManager(
            script="/test/speedtest.sh", command=command, timeout=180, wall_clock=lambda: 1234
        )
        self.assertTrue(manager.start())
        self.assertTrue(entered.wait(1))
        self.assertEqual(manager.snapshot()["status"], "running")
        self.assertFalse(manager.start())
        release.set()
        manager.thread.join(2)
        status = manager.snapshot()
        self.assertEqual(status["status"], "complete")
        self.assertEqual(status["download_mbps"], 50.0)
        self.assertEqual(status["completed_at"], 1234)


class CopLedManagerTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.store = dashboard.StateStore(os.path.join(self.tempdir.name, "state.json"))
        self.clock = FakeClock()
        self.engine = FakeEngine()
        self.target = {
            "state": "unavailable",
            "brightness": None,
            "color_temp_kelvin": None,
        }
        self.calls = []

        def command(args, timeout):
            self.calls.append(tuple(args))
            if args[1:3] == ["status", "light.ext_led"]:
                return SimpleNamespace(returncode=0, stdout=json.dumps(self.target), stderr="")
            if args[1:3] == ["set", "light.ext_led"]:
                self.target = {
                    "state": "on",
                    "brightness": int(args[3]),
                    "color_temp_kelvin": int(args[4]),
                }
                return SimpleNamespace(returncode=0, stdout="{}", stderr="")
            raise AssertionError(args)

        self.manager = dashboard.CopLedManager(
            self.store,
            self.engine,
            command=command,
            clock=self.clock,
            wall_clock=lambda: 1_700_000_000 + self.clock(),
            retry_interval=5,
            verify_interval=30,
        )

    def tearDown(self):
        self.tempdir.cleanup()

    def test_waits_for_wifi_then_applies_and_confirms_fixed_settings(self):
        self.store.set("cop_alert", True)
        waiting = self.manager.tick()
        self.assertEqual(waiting["phase"], "waiting")
        self.assertEqual(
            waiting["desired"], {"brightness": 255, "color_temp_kelvin": 2702}
        )
        self.assertFalse(any(call[1] == "set" for call in self.calls))

        self.target = {"state": "off", "brightness": 1, "color_temp_kelvin": 6500}
        self.clock.advance(5)
        confirmed = self.manager.tick()
        self.assertEqual(confirmed["phase"], "confirmed")
        self.assertIn("100%", confirmed["message"])
        self.assertEqual(self.target["brightness"], 255)
        self.assertEqual(self.target["color_temp_kelvin"], 2702)
        self.assertTrue(any(call[1] == "set" for call in self.calls))

    def test_pauses_while_engine_running_and_does_not_touch_light(self):
        self.store.set("cop_alert", True)
        self.engine.running = True
        status = self.manager.tick()
        self.assertEqual(status["phase"], "paused")
        self.assertEqual(self.calls, [])

    def test_reports_unavailable_after_wifi_grace_but_keeps_retrying(self):
        self.store.set("cop_alert", True)
        self.assertEqual(self.manager.tick()["phase"], "waiting")
        self.clock.advance(dashboard.COP_LED_CONNECT_GRACE)
        status = self.manager.tick()
        self.assertEqual(status["phase"], "unavailable")
        self.assertIn("still retrying", status["message"])
        self.assertIsNotNone(status["last_error"])

    def test_inactive_alert_never_queries_or_sets_lights(self):
        status = self.manager.tick()
        self.assertEqual(status["phase"], "inactive")
        self.assertEqual(self.calls, [])

class CopAlertManagerTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.clock = FakeClock()
        self.engine = FakeEngine()
        self.entity_state = "off"
        self.calls = []
        self.wakes = []
        self.store = dashboard.StateStore(os.path.join(self.tempdir.name, "state.json"))

        def command(args, timeout):
            self.calls.append(tuple(args))
            if args[0] == dashboard.TUYA_STATUS:
                return SimpleNamespace(stdout=self.entity_state + "\n", stderr="", returncode=0)
            if args[0] == dashboard.TUYA_TOGGLE:
                self.entity_state = args[2]
                return SimpleNamespace(stdout=args[2] + "\n", stderr="", returncode=0)
            if args[0] == dashboard.NTFY_SEND:
                return SimpleNamespace(stdout="", stderr="", returncode=0)
            raise AssertionError(args)

        def wake():
            self.wakes.append(self.clock())
            return True, "wake ok"

        self.manager = dashboard.CopAlertManager(
            self.store,
            self.engine,
            command=command,
            wake=wake,
            runtime_dir=os.path.join(self.tempdir.name, "run"),
            clock=self.clock,
            wall_clock=lambda: 1_700_000_000 + self.clock(),
        )

    def tearDown(self):
        self.wait_for_ntfy()
        self.tempdir.cleanup()

    def wait_for_ntfy(self, manager=None):
        manager = manager or self.manager
        worker = manager.ntfy_thread
        if worker:
            worker.join(1)
            self.assertFalse(worker.is_alive(), "ntfy test worker did not finish")

    def test_parked_running_parked_transition(self):
        status = self.manager.set_active(True)
        self.assertTrue(status["active"])
        self.assertEqual(self.entity_state, "on")
        self.assertTrue(os.path.isfile(self.manager.active_marker))
        self.wait_for_ntfy()
        self.assertTrue(any(call[0] == dashboard.NTFY_SEND for call in self.calls))

        self.manager.tick()
        self.assertEqual(len(self.wakes), 1)
        self.assertEqual(self.entity_state, "on")
        self.assertFalse(os.path.exists(self.manager.engine_marker))

        self.engine.running = True
        self.engine.rpm = 752.0
        self.clock.advance(max(dashboard.FLOOD_CHECK_INTERVAL, dashboard.WAKE_INTERVAL) + 1)
        self.manager.tick()
        self.assertEqual(self.entity_state, "off")
        self.assertEqual(len(self.wakes), 1, "running engine must suppress diagnostic wake")
        self.assertTrue(os.path.isfile(self.manager.engine_marker))

        self.engine.running = False
        self.engine.rpm = 0.0
        self.clock.advance(max(dashboard.FLOOD_CHECK_INTERVAL, dashboard.WAKE_INTERVAL) + 1)
        self.manager.tick()
        self.assertEqual(self.entity_state, "on")
        self.assertEqual(len(self.wakes), 2)
        self.assertFalse(os.path.exists(self.manager.engine_marker))

        self.manager.set_active(False)
        self.assertEqual(self.entity_state, "off")
        self.assertFalse(os.path.exists(self.manager.active_marker))

    def test_state_persists_across_manager_recreation(self):
        self.manager.set_active(True)
        self.wait_for_ntfy()
        reloaded = dashboard.StateStore(self.store.path)
        self.assertTrue(reloaded.get("cop_alert"))

    def test_activation_ntfy_is_immediate_nonblocking_and_single_flight(self):
        started = threading.Event()
        release = threading.Event()
        ntfy_calls = []
        ntfy_timeouts = []

        def command(args, timeout):
            if args[0] == dashboard.NTFY_SEND:
                ntfy_calls.append(tuple(args))
                ntfy_timeouts.append(timeout)
                started.set()
                release.wait(2)
                return SimpleNamespace(stdout="", stderr="", returncode=0)
            if args[0] == dashboard.TUYA_TOGGLE:
                return SimpleNamespace(stdout=args[2] + "\n", stderr="", returncode=0)
            if args[0] == dashboard.TUYA_STATUS:
                return SimpleNamespace(stdout="on\n", stderr="", returncode=0)
            raise AssertionError(args)

        manager = dashboard.CopAlertManager(
            self.store,
            self.engine,
            command=command,
            wake=lambda: (True, "wake ok"),
            runtime_dir=os.path.join(self.tempdir.name, "nonblocking-run"),
            clock=self.clock,
            wall_clock=lambda: 1_700_000_000 + self.clock(),
        )
        try:
            status = manager.set_active(True)
            self.assertTrue(status["active"])
            self.assertTrue(started.wait(1), "activation did not start ntfy")
            self.assertTrue(manager.ntfy_pending)
            self.assertEqual(len(ntfy_calls), 1)
            self.assertEqual(ntfy_timeouts, [dashboard.NTFY_TIMEOUT])

            self.clock.advance(dashboard.NTFY_INTERVAL + 1)
            manager.tick()
            self.assertEqual(len(ntfy_calls), 1, "a blocked send must not accumulate workers")
        finally:
            release.set()
            self.wait_for_ntfy(manager)

    def test_ntfy_worker_failure_does_not_escape_or_stop_cop_alert(self):
        def command(args, timeout):
            if args[0] == dashboard.NTFY_SEND:
                raise RuntimeError("network unavailable")
            if args[0] == dashboard.TUYA_TOGGLE:
                return SimpleNamespace(stdout=args[2] + "\n", stderr="", returncode=0)
            if args[0] == dashboard.TUYA_STATUS:
                return SimpleNamespace(stdout="on\n", stderr="", returncode=0)
            raise AssertionError(args)

        manager = dashboard.CopAlertManager(
            self.store,
            self.engine,
            command=command,
            wake=lambda: (True, "wake ok"),
            runtime_dir=os.path.join(self.tempdir.name, "failure-run"),
            clock=self.clock,
            wall_clock=lambda: 1_700_000_000 + self.clock(),
        )
        manager.set_active(True)
        self.wait_for_ntfy(manager)
        self.assertTrue(manager.snapshot()["active"])
        self.assertIn("network unavailable", manager.snapshot()["last_error"])
        manager.tick()


class DashboardRouteTests(unittest.TestCase):
    def test_index_and_manifest(self):
        client = dashboard.app.test_client()
        page = client.get("/")
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"COP ALERT", page.data)
        self.assertIn(b"Starlink", page.data)
        self.assertIn(b'id="openwrt-title">OpenWrt', page.data)
        self.assertIn(b"MWAN3", page.data)
        self.assertIn(b"ext_led", page.data)
        self.assertIn(b'id="cop-led"', page.data)
        self.assertNotIn(b"ext_flood", page.data)
        self.assertNotIn(b'id="flood"', page.data)
        self.assertNotIn(b">Connectivity<", page.data)
        self.assertNotIn(b"UBNT Availability", page.data)
        self.assertNotIn(b"UBNT Wireless", page.data)
        self.assertNotIn(b'id="ubnt-dot"', page.data)
        self.assertNotIn(b'id="wireless-dot"', page.data)
        self.assertNotIn(b">Internet Connectivity<", page.data)
        self.assertNotIn(b">Reachable<", page.data)
        self.assertNotIn(b"mwan-chip paused", page.data)
        self.assertNotIn(" · paused".encode(), page.data)
        self.assertIn(b"Speed Test", page.data)
        self.assertIn(b'data-running-label="Testing', page.data)
        self.assertIn(b'class="network-card-heading ubnt-card-heading"', page.data)
        self.assertIn(b'class="network-card-heading" id="openwrt-title"', page.data)
        self.assertIn(b'class="network-card-heading speedtest-card-head"', page.data)
        self.assertIn(b'id="sonos-track"', page.data)
        self.assertIn(b'id="sonos-progress"', page.data)
        self.assertIn(b'data-transport="play_pause"', page.data)
        self.assertIn(b"Group volume", page.data)
        self.assertIn(b"data-group-mute", page.data)
        self.assertIn(b"Disks &amp; Torrents", page.data)
        self.assertIn(b'id="system-monitor"', page.data)
        self.assertIn(b'id="system-monitor-panel"', page.data)
        self.assertIn(b'id="monitor-diagnosis"', page.data)
        self.assertIn(b'id="monitor-events"', page.data)
        self.assertIn(b'id="system-monitor-network"', page.data)
        self.assertIn(b'id="system-monitor-disk"', page.data)
        self.assertIn(b'id="usb-devices"', page.data)
        self.assertIn(b'id="usb-panel"', page.data)
        self.assertIn(b'id="usb-device-list"', page.data)
        self.assertIn(b'id="monitor-io-details"', page.data)
        self.assertIn(b'id="monitor-throttling"', page.data)
        self.assertIn(b'id="monitor-process-current"', page.data)
        self.assertIn(b'id="monitor-offenders"', page.data)
        self.assertIn(b'id="system-monitor-temperature"', page.data)
        self.assertIn(b'id="system-monitor-throttle"', page.data)
        self.assertIn(b'id="monitor-crash-analyze"', page.data)
        self.assertIn(b'id="monitor-crash-history"', page.data)
        self.assertIn(b'id="monitor-crash-timeline"', page.data)
        self.assertIn(b'data-monitor-hours="168"', page.data)
        self.assertIn(b'id="price-checks"', page.data)
        self.assertIn(b'id="price-panel"', page.data)
        self.assertIn(b'id="price-add-form"', page.data)
        self.assertIn(b'id="price-edit-cancel"', page.data)
        self.assertIn(b"Check all now", page.data)
        self.assertIn(b"Managed disks", page.data)
        self.assertIn(b'id="lighting-title">Lighting', page.data)
        self.assertIn(b'id="lighting-master"', page.data)
        self.assertIn(b'id="lighting-panel"', page.data)
        self.assertIn(b'id="lighting-groups"', page.data)
        self.assertIn(b'id="tile-edit"', page.data)
        self.assertIn(b'id="tile-grid"', page.data)
        self.assertIn(b'aria-label="Edit tile positions"', page.data)
        self.assertIn(b"UBNT Wi-Fi", page.data)
        self.assertIn(b'id="ubnt-radio-dot"', page.data)
        self.assertIn(b'id="openwrt-age"', page.data)
        self.assertNotIn(b'id="connectivity-age"', page.data)
        self.assertIn(b'id="openwrt-card" data-dashboard-tile', page.data)
        self.assertIn(b'id="speedtest-button" data-dashboard-tile', page.data)
        self.assertEqual(page.data.count(b"data-dashboard-tile"), 12)
        self.assertIn(b'class="network-card speedtest-card"', page.data)
        self.assertIn(b'id="ubnt-network-list"', page.data)
        self.assertIn(b'id="ubnt-password-form"', page.data)
        self.assertIn(b'data-policy-field="disks_enabled"', page.data)
        self.assertIn(b'data-policy-field="torrents_enabled"', page.data)
        self.assertIn(b'data-policy-field="allow_starlink_torrents"', page.data)
        self.assertIn(b'id="disk-runtime-state"', page.data)
        self.assertIn(b'id="torrent-runtime-state"', page.data)
        self.assertIn(b"Ignition always overrides disk permission", page.data)
        self.assertIn(b"requested-on Torrents switch is shown as blocked", page.data)
        self.assertIn(b"Requires Disks enabled", page.data)
        self.assertIn(b"does not override disk or global torrent permission", page.data)
        self.assertIn(
            b"Starlink torrenting requires Disks enabled, Torrents enabled", page.data
        )
        self.assertNotIn(b"<style>", page.data)
        self.assertIn(b'href="/static/van_dashboard.css"', page.data)
        self.assertIn(b'src="/static/van_dashboard.js"', page.data)
        javascript = client.get("/static/van_dashboard.js")
        stylesheet = client.get("/static/van_dashboard.css")
        self.addCleanup(javascript.close)
        self.addCleanup(stylesheet.close)
        self.assertEqual(javascript.status_code, 200)
        self.assertEqual(stylesheet.status_code, 200)
        self.assertIn(b"data-speaker-mute", javascript.data)
        self.assertIn(b"data-ubnt-profile", javascript.data)
        self.assertIn(b"startUbntWifi('provision'", javascript.data)
        self.assertIn(b"function renderUbntTile()", javascript.data)
        self.assertIn(b"networkState('ubnt-wifi-dot'", javascript.data)
        self.assertIn(b"networkState('ubnt-radio-dot'", javascript.data)
        self.assertIn(b"label.dataset.idleLabel", javascript.data)
        self.assertIn(b"label.dataset.runningLabel", javascript.data)
        self.assertIn(b"cop-led", javascript.data)
        self.assertNotIn(b"dashboard.ext_flood", javascript.data)
        self.assertNotIn(b"'Speed Test'", javascript.data)
        self.assertNotIn(b'"Speed Test"', javascript.data)
        self.assertIn(b"$('openwrt-age').textContent", javascript.data)
        self.assertNotIn(b"$('connectivity-age').textContent", javascript.data)
        self.assertNotIn(b"wireless-status", javascript.data)
        self.assertNotIn(b"ubnt-dot", javascript.data)
        self.assertIn(b"Stopped because disks are disabled", javascript.data)
        self.assertIn(b"function policyRequestBlocked", javascript.data)
        self.assertIn(b"function renderLighting(next)", javascript.data)
        self.assertIn(b"function renderPriceChecks(response)", javascript.data)
        self.assertIn(b"function renderSystemMonitor(response)", javascript.data)
        self.assertIn(b"function renderUsbDevices(response)", javascript.data)
        self.assertIn(b"/api/usb-devices", javascript.data)
        self.assertIn(b"function renderCrashAnalysis(payload)", javascript.data)
        self.assertIn(b"function renderCrashHistory(payload)", javascript.data)
        self.assertIn(b"function monitorEventState(event)", javascript.data)
        self.assertIn(b"function thermalSensorLabel(sensor)", javascript.data)
        self.assertIn(b"function monitorProcessList(items", javascript.data)
        self.assertIn(b"No events in range (${monitorRangeLabel()})", javascript.data)
        self.assertIn(b"function formatRate(value)", javascript.data)
        self.assertIn(b"network_rx_bytes_per_second", javascript.data)
        self.assertIn(b"disk_write_bytes_per_second", javascript.data)
        self.assertIn(b"/api/system-monitor?hours=", javascript.data)
        self.assertIn(b"system-monitor/crash-analysis", javascript.data)
        self.assertIn(b"price-checks/check", javascript.data)
        self.assertIn(b"price-checks/edit", javascript.data)
        self.assertIn(b"data-price-edit", javascript.data)
        self.assertIn(b"data-price-remove", javascript.data)
        self.assertIn(b"data-light-brightness", javascript.data)
        self.assertIn(b"TILE_ORDER_STORAGE_KEY", javascript.data)
        self.assertIn(b"localStorage.setItem", javascript.data)
        self.assertIn(b"function setupTileEditing()", javascript.data)
        self.assertIn(b"'pointerdown'", javascript.data)
        self.assertIn(b"'pointermove'", javascript.data)
        self.assertIn(b"ON \xc2\xb7 BLOCKED", javascript.data)
        self.assertIn(b"bookUrl.port", javascript.data)
        self.assertIn(b"'8787'", javascript.data)
        self.assertIn(b".policy-toggle", stylesheet.data)
        self.assertIn(b".policy-toggle.blocked", stylesheet.data)
        self.assertIn(b".policy-runtime-state::before", stylesheet.data)
        self.assertIn(b".monitor-crash-button", stylesheet.data)
        self.assertIn(b".monitor-crash-history-item", stylesheet.data)
        self.assertIn(b".lighting-master", stylesheet.data)
        self.assertIn(b".lighting-slider", stylesheet.data)
        self.assertIn(b".price-row", stylesheet.data)
        self.assertIn(b".price-form-grid", stylesheet.data)
        self.assertIn(b".monitor-diagnosis", stylesheet.data)
        self.assertIn(b".monitor-event-state", stylesheet.data)
        self.assertIn(b".monitor-io-details", stylesheet.data)
        self.assertIn(b".monitor-io-row", stylesheet.data)
        self.assertIn(b".usb-device-row", stylesheet.data)
        self.assertIn(b".usb-label", stylesheet.data)
        self.assertIn(b".tile-edit-button", stylesheet.data)
        self.assertIn(
            b"body.tiles-editing #tile-grid > [data-dashboard-tile]",
            stylesheet.data,
        )
        self.assertNotIn(b".network-cards", stylesheet.data)
        self.assertIn(b".network-card-heading", stylesheet.data)
        self.assertIn(b".mwan-list", stylesheet.data)
        self.assertIn(b"flex-direction: column", stylesheet.data)
        self.assertIn(b".speedtest-card", stylesheet.data)
        self.assertNotIn(b".openwrt-grid", stylesheet.data)
        self.assertIn(b".ubnt-network-row", stylesheet.data)
        manifest = client.get("/manifest.webmanifest")
        self.assertEqual(manifest.status_code, 200)
        self.assertEqual(manifest.json["name"], "Van Dashboard")

    def test_sync_scripts_deploys_dashboard_assets(self):
        repository = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        with open(os.path.join(repository, "pi", "sync_scripts.sh"), encoding="utf-8") as handle:
            sync_script = handle.read()
        self.assertIn(
            'cp -R "$pi_apps/van_dashboard/templates" "$python_stage/"',
            sync_script,
        )
        self.assertIn(
            'cp -R "$pi_apps/van_dashboard/static" "$python_stage/"',
            sync_script,
        )

    def test_ntfy_helper_has_bounded_network_timeouts(self):
        repository = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        with open(os.path.join(repository, "pi", "scripts", "ntfy_send.sh"), encoding="utf-8") as handle:
            script = handle.read()
        self.assertIn("--connect-timeout 5", script)
        self.assertIn("--max-time 15", script)

    def test_connectivity_and_speedtest_status_routes(self):
        client = dashboard.app.test_client()
        connectivity = client.get("/api/connectivity")
        self.assertEqual(connectivity.status_code, 200)
        self.assertEqual(connectivity.headers["Cache-Control"], "no-store")
        self.assertIn("router", connectivity.json["connectivity"])
        speedtest = client.get("/api/speedtest")
        self.assertEqual(speedtest.status_code, 200)
        self.assertIn(speedtest.json["speedtest"]["status"], ("idle", "complete", "error"))

    def test_usb_status_route_is_read_only_and_uncached(self):
        calls = []

        class FakeUsbDevices:
            def refresh(self):
                calls.append("refresh")
                return {
                    "checked_at": 123,
                    "last_success_at": 123,
                    "last_error": None,
                    "present_device_count": 2,
                    "unplugged_device_count": 0,
                    "storage_labels": ["movingparts"],
                    "devices": [],
                }

        original = dashboard.usb_devices
        dashboard.usb_devices = FakeUsbDevices()
        try:
            client = dashboard.app.test_client()
            response = client.get("/api/usb-devices")
            rejected = client.get("/api/usb-devices?command=anything")
        finally:
            dashboard.usb_devices = original

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(response.json["usb"]["present_device_count"], 2)
        self.assertEqual(rejected.status_code, 400)
        self.assertEqual(calls, ["refresh"])

    def test_system_monitor_route_has_bounded_ranges(self):
        calls = []

        class FakeSystemMonitor:
            def report(self, hours):
                calls.append(hours)
                return {
                    "ok": True,
                    "status": {"available": True, "stale": False, "current": {}},
                    "diagnosis": {"level": "good", "headline": "No faults"},
                    "peaks": {},
                    "events": [],
                }

        original = dashboard.system_monitor
        dashboard.system_monitor = FakeSystemMonitor()
        try:
            client = dashboard.app.test_client()
            default = client.get("/api/system-monitor")
            week = client.get("/api/system-monitor?hours=168")
            invalid = client.get("/api/system-monitor?hours=25")
            extra = client.get("/api/system-monitor?hours=24&command=anything")
        finally:
            dashboard.system_monitor = original

        self.assertEqual(default.status_code, 200)
        self.assertEqual(default.headers["Cache-Control"], "no-store")
        self.assertEqual(week.status_code, 200)
        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(extra.status_code, 400)
        self.assertEqual(calls, [6, 168])

    def test_crash_analysis_routes_save_and_return_full_history(self):
        calls = []

        class FakeSystemMonitor:
            def crash_analysis(self):
                calls.append(("analyze",))
                return {"ok": True, "saved": True, "analysis": {"available": True}}

            def crash_history(self, limit):
                calls.append(("history", limit))
                return {"ok": True, "history": []}

        original = dashboard.system_monitor
        dashboard.system_monitor = FakeSystemMonitor()
        try:
            client = dashboard.app.test_client()
            analysis = client.post("/api/system-monitor/crash-analysis")
            history = client.get("/api/system-monitor/crashes")
            bad_analysis = client.post(
                "/api/system-monitor/crash-analysis", data={"boot": "anything"}
            )
            bad_history = client.get("/api/system-monitor/crashes?limit=2")
        finally:
            dashboard.system_monitor = original

        self.assertEqual(analysis.status_code, 200)
        self.assertTrue(analysis.json["saved"])
        self.assertEqual(analysis.headers["Cache-Control"], "no-store")
        self.assertEqual(history.status_code, 200)
        self.assertEqual(history.headers["Cache-Control"], "no-store")
        self.assertEqual(bad_analysis.status_code, 400)
        self.assertEqual(bad_history.status_code, 400)
        self.assertEqual(calls, [("analyze",), ("history", 20)])

    def test_lighting_routes_are_authoritative_and_reject_unknown_inputs(self):
        calls = []
        status = {
            "state": "off",
            "on_count": 0,
            "available_count": 1,
            "total_count": 1,
            "groups": [],
        }

        class FakeLighting:
            entities = {"light.wiz_kitchen"}
            targets = {"all": ("light.wiz_kitchen",), "light.wiz_kitchen": ("light.wiz_kitchen",)}

            def status(self):
                calls.append(("status",))
                return status

            def set_power(self, target, enabled):
                calls.append(("power", target, enabled))
                return {**status, "state": "on" if enabled else "off"}

            def set_brightness(self, entity, brightness):
                calls.append(("brightness", entity, brightness))
                return {**status, "state": "on", "on_count": 1}

        original = dashboard.lighting
        dashboard.lighting = FakeLighting()
        try:
            client = dashboard.app.test_client()
            read = client.get("/api/lights")
            power = client.post(
                "/api/lights/power", data={"target": "all", "value": "true"}
            )
            brightness = client.post(
                "/api/lights/brightness",
                data={"entity": "light.wiz_kitchen", "brightness": "42"},
            )
            unknown_target = client.post(
                "/api/lights/power",
                data={"target": "switch.starlink", "value": "true"},
            )
            unknown_entity = client.post(
                "/api/lights/brightness",
                data={"entity": "light.unknown", "brightness": "42"},
            )
            bad_value = client.post(
                "/api/lights/power", data={"target": "all", "value": "toggle"}
            )
            extra = client.post(
                "/api/lights/power",
                data={"target": "all", "value": "true", "command": "anything"},
            )
        finally:
            dashboard.lighting = original

        self.assertEqual(read.status_code, 200)
        self.assertEqual(read.headers["Cache-Control"], "no-store")
        self.assertEqual(power.status_code, 200)
        self.assertEqual(brightness.status_code, 200)
        self.assertEqual(unknown_target.status_code, 400)
        self.assertEqual(unknown_entity.status_code, 400)
        self.assertEqual(bad_value.status_code, 400)
        self.assertEqual(extra.status_code, 400)
        self.assertEqual(
            calls,
            [
                ("status",),
                ("power", "all", True),
                ("brightness", "light.wiz_kitchen", 42),
            ],
        )

    def test_ubnt_wifi_routes_are_narrow_and_nonblocking(self):
        calls = []
        wifi = {
            "version": 1,
            "reachable": True,
            "checked_at": 123,
            "state": {"associated_ssid": "denlink", "automatic_paused": False},
            "profiles": [],
            "networks": [],
        }

        class FakeUbntWifi:
            def request_refresh(self):
                calls.append(("refresh", None))

            def start(self, kind, payload=None):
                calls.append((kind, dict(payload or {})))
                return True

            def snapshot(self):
                return {
                    "wifi": wifi,
                    "operation": {"status": "running", "kind": "test"},
                }

        original = dashboard.ubnt_wifi
        dashboard.ubnt_wifi = FakeUbntWifi()
        try:
            client = dashboard.app.test_client()
            status = client.get("/api/ubnt-wifi")
            scan = client.post("/api/ubnt-wifi/scan")
            connect = client.post(
                "/api/ubnt-wifi/connect", data={"profile": "Known Camp"}
            )
            provision = client.post(
                "/api/ubnt-wifi/provision",
                data={
                    "ssid": "New Camp",
                    "security": "wpa",
                    "bssid": "00:11:22:33:44:55",
                    "password": "test-password",
                },
            )
            resume = client.post("/api/ubnt-wifi/resume")
            unknown_security = client.post(
                "/api/ubnt-wifi/provision",
                data={
                    "ssid": "Old Camp",
                    "security": "wep",
                    "bssid": "00:11:22:33:44:55",
                    "password": "test-password",
                },
            )
            extra_scan_input = client.post(
                "/api/ubnt-wifi/scan", data={"command": "anything"}
            )
            extra_connect_input = client.post(
                "/api/ubnt-wifi/connect",
                data={"profile": "Known Camp", "command": "anything"},
            )
        finally:
            dashboard.ubnt_wifi = original

        self.assertEqual(status.status_code, 200)
        self.assertEqual(status.headers["Cache-Control"], "no-store")
        self.assertEqual(scan.status_code, 202)
        self.assertEqual(connect.status_code, 202)
        self.assertEqual(provision.status_code, 202)
        self.assertEqual(resume.status_code, 202)
        self.assertEqual(unknown_security.status_code, 400)
        self.assertEqual(extra_scan_input.status_code, 400)
        self.assertEqual(extra_connect_input.status_code, 400)
        self.assertEqual(
            calls,
            [
                ("refresh", None),
                ("scan", {}),
                ("connect", {"profile": "Known Camp"}),
                (
                    "provision",
                    {
                        "ssid": "New Camp",
                        "security": "wpa",
                        "bssid": "00:11:22:33:44:55",
                        "password": "test-password",
                    },
                ),
                ("resume", {}),
            ],
        )

    def test_storage_policy_get_update_and_input_rejection(self):
        policy = {
            "version": 1,
            "disks_enabled": True,
            "torrents_enabled": True,
            "allow_starlink_torrents": False,
            "runtime": {
                "disks_mounted": True,
                "mounted_disk_labels": ["movingparts"],
                "qbittorrent_running": True,
            },
        }
        calls = []

        def command(args, timeout):
            calls.append(list(args))
            if args == [dashboard.POLICYCTL, "--json", "torrents", "off"]:
                policy["torrents_enabled"] = False
            return SimpleNamespace(returncode=0, stdout=json.dumps(policy), stderr="")

        original = dashboard.storage_policy
        dashboard.storage_policy = dashboard.StoragePolicyManager(command=command)
        try:
            client = dashboard.app.test_client()
            status = client.get("/api/storage-policy")
            updated = client.post(
                "/api/storage-policy",
                data={"field": "torrents_enabled", "value": "false"},
            )
            unknown_field = client.post(
                "/api/storage-policy",
                data={"field": "command", "value": "true"},
            )
            unknown_value = client.post(
                "/api/storage-policy",
                data={"field": "disks_enabled", "value": "toggle"},
            )
            extra_input = client.post(
                "/api/storage-policy",
                data={"field": "disks_enabled", "value": "true", "extra": "x"},
            )
            duplicate_input = client.post(
                "/api/storage-policy",
                data={"field": ["disks_enabled", "torrents_enabled"], "value": "true"},
            )
        finally:
            dashboard.storage_policy = original

        self.assertEqual(status.status_code, 200)
        self.assertEqual(status.json["policy"]["version"], 1)
        self.assertEqual(updated.status_code, 200)
        self.assertFalse(updated.json["policy"]["torrents_enabled"])
        self.assertEqual(unknown_field.status_code, 400)
        self.assertEqual(unknown_value.status_code, 400)
        self.assertEqual(extra_input.status_code, 400)
        self.assertEqual(duplicate_input.status_code, 400)
        self.assertEqual(
            calls,
            [
                [dashboard.POLICYCTL, "--json", "status"],
                [dashboard.POLICYCTL, "--json", "torrents", "off"],
                [dashboard.POLICYCTL, "--json", "status"],
            ],
        )

    def test_starlink_power_change_requests_policy_reconciliation(self):
        events = []

        class FakeStarlink:
            def toggle(self):
                events.append("toggle")
                return {"state": "on", "available": True}

        class FakeConnectivity:
            def request_refresh(self):
                events.append("connectivity")

        def command(args, timeout):
            events.append(list(args))
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        originals = (dashboard.starlink, dashboard.connectivity, dashboard.storage_policy)
        dashboard.starlink = FakeStarlink()
        dashboard.connectivity = FakeConnectivity()
        dashboard.storage_policy = dashboard.StoragePolicyManager(command=command)
        try:
            response = dashboard.app.test_client().post("/api/starlink")
        finally:
            dashboard.starlink, dashboard.connectivity, dashboard.storage_policy = originals

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            events,
            ["toggle", "connectivity", [dashboard.POLICYCTL, "reconcile"]],
        )

    def test_sonos_transport_volume_and_mute_routes(self):
        front = FakeSpeaker("Front", 28, "PLAYING")
        art_requests = []

        def art_opener(request, timeout):
            art_requests.append((request.full_url, timeout))
            return FakeArtResponse()

        with tempfile.TemporaryDirectory() as tempdir:
            original = dashboard.sonos
            dashboard.sonos = dashboard.SonosController(
                dashboard.StateStore(os.path.join(tempdir, "state.json")),
                discover_func=lambda timeout: {front},
                art_opener=art_opener,
            )
            try:
                client = dashboard.app.test_client()
                speakers = client.get("/api/speakers")
                album_art = client.get(speakers.json["now_playing"]["album_art"])
                transport = client.post(
                    "/api/speakers/transport", data={"action": "play_pause"}
                )
                group_volume = client.post(
                    "/api/speakers/group-volume", data={"volume": "74"}
                )
                group_mute = client.post(
                    "/api/speakers/group-mute", data={"muted": "true"}
                )
                speaker_mute = client.post(
                    "/api/speakers/mute", data={"name": "Front", "muted": "true"}
                )
            finally:
                dashboard.sonos = original

        self.assertEqual(transport.status_code, 200)
        self.assertEqual(album_art.status_code, 200)
        self.assertEqual(album_art.mimetype, "image/jpeg")
        self.assertEqual(album_art.data, b"\xff\xd8\xfffake-jpeg")
        self.assertEqual(
            art_requests,
            [(front.track_info["album_art"], dashboard.SONOS_ART_TIMEOUT)],
        )
        self.assertEqual(front.transport_calls, ["pause"])
        self.assertEqual(group_volume.json["volume"], 74)
        self.assertEqual(front.group.volume, 74)
        self.assertTrue(group_mute.json["muted"])
        self.assertTrue(front.group.mute)
        self.assertTrue(speaker_mute.json["muted"])
        self.assertTrue(front.mute)

    def test_cop_alert_rejects_ambiguous_input_without_side_effects(self):
        client = dashboard.app.test_client()
        response = client.post("/api/cop-alert", data={"active": "maybe"})
        self.assertEqual(response.status_code, 400)

    def test_cross_origin_control_is_rejected(self):
        client = dashboard.app.test_client()
        response = client.post(
            "/api/cop-alert",
            data={"active": "true"},
            headers={"Origin": "https://example.com", "X-Van-Dashboard": "1"},
        )
        self.assertEqual(response.status_code, 403)


if __name__ == "__main__":
    unittest.main()
