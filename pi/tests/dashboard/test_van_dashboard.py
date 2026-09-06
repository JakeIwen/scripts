import copy
import io
import json
import os
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock
from email.message import Message
from pathlib import Path
from types import SimpleNamespace

from pi.apps.van_dashboard import van_dashboard as dashboard
from pi.scripts import usb_watch

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


class FakeClock:
    def __init__(self, value=100.0):
        self.value = value

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


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

    def test_idle_track_sentinels_are_reported_as_missing_progress(self):
        speaker = FakeSpeaker("Front", 28, "PAUSED_PLAYBACK")
        speaker.track_info = {
            "title": "",
            "artist": "",
            "album": "",
            "position": "NOT_IMPLEMENTED",
            "duration": "NOT_IMPLEMENTED",
            "album_art": "",
        }

        with tempfile.TemporaryDirectory() as tempdir:
            store = dashboard.StateStore(os.path.join(tempdir, "state.json"))
            controller = dashboard.SonosController(
                store, discover_func=lambda timeout: {speaker}
            )
            now_playing = controller.snapshot()["now_playing"]

        self.assertEqual(now_playing["title"], "Nothing playing")
        self.assertEqual(now_playing["position"], "")
        self.assertEqual(now_playing["duration"], "")
        self.assertIsNone(now_playing["album_art"])

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
                "default_policy": "balanced",
                "route_members": [{"name": "clientwan", "percent": 100}],
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
        self.assertEqual(status["router"]["route_members"][0]["name"], "clientwan")
        self.assertEqual(status["ubnt"]["ssid"], "denlink")
        self.assertFalse(status["stale"])
        monitor.request_refresh()
        self.assertTrue(monitor.refresh_requested)

    def test_active_lease_runs_successful_collections_sequentially(self):
        calls = []
        second_collection = threading.Event()
        command_gate = threading.Lock()

        def command(args, timeout):
            self.assertTrue(
                command_gate.acquire(blocking=False), "collector calls overlapped"
            )
            try:
                calls.append(len(calls) + 1)
                if len(calls) >= 2:
                    second_collection.set()
                threading.Event().wait(0.01)
                payload = {
                    "checked_at": 1_700_000_100 + len(calls),
                    "internet": {"online": True},
                    "router": {"reachable": True},
                    "ubnt": {"reachable": True},
                }
                return SimpleNamespace(
                    returncode=0, stdout=json.dumps(payload), stderr=""
                )
            finally:
                command_gate.release()

        monitor = dashboard.ConnectivityMonitor(
            collector="/test/connectivity",
            interval=60,
            command=command,
        )
        monitor.mark_active(lease=1)
        monitor.start()
        try:
            self.assertTrue(
                second_collection.wait(1), "active collection did not repeat"
            )
            self.assertTrue(monitor.snapshot()["active_mode"])
        finally:
            monitor.stop()
            monitor.thread.join(1)
        self.assertGreaterEqual(len(calls), 2)
        self.assertFalse(monitor.thread.is_alive())


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
        controller.mute(7, 3)
        controller.schedule()
        controller.parse_schedule("30 8,16 * * 1-5")
        controller.set_schedule("30 8,16 * * 1-5")
        controller.remove(7)
        controller.check(7)
        controller.add_search(
            "ebay", "https://www.ebay.com/sch/i.html?_nkw=tool", "Tools"
        )
        controller.dismiss_search_result(8, "123456789012")
        controller.remove_search(8)
        controller.check_search(8)
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
        self.assertEqual(calls[3], (prefix + ["mute", "7", "3"], 20))
        self.assertEqual(calls[4], (prefix + ["schedule"], 25))
        self.assertEqual(
            calls[5], (prefix + ["schedule-parse", "30 8,16 * * 1-5"], 25)
        )
        self.assertEqual(
            calls[6], (prefix + ["schedule-set", "30 8,16 * * 1-5"], 30)
        )
        self.assertEqual(calls[7], (prefix + ["remove", "7"], 20))
        self.assertEqual(calls[8], (prefix + ["check", "7"], 91))
        self.assertEqual(
            calls[9],
            (
                prefix
                + [
                    "search-add",
                    "ebay",
                    "https://www.ebay.com/sch/i.html?_nkw=tool",
                    "Tools",
                ],
                20,
            ),
        )
        self.assertEqual(
            calls[10],
            (prefix + ["search-dismiss", "8", "123456789012"], 20),
        )
        self.assertEqual(calls[11], (prefix + ["search-remove", "8"], 20))
        self.assertEqual(calls[12], (prefix + ["search-check", "8"], 91))

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

            def schedule(self):
                calls.append(("schedule",))
                return {
                    "ok": True,
                    "schedule": {
                        "expression": "0 10,15,20 * * *",
                        "description": "At minute 0 past hours 10, 15 and 20",
                        "error": None,
                    },
                }

            def set_schedule(self, expression):
                calls.append(("set_schedule", expression))
                return {
                    "ok": True,
                    "schedule": {
                        "expression": expression,
                        "description": "At minute 30 past hours 8 and 16",
                        "error": None,
                    },
                }

            def parse_schedule(self, expression):
                calls.append(("parse_schedule", expression))
                return {
                    "ok": True,
                    "schedule": {
                        "expression": expression,
                        "description": "At minute 30 past hours 8 and 16",
                        "error": None,
                        "error_code": None,
                    },
                }

            def add(self, *args):
                calls.append(("add", *args))
                return {**payload, "item": {"display_title": "Example"}}

            def edit(self, *args):
                calls.append(("edit", *args))
                return {**payload, "item": {"display_title": "Updated"}}

            def check(self, target):
                calls.append(("check", target))
                return {**payload, "checked": [{"id": 7}]}

            def mute(self, item_id, days):
                calls.append(("mute", item_id, days))
                return {
                    **payload,
                    "item": {"display_title": "Updated", "notifications_muted": True},
                }

            def remove(self, item_id):
                calls.append(("remove", item_id))
                return {**payload, "removed": {"display_title": "Example"}}

            def add_search(self, *args):
                calls.append(("add_search", *args))
                return {**payload, "search": {"display_title": "Tools"}}

            def dismiss_search_result(self, search_id, item_id):
                calls.append(("dismiss_search_result", search_id, item_id))
                return {
                    **payload,
                    "dismissed_result": {"title": "Unwanted tool"},
                }

            def remove_search(self, search_id):
                calls.append(("remove_search", search_id))
                return {
                    **payload,
                    "removed_search": {"display_title": "Tools"},
                }

            def check_search(self, target):
                calls.append(("check_search", target))
                return {**payload, "search_checked": [{"id": 8}]}

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
        schedule = self.client.post(
            "/api/price-checks/schedule",
            data={"expression": "30 8,16 * * 1-5"},
        )
        self.assertEqual(schedule.status_code, 200)
        self.assertIn("Schedule updated", schedule.get_json()["message"])
        preview = self.client.post(
            "/api/price-checks/schedule/parse",
            data={"expression": "30 8,16 * * 1-5"},
        )
        self.assertEqual(preview.status_code, 200)
        self.assertEqual(preview.get_json()["schedule"]["error_code"], None)
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
        mute = self.client.post(
            "/api/price-checks/mute", data={"id": "7", "days": "3"}
        )
        self.assertEqual(mute.status_code, 200)
        self.assertEqual(
            mute.get_json()["message"], "Muted notifications for Updated for 3 days"
        )
        self.assertEqual(
            self.client.post("/api/price-checks/check", data={"target": "7"}).status_code,
            200,
        )
        self.assertEqual(
            self.client.post("/api/price-checks/remove", data={"id": "7"}).status_code,
            200,
        )
        search_add = self.client.post(
            "/api/price-checks/searches/add",
            data={
                "parser": "ebay",
                "url": "https://www.ebay.com/sch/i.html?_nkw=tool",
                "title": "Tools",
            },
        )
        self.assertEqual(search_add.status_code, 200)
        self.assertEqual(search_add.get_json()["message"], "Watching Tools")
        self.assertEqual(
            self.client.post(
                "/api/price-checks/searches/dismiss",
                data={"id": "8", "item_id": "123456789012"},
            ).status_code,
            200,
        )
        self.assertEqual(
            self.client.post(
                "/api/price-checks/searches/check", data={"target": "8"}
            ).status_code,
            200,
        )
        self.assertEqual(
            self.client.post(
                "/api/price-checks/searches/remove", data={"id": "8"}
            ).status_code,
            200,
        )
        self.assertEqual(
            calls,
            [
                ("status",),
                ("schedule",),
                ("add", "amazon", "55", "https://example.com/item", "Example"),
                ("set_schedule", "30 8,16 * * 1-5"),
                ("parse_schedule", "30 8,16 * * 1-5"),
                (
                    "edit",
                    "7",
                    "amazon",
                    "45",
                    "https://example.com/updated",
                    "Updated",
                ),
                ("mute", "7", 3),
                ("check", "7"),
                ("remove", "7"),
                (
                    "add_search",
                    "ebay",
                    "https://www.ebay.com/sch/i.html?_nkw=tool",
                    "Tools",
                ),
                ("dismiss_search_result", "8", "123456789012"),
                ("check_search", "8"),
                ("remove_search", "8"),
            ],
        )

    def test_rejects_bad_forms_before_running_cli(self):
        class NeverCalled:
            def __getattr__(self, _name):
                self.fail("controller should not be called")

        response = self.client.post("/api/price-checks/check", data={"target": "bad"})
        self.assertEqual(response.status_code, 400)
        response = self.client.post(
            "/api/price-checks/mute", data={"id": "7", "days": "-1"}
        )
        self.assertEqual(response.status_code, 400)

    def test_schedule_failure_reports_that_previous_crontab_was_restored(self):
        class FailedSchedule:
            def set_schedule(self, _expression):
                raise dashboard.PriceCheckCommandError(
                    "crontab update failed; previous crontab restored"
                )

        dashboard.price_checks = FailedSchedule()
        response = self.client.post(
            "/api/price-checks/schedule",
            data={"expression": "30 8,16 * * 1-5"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("previous crontab restored", response.get_json()["message"])


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
        "profiles": [
            {
                "name": "denlink",
                "ssid": "denlink",
                "security": "wpa",
                "priority": 10,
                "bssid": "4E:EA:85:26:34:F4",
                "has_password": True,
                "output_power_dbm": 21,
                "rate_module": "atheros",
                "rate_auto": True,
                "rate_mcs": 4,
            }
        ],
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

    def test_profile_update_uses_fixed_tool_argv_and_does_not_retain_password(self):
        calls = []

        def command(args, timeout, input_text=None):
            calls.append((list(args), timeout, input_text))
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(
                    {"ok": True, "message": "updated", "wifi": self.WIFI}
                ),
                stderr="",
            )

        manager = dashboard.UbntWifiController(
            tool="/test/ubnt_wifi.py", command=command, wall_clock=FakeClock(200)
        )
        payload = {
            "profile": "denlink",
            "password": "replacement-password",
            "bssid": "00:11:22:33:44:55",
            "output_power_dbm": 18,
            "rate_module": "ewma_ht",
            "rate_auto": False,
            "rate_mcs": 4,
            "apply_now": False,
        }
        manager._run("update-profile", payload)

        self.assertEqual(
            calls[0][0], ["/test/ubnt_wifi.py", "--json", "update-profile"]
        )
        self.assertNotIn("replacement-password", " ".join(calls[0][0]))
        self.assertIn('"password":"replacement-password"', calls[0][2])
        self.assertEqual(payload["password"], "")
        self.assertEqual(manager.snapshot()["operation"]["status"], "complete")

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

    def test_abort_waits_for_safe_completion_then_resumes_automatic_selection(self):
        connect_started = threading.Event()
        release_connect = threading.Event()
        calls = []

        def command(args, timeout, input_text=None):
            calls.append(list(args))
            if args[-1] == "connect":
                connect_started.set()
                self.assertTrue(release_connect.wait(2))
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(
                    {"ok": True, "message": "complete", "wifi": self.WIFI}
                ),
                stderr="",
            )

        manager = dashboard.UbntWifiController(
            tool="/test/ubnt_wifi.py", command=command, wall_clock=FakeClock(200)
        )
        self.assertTrue(manager.start("connect", {"profile": "denlink"}))
        self.assertTrue(connect_started.wait(1))

        self.assertTrue(manager.abort())
        self.assertEqual(manager.snapshot()["operation"]["kind"], "abort")
        self.assertFalse(manager.abort())
        release_connect.set()
        manager.thread.join(2)

        operation = manager.snapshot()["operation"]
        self.assertEqual(operation["status"], "complete")
        self.assertEqual(operation["kind"], "abort")
        self.assertIn("automatic selection resumed", operation["message"])
        self.assertEqual(
            calls,
            [
                ["/test/ubnt_wifi.py", "--json", "connect"],
                ["/test/ubnt_wifi.py", "--json", "resume"],
            ],
        )


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
                "color_mode": "rgbww" if default_state == "on" else None,
                "supported_color_modes": ["color_temp", "rgbww"],
                "hs_color": [28.5, 75.0] if default_state == "on" else None,
                "color_temp_kelvin": None,
                "min_color_temp_kelvin": 2202,
                "max_color_temp_kelvin": 6535,
            }
            for _group_id, _group_label, lights in dashboard.LIGHT_GROUPS
            for entity, _label in lights
        ]

    def test_status_preserves_configured_groups_and_reports_percent(self):
        values = self.light_values()
        values[0].update(
            state="on", brightness=128, color_mode="rgbww", hs_color=[28.5, 75.0]
        )

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
        self.assertTrue(status["groups"][0]["lights"][0]["supports_hue"])
        self.assertEqual(status["groups"][0]["lights"][0]["hue"], 28.5)
        self.assertTrue(
            status["groups"][0]["lights"][0]["supports_color_temperature"]
        )
        self.assertEqual(
            status["groups"][0]["lights"][0]["min_color_temp_kelvin"], 2202
        )

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
            elif args[:3] == [dashboard.TUYA_LIGHT, "hue", "light.wiz_kitchen"]:
                values[4]["state"] = "on"
                values[4]["color_mode"] = "rgbww"
                values[4]["hs_color"] = [int(args[3]), 100]
                values[4]["color_temp_kelvin"] = None
            elif args[:3] == [
                dashboard.TUYA_LIGHT,
                "temperature",
                "light.wiz_kitchen",
            ]:
                values[4]["state"] = "on"
                values[4]["color_mode"] = "color_temp"
                values[4]["hs_color"] = None
                values[4]["color_temp_kelvin"] = int(args[3])
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(values) if args == [dashboard.TUYA_LIGHT, "list"] else "",
                stderr="",
            )

        controller = dashboard.LightingController(command=command)
        powered = controller.set_power("light.wiz_kitchen", True)
        dimmed = controller.set_brightness("light.wiz_kitchen", 40)
        colored = controller.set_hue("light.wiz_kitchen", 210)
        warmed = controller.set_color_temperature("light.wiz_kitchen", 3200)
        self.assertEqual(powered["on_count"], 1)
        self.assertEqual(dimmed["groups"][2]["lights"][0]["brightness"], 40)
        self.assertEqual(colored["groups"][2]["lights"][0]["hue"], 210.0)
        self.assertEqual(
            warmed["groups"][2]["lights"][0]["color_temp_kelvin"], 3200
        )
        self.assertEqual(calls[0], [dashboard.TUYA_TOGGLE, "light.wiz_kitchen", "on"])
        self.assertEqual(calls[1], [dashboard.TUYA_LIGHT, "list"])
        self.assertEqual(calls[2], [dashboard.TUYA_LIGHT, "set", "light.wiz_kitchen", "102"])
        self.assertEqual(calls[3], [dashboard.TUYA_LIGHT, "list"])
        self.assertEqual(calls[4], [dashboard.TUYA_LIGHT, "hue", "light.wiz_kitchen", "210"])
        self.assertEqual(calls[5], [dashboard.TUYA_LIGHT, "list"])
        self.assertEqual(
            calls[6],
            [dashboard.TUYA_LIGHT, "temperature", "light.wiz_kitchen", "3200"],
        )
        self.assertEqual(calls[7], [dashboard.TUYA_LIGHT, "list"])
        with self.assertRaisesRegex(ValueError, "unknown lighting target"):
            controller.set_power("switch.starlink", True)
        with self.assertRaisesRegex(ValueError, "unknown light entity"):
            controller.set_brightness("light.not_configured", 50)
        with self.assertRaisesRegex(ValueError, "hue must be"):
            controller.set_hue("light.wiz_kitchen", 361)
        with self.assertRaisesRegex(ValueError, "color temperature must be"):
            controller.set_color_temperature("light.wiz_kitchen", 1999)

    def test_rejects_bad_schema_and_reports_timeout(self):
        with self.assertRaises(dashboard.LightingCommandError):
            dashboard.LightingController.parse_status('{"not":"a list"}')
        bad_color = self.light_values()
        bad_color[0]["supported_color_modes"] = ["rgb", "rgb"]
        with self.assertRaisesRegex(
            dashboard.LightingCommandError, "supported color modes"
        ):
            dashboard.LightingController.parse_status(json.dumps(bad_color))
        bad_color[0]["supported_color_modes"] = [{"mode": "rgb"}]
        with self.assertRaisesRegex(
            dashboard.LightingCommandError, "supported color modes"
        ):
            dashboard.LightingController.parse_status(json.dumps(bad_color))

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


class DiskManagerTests(unittest.TestCase):
    CURRENT_BOOT_ID = "b" * 32
    PREVIOUS_BOOT_ID = "a" * 32
    CONFIG = """\
MOUNT_LABELS=(
  movingparts
  EXFAT512
)
ALWAYS_MOUNT_LABELS=(
  EXFAT512
)
MANUAL_MOUNT_LABELS=(
  bigboi
)
HDD_LABELS=(
  movingparts
  bigboi
)
"""

    @staticmethod
    def lsblk(movingparts_mounted=True):
        return {
            "blockdevices": [
                {
                    "name": "sda",
                    "path": "/dev/sda",
                    "type": "disk",
                    "tran": "usb",
                    "size": 5_000_000_000_000,
                    "mountpoints": [None],
                    "children": [
                        {
                            "name": "sda1",
                            "path": "/dev/sda1",
                            "pkname": "sda",
                            "type": "part",
                            "label": "movingparts",
                            "partlabel": None,
                            "fstype": "ext4",
                            "size": 4_999_000_000_000,
                            "mountpoints": (
                                ["/mnt/movingparts"] if movingparts_mounted else [None]
                            ),
                        }
                    ],
                },
                {
                    "name": "sdb",
                    "path": "/dev/sdb",
                    "type": "disk",
                    "tran": "usb",
                    "size": 6_000_000_000_000,
                    "mountpoints": [None],
                    "children": [
                        {
                            "name": "sdb1",
                            "path": "/dev/sdb1",
                            "pkname": "sdb",
                            "type": "part",
                            "label": "bigboi",
                            "partlabel": None,
                            "fstype": "ext4",
                            "size": 5_999_000_000_000,
                            "mountpoints": [None],
                        }
                    ],
                },
            ]
        }

    def manager(self, tempdir, command, **kwargs):
        config = os.path.join(tempdir, "disk_policy.sh")
        boot_id = os.path.join(tempdir, "boot_id")
        kwargs.setdefault("health_dir", os.path.join(tempdir, "health"))
        kwargs.setdefault("event_database", os.path.join(tempdir, "events.sqlite3"))
        with open(config, "w", encoding="utf-8") as handle:
            handle.write(self.CONFIG)
        with open(boot_id, "w", encoding="ascii") as handle:
            value = self.CURRENT_BOOT_ID
            handle.write(
                f"{value[:8]}-{value[8:12]}-{value[12:16]}-"
                f"{value[16:20]}-{value[20:]}\n"
            )
        return dashboard.DiskManager(
            config=config,
            control="/test/diskctl",
            hold_dir=os.path.join(tempdir, "holds"),
            boot_id_path=boot_id,
            command=command,
            wall_clock=lambda: 1_000,
            **kwargs,
        )

    def test_status_reports_managed_backup_absent_and_eject_hold_states(self):
        with tempfile.TemporaryDirectory() as tempdir:
            hold_dir = os.path.join(tempdir, "holds")
            os.mkdir(hold_dir)
            with open(os.path.join(hold_dir, "EXFAT512"), "w", encoding="ascii") as handle:
                handle.write("1060\n")

            def command(args, timeout):
                self.assertEqual(args[0], dashboard.LSBLK)
                return SimpleNamespace(
                    returncode=0,
                    stdout=json.dumps(self.lsblk()),
                    stderr="",
                )

            status = self.manager(tempdir, command).status()

        self.assertEqual(
            [disk["label"] for disk in status["disks"]],
            ["movingparts", "EXFAT512", "bigboi"],
        )
        movingparts, exfat, bigboi = status["disks"]
        self.assertTrue(movingparts["mounted"])
        self.assertTrue(movingparts["controllable"])
        self.assertTrue(movingparts["requires_disk_policy"])
        self.assertEqual(movingparts["device"], "/dev/sda")
        self.assertFalse(exfat["attached"])
        self.assertEqual(exfat["role"], "always")
        self.assertTrue(exfat["automatic_mount"])
        self.assertFalse(exfat["requires_disk_policy"])
        self.assertEqual(exfat["hold_remaining_seconds"], 60)
        self.assertEqual(exfat["hold_until"], 1060)
        self.assertEqual(bigboi["role"], "backup")
        self.assertFalse(bigboi["automatic_mount"])
        self.assertTrue(bigboi["requires_disk_policy"])
        self.assertTrue(bigboi["controllable"])
        self.assertTrue(bigboi["attached"])

    def test_health_maps_captured_storage_errors_and_honors_later_clean_check(self):
        with tempfile.TemporaryDirectory() as tempdir:
            database = os.path.join(tempdir, "events.sqlite3")
            health_dir = os.path.join(tempdir, "health")
            os.mkdir(health_dir)
            connection = sqlite3.connect(database)
            connection.execute(
                """
                CREATE TABLE events (
                    timestamp REAL,
                    category TEXT,
                    severity TEXT,
                    message TEXT,
                    state_json TEXT
                )
                """
            )
            connection.execute(
                "INSERT INTO events VALUES (?, ?, ?, ?, ?)",
                (
                    950,
                    "storage",
                    "critical",
                    "EXT4-fs (sda1): test I/O error",
                    json.dumps(
                        {
                            "boot_id": self.PREVIOUS_BOOT_ID,
                            "disk_io": {
                                "devices": [
                                    {"name": "sda", "labels": ["movingparts"]}
                                ]
                            }
                        }
                    ),
                ),
            )
            connection.commit()
            connection.close()

            def command(args, timeout):
                return SimpleNamespace(
                    returncode=0,
                    stdout=json.dumps(self.lsblk(movingparts_mounted=False)),
                    stderr="",
                )

            manager = self.manager(
                tempdir,
                command,
                health_dir=health_dir,
                event_database=database,
            )
            disk = manager.status()["disks"][0]
            self.assertEqual(disk["health"]["state"], "unknown")
            self.assertIsNone(disk["health"]["event_scope"])
            self.assertEqual(
                disk["health"]["observation"], "Currently attached and unmounted"
            )
            self.assertEqual(disk["health"]["recent_error_count"], 1)
            self.assertEqual(disk["health"]["current_boot_error_count"], 0)
            self.assertEqual(disk["health"]["previous_boot_error_count"], 1)
            self.assertIsNone(disk["health"]["current_error_message"])

            with open(
                os.path.join(health_dir, "movingparts.json"),
                "w",
                encoding="utf-8",
            ) as handle:
                json.dump(
                    {
                        "version": 1,
                        "label": "movingparts",
                        "state": "healthy",
                        "message": "Filesystem verified clean",
                        "checked_at": 960,
                    },
                    handle,
                )
            disk = manager.status()["disks"][0]
            self.assertEqual(disk["health"]["state"], "healthy")
            self.assertEqual(disk["health"]["basis"], "offline_check")
            self.assertEqual(disk["health"]["event_scope"], "cleared")
            self.assertEqual(disk["health"]["recent_error_count"], 0)
            self.assertEqual(disk["health"]["historical_error_count"], 1)

    def test_current_boot_storage_error_remains_critical(self):
        with tempfile.TemporaryDirectory() as tempdir:
            database = os.path.join(tempdir, "events.sqlite3")
            connection = sqlite3.connect(database)
            connection.execute(
                """
                CREATE TABLE events (
                    timestamp REAL,
                    category TEXT,
                    severity TEXT,
                    message TEXT,
                    state_json TEXT
                )
                """
            )
            connection.execute(
                "INSERT INTO events VALUES (?, ?, ?, ?, ?)",
                (
                    990,
                    "storage",
                    "critical",
                    "EXT4-fs (sda1): current I/O error",
                    json.dumps(
                        {
                            "boot_id": self.CURRENT_BOOT_ID,
                            "disk_io": {
                                "devices": [
                                    {"name": "sda", "labels": ["movingparts"]}
                                ]
                            },
                        }
                    ),
                ),
            )
            connection.commit()
            connection.close()

            manager = self.manager(
                tempdir,
                lambda args, timeout: SimpleNamespace(
                    returncode=0,
                    stdout=json.dumps(self.lsblk()),
                    stderr="",
                ),
                event_database=database,
            )
            with mock.patch.object(
                dashboard.DiskManager,
                "_mount_health",
                return_value={
                    "read_only": False,
                    "accessible": True,
                    "writable": True,
                    "error": None,
                },
            ):
                health = manager.status()["disks"][0]["health"]

        self.assertEqual(health["state"], "critical")
        self.assertEqual(health["event_scope"], "current_boot")
        self.assertEqual(health["current_boot_error_count"], 1)
        self.assertIn("current I/O error", health["current_error_message"])
        self.assertEqual(health["current_error_at"], 990)
        self.assertEqual(
            health["observation"], "Currently mounted read/write; access checks pass"
        )
        self.assertTrue(health["repairable"])

    def test_always_mount_labels_must_be_automatic_mount_labels(self):
        invalid = self.CONFIG.replace(
            "ALWAYS_MOUNT_LABELS=(\n  EXFAT512\n)",
            "ALWAYS_MOUNT_LABELS=(\n  unknown-flash\n)",
        )
        with self.assertRaisesRegex(dashboard.DiskCommandError, "must be a subset"):
            dashboard.DiskManager.parse_config(invalid)

    def test_eject_runs_only_fixed_diskctl_argv_in_background(self):
        calls = []

        def command(args, timeout):
            calls.append((list(args), timeout))
            if args[0] == dashboard.LSBLK:
                return SimpleNamespace(
                    returncode=0,
                    stdout=json.dumps(self.lsblk()),
                    stderr="",
                )
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        with tempfile.TemporaryDirectory() as tempdir:
            manager = self.manager(tempdir, command, action_timeout=23)
            manager.start_action("movingparts", "eject")
            manager.thread.join(2)
            status = manager.status()

        self.assertIn((["/test/diskctl", "eject", "movingparts"], 23), calls)
        self.assertEqual(status["operation"]["status"], "complete")
        self.assertEqual(status["operation"]["label"], "movingparts")

    def test_manual_disk_mount_runs_only_fixed_diskctl_argv_in_background(self):
        calls = []

        def command(args, timeout):
            calls.append((list(args), timeout))
            if args[0] == dashboard.LSBLK:
                return SimpleNamespace(
                    returncode=0,
                    stdout=json.dumps(self.lsblk()),
                    stderr="",
                )
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        with tempfile.TemporaryDirectory() as tempdir:
            manager = self.manager(tempdir, command, action_timeout=23)
            manager.start_action("bigboi", "mount")
            manager.thread.join(2)
            status = manager.status()

        self.assertIn((["/test/diskctl", "mount", "bigboi"], 23), calls)
        self.assertEqual(status["operation"]["status"], "complete")
        self.assertEqual(status["operation"]["label"], "bigboi")

    def test_ext4_repair_runs_only_fixed_diskctl_argv_in_background(self):
        calls = []

        def command(args, timeout):
            calls.append((list(args), timeout))
            if args[0] == dashboard.LSBLK:
                return SimpleNamespace(
                    returncode=0,
                    stdout=json.dumps(self.lsblk()),
                    stderr="",
                )
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        with tempfile.TemporaryDirectory() as tempdir:
            manager = self.manager(tempdir, command, action_timeout=23)
            manager.start_action("movingparts", "repair")
            manager.thread.join(2)
            status = manager.status()

        self.assertIn((["/test/diskctl", "repair", "movingparts"], 23), calls)
        self.assertEqual(status["operation"]["status"], "complete")
        self.assertEqual(status["operation"]["label"], "movingparts")

    def test_rejects_unknown_labels_actions_and_inapplicable_state(self):
        def command(args, timeout):
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(self.lsblk()),
                stderr="",
            )

        with tempfile.TemporaryDirectory() as tempdir:
            manager = self.manager(tempdir, command)
            with self.assertRaisesRegex(ValueError, "unknown controllable"):
                manager.start_action("/dev/sda", "eject")
            with self.assertRaisesRegex(dashboard.DiskCommandError, "not mounted"):
                manager.start_action("bigboi", "eject")
            with self.assertRaisesRegex(ValueError, "unknown disk action"):
                manager.start_action("movingparts", "cycle")
            with self.assertRaisesRegex(dashboard.DiskCommandError, "already mounted"):
                manager.start_action("movingparts", "mount")
            with self.assertRaisesRegex(dashboard.DiskCommandError, "not attached"):
                manager.start_action("EXFAT512", "mount")

    def test_action_failure_and_timeout_are_reported_without_blocking_request(self):
        for failure, expected in (("failure", "diskctl failed"), ("timeout", "timed out after 3")):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as tempdir:
                def command(args, timeout):
                    if args[0] == dashboard.LSBLK:
                        return SimpleNamespace(
                            returncode=0,
                            stdout=json.dumps(self.lsblk()),
                            stderr="",
                        )
                    if failure == "timeout":
                        raise subprocess.TimeoutExpired(args, timeout)
                    return SimpleNamespace(returncode=1, stdout="", stderr="diskctl failed")

                manager = self.manager(tempdir, command, action_timeout=3)
                manager.start_action("movingparts", "eject")
                manager.thread.join(2)
                operation = manager.status()["operation"]
                self.assertEqual(operation["status"], "error")
                self.assertIn(expected, operation["error"])


class SystemPowerControllerTests(unittest.TestCase):
    def test_reads_uptime_and_reports_boot_timestamp(self):
        with tempfile.TemporaryDirectory() as tempdir:
            uptime = Path(tempdir) / "uptime"
            uptime.write_text("93784.75 100.0\n", encoding="utf-8")
            result = dashboard.read_system_uptime(
                str(uptime), wall_clock=FakeClock(200000)
            )
        self.assertEqual(result["seconds"], 93784)
        self.assertEqual(
            result["booted_at"], "1970-01-02T05:30:15.250000+00:00"
        )

    def test_invalid_uptime_is_reported_as_unavailable(self):
        with tempfile.TemporaryDirectory() as tempdir:
            uptime = Path(tempdir) / "uptime"
            uptime.write_text("not-a-number\n", encoding="utf-8")
            result = dashboard.read_system_uptime(str(uptime))
        self.assertEqual(result, {"seconds": None, "booted_at": None})

    def test_runs_only_the_fixed_script_for_each_action(self):
        calls = []

        def command(args, timeout):
            calls.append((list(args), timeout))
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        controller = dashboard.SystemPowerController(
            scripts={
                "reboot": "/test/safe_reboot.sh",
                "power-down": "/test/safe_power_down.sh",
            },
            command=command,
            timeout=23,
            wall_clock=FakeClock(1000),
        )
        controller.start_action("reboot")
        controller.thread.join(2)
        self.assertEqual(controller.snapshot()["status"], "complete")
        controller.start_action("power-down")
        controller.thread.join(2)
        self.assertEqual(controller.snapshot()["status"], "complete")
        self.assertEqual(
            calls,
            [
                (["/test/safe_reboot.sh"], 23),
                (["/test/safe_power_down.sh"], 23),
            ],
        )

    def test_rejects_unknown_and_overlapping_actions(self):
        started = threading.Event()
        release = threading.Event()

        def command(args, timeout):
            started.set()
            release.wait(2)
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        controller = dashboard.SystemPowerController(command=command)
        with self.assertRaisesRegex(ValueError, "unknown system power action"):
            controller.start_action("shell-command")
        controller.start_action("reboot")
        self.assertTrue(started.wait(1))
        with self.assertRaisesRegex(dashboard.SystemPowerError, "already running"):
            controller.start_action("power-down")
        release.set()
        controller.thread.join(2)

    def test_reports_script_failure_and_timeout(self):
        for failure, expected in (
            ("failure", "unmount failed"),
            ("timeout", "timed out after 3"),
        ):
            with self.subTest(failure=failure):
                def command(args, timeout):
                    if failure == "timeout":
                        raise subprocess.TimeoutExpired(args, timeout)
                    return SimpleNamespace(
                        returncode=1, stdout="", stderr="unmount failed"
                    )

                controller = dashboard.SystemPowerController(
                    command=command, timeout=3
                )
                controller.start_action("reboot")
                controller.thread.join(2)
                operation = controller.snapshot()
                self.assertEqual(operation["status"], "error")
                self.assertIn(expected, operation["error"])


class DashboardRestartControllerTests(unittest.TestCase):
    def test_schedules_only_the_fixed_dashboard_service_restart(self):
        calls = []

        def command(args, timeout):
            calls.append((list(args), timeout))
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        controller = dashboard.DashboardRestartController(
            sudo="/test/sudo",
            systemd_run="/test/systemd-run",
            systemctl="/test/systemctl",
            service="test-dashboard.service",
            command=command,
            timeout=7,
            wall_clock=FakeClock(1000),
        )
        result = controller.restart()

        self.assertEqual(result, {"scheduled_at": 1000})
        self.assertEqual(len(calls), 1)
        args, timeout = calls[0]
        self.assertEqual(timeout, 7)
        self.assertEqual(args[:5], [
            "/test/sudo",
            "-n",
            "/test/systemd-run",
            "--quiet",
            "--collect",
        ])
        self.assertRegex(args[5], r"^--unit=van-dashboard-web-restart-\d+-1000$")
        self.assertEqual(
            args[6:],
            [
                "--on-active=1s",
                "/test/systemctl",
                "restart",
                "test-dashboard.service",
            ],
        )

    def test_reports_scheduler_failure_and_timeout(self):
        for failure, expected in (
            ("failure", "scheduler failed"),
            ("timeout", "timed out after 4 seconds"),
        ):
            with self.subTest(failure=failure):
                def command(args, timeout):
                    if failure == "timeout":
                        raise subprocess.TimeoutExpired(args, timeout)
                    return SimpleNamespace(
                        returncode=1, stdout="", stderr="scheduler failed"
                    )

                controller = dashboard.DashboardRestartController(
                    command=command, timeout=4
                )
                with self.assertRaisesRegex(
                    dashboard.DashboardRestartError, expected
                ):
                    controller.restart()


class TelemetrySummaryReaderTests(unittest.TestCase):
    @staticmethod
    def opener(payload, calls):
        def open_snapshot(request, timeout):
            calls.append((request.full_url, timeout))
            return io.BytesIO(json.dumps(payload).encode())

        return open_snapshot

    @staticmethod
    def engine_off_payload(value=12.7, observed_at="2026-07-26T20:30:00+00:00"):
        return {
            "version": 1,
            "service": "van-telemetry",
            "role": "engine_off_voltage",
            "sample_type": "engine_off",
            "value": value,
            "unit": "V",
            "source": "ccan.broadcast.0x41a",
            "bus": "c-can",
            "acquisition": "passive",
            "quality": "verified",
            "observed_at": observed_at,
            "engine_stopped_at": "2026-07-26T20:29:00+00:00",
            "saved_at": "2026-07-26T20:31:00+00:00",
        }

    def test_prefers_fresh_live_voltage(self):
        calls = []
        payload = {
            "metrics": {
                "battery.voltage": {
                    "available": True,
                    "stale": False,
                    "value": 12.61,
                    "observed_at": "2026-07-27T01:24:52+00:00",
                }
            }
        }
        reader = dashboard.TelemetrySummaryReader(
            snapshot_url="http://telemetry.test/v1/snapshot",
            voltage_csv="/does/not/matter.csv",
            timeout=2.5,
            opener=self.opener(payload, calls),
        )
        result = reader.snapshot()
        self.assertEqual(result["source"], "live")
        self.assertEqual(result["value"], 12.61)
        self.assertEqual(
            result["observed_at"], "2026-07-27T01:24:52+00:00"
        )
        self.assertEqual(calls, [("http://telemetry.test/v1/snapshot", 2.5)])

    def test_uses_latest_valid_voltage_mon_sample_when_live_is_stale(self):
        payload = {
            "metrics": {
                "battery.voltage": {
                    "available": True,
                    "stale": True,
                    "value": 13.2,
                }
            }
        }
        with tempfile.TemporaryDirectory() as tempdir:
            voltage_csv = Path(tempdir) / "voltage.csv"
            voltage_csv.write_text(
                "2026-07-25T05:06:05,,CAN-CH confirmed\n"
                "2026-07-26T19:24:52,12.6,wake-assisted C-CAN\n"
                "2026-07-26T20:00:00,,bus silent\n",
                encoding="utf-8",
            )
            reader = dashboard.TelemetrySummaryReader(
                voltage_csv=str(voltage_csv),
                opener=self.opener(payload, []),
            )
            result = reader.snapshot()
        self.assertEqual(result["source"], "voltage_mon")
        self.assertEqual(result["value"], 12.6)
        self.assertTrue(result["observed_at"].startswith("2026-07-26T19:24:52"))

    def test_uses_engine_off_sample_when_it_is_newer_than_scheduled_log(self):
        payload = {
            "metrics": {
                "battery.voltage": {
                    "available": True,
                    "stale": True,
                    "value": 13.8,
                }
            }
        }
        with tempfile.TemporaryDirectory() as tempdir:
            voltage_csv = Path(tempdir) / "voltage.csv"
            voltage_csv.write_text(
                "2026-07-26T20:00:00+00:00,12.5,scheduled\n",
                encoding="utf-8",
            )
            engine_off = Path(tempdir) / "engine-off.json"
            engine_off.write_text(
                json.dumps(self.engine_off_payload()),
                encoding="utf-8",
            )
            reader = dashboard.TelemetrySummaryReader(
                voltage_csv=str(voltage_csv),
                engine_off_status=str(engine_off),
                opener=self.opener(payload, []),
            )
            result = reader.snapshot()

        self.assertEqual(result["source"], "engine_off")
        self.assertEqual(result["value"], 12.7)
        self.assertEqual(result["detail"], "Engine-off passive sample")

    def test_uses_scheduled_log_when_it_is_newer_than_engine_off_sample(self):
        payload = {"metrics": {"battery.voltage": {"available": False}}}
        with tempfile.TemporaryDirectory() as tempdir:
            voltage_csv = Path(tempdir) / "voltage.csv"
            voltage_csv.write_text(
                "2026-07-26T22:00:00+00:00,12.4,scheduled\n",
                encoding="utf-8",
            )
            engine_off = Path(tempdir) / "engine-off.json"
            engine_off.write_text(
                json.dumps(self.engine_off_payload()),
                encoding="utf-8",
            )
            reader = dashboard.TelemetrySummaryReader(
                voltage_csv=str(voltage_csv),
                engine_off_status=str(engine_off),
                opener=self.opener(payload, []),
            )
            result = reader.snapshot()

        self.assertEqual(result["source"], "voltage_mon")
        self.assertEqual(result["value"], 12.4)

    def test_engine_off_reader_rejects_links_oversized_and_wrong_role(self):
        with tempfile.TemporaryDirectory() as tempdir:
            target = Path(tempdir) / "target.json"
            target.write_text(json.dumps(self.engine_off_payload()), encoding="utf-8")
            linked = Path(tempdir) / "linked.json"
            linked.symlink_to(target)
            self.assertIsNone(dashboard.read_engine_off_voltage_sample(str(linked)))

            target.write_bytes(
                b"x" * (dashboard.ENGINE_OFF_VOLTAGE_STATUS_MAX_BYTES + 1)
            )
            self.assertIsNone(dashboard.read_engine_off_voltage_sample(str(target)))

            wrong = self.engine_off_payload()
            wrong["role"] = "another_role"
            target.write_text(json.dumps(wrong), encoding="utf-8")
            self.assertIsNone(dashboard.read_engine_off_voltage_sample(str(target)))

    def test_live_failure_and_missing_log_return_no_data(self):
        def unavailable(_request, timeout):
            raise OSError(f"timeout after {timeout}")

        reader = dashboard.TelemetrySummaryReader(
            voltage_csv="/missing/voltage.csv",
            opener=unavailable,
        )
        self.assertEqual(
            reader.snapshot(),
            {
                "available": False,
                "value": None,
                "unit": "V",
                "source": None,
                "observed_at": None,
                "detail": "No battery voltage reading available",
            },
        )


class TelemetryServiceTests(unittest.TestCase):
    def test_status_and_toggle_use_only_the_fixed_broker(self):
        active = False
        calls = []

        def command(args, timeout):
            nonlocal active
            calls.append(list(args))
            if args[0] == dashboard.SYSTEMCTL:
                return SimpleNamespace(
                    returncode=0 if active else 3,
                    stdout="",
                    stderr="",
                )
            active = args[3] == "start"
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        original = dashboard.run_command
        dashboard.run_command = command
        try:
            self.assertFalse(dashboard.telemetry_service_status()["running"])
            action, status = dashboard.toggle_telemetry_service()
            self.assertEqual((action, status["running"]), ("start", True))
            action, status = dashboard.toggle_telemetry_service()
            self.assertEqual((action, status["running"]), ("stop", False))
        finally:
            dashboard.run_command = original
        self.assertIn(
            [dashboard.SUDO, "-n", dashboard.SYSTEMCTL, "start", dashboard.TELEMETRY_SERVICE],
            calls,
        )
        self.assertIn(
            [dashboard.SUDO, "-n", dashboard.SYSTEMCTL, "stop", dashboard.TELEMETRY_SERVICE],
            calls,
        )

    def test_failures_are_contained(self):
        for failure, expected in (
            ("failure", "systemctl failed"),
            ("timeout", "timed out after 3 seconds"),
        ):
            with self.subTest(failure=failure):
                def command(args, timeout):
                    if failure == "timeout":
                        raise subprocess.TimeoutExpired(args, timeout)
                    return SimpleNamespace(
                        returncode=1, stdout="", stderr="systemctl failed"
                    )

                original_timeout = dashboard.TELEMETRY_SERVICE_TIMEOUT
                original_command = dashboard.run_command
                dashboard.TELEMETRY_SERVICE_TIMEOUT = 3
                dashboard.run_command = command
                try:
                    with self.assertRaisesRegex(
                        dashboard.TelemetryServiceError, expected
                    ):
                        dashboard.telemetry_service_status()
                finally:
                    dashboard.TELEMETRY_SERVICE_TIMEOUT = original_timeout
                    dashboard.run_command = original_command


class VoltageCheckManagerTests(unittest.TestCase):
    def test_runs_guarded_monitor_in_background_and_blocks_duplicates(self):
        with tempfile.TemporaryDirectory() as tempdir:
            voltage_csv = Path(tempdir) / "voltage.csv"
            voltage_csv.write_text(
                "2026-08-10T10:00:00-05:00,12.4,old\n",
                encoding="utf-8",
            )
            entered = threading.Event()
            release = threading.Event()
            calls = []

            def command(args, timeout):
                calls.append((list(args), timeout))
                entered.set()
                release.wait(2)
                voltage_csv.write_text(
                    "2026-08-10T10:01:00-05:00,12.4,new\n",
                    encoding="utf-8",
                )
                return SimpleNamespace(returncode=0, stdout="", stderr="")

            manager = dashboard.VoltageCheckManager(
                tool="/test/voltage_mon.sh",
                voltage_csv=str(voltage_csv),
                command=command,
                timeout=12,
                wall_clock=lambda: 123,
            )
            self.assertTrue(manager.start())
            self.assertTrue(entered.wait(1))
            self.assertEqual(manager.snapshot()["status"], "running")
            self.assertFalse(manager.start())
            release.set()
            manager.thread.join(2)

        self.assertEqual(
            calls,
            [(["/test/voltage_mon.sh", "--no-notify"], 12)],
        )
        self.assertEqual(manager.snapshot()["status"], "complete")

    def test_accepts_low_voltage_exit_and_reports_failures(self):
        with tempfile.TemporaryDirectory() as tempdir:
            voltage_csv = Path(tempdir) / "voltage.csv"
            voltage_csv.write_text(
                "2026-08-10T10:00:00-05:00,12.4,old\n",
                encoding="utf-8",
            )

            def low_voltage(_args, timeout):
                self.assertEqual(timeout, dashboard.VOLTAGE_CHECK_TIMEOUT)
                voltage_csv.write_text(
                    "2026-08-10T10:01:00-05:00,11.9,new low voltage\n",
                    encoding="utf-8",
                )
                return SimpleNamespace(returncode=2, stdout="", stderr="")

            manager = dashboard.VoltageCheckManager(
                voltage_csv=str(voltage_csv),
                command=low_voltage,
            )
            manager.start()
            manager.thread.join(2)
            self.assertEqual(manager.snapshot()["status"], "complete")

            for outcome, expected in (
                ("failed", "monitor failed"),
                ("unchanged", "did not record a new voltage sample"),
                ("timeout", "timed out"),
            ):
                def command(args, timeout, outcome=outcome):
                    if outcome == "timeout":
                        raise subprocess.TimeoutExpired(args, timeout)
                    if outcome == "failed":
                        return SimpleNamespace(
                            returncode=1,
                            stdout="",
                            stderr="monitor failed",
                        )
                    return SimpleNamespace(returncode=0, stdout="", stderr="")

                failed = dashboard.VoltageCheckManager(
                    voltage_csv=str(voltage_csv),
                    command=command,
                    timeout=4,
                )
                with self.subTest(outcome=outcome):
                    failed.start()
                    failed.thread.join(2)
                    self.assertEqual(failed.snapshot()["status"], "error")
                    self.assertIn(expected, failed.snapshot()["error"])


class UsbWatchScriptTests(unittest.TestCase):
    def test_json_snapshot_reuses_usb_and_filesystem_label_discovery(self):
        current = {
            ("001", "ID 1d6b:0002 Linux Foundation 2.0 root hub"): [1],
            ("002", "ID abcd:1234 Example Storage Device"): [7],
        }
        labels = {("002", 7): ["movingparts"]}
        instances = {
            ("002", 7): {
                "device_number": 7,
                "location": "2-2.3",
                "parent_location": "2-2",
                "port": 3,
            }
        }
        payload = usb_watch.json_snapshot(
            current=current, labelmap=labels, instances=instances
        )
        self.assertEqual(payload["version"], 2)
        self.assertEqual(payload["devices"][0]["device_id"], "1d6b:0002")
        self.assertTrue(payload["devices"][0]["root_hub"])
        self.assertEqual(payload["devices"][1]["description"], "Example Storage Device")
        self.assertEqual(payload["devices"][1]["labels"], ["movingparts"])
        self.assertEqual(payload["devices"][1]["instances"][0]["location"], "2-2.3")
        self.assertEqual(payload["devices"][1]["instances"][0]["labels"], ["movingparts"])


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

    def test_remembers_topology_for_partial_and_unplugged_devices(self):
        def instance(device_number, location, parent, port):
            return {
                "device_number": device_number,
                "location": location,
                "parent_location": parent,
                "port": port,
                "labels": [],
            }

        base = self.device("abcd:1234", "Twin readers", count=2)
        samples = [
            json.dumps(
                {
                    "version": 2,
                    "devices": [
                        {
                            **base,
                            "instances": [
                                instance(4, "2-2.1", "2-2", 1),
                                instance(5, "2-2.2", "2-2", 2),
                            ],
                        }
                    ],
                }
            ),
            json.dumps(
                {
                    "version": 2,
                    "devices": [
                        {
                            **base,
                            "present_count": 1,
                            "instances": [instance(4, "2-2.1", "2-2", 1)],
                        }
                    ],
                }
            ),
            json.dumps({"version": 2, "devices": []}),
        ]

        monitor = dashboard.UsbDeviceMonitor(
            command=lambda _args, timeout: SimpleNamespace(
                returncode=0,
                stdout=samples.pop(0),
                stderr="",
            )
        )
        monitor.refresh()
        partial = monitor.refresh()["devices"][0]
        self.assertEqual(
            [item["location"] for item in partial["instances"]],
            ["2-2.1"],
        )
        self.assertEqual(
            [item["location"] for item in partial["known_instances"]],
            ["2-2.1", "2-2.2"],
        )

        unplugged = monitor.refresh()["devices"][0]
        self.assertEqual(unplugged["instances"], [])
        self.assertEqual(
            [item["location"] for item in unplugged["known_instances"]],
            ["2-2.1", "2-2.2"],
        )

    def test_parser_rejects_unexpected_schema(self):
        invalid = (
            "not-json",
            json.dumps({"version": 3, "devices": []}),
            self.payload(self.device("not-an-id", "Bad device")),
            self.payload({**self.device("abcd:1234", "Bad labels"), "labels": "disk"}),
        )
        for payload in invalid:
            with self.subTest(payload=payload):
                with self.assertRaises(ValueError):
                    dashboard.UsbDeviceMonitor.parse_current(payload)

    def test_parser_accepts_version_two_topology_instances(self):
        device = {
            **self.device("abcd:1234", "Storage", labels=["movingparts"]),
            "instances": [
                {
                    "device_number": 5,
                    "location": "2-2.3",
                    "parent_location": "2-2",
                    "port": 3,
                    "labels": ["movingparts"],
                }
            ],
        }
        parsed = dashboard.UsbDeviceMonitor.parse_current(
            json.dumps({"version": 2, "devices": [device]})
        )
        self.assertEqual(next(iter(parsed.values()))["instances"][0]["port"], 3)


class UsbPortControllerTests(unittest.TestCase):
    UHUB_STATUS = """Current status for hub 2 [1d6b:0003 Linux xHCI Host Controller, USB 3.00, 2 ports, ppps]
  Port 1: 0203 power 5gbps U0 enable connect [0781:5591 SanDisk Drive]
  Port 2: 02a0 power 5gbps Rx.Detect
"""

    @staticmethod
    def write(path, value):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(str(value))

    @staticmethod
    def usb_state():
        return {
            "devices": [
                {
                    "description": "GenesysLogic USB3.1 Hub",
                    "device_id": "05e3:0626",
                    "present_count": 1,
                    "root_hub": False,
                    "instances": [
                        {
                            "device_number": 3,
                            "location": "2-2",
                            "parent_location": "2",
                            "port": 2,
                            "labels": [],
                        }
                    ],
                },
                {
                    "description": "Seagate Portable",
                    "device_id": "0bc2:2344",
                    "present_count": 1,
                    "root_hub": False,
                    "instances": [
                        {
                            "device_number": 5,
                            "location": "2-2.3",
                            "parent_location": "2-2",
                            "port": 3,
                            "labels": ["movingparts"],
                        }
                    ],
                },
            ]
        }

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.sys_root = os.path.join(self.tempdir.name, "sys")
        self.dev_root = os.path.join(self.tempdir.name, "dev")
        self.mounts = os.path.join(self.tempdir.name, "mounts")
        self.write(
            os.path.join(
                self.sys_root,
                "bus",
                "usb",
                "devices",
                "2-0:1.0",
                "usb2-port1",
                "disable",
            ),
            "0\n",
        )
        for port in (3, 4):
            self.write(
                os.path.join(
                    self.sys_root,
                    "bus",
                    "usb",
                    "devices",
                    "2-2:1.0",
                    f"2-2-port{port}",
                    "disable",
                ),
                "0\n",
            )
        os.makedirs(os.path.join(self.dev_root, "disk", "by-label"), exist_ok=True)
        disk = os.path.join(self.dev_root, "sda1")
        self.write(disk, "")
        os.symlink(disk, os.path.join(self.dev_root, "disk", "by-label", "movingparts"))
        self.write(self.mounts, f"{disk} /mnt/movingparts ext4 rw 0 0\n")
        self.calls = []

        class Devices:
            def refresh(inner_self):
                return UsbPortControllerTests.usb_state()

        def command(args, timeout, input_text=None):
            self.calls.append((list(args), timeout, input_text))
            if args == [dashboard.SUDO, "-n", dashboard.UHUBCTL]:
                return SimpleNamespace(returncode=0, stdout=self.UHUB_STATUS, stderr="")
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        self.controller = dashboard.UsbPortController(
            Devices(),
            command=command,
            sys_root=self.sys_root,
            dev_root=self.dev_root,
            mounts_path=self.mounts,
            timeout=4,
            snapshot_ttl=300,
            wall_clock=lambda: 1000,
            sleeper=lambda _seconds: None,
        )

    def tearDown(self):
        self.tempdir.cleanup()

    def test_parses_power_hubs_and_maps_readable_devices_to_kernel_ports(self):
        state = self.controller.discover()
        ports = {
            port["key"]: port
            for hub in state["hubs"]
            for port in hub["ports"]
        }
        self.assertEqual(ports["2:1"]["method"], "power")
        self.assertEqual(ports["2-2:3"]["method"], "disable")
        self.assertEqual(ports["2-2:3"]["device_descriptions"], ["Seagate Portable"])
        self.assertEqual(ports["2-2:3"]["mounted_labels"], ["movingparts"])
        self.assertEqual(ports["2-2:4"]["device_descriptions"], [])
        self.assertTrue(state["loaded"])
        self.assertEqual(state["expires_at"], 1300)

    def test_passive_snapshot_never_runs_uhubctl(self):
        state = self.controller.snapshot()

        self.assertEqual(self.calls, [])
        self.assertFalse(state["loaded"])
        self.assertFalse(state["expired"])
        self.assertEqual(state["hubs"], [])

    def test_explicit_discovery_is_serialized(self):
        active = 0
        maximum = 0
        state_lock = threading.Lock()
        first_entered = threading.Event()
        release = threading.Event()

        class Devices:
            def refresh(inner_self):
                return UsbPortControllerTests.usb_state()

        def command(args, timeout, input_text=None):
            nonlocal active, maximum
            self.assertEqual(args, [dashboard.SUDO, "-n", dashboard.UHUBCTL])
            with state_lock:
                active += 1
                maximum = max(maximum, active)
                if not first_entered.is_set():
                    first_entered.set()
            release.wait(2)
            with state_lock:
                active -= 1
            return SimpleNamespace(returncode=0, stdout=self.UHUB_STATUS, stderr="")

        controller = dashboard.UsbPortController(
            Devices(),
            command=command,
            sys_root=self.sys_root,
            dev_root=self.dev_root,
            mounts_path=self.mounts,
            wall_clock=lambda: 1000,
        )
        threads = [threading.Thread(target=controller.discover) for _ in range(2)]
        for thread in threads:
            thread.start()
        self.assertTrue(first_entered.wait(1))
        release.set()
        for thread in threads:
            thread.join(2)

        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(maximum, 1)

    def test_merges_companion_hub_trees_into_ten_physical_ports(self):
        hubs = []
        targets = {}
        connected = {
            ("usb3", "2", 1): {
                "device_descriptions": ["Seagate Portable"],
                "downstream_device_count": 1,
                "storage_labels": ["mbp2tbkup"],
                "mounted_labels": ["mbp2tbkup"],
            },
            ("usb2", "2.4", 3): {
                "device_descriptions": ["PEAK System PCAN-USB"],
                "downstream_device_count": 1,
            },
            ("usb2", "2.4.4", 1): {
                "device_descriptions": ["Samsung Android"],
                "downstream_device_count": 1,
            },
        }

        for side, prefix in (("usb2", "1-1."), ("usb3", "2-")):
            for route in ("2", "2.4", "2.4.4"):
                location = prefix + route
                ports = []
                for port_number in range(1, 5):
                    key = f"{location}:{port_number}"
                    values = {
                        "key": key,
                        "location": location,
                        "port": port_number,
                        "method": "power",
                        "enabled": True,
                        "device_descriptions": [],
                        "downstream_device_count": 0,
                        "storage_labels": [],
                        "mounted_labels": [],
                    }
                    values.update(connected.get((side, route, port_number), {}))
                    ports.append(values)
                    targets[key] = {**values, "disable_path": None}
                hubs.append(
                    {
                        "location": location,
                        "description": (
                            "Realtek Semiconductor Corp. RTS5411 Hub"
                            if side == "usb2"
                            else "Realtek Semiconductor Corp. Hub"
                        ),
                        "device_id": "0bda:5411" if side == "usb2" else "0bda:0411",
                        "method": "power",
                        "ports": ports,
                    }
                )

        presented = dashboard.UsbPortController._presentation_hubs(hubs, targets)

        self.assertEqual(len(presented), 1)
        physical = presented[0]
        self.assertTrue(physical["physical"])
        self.assertFalse(physical["advanced"])
        self.assertEqual(
            physical["detail"],
            "Realtek Semiconductor Corp. Hub · ID 0bda:0411"
            " · 10 physical ports · paired USB 2/USB 3 · route 2-2",
        )
        self.assertEqual([port["port"] for port in physical["ports"]], list(range(1, 11)))
        self.assertEqual(
            physical["ports"][0]["device_descriptions"], ["Seagate Portable"]
        )
        self.assertEqual(
            physical["ports"][5]["device_descriptions"], ["PEAK System PCAN-USB"]
        )
        self.assertEqual(
            physical["ports"][6]["device_descriptions"], ["Samsung Android"]
        )
        self.assertEqual(physical["ports"][0]["mounted_labels"], ["mbp2tbkup"])

        android_target = targets[physical["ports"][6]["key"]]
        self.assertEqual(android_target["location"], "2-2.4.4")
        self.assertEqual(android_target["port"], 1)
        self.assertEqual(android_target["device_descriptions"], ["Samsung Android"])

    def test_keeps_distinct_root_external_hubs_in_separate_panes(self):
        hubs = []
        targets = {}
        for route, manufacturer in (
            ("2", "Realtek Semiconductor Corp. Hub"),
            ("3", "Genesys Logic, Inc. Hub"),
        ):
            for side, location in (
                ("usb2", f"1-1.{route}"),
                ("usb3", f"2-{route}"),
            ):
                ports = []
                for port_number in range(1, 5):
                    key = f"{location}:{port_number}"
                    port = {
                        "key": key,
                        "location": location,
                        "port": port_number,
                        "method": "power",
                        "enabled": True,
                        "device_descriptions": [],
                        "downstream_device_count": 0,
                        "storage_labels": [],
                        "mounted_labels": [],
                    }
                    ports.append(port)
                    targets[key] = {**port, "disable_path": None}
                hubs.append(
                    {
                        "location": location,
                        "description": (
                            f"{manufacturer} USB2 companion"
                            if side == "usb2"
                            else manufacturer
                        ),
                        "device_id": (
                            "0bda:5411"
                            if route == "2"
                            else "05e3:0626"
                        ),
                        "method": "power",
                        "ports": ports,
                    }
                )

        presented = dashboard.UsbPortController._presentation_hubs(hubs, targets)

        self.assertEqual(len(presented), 2)
        self.assertEqual(
            [hub["location"] for hub in presented],
            ["2-2", "2-3"],
        )
        self.assertTrue(all(hub["description"] == "External USB hub" for hub in presented))
        self.assertIn("Realtek Semiconductor Corp. Hub", presented[0]["detail"])
        self.assertIn("Genesys Logic, Inc. Hub", presented[1]["detail"])

    def test_marks_pi_root_and_internal_hubs_as_advanced(self):
        hubs = [
            {
                "location": location,
                "description": "Internal hub",
                "method": "power",
                "ports": [],
            }
            for location in ("1", "2", "1-1")
        ]

        presented = dashboard.UsbPortController._presentation_hubs(hubs, {})

        self.assertTrue(all(hub["advanced"] for hub in presented))

    def test_rejects_unknown_inputs_and_mounted_storage(self):
        self.controller.discover()
        with self.assertRaisesRegex(ValueError, "unknown USB port"):
            self.controller.start_action("../../etc:1", "off")
        with self.assertRaisesRegex(ValueError, "must be on, off, or cycle"):
            self.controller.start_action("2:1", "toggle")
        with self.assertRaisesRegex(RuntimeError, "refusing to disconnect mounted storage"):
            self.controller.start_action("2-2:3", "off")

    def test_uses_exact_uhubctl_and_kernel_tee_commands(self):
        self.controller.discover()
        power_target = copy.deepcopy(self.controller.targets["2:1"])
        data_target = copy.deepcopy(self.controller.targets["2-2:4"])
        self.controller._run_action(power_target, "off", 1000)
        self.controller._run_action(data_target, "cycle", 1000)
        self.assertIn(
            (
                [
                    dashboard.SUDO,
                    "-n",
                    dashboard.UHUBCTL,
                    "-l",
                    "2",
                    "-p",
                    "1",
                    "-a",
                    "off",
                ],
                4,
                None,
            ),
            self.calls,
        )
        tee_calls = [call for call in self.calls if dashboard.TEE in call[0]]
        self.assertEqual([call[2] for call in tee_calls], ["1\n", "0\n"])
        status_calls = [
            call
            for call in self.calls
            if call[0] == [dashboard.SUDO, "-n", dashboard.UHUBCTL]
        ]
        self.assertEqual(len(status_calls), 1)
        self.assertFalse(self.controller.snapshot()["loaded"])

    def test_uses_fixed_usb2_recovery_tool_without_status_discovery(self):
        self.controller._run_recovery(1000)

        self.assertIn(
            ([dashboard.USB2_RECOVERY_TOOL], dashboard.USB2_RECOVERY_TIMEOUT, None),
            self.calls,
        )
        self.assertEqual(self.controller.snapshot()["operation"]["status"], "complete")
        self.assertEqual(self.controller.snapshot()["operation"]["action"], "restore")
        self.assertNotIn(
            ([dashboard.SUDO, "-n", dashboard.UHUBCTL], 4, None),
            self.calls,
        )


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
        self.ignition = False
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
            command=command,
            ignition_on=lambda: self.ignition,
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

    def test_pauses_while_ignition_is_on_and_does_not_touch_light(self):
        self.store.set("cop_alert", True)
        self.ignition = True
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


class CopCanWakeStatusReaderTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.path = os.path.join(self.tempdir.name, "status.json")
        self.commands = []

    def payload(self, **changes):
        value = {
            "service": "van-cop-can-wake",
            "role": "c-can",
            "schema_version": 1,
            "wake_method": "must not escape the trust boundary",
            "state": "active_waiting",
            "marker_active": True,
            "ignition_on": False,
            "transaction_in_progress": False,
            "activation_debounce_seconds": 0.25,
            "preexisting_marker_delay_seconds": 3.0,
            "safety_retry_seconds": 0.5,
            "fixed_success_cadence_seconds": 15.0,
            "next_attempt_at": "2001-01-01T00:00:15+00:00",
            "wake_count": 4,
            "last_attempt_at": "2001-01-01T00:00:00+00:00",
            "last_success_at": "2001-01-01T00:00:00+00:00",
            "last_reason": "fixed wake",
            "last_detail": "wake accepted",
            "last_blocked_at": None,
            "last_blocked_reason": None,
            "last_blocked_detail": None,
            "generated_at": "2001-01-01T00:00:01+00:00",
        }
        value.update(changes)
        return value

    def write_payload(self, payload):
        with open(self.path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle)

    def command(self, args, timeout):
        self.commands.append((list(args), timeout))
        return SimpleNamespace(returncode=0, stdout="active\n", stderr="")

    def reader(self, **changes):
        return dashboard.CopCanWakeStatusReader(
            path=self.path,
            command=self.command,
            systemctl="/test/systemctl",
            timeout=2,
            **changes,
        )

    def test_sanitizes_valid_status_without_treating_old_generation_as_stale(self):
        self.write_payload(self.payload(secret="drop me"))
        status = self.reader().snapshot()

        self.assertTrue(status["available"])
        self.assertTrue(status["service_active"])
        self.assertEqual(status["state"], "active_waiting")
        self.assertEqual(status["generated_at"], "2001-01-01T00:00:01+00:00")
        self.assertEqual(status["wake_count"], 4)
        self.assertNotIn("service", status)
        self.assertNotIn("role", status)
        self.assertNotIn("schema_version", status)
        self.assertNotIn("wake_method", status)
        self.assertNotIn("secret", status)
        self.assertEqual(
            self.commands,
            [(["/test/systemctl", "is-active", "van-cop-can-wake.service"], 2)],
        )

    def test_inactive_service_preserves_sanitized_diagnostic_history(self):
        self.write_payload(
            self.payload(
                state="blocked",
                last_blocked_detail="another process owns the adapter",
            )
        )

        def inactive(_args, timeout):
            self.assertEqual(timeout, 2)
            return SimpleNamespace(returncode=3, stdout="inactive\n", stderr="")

        status = dashboard.CopCanWakeStatusReader(
            path=self.path,
            command=inactive,
            systemctl="/test/systemctl",
            timeout=2,
        ).snapshot()
        self.assertFalse(status["available"])
        self.assertFalse(status["service_active"])
        self.assertEqual(status["state"], "blocked")
        self.assertEqual(
            status["last_blocked_detail"],
            "another process owns the adapter",
        )

    def test_rejects_malformed_identity_state_and_field_types(self):
        invalid_payloads = (
            self.payload(service="other-service"),
            self.payload(role="b-can"),
            self.payload(schema_version=2),
            self.payload(schema_version=True),
            self.payload(state="surprise"),
            self.payload(marker_active="yes"),
            self.payload(wake_count=-1),
            self.payload(safety_retry_seconds=float("inf")),
            self.payload(last_detail=7),
            ["not", "an", "object"],
        )
        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                self.write_payload(payload)
                status = self.reader().snapshot()
                self.assertFalse(status["available"])
                self.assertEqual(
                    status["error"],
                    "CAN wake supervisor status is invalid",
                )
                self.assertNotIn("state", status)

    def test_rejects_symlink_non_regular_malformed_and_oversized_files(self):
        target = os.path.join(self.tempdir.name, "target.json")
        with open(target, "w", encoding="utf-8") as handle:
            json.dump(self.payload(), handle)
        os.symlink(target, self.path)
        self.assertFalse(self.reader().snapshot()["available"])
        os.unlink(self.path)

        os.mkfifo(self.path)
        self.assertFalse(self.reader().snapshot()["available"])
        os.unlink(self.path)

        with open(self.path, "w", encoding="utf-8") as handle:
            handle.write("{not-json")
        self.assertFalse(self.reader().snapshot()["available"])

        with open(self.path, "wb") as handle:
            handle.write(b"x" * 33)
        self.assertFalse(self.reader(max_bytes=32).snapshot()["available"])

    def test_service_check_timeout_is_offline_but_status_remains_sanitized(self):
        self.write_payload(self.payload(state="idle", marker_active=False))

        def timeout(_args, timeout):
            raise subprocess.TimeoutExpired("systemctl", timeout)

        status = dashboard.CopCanWakeStatusReader(
            path=self.path,
            command=timeout,
            timeout=0.1,
        ).snapshot()
        self.assertFalse(status["available"])
        self.assertFalse(status["service_active"])
        self.assertEqual(status["state"], "idle")
        self.assertIn("timed out", status["error"])


class CopAlertManagerTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.clock = FakeClock()
        self.ignition = False
        self.entity_state = "off"
        self.calls = []
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

        self.manager = dashboard.CopAlertManager(
            self.store,
            command=command,
            ignition_on=lambda: self.ignition,
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

    def test_marker_and_exterior_alert_follow_requested_state_and_ignition(self):
        status = self.manager.set_active(True)
        self.assertTrue(status["active"])
        self.assertEqual(self.entity_state, "on")
        self.assertTrue(os.path.isfile(self.manager.active_marker))
        self.wait_for_ntfy()
        self.assertTrue(any(call[0] == dashboard.NTFY_SEND for call in self.calls))

        self.manager.tick()
        self.assertEqual(self.entity_state, "on")

        self.ignition = True
        self.clock.advance(dashboard.FLOOD_CHECK_INTERVAL + 1)
        self.manager.tick()
        self.assertEqual(self.entity_state, "off")

        self.ignition = False
        self.clock.advance(dashboard.FLOOD_CHECK_INTERVAL + 1)
        self.manager.tick()
        self.assertEqual(self.entity_state, "on")

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
            command=command,
            ignition_on=lambda: self.ignition,
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
            command=command,
            ignition_on=lambda: self.ignition,
            runtime_dir=os.path.join(self.tempdir.name, "failure-run"),
            clock=self.clock,
            wall_clock=lambda: 1_700_000_000 + self.clock(),
        )
        manager.set_active(True)
        self.wait_for_ntfy(manager)
        self.assertTrue(manager.snapshot()["active"])
        self.assertIn("network unavailable", manager.snapshot()["last_error"])
        manager.tick()


class BackupManagerTests(unittest.TestCase):
    NOW = 1_800_000_000

    @staticmethod
    def lsblk_payload(mounted=False):
        return {
            "blockdevices": [
                {
                    "name": "sdb",
                    "path": "/dev/sdb",
                    "pkname": None,
                    "label": None,
                    "size": 32_000_000_000,
                    "mountpoints": [None],
                    "children": [
                        {
                            "name": "sdb1",
                            "path": "/dev/sdb1",
                            "pkname": "sdb",
                            "label": "bootfs",
                            "size": 500_000_000,
                            "mountpoints": ["/mnt/test"] if mounted else [None],
                        },
                        {
                            "name": "sdb2",
                            "path": "/dev/sdb2",
                            "pkname": "sdb",
                            "label": "hotspare-a",
                            "size": 31_500_000_000,
                            "mountpoints": [None],
                        },
                    ],
                }
            ]
        }

    def make_files(self, tempdir, tm_running=True):
        config = os.path.join(tempdir, "backup_conf.sh")
        stamps = os.path.join(tempdir, "stamps")
        bundle = os.path.join(tempdir, "m4mac.sparsebundle")
        os.makedirs(stamps)
        os.makedirs(bundle)
        os.makedirs(os.path.join(tempdir, "proc"))
        with open(config, "w", encoding="utf-8") as handle:
            handle.write(
                "CLONE_TARGETS=(hotspare-a:7 hotspare-b:14)\n"
                "BORG_STALE_HOURS=48\n"
                "EXFAT_SNAPSHOT_STALE_HOURS=48\n"
                "OPENWRT_BACKUP_STALE_HOURS=72\n"
                "CLONE_STALE_FACTOR=2\n"
                "CLONE_CARD_NOMINAL_GB=64\n"
            )
        stamp_times = {
            "borg_ok": self.NOW - 3600,
            "exfat512_ok": self.NOW - 5400,
            "openwrt_ok": self.NOW - 7200,
            "clone_hotspare-a": self.NOW - 2 * 86400,
            "clone_hotspare-b": self.NOW - 40 * 86400,
        }
        for name, timestamp in stamp_times.items():
            path = os.path.join(stamps, name)
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("recorded\n")
            os.utime(path, (timestamp, timestamp))
        history = {
            "Snapshots": [
                {
                    "com.apple.backupd.SnapshotCompletionDate": dashboard.datetime.datetime.utcfromtimestamp(
                        timestamp
                    ),
                    "com.apple.backupd.SnapshotName": "test-snapshot",
                }
                for timestamp in (self.NOW - 7200, self.NOW - 1800)
            ]
        }
        with open(
            os.path.join(bundle, "com.apple.TimeMachine.SnapshotHistory.plist"), "wb"
        ) as handle:
            dashboard.plistlib.dump(history, handle)
        results_path = os.path.join(bundle, "com.apple.TimeMachine.Results.plist")
        with open(results_path, "wb") as handle:
            dashboard.plistlib.dump(
                {
                    "Running": tm_running,
                    "Progress": {"Percent": 0.375, "bytes": 250, "totalBytes": 1000},
                },
                handle,
            )
        os.utime(results_path, (self.NOW - 30, self.NOW - 30))
        return config, stamps, bundle

    def test_reads_borg_hotswap_and_time_machine_evidence(self):
        with tempfile.TemporaryDirectory() as tempdir:
            config, stamps, bundle = self.make_files(tempdir)

            def command(args, timeout):
                self.assertEqual(args[0], dashboard.LSBLK)
                return SimpleNamespace(
                    returncode=0,
                    stdout=json.dumps(self.lsblk_payload()),
                    stderr="",
                )

            manager = dashboard.BackupManager(
                config=config,
                stamp_dir=stamps,
                time_machine_bundle=bundle,
                command=command,
                wall_clock=lambda: self.NOW,
                process_root=os.path.join(tempdir, "proc"),
            )
            status = manager.status()

        self.assertFalse(status["borg"]["stale"])
        self.assertEqual(status["borg"]["last_success_at"], self.NOW - 3600)
        self.assertFalse(status["exfat_snapshot"]["stale"])
        self.assertEqual(
            status["exfat_snapshot"]["last_success_at"], self.NOW - 5400
        )
        self.assertFalse(status["openwrt"]["stale"])
        self.assertEqual(status["openwrt"]["last_success_at"], self.NOW - 7200)
        self.assertEqual(
            [card["label"] for card in status["hotswaps"]],
            ["hotspare-a", "hotspare-b"],
        )
        self.assertTrue(status["hotswaps"][0]["attached"])
        self.assertFalse(status["hotswaps"][0]["mounted"])
        self.assertFalse(status["hotswaps"][0]["stale"])
        self.assertFalse(status["hotswaps"][1]["attached"])
        self.assertTrue(status["hotswaps"][1]["stale"])
        self.assertEqual(status["time_machine"]["last_backup_at"], self.NOW - 1800)
        self.assertTrue(status["time_machine"]["running"])
        self.assertEqual(status["time_machine"]["progress_percent"], 37.5)
        self.assertEqual(
            status["settings"],
            {
                "clone_card_nominal_gb": 64,
                "root_used_max_gib": 52,
                "clone_card_nominal_gb_options": [32, 64, 128, 256],
            },
        )
        self.assertEqual(status["health"], "running")

    def test_clone_card_capacity_is_validated_persisted_and_reported(self):
        with tempfile.TemporaryDirectory() as tempdir:
            config, stamps, bundle = self.make_files(tempdir, tm_running=False)
            setting = os.path.join(tempdir, "clone_card_nominal_gb")
            manager = dashboard.BackupManager(
                config=config,
                stamp_dir=stamps,
                clone_card_size_path=setting,
                time_machine_bundle=bundle,
                command=lambda args, timeout: SimpleNamespace(
                    returncode=0,
                    stdout=json.dumps(self.lsblk_payload()),
                    stderr="",
                ),
                wall_clock=lambda: self.NOW,
                process_root=os.path.join(tempdir, "proc"),
            )

            updated = manager.set_clone_card_nominal_gb("128")
            self.assertEqual(Path(setting).read_text(encoding="utf-8"), "128\n")
            self.assertEqual(updated["settings"]["clone_card_nominal_gb"], 128)
            self.assertEqual(updated["settings"]["root_used_max_gib"], 104)
            for invalid in ("", "16", "032", "512", "64; touch /tmp/nope"):
                with self.subTest(invalid=invalid):
                    with self.assertRaisesRegex(ValueError, "32, 64, 128, or 256"):
                        manager.set_clone_card_nominal_gb(invalid)
            self.assertEqual(Path(setting).read_text(encoding="utf-8"), "128\n")

            with open(setting, "w", encoding="utf-8") as handle:
                handle.write("unsupported\n")
            with self.assertRaisesRegex(
                dashboard.BackupStatusError, "capacity setting is invalid"
            ):
                manager.status()

    def test_detects_scheduled_borg_and_exfat_parents_from_exact_proc_argv(self):
        with tempfile.TemporaryDirectory() as tempdir:
            config, stamps, bundle = self.make_files(tempdir, tm_running=False)
            proc_root = os.path.join(tempdir, "proc")
            progress_dir = os.path.join(stamps, "progress")
            os.makedirs(progress_dir)
            for pid, script in (
                ("101", "/test/pi_backup.sh"),
                ("102", "/test/exfat_snapshot.sh"),
                ("104", "/test/openwrt_backup.sh"),
            ):
                process = os.path.join(proc_root, pid)
                os.makedirs(process)
                with open(os.path.join(process, "cmdline"), "wb") as handle:
                    handle.write(b"/bin/bash\0" + os.fsencode(script) + b"\0")
            for kind, pid, phase, detail in (
                ("borg", 101, "openwrt", "Exporting the OpenWrt recovery bundle"),
                ("exfat", 102, "copying", "Copying files into the snapshot"),
                ("openwrt", 104, "validating", "Validating router checksums"),
            ):
                with open(
                    os.path.join(progress_dir, f"{kind}.state"),
                    "w",
                    encoding="utf-8",
                ) as handle:
                    handle.write(
                        "version=1\n"
                        f"pid={pid}\n"
                        f"started_at={self.NOW - 120}\n"
                        f"updated_at={self.NOW - 10}\n"
                        f"phase={phase}\n"
                        f"detail={detail}\n"
                    )
            with open(os.path.join(progress_dir, "exfat.rsync"), "wb") as handle:
                handle.write(
                    b"  1,048,576  25%  10.00MB/s 0:00:02 "
                    b"(xfr#7, to-chk=12/25)\r"
                )
            false_match = os.path.join(proc_root, "103")
            os.makedirs(false_match)
            with open(os.path.join(false_match, "cmdline"), "wb") as handle:
                handle.write(b"/bin/sh\0mention /test/pi_backup.sh only\0")

            manager = dashboard.BackupManager(
                config=config,
                stamp_dir=stamps,
                borg_tool="/test/pi_backup.sh",
                exfat_tool="/test/exfat_snapshot.sh",
                openwrt_tool="/test/openwrt_backup.sh",
                time_machine_bundle=bundle,
                command=lambda args, timeout: SimpleNamespace(
                    returncode=0,
                    stdout=json.dumps(self.lsblk_payload()),
                    stderr="",
                ),
                wall_clock=lambda: self.NOW,
                process_root=proc_root,
            )
            status = manager.status()

        self.assertTrue(status["borg"]["running"])
        self.assertTrue(status["exfat_snapshot"]["running"])
        self.assertTrue(status["openwrt"]["running"])
        self.assertEqual(status["borg"]["progress"]["phase"], "openwrt")
        self.assertEqual(
            status["exfat_snapshot"]["progress"]["progress_percent"], 25
        )
        self.assertEqual(
            status["exfat_snapshot"]["progress"]["bytes_processed"], 1_048_576
        )
        self.assertEqual(
            status["exfat_snapshot"]["progress"]["files_remaining"], 12
        )
        self.assertEqual(status["openwrt"]["progress"]["phase"], "validating")
        self.assertEqual(status["health"], "running")

    def test_stop_request_is_nonblocking_fixed_and_requires_exact_running_kind(self):
        with tempfile.TemporaryDirectory() as tempdir:
            config, stamps, bundle = self.make_files(tempdir, tm_running=False)
            proc_root = os.path.join(tempdir, "proc")
            process = os.path.join(proc_root, "101")
            os.makedirs(process)
            with open(os.path.join(process, "cmdline"), "wb") as handle:
                handle.write(b"/bin/bash\0/test/pi_backup.sh\0")
            calls = []
            stop_started = threading.Event()
            release_stop = threading.Event()

            def command(args, timeout):
                calls.append((list(args), timeout))
                if args[0] == dashboard.LSBLK:
                    return SimpleNamespace(
                        returncode=0,
                        stdout=json.dumps(self.lsblk_payload()),
                        stderr="",
                    )
                stop_started.set()
                release_stop.wait(2)
                return SimpleNamespace(returncode=0, stdout="stopped", stderr="")

            manager = dashboard.BackupManager(
                config=config,
                stamp_dir=stamps,
                borg_tool="/test/pi_backup.sh",
                exfat_tool="/test/exfat_snapshot.sh",
                abort_tool="/test/abort_backup.sh",
                time_machine_bundle=bundle,
                command=command,
                stop_timeout=45,
                wall_clock=lambda: self.NOW,
                process_root=proc_root,
            )
            status = manager.request_stop("borg")
            self.assertTrue(stop_started.wait(1))
            self.assertEqual(status["stop"]["status"], "running")
            self.assertEqual(status["stop"]["kind"], "borg")
            with self.assertRaisesRegex(dashboard.BackupStatusError, "already running"):
                manager.request_stop("borg")
            with self.assertRaisesRegex(dashboard.BackupStatusError, "no active exfat"):
                manager.request_stop("exfat")
            with self.assertRaisesRegex(ValueError, "unknown backup kind"):
                manager.request_stop("restore")
            release_stop.set()
            manager.stop_thread.join(2)
            self.assertFalse(manager.stop_thread.is_alive())
            self.assertEqual(manager.stop_operation["status"], "complete")

        stop_calls = [call for call in calls if call[0][0] == dashboard.SUDO]
        self.assertEqual(
            stop_calls,
            [
                (
                    [
                        dashboard.SUDO,
                        "-n",
                        "/test/abort_backup.sh",
                        "--user",
                        "borg",
                    ],
                    45,
                )
            ],
        )

    def test_openwrt_status_requires_a_fresh_verified_stamp(self):
        with tempfile.TemporaryDirectory() as tempdir:
            config, stamps, bundle = self.make_files(tempdir, tm_running=False)
            manager = dashboard.BackupManager(
                config=config,
                stamp_dir=stamps,
                time_machine_bundle=bundle,
                command=lambda args, timeout: SimpleNamespace(
                    returncode=0,
                    stdout=json.dumps(self.lsblk_payload()),
                    stderr="",
                ),
                wall_clock=lambda: self.NOW,
                process_root=os.path.join(tempdir, "proc"),
            )

            stamp = os.path.join(stamps, "openwrt_ok")
            os.unlink(stamp)
            missing = manager.status()["openwrt"]
            self.assertTrue(missing["stale"])

            with open(stamp, "w", encoding="utf-8") as handle:
                handle.write("verified\n")
            old_stamp = self.NOW - 73 * 3600
            os.utime(stamp, (old_stamp, old_stamp))
            stale = manager.status()["openwrt"]
            self.assertTrue(stale["stale"])
            self.assertEqual(stale["stale_hours"], 72)

    def test_rejects_invalid_configuration_and_stale_running_metadata(self):
        invalid = (
            "CLONE_TARGETS=()",
            "CLONE_TARGETS=(hotspare-a:0)",
            "CLONE_TARGETS=(../../sda:7)",
            "CLONE_TARGETS=(hotspare-a:7 hotspare-a:14)",
        )
        for value in invalid:
            with self.subTest(value=value):
                with self.assertRaises(dashboard.BackupStatusError):
                    dashboard.BackupManager.parse_config(value)

        with tempfile.TemporaryDirectory() as tempdir:
            config, stamps, bundle = self.make_files(tempdir)
            results = os.path.join(bundle, "com.apple.TimeMachine.Results.plist")
            os.utime(results, (self.NOW - 3600, self.NOW - 3600))
            manager = dashboard.BackupManager(
                config=config,
                stamp_dir=stamps,
                time_machine_bundle=bundle,
                command=lambda args, timeout: SimpleNamespace(
                    returncode=0,
                    stdout=json.dumps(self.lsblk_payload()),
                    stderr="",
                ),
                wall_clock=lambda: self.NOW,
                process_root=os.path.join(tempdir, "proc"),
            )
            self.assertFalse(manager.status()["time_machine"]["running"])

    def test_clone_uses_fixed_root_wrapper_and_whitelisted_attached_label(self):
        with tempfile.TemporaryDirectory() as tempdir:
            config, stamps, bundle = self.make_files(tempdir, tm_running=False)
            calls = []

            def command(args, timeout):
                calls.append((list(args), timeout))
                if args[0] == dashboard.LSBLK:
                    return SimpleNamespace(
                        returncode=0,
                        stdout=json.dumps(self.lsblk_payload()),
                        stderr="",
                    )
                return SimpleNamespace(returncode=0, stdout="clone complete", stderr="")

            manager = dashboard.BackupManager(
                config=config,
                stamp_dir=stamps,
                clone_tool="/test/clone_now.sh",
                time_machine_bundle=bundle,
                command=command,
                clone_timeout=123,
                wall_clock=lambda: self.NOW,
                process_root=os.path.join(tempdir, "proc"),
            )
            manager.start_clone("hotspare-a")
            manager.thread.join(2)
            self.assertFalse(manager.thread.is_alive())
            self.assertEqual(manager.operation["status"], "complete")
            clone_calls = [call for call in calls if call[0][0] == dashboard.SUDO]
            self.assertEqual(
                clone_calls,
                [([dashboard.SUDO, "-n", "/test/clone_now.sh", "hotspare-a"], 123)],
            )
            with self.assertRaisesRegex(ValueError, "unknown hotspare"):
                manager.start_clone("/dev/sda")

    def test_clone_refuses_unattached_or_mounted_cards(self):
        with tempfile.TemporaryDirectory() as tempdir:
            config, stamps, bundle = self.make_files(tempdir, tm_running=False)
            for mounted, target, message in (
                (False, "hotspare-b", "not attached"),
                (True, "hotspare-a", "mounted partitions"),
            ):
                manager = dashboard.BackupManager(
                    config=config,
                    stamp_dir=stamps,
                    time_machine_bundle=bundle,
                    command=lambda args, timeout, mounted=mounted: SimpleNamespace(
                        returncode=0,
                        stdout=json.dumps(self.lsblk_payload(mounted=mounted)),
                        stderr="",
                    ),
                    wall_clock=lambda: self.NOW,
                    process_root=os.path.join(tempdir, "proc"),
                )
                with self.subTest(target=target):
                    with self.assertRaisesRegex(dashboard.BackupStatusError, message):
                        manager.start_clone(target)

    def test_clone_failure_and_timeout_are_reported_by_background_operation(self):
        with tempfile.TemporaryDirectory() as tempdir:
            config, stamps, bundle = self.make_files(tempdir, tm_running=False)
            for outcome, message in (("failed", "card write failed"), ("timeout", "timed out")):
                def command(args, timeout, outcome=outcome):
                    if args[0] == dashboard.LSBLK:
                        return SimpleNamespace(
                            returncode=0,
                            stdout=json.dumps(self.lsblk_payload()),
                            stderr="",
                        )
                    if outcome == "timeout":
                        raise subprocess.TimeoutExpired(args, timeout)
                    return SimpleNamespace(
                        returncode=1,
                        stdout="",
                        stderr="card write failed",
                    )

                manager = dashboard.BackupManager(
                    config=config,
                    stamp_dir=stamps,
                    time_machine_bundle=bundle,
                    command=command,
                    clone_timeout=4,
                    wall_clock=lambda: self.NOW,
                    process_root=os.path.join(tempdir, "proc"),
                )
                with self.subTest(outcome=outcome):
                    manager.start_clone("hotspare-a")
                    manager.thread.join(2)
                    self.assertEqual(manager.operation["status"], "error")
                    self.assertIn(message, manager.operation["error"])

    def test_manual_backups_use_separate_fixed_force_commands_and_stamps(self):
        with tempfile.TemporaryDirectory() as tempdir:
            config, stamps, bundle = self.make_files(tempdir, tm_running=False)
            calls = []

            def command(args, timeout):
                calls.append((list(args), timeout))
                if args[0] == dashboard.LSBLK:
                    return SimpleNamespace(
                        returncode=0,
                        stdout=json.dumps(self.lsblk_payload()),
                        stderr="",
                    )
                stamp_name = (
                    "borg_ok" if args[2] == "/test/pi_backup.sh" else "exfat512_ok"
                )
                stamp = os.path.join(stamps, stamp_name)
                os.utime(stamp, (self.NOW + 1, self.NOW + 1))
                return SimpleNamespace(
                    returncode=0,
                    stdout="backup complete",
                    stderr="",
                )

            manager = dashboard.BackupManager(
                config=config,
                stamp_dir=stamps,
                borg_tool="/test/pi_backup.sh",
                exfat_tool="/test/exfat_snapshot.sh",
                time_machine_bundle=bundle,
                command=command,
                backup_timeout=321,
                wall_clock=lambda: self.NOW,
                process_root=os.path.join(tempdir, "proc"),
            )
            manager.start_borg_backup()
            manager.thread.join(2)
            self.assertFalse(manager.thread.is_alive())
            self.assertEqual(manager.operation["status"], "complete")
            self.assertEqual(manager.operation["kind"], "borg")
            manager.start_exfat_backup()
            manager.thread.join(2)
            self.assertFalse(manager.thread.is_alive())
            self.assertEqual(manager.operation["status"], "complete")
            self.assertEqual(manager.operation["kind"], "exfat")
            backup_calls = [call for call in calls if call[0][0] == dashboard.SUDO]
            self.assertEqual(
                backup_calls,
                [
                    ([dashboard.SUDO, "-n", "/test/pi_backup.sh", "--force"], 321),
                    (
                        [
                            dashboard.SUDO,
                            "-n",
                            "/test/exfat_snapshot.sh",
                            "--force",
                        ],
                        321,
                    ),
                ],
            )

    def test_manual_borg_failure_timeout_and_deferred_run_are_reported(self):
        with tempfile.TemporaryDirectory() as tempdir:
            config, stamps, bundle = self.make_files(tempdir, tm_running=False)
            for outcome, message in (
                ("failed", "borg failed"),
                ("timeout", "timed out"),
                (
                    "deferred",
                    "without recording a new Borg success",
                ),
            ):

                def command(args, timeout, outcome=outcome):
                    if args[0] == dashboard.LSBLK:
                        return SimpleNamespace(
                            returncode=0,
                            stdout=json.dumps(self.lsblk_payload()),
                            stderr="",
                        )
                    if outcome == "timeout":
                        raise subprocess.TimeoutExpired(args, timeout)
                    if outcome == "failed":
                        return SimpleNamespace(
                            returncode=1,
                            stdout="",
                            stderr="borg failed",
                        )
                    return SimpleNamespace(
                        returncode=0,
                        stdout="requested policy disables HDDs, deferring backup",
                        stderr="",
                    )

                manager = dashboard.BackupManager(
                    config=config,
                    stamp_dir=stamps,
                    borg_tool="/test/pi_backup.sh",
                    time_machine_bundle=bundle,
                    command=command,
                    backup_timeout=4,
                    wall_clock=lambda: self.NOW,
                    process_root=os.path.join(tempdir, "proc"),
                )
                with self.subTest(outcome=outcome):
                    manager.start_borg_backup()
                    manager.thread.join(2)
                    self.assertEqual(manager.operation["status"], "error")
                    self.assertIn(message, manager.operation["error"])

    def test_clone_wrapper_uses_shared_lock_and_never_initializes_a_card(self):
        repository = str(REPOSITORY_ROOT)
        path = os.path.join(repository, "pi", "scripts", "backup", "clone_now.sh")
        with open(path, encoding="utf-8") as handle:
            script = handle.read()
        self.assertIn("acquire_job_lock", script)
        self.assertIn('/home/pi/scripts/backup/clone_to_sd.sh "$label"', script)
        self.assertNotIn('clone_to_sd.sh --init', script)


class IgnitionMonitorControllerTests(unittest.TestCase):
    CONTROL = {
        "version": 1,
        "status": "disabled",
        "active": False,
        "deadline": 1_700_007_200,
        "remaining_seconds": 7200,
        "checked_at": 1_700_000_000,
    }
    SERVICE = "ActiveState=active\nSubState=running\nUnitFileState=enabled\n"

    def test_status_parses_exact_control_and_systemd_schemas(self):
        calls = []

        def command(args, timeout):
            calls.append((list(args), timeout))
            output = self.SERVICE if args[0] == "/test/systemctl" else json.dumps(self.CONTROL)
            return SimpleNamespace(returncode=0, stdout=output, stderr="")

        controller = dashboard.IgnitionMonitorController(
            control="/test/ignitionmonctl",
            systemctl="/test/systemctl",
            command=command,
            timeout=4,
        )
        status = controller.status()
        self.assertTrue(status["service"]["running"])
        self.assertTrue(status["service"]["enabled"])
        self.assertFalse(status["monitor"]["active"])
        self.assertEqual(status["monitor"]["remaining_seconds"], 7200)
        self.assertEqual(
            calls,
            [
                (
                    [
                        "/test/systemctl",
                        "show",
                        "ignitionmon.service",
                        "--property=ActiveState",
                        "--property=SubState",
                        "--property=UnitFileState",
                        "--no-pager",
                    ],
                    4,
                ),
                (["/test/ignitionmonctl", "status", "--json"], 4),
            ],
        )

    def test_disable_and_enable_use_fixed_argv_then_refresh_authoritative_state(self):
        calls = []

        def command(args, timeout):
            calls.append(list(args))
            if args[0] == "/test/systemctl":
                output = self.SERVICE
            elif args[1:] == ["status", "--json"]:
                output = json.dumps(self.CONTROL)
            else:
                output = "updated\n"
            return SimpleNamespace(returncode=0, stdout=output, stderr="")

        controller = dashboard.IgnitionMonitorController(
            control="/test/ignitionmonctl",
            systemctl="/test/systemctl",
            command=command,
        )
        controller.disable(90)
        controller.enable()
        self.assertEqual(calls[0], ["/test/ignitionmonctl", "disable", "90m"])
        self.assertEqual(calls[3], ["/test/ignitionmonctl", "enable"])
        self.assertEqual(calls[2], ["/test/ignitionmonctl", "status", "--json"])
        self.assertEqual(calls[5], ["/test/ignitionmonctl", "status", "--json"])
        for invalid in (True, 0, -1, dashboard.IGNITIONMON_MAX_MINUTES + 1, "30"):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    controller.disable(invalid)

    def test_rejects_bad_status_and_bounds_command_failures(self):
        invalid = (
            "not json",
            json.dumps({**self.CONTROL, "extra": True}),
            json.dumps({**self.CONTROL, "active": True}),
            json.dumps({**self.CONTROL, "remaining_seconds": 7199}),
        )
        for output in invalid:
            with self.subTest(output=output):
                with self.assertRaises(dashboard.IgnitionMonitorCommandError):
                    dashboard.IgnitionMonitorController.parse_control_status(output)

        failed = dashboard.IgnitionMonitorController(
            command=lambda args, timeout: SimpleNamespace(
                returncode=1, stdout="", stderr="unit unavailable"
            )
        )
        with self.assertRaisesRegex(dashboard.IgnitionMonitorCommandError, "unit unavailable"):
            failed.status()

        def timeout(args, timeout):
            raise subprocess.TimeoutExpired(args, timeout)

        timed_out = dashboard.IgnitionMonitorController(command=timeout, timeout=3)
        with self.assertRaisesRegex(dashboard.IgnitionMonitorCommandError, "timed out after 3"):
            timed_out.status()


class VonstarClientTests(unittest.TestCase):
    def setUp(self):
        self.responses = []
        self.requests = []
        self.connections = []

    def socket_stat(self, _path):
        return SimpleNamespace(st_mode=stat.S_IFSOCK | 0o660)

    def factory(self, path, timeout):
        test = self

        class Connection:
            def __init__(self):
                self.path = path
                self.timeout = timeout
                self.closed = False

            def request(self, method, request_path, body=None, headers=None):
                test.requests.append((method, request_path, body, headers or {}))

            def getresponse(self):
                status, payload = test.responses.pop(0)
                raw = json.dumps(payload).encode("utf-8")
                return SimpleNamespace(
                    status=status,
                    read=lambda _limit: raw,
                )

            def close(self):
                self.closed = True

        connection = Connection()
        self.connections.append(connection)
        return connection

    def client(self):
        return dashboard.VonstarClient(
            socket_path="/run/test-vonstar.sock",
            status_timeout=1.5,
            action_timeout=24,
            connection_factory=self.factory,
            lstat=self.socket_stat,
        )

    @staticmethod
    def access_state():
        unmapped = {
            "locked": None,
            "ajar": None,
            "lock_quality": "unmapped_individual_door",
            "ajar_quality": "unmapped_individual_door",
        }
        return {
            "observed_at": "2026-08-28T01:52:00+00:00",
            "complete": False,
            "lock_domains": {
                "state": "locked",
                "front_locked": True,
                "cargo_locked": True,
                "quality": "verified",
                "source": "must-be-dropped",
            },
            "doors": {
                "driver": {
                    **unmapped,
                    "ajar": False,
                    "ajar_quality": "candidate_one_controlled_trial",
                },
                "passenger": dict(unmapped),
                "sliding": {
                    **unmapped,
                    "reported_closed": True,
                    "physical_state_observable": False,
                    "ajar_quality": "hardware_bypass_forced_closed",
                },
                "rear": dict(unmapped),
            },
            "wake": {
                "count": 1,
                "role": "must-be-dropped",
                "detail": "must-be-dropped",
                "additional_tx_after_wake": 1,
                "restored_passive_after_sampling": True,
            },
            "observations": {
                "b_can": {"frames": ["read-only-diagnostic-observation"]},
            },
            "limitations": ["individual door lock booleans are not mapped"],
        }

    def test_status_is_identity_checked_and_sanitized(self):
        self.responses.append(
            (
                200,
                {
                    "ok": True,
                    "vonstar": {
                        "service": "vonstar",
                        "schema_version": 1,
                        "available": True,
                        "mode": "execute",
                        "busy": False,
                        "actions": {
                            "lock_all": {"label": "untrusted"},
                            "unlock_front": {"label": "untrusted"},
                            "unlock_cargo": {"label": "untrusted"},
                        },
                        "cooldown_seconds": 3.0,
                        "last_result": {
                            "ok": True,
                            "action": "unlock_front",
                            "label": "untrusted",
                            "request_id": "secret-ish-internal-value",
                            "payload_hex": "must-not-reach-browser",
                            "counter_streak": [1, 2, 3],
                            "send_count": 1,
                            "completed_at": "2026-08-26T23:40:15+00:00",
                        },
                    },
                },
            )
        )
        status = self.client().snapshot()

        self.assertTrue(status["available"])
        self.assertEqual(status["mode"], "execute")
        self.assertEqual(status["actions"], dashboard.VONSTAR_ACTIONS)
        self.assertEqual(
            status["last_result"],
            {
                "ok": True,
                "action": "unlock_front",
                "label": "Unlock Front",
                "completed_at": "2026-08-26T23:40:15+00:00",
            },
        )
        self.assertNotIn("schema_version", status)

    def test_action_posts_only_unique_request_id_to_fixed_path(self):
        self.responses.append(
            (
                200,
                {
                    "ok": True,
                    "action": "lock_all",
                    "label": "untrusted",
                    "request_id": "dash-internal",
                    "payload_hex": "must-not-reach-browser",
                    "send_count": 1,
                    "restored_passive": True,
                    "started_at": "start",
                    "completed_at": "finish",
                },
            )
        )
        with mock.patch(
            "pi.apps.van_dashboard.van_dashboard_vonstar.secrets.token_hex",
            return_value="a" * 32,
        ):
            result = self.client().perform("lock_all")

        method, path, body, headers = self.requests[0]
        self.assertEqual(method, "POST")
        self.assertEqual(path, "/v1/actions/lock_all")
        self.assertEqual(json.loads(body), {"request_id": "dash-" + "a" * 32})
        self.assertEqual(headers, {"Content-Type": "application/json"})
        self.assertEqual(self.connections[0].timeout, 24)
        self.assertEqual(
            result,
            {
                "ok": True,
                "action": "lock_all",
                "label": "Lock All",
                "started_at": "start",
                "completed_at": "finish",
            },
        )

    def test_access_state_posts_only_unique_request_id_and_sanitizes_snapshot(self):
        self.responses.append(
            (
                200,
                {
                    "ok": True,
                    "operation": "access_state",
                    "request_id": "must-be-dropped",
                    "started_at": "start",
                    "completed_at": "finish",
                    "access_state": self.access_state(),
                },
            )
        )
        with mock.patch(
            "pi.apps.van_dashboard.van_dashboard_vonstar.secrets.token_hex",
            return_value="b" * 32,
        ):
            result = self.client().read_access_state()

        method, path, body, headers = self.requests[0]
        self.assertEqual(method, "POST")
        self.assertEqual(path, "/v1/access-state")
        self.assertEqual(
            json.loads(body),
            {"request_id": "dash-state-" + "b" * 32},
        )
        self.assertEqual(headers, {"Content-Type": "application/json"})
        self.assertEqual(result["operation"], "access_state")
        self.assertNotIn("request_id", result)
        self.assertEqual(result["access_state"]["lock_domains"]["state"], "locked")
        self.assertNotIn("source", result["access_state"]["lock_domains"])
        self.assertEqual(
            result["access_state"]["doors"]["sliding"]["physical_state_observable"],
            False,
        )
        self.assertEqual(
            result["access_state"]["wake"],
            {"count": 1, "restored_passive_after_sampling": True},
        )
        self.assertIn("observations", result["access_state"])

    def test_status_preserves_successful_access_state_as_sanitized_last_result(self):
        self.responses.append(
            (
                200,
                {
                    "ok": True,
                    "vonstar": {
                        "service": "vonstar",
                        "schema_version": 1,
                        "available": True,
                        "mode": "execute",
                        "busy": False,
                        "actions": {
                            "lock_all": {},
                            "unlock_front": {},
                            "unlock_cargo": {},
                        },
                        "cooldown_seconds": 3.0,
                        "last_result": {
                            "ok": True,
                            "operation": "access_state",
                            "request_id": "must-be-dropped",
                            "access_state": self.access_state(),
                        },
                    },
                },
            )
        )
        status = self.client().snapshot()
        self.assertTrue(status["available"])
        self.assertEqual(status["last_result"]["operation"], "access_state")
        self.assertNotIn("request_id", status["last_result"])

    def test_unknown_action_never_opens_a_connection(self):
        with self.assertRaisesRegex(ValueError, "unknown Vonstar action"):
            self.client().perform("custom_payload")
        self.assertEqual(self.connections, [])

    def test_symlink_or_non_socket_path_is_rejected(self):
        client = dashboard.VonstarClient(
            socket_path="/run/not-a-socket",
            connection_factory=self.factory,
            lstat=lambda _path: SimpleNamespace(st_mode=stat.S_IFLNK | 0o777),
        )
        status = client.snapshot()
        self.assertFalse(status["available"])
        self.assertEqual(status["mode"], "unavailable")
        self.assertIn("socket is invalid", status["error"])
        self.assertEqual(self.connections, [])

    def test_wrong_identity_schema_and_catalog_fail_closed(self):
        invalid_statuses = (
            {"service": "other", "schema_version": 1},
            {"service": "vonstar", "schema_version": True},
            {
                "service": "vonstar",
                "schema_version": 1,
                "available": True,
                "mode": "execute",
                "busy": False,
                "actions": {"unlock_front": {}},
                "cooldown_seconds": 3,
            },
        )
        for invalid in invalid_statuses:
            with self.subTest(invalid=invalid):
                self.responses.append((200, {"ok": True, "vonstar": invalid}))
                self.assertFalse(self.client().snapshot()["available"])

    def test_oversized_response_fails_closed(self):
        class OversizedConnection:
            def request(self, _method, _path, body=None, headers=None):
                pass

            @staticmethod
            def getresponse():
                return SimpleNamespace(
                    status=200,
                    read=lambda _limit: b"x" * (64 * 1024 + 1),
                )

            def close(self):
                pass

        client = dashboard.VonstarClient(
            socket_path="/run/test-vonstar.sock",
            connection_factory=lambda _path, _timeout: OversizedConnection(),
            lstat=self.socket_stat,
        )
        status = client.snapshot()
        self.assertFalse(status["available"])
        self.assertIn("too large", status["error"])


class DashboardRouteTests(unittest.TestCase):
    def test_manifest_and_legacy_routes_are_removed(self):
        client = dashboard.app.test_client()
        legacy = client.get("/legacy")
        javascript = client.get("/static/van_dashboard.js")
        stylesheet = client.get("/static/van_dashboard.css")
        manifest = client.get("/manifest.webmanifest")
        self.addCleanup(legacy.close)
        self.addCleanup(javascript.close)
        self.addCleanup(stylesheet.close)
        self.addCleanup(manifest.close)

        self.assertEqual(legacy.status_code, 404)
        self.assertEqual(javascript.status_code, 404)
        self.assertEqual(stylesheet.status_code, 404)
        self.assertEqual(manifest.status_code, 200)
        self.assertEqual(manifest.json["name"], "Van Dashboard")

        with tempfile.TemporaryDirectory() as directory:
            with mock.patch.object(dashboard, "REACT_FRONTEND_ROOT", directory):
                missing = client.get("/")
            missing_data = missing.data
            missing.close()

        self.assertEqual(missing.status_code, 503)
        self.assertIn(b"React dashboard build is unavailable", missing_data)

    def test_root_serves_react_build_and_hashed_assets(self):
        with tempfile.TemporaryDirectory() as directory:
            os.makedirs(os.path.join(directory, "assets"))
            with open(os.path.join(directory, "index.html"), "wb") as handle:
                handle.write(b"<!doctype html><main>React dashboard</main>")
            with open(os.path.join(directory, "assets", "app-deadbeef.js"), "wb") as handle:
                handle.write(b"window.reactDashboard = true;")

            client = dashboard.app.test_client()
            with mock.patch.object(dashboard, "REACT_FRONTEND_ROOT", directory):
                root = client.get("/")
                asset = client.get("/assets/app-deadbeef.js")

            root_data = root.data
            root_cache = root.headers["Cache-Control"]
            asset_cache = asset.headers["Cache-Control"]
            root.close()
            asset.close()

        self.assertEqual(root.status_code, 200)
        self.assertIn(b"React dashboard", root_data)
        self.assertEqual(root_cache, "no-store")
        self.assertEqual(asset.status_code, 200)
        self.assertEqual(asset_cache, "public, max-age=31536000, immutable")

    def test_sync_scripts_deploys_react_only_dashboard_backend(self):
        repository = str(REPOSITORY_ROOT)
        with open(os.path.join(repository, "pi", "sync_scripts.sh"), encoding="utf-8") as handle:
            sync_script = handle.read()
        self.assertNotIn("$pi_apps/van_dashboard/templates", sync_script)
        self.assertNotIn("$pi_apps/van_dashboard/static", sync_script)
        self.assertIn('mkdir -p "$python_stage/templates" "$python_stage/static"', sync_script)
        self.assertIn('shared_sh="$dsc/shared/sh"', sync_script)
        self.assertIn(
            'cp -a "$shared_sh/." "$staged_scripts/"',
            sync_script,
        )
        self.assertNotIn("/home/pi/scripts/shared", sync_script)
        update_services = (
            REPOSITORY_ROOT / "pi" / "scripts" / "update_services.sh"
        ).read_text(encoding="utf-8")
        for retired in (
            "templates/van_dashboard.html",
            "static/van_dashboard.js",
            "static/van_dashboard.css",
        ):
            self.assertIn(retired, update_services)

    def test_dashboard_modules_are_flat_deployable_restart_dependencies(self):
        backend = REPOSITORY_ROOT / "pi" / "apps" / "van_dashboard"
        modules = sorted(
            path.name
            for path in backend.glob("van_dashboard_*.py")
        )
        self.assertEqual(
            modules,
            [
                "van_dashboard_backups.py",
                "van_dashboard_block_devices.py",
                "van_dashboard_common.py",
                "van_dashboard_cop.py",
                "van_dashboard_disks.py",
                "van_dashboard_home.py",
                "van_dashboard_integrations.py",
                "van_dashboard_network.py",
                "van_dashboard_sonos.py",
                "van_dashboard_storage.py",
                "van_dashboard_system.py",
                "van_dashboard_telemetry.py",
                "van_dashboard_usb.py",
                "van_dashboard_vonstar.py",
            ],
        )
        service = (
            REPOSITORY_ROOT / "pi" / "services" / "van-dashboard.service"
        ).read_text(encoding="utf-8")
        for module in modules:
            self.assertIn(
                "ExecStartPre=/usr/bin/test -r "
                f"/home/pi/scripts/python-automation/{module}",
                service,
            )
        self.assertNotIn("van_dashboard.html", service)
        self.assertNotIn("static/van_dashboard.js", service)
        self.assertNotIn("static/van_dashboard.css", service)

    def test_dashboard_imports_from_deployed_flat_layout(self):
        backend = REPOSITORY_ROOT / "pi" / "apps" / "van_dashboard"
        compute = (
            REPOSITORY_ROOT
            / "pi"
            / "van_compute"
            / "scripts"
            / "van_compute_metrics.py"
        )
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            for path in backend.glob("van_dashboard*.py"):
                shutil.copy2(path, target / path.name)
            shutil.copy2(compute, target / "van_compute_metrics.py")
            environment = os.environ.copy()
            environment["PYTHONPATH"] = directory
            environment["VAN_DASHBOARD_STATE_PATH"] = str(target / "state.json")
            result = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    (
                        "import van_dashboard as dashboard; "
                        "assert dashboard.app.name == 'van_dashboard'; "
                        "assert dashboard.CopAlertManager.__module__ "
                        "== 'van_dashboard_cop'"
                    ),
                ],
                cwd=directory,
                env=environment,
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_ntfy_helper_has_bounded_network_timeouts(self):
        repository = str(REPOSITORY_ROOT)
        with open(os.path.join(repository, "pi", "scripts", "ntfy_send.sh"), encoding="utf-8") as handle:
            script = handle.read()
        self.assertIn("--connect-timeout 5", script)
        self.assertIn("--max-time 15", script)

    def test_dashboard_cop_controller_is_can_free(self):
        backend = REPOSITORY_ROOT / "pi" / "apps" / "van_dashboard"
        source = "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted(backend.glob("van_dashboard*.py"))
        )
        self.assertIn('ACTIVE_MARKER = os.path.join(RUNTIME_DIR, "cop-alert.active")', source)
        self.assertNotIn("socket.AF_CAN", source)
        self.assertNotIn("socket.CAN_RAW", source)
        self.assertNotIn("import isotp", source)
        self.assertNotIn("lib.can_wake", source)
        self.assertNotIn("CAN_CHANNEL", source)

    def test_vonstar_dashboard_boundary_is_fixed_and_can_free(self):
        backend = REPOSITORY_ROOT / "pi" / "apps" / "van_dashboard"
        source = (backend / "van_dashboard_vonstar.py").read_text(encoding="utf-8")
        facade = (backend / "van_dashboard.py").read_text(encoding="utf-8")
        self.assertIn("socket.AF_UNIX", source)
        self.assertIn('"/run/vonstar/api.sock"', source)
        self.assertIn('payload={"request_id": request_id}', source)
        self.assertEqual(
            set(dashboard.VONSTAR_ACTIONS),
            {"lock_all", "unlock_front", "unlock_cargo"},
        )
        for forbidden in (
            "socket.AF_CAN",
            "socket.CAN_RAW",
            "import isotp",
            "lib.can_wake",
            "payload_hex",
            "counter_streak",
            "send_count",
            "CAN_CHANNEL",
        ):
            self.assertNotIn(forbidden, source)
            self.assertNotIn(forbidden, facade)

    def test_vonstar_routes_whitelist_action_and_sanitize_response(self):
        calls = []
        access_calls = []

        class FakeVonstar:
            @staticmethod
            def snapshot():
                return {
                    "service": "vonstar",
                    "available": True,
                    "mode": "execute",
                    "busy": False,
                    "actions": dashboard.VONSTAR_ACTIONS,
                    "cooldown_seconds": 3,
                    "last_result": None,
                    "error": None,
                }

            @staticmethod
            def perform(action):
                calls.append(action)
                if action not in dashboard.VONSTAR_ACTIONS:
                    raise ValueError("unknown Vonstar action")
                return {
                    "action": action,
                    "label": dashboard.VONSTAR_ACTIONS[action]["label"],
                    "ok": True,
                }

            @staticmethod
            def read_access_state():
                access_calls.append("read")
                return {
                    "ok": True,
                    "operation": "access_state",
                    "access_state": VonstarClientTests.access_state(),
                }

        client = dashboard.app.test_client()
        with mock.patch.object(dashboard, "vonstar", FakeVonstar()):
            page = client.get("/")
            dashboard_status = client.get("/api/status")
            status = client.get("/api/vonstar")
            invalid_query = client.get("/api/vonstar?fresh=1")
            extra = client.post(
                "/api/vonstar",
                data={"action": "lock_all", "payload": "forbidden"},
            )
            duplicate = client.post(
                "/api/vonstar",
                data={"action": ["lock_all", "unlock_front"]},
            )
            unknown = client.post("/api/vonstar", data={"action": "raw_can"})
            accepted = client.post("/api/vonstar", data={"action": "unlock_front"})
            access_query = client.post("/api/vonstar/access-state?fresh=1")
            access_body = client.post(
                "/api/vonstar/access-state",
                data={"unexpected": "input"},
            )
            self.assertEqual(access_calls, [])
            access = client.post("/api/vonstar/access-state")

        self.assertEqual(page.status_code, 200)
        self.assertEqual(dashboard_status.status_code, 200)
        self.assertEqual(status.status_code, 200)
        self.assertEqual(status.headers["Cache-Control"], "no-store")
        self.assertEqual(invalid_query.status_code, 400)
        self.assertEqual(extra.status_code, 400)
        self.assertEqual(duplicate.status_code, 400)
        self.assertEqual(unknown.status_code, 400)
        self.assertEqual(accepted.status_code, 200)
        self.assertEqual(calls, ["raw_can", "unlock_front"])
        self.assertEqual(accepted.json["result"]["action"], "unlock_front")
        self.assertNotIn("payload_hex", accepted.json["result"])
        self.assertEqual(access_query.status_code, 400)
        self.assertEqual(access_body.status_code, 400)
        self.assertEqual(access.status_code, 200)
        self.assertEqual(access.headers["Cache-Control"], "no-store")
        self.assertEqual(access_calls, ["read"])
        self.assertEqual(access.json["access_state"]["lock_domains"]["state"], "locked")

    def test_status_route_exposes_sanitized_cop_can_wake_snapshot(self):
        class FakeCanWake:
            @staticmethod
            def snapshot():
                return {
                    "available": True,
                    "service_active": True,
                    "state": "idle",
                    "marker_active": False,
                }

        client = dashboard.app.test_client()
        with mock.patch.object(dashboard, "cop_can_wake", FakeCanWake()):
            response = client.get("/api/status")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["cop_can_wake"]["state"], "idle")
        self.assertNotIn("wake_method", response.json["cop_can_wake"])

    def test_connectivity_and_speedtest_status_routes(self):
        client = dashboard.app.test_client()
        connectivity = client.get("/api/connectivity")
        self.assertEqual(connectivity.status_code, 200)
        self.assertEqual(connectivity.headers["Cache-Control"], "no-store")
        self.assertIn("router", connectivity.json["connectivity"])
        speedtest = client.get("/api/speedtest")
        self.assertEqual(speedtest.status_code, 200)
        self.assertIn(speedtest.json["speedtest"]["status"], ("idle", "complete", "error"))

    def test_connectivity_active_query_renews_worker_lease_without_collecting(self):
        calls = []

        class FakeConnectivity:
            def mark_active(self):
                calls.append("active")

            def snapshot(self):
                calls.append("snapshot")
                return {"router": {}, "active_mode": bool(calls)}

        client = dashboard.app.test_client()
        with mock.patch.object(dashboard, "connectivity", FakeConnectivity()):
            response = client.get("/api/connectivity?active=1")
            invalid_value = client.get("/api/connectivity?active=true")
            unknown = client.get("/api/connectivity?fresh=1")
            duplicate = client.get("/api/connectivity?active=1&active=1")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(calls, ["active", "snapshot"])
        self.assertEqual(invalid_value.status_code, 400)
        self.assertEqual(unknown.status_code, 400)
        self.assertEqual(duplicate.status_code, 400)

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

        class FakeUsbPorts:
            def snapshot(self):
                calls.append("ports-snapshot")
                return {
                    "loaded": False,
                    "checked_at": None,
                    "hubs": [],
                    "operation": {"status": "idle"},
                }

        original = dashboard.usb_devices
        original_ports = dashboard.usb_ports
        dashboard.usb_devices = FakeUsbDevices()
        dashboard.usb_ports = FakeUsbPorts()
        try:
            client = dashboard.app.test_client()
            response = client.get("/api/usb-devices")
            rejected = client.get("/api/usb-devices?command=anything")
        finally:
            dashboard.usb_devices = original
            dashboard.usb_ports = original_ports

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(response.json["usb"]["present_device_count"], 2)
        self.assertEqual(rejected.status_code, 400)
        self.assertEqual(calls, ["refresh", "ports-snapshot"])

    def test_usb_port_discovery_route_is_explicit_and_csrf_protected(self):
        calls = []

        class FakeUsbPorts:
            def discover(self):
                calls.append("discover")
                return {
                    "loaded": True,
                    "checked_at": 123,
                    "hubs": [],
                    "operation": {"status": "idle"},
                }

        original = dashboard.usb_ports
        dashboard.usb_ports = FakeUsbPorts()
        try:
            client = dashboard.app.test_client()
            accepted = client.post(
                "/api/usb-ports/discover",
                headers={"X-Van-Dashboard": "1"},
            )
            supplied_input = client.post(
                "/api/usb-ports/discover",
                data={"refresh": "again"},
                headers={"X-Van-Dashboard": "1"},
            )
            cross_origin = client.post(
                "/api/usb-ports/discover",
                headers={
                    "X-Van-Dashboard": "1",
                    "Origin": "https://example.invalid",
                },
            )
        finally:
            dashboard.usb_ports = original

        self.assertEqual(accepted.status_code, 200)
        self.assertEqual(accepted.headers["Cache-Control"], "no-store")
        self.assertTrue(accepted.json["usb_ports"]["loaded"])
        self.assertEqual(supplied_input.status_code, 400)
        self.assertEqual(cross_origin.status_code, 403)
        self.assertEqual(calls, ["discover"])

    def test_usb_port_action_route_is_whitelisted_and_csrf_protected(self):
        calls = []

        class FakeUsbPorts:
            def start_action(self, port, action):
                calls.append((port, action))
                return {"operation": {"status": "running", "key": port, "action": action}}

        original = dashboard.usb_ports
        dashboard.usb_ports = FakeUsbPorts()
        try:
            client = dashboard.app.test_client()
            accepted = client.post(
                "/api/usb-ports/action",
                data={"port": "2-2:3", "action": "off"},
                headers={"X-Van-Dashboard": "1"},
            )
            unknown_field = client.post(
                "/api/usb-ports/action",
                data={"port": "2-2:3", "action": "off", "command": "anything"},
                headers={"X-Van-Dashboard": "1"},
            )
            cross_origin = client.post(
                "/api/usb-ports/action",
                data={"port": "2-2:3", "action": "off"},
                headers={
                    "X-Van-Dashboard": "1",
                    "Origin": "https://example.invalid",
                },
            )
        finally:
            dashboard.usb_ports = original

        self.assertEqual(accepted.status_code, 200)
        self.assertEqual(accepted.headers["Cache-Control"], "no-store")
        self.assertEqual(unknown_field.status_code, 400)
        self.assertEqual(cross_origin.status_code, 403)
        self.assertEqual(calls, [("2-2:3", "off")])

    def test_usb2_recovery_route_accepts_no_input_and_is_csrf_protected(self):
        calls = []

        class FakeUsbPorts:
            def start_recovery(self):
                calls.append("recover")
                return {"operation": {"status": "running", "action": "restore"}}

        original = dashboard.usb_ports
        dashboard.usb_ports = FakeUsbPorts()
        try:
            client = dashboard.app.test_client()
            accepted = client.post(
                "/api/usb-ports/recover",
                headers={"X-Van-Dashboard": "1"},
            )
            supplied_input = client.post(
                "/api/usb-ports/recover",
                data={"location": "anything"},
                headers={"X-Van-Dashboard": "1"},
            )
            cross_origin = client.post(
                "/api/usb-ports/recover",
                headers={
                    "X-Van-Dashboard": "1",
                    "Origin": "https://example.invalid",
                },
            )
        finally:
            dashboard.usb_ports = original

        self.assertEqual(accepted.status_code, 200)
        self.assertEqual(accepted.headers["Cache-Control"], "no-store")
        self.assertEqual(supplied_input.status_code, 400)
        self.assertEqual(cross_origin.status_code, 403)
        self.assertEqual(calls, ["recover"])

    def test_backup_routes_are_narrow_nonblocking_and_uncached(self):
        calls = []
        state = {
            "checked_at": 123,
            "health": "good",
            "settings": {
                "clone_card_nominal_gb": 64,
                "root_used_max_gib": 52,
                "clone_card_nominal_gb_options": [32, 64, 128, 256],
            },
            "borg": {"last_success_at": 100, "stale": False},
            "exfat_snapshot": {"last_success_at": 100, "stale": False},
            "hotswaps": [],
            "time_machine": {"available": True, "snapshots": []},
            "operation": {"status": "idle"},
        }

        class FakeBackups:
            def status(self):
                calls.append(("status",))
                return state

            def start_borg_backup(self):
                calls.append(("borg",))
                return {
                    **state,
                    "operation": {"status": "running", "kind": "borg"},
                }

            def start_exfat_backup(self):
                calls.append(("exfat",))
                return {
                    **state,
                    "operation": {"status": "running", "kind": "exfat"},
                }

            def start_clone(self, target):
                calls.append(("clone", target))
                if target != "hotspare-a":
                    raise ValueError("unknown hotspare target")
                return {**state, "operation": {"status": "running", "target": target}}

            def set_clone_card_nominal_gb(self, value):
                calls.append(("capacity", value))
                if value not in ("32", "64", "128", "256"):
                    raise ValueError("unsupported clone card capacity")
                return {
                    **state,
                    "settings": {
                        **state["settings"],
                        "clone_card_nominal_gb": int(value),
                        "root_used_max_gib": int(value) * 13 // 16,
                    },
                }

            def request_stop(self, kind):
                calls.append(("stop", kind))
                return {
                    **state,
                    "stop": {"status": "running", "kind": kind},
                }

        original = dashboard.backups
        dashboard.backups = FakeBackups()
        try:
            client = dashboard.app.test_client()
            status = client.get("/api/backups")
            started = client.post(
                "/api/backups/clone",
                data={"target": "hotspare-a"},
                headers={"X-Van-Dashboard": "1"},
            )
            capacity = client.post(
                "/api/backups/settings/clone-card-size",
                data={"nominal_gb": "128"},
                headers={"X-Van-Dashboard": "1"},
            )
            bad_capacity = client.post(
                "/api/backups/settings/clone-card-size",
                data={"nominal_gb": "512"},
                headers={"X-Van-Dashboard": "1"},
            )
            capacity_extra = client.post(
                "/api/backups/settings/clone-card-size",
                data={"nominal_gb": "64", "path": "/dev/sda"},
                headers={"X-Van-Dashboard": "1"},
            )
            borg = client.post(
                "/api/backups/borg",
                headers={"X-Van-Dashboard": "1"},
            )
            exfat = client.post(
                "/api/backups/exfat",
                headers={"X-Van-Dashboard": "1"},
            )
            borg_stop = client.post(
                "/api/backups/borg/stop",
                headers={"X-Van-Dashboard": "1"},
            )
            exfat_stop = client.post(
                "/api/backups/exfat/stop",
                headers={"X-Van-Dashboard": "1"},
            )
            unknown = client.post(
                "/api/backups/clone",
                data={"target": "/dev/sda"},
                headers={"X-Van-Dashboard": "1"},
            )
            extra = client.post(
                "/api/backups/clone",
                data={"target": "hotspare-a", "command": "--init"},
                headers={"X-Van-Dashboard": "1"},
            )
            borg_extra = client.post(
                "/api/backups/borg",
                data={"command": "--anything"},
                headers={"X-Van-Dashboard": "1"},
            )
            stop_extra = client.post(
                "/api/backups/borg/stop",
                data={"signal": "KILL"},
                headers={"X-Van-Dashboard": "1"},
            )
            stop_query = client.post(
                "/api/backups/exfat/stop?signal=KILL",
                headers={"X-Van-Dashboard": "1"},
            )
            query = client.get("/api/backups?device=sda")
            cross_origin = client.post(
                "/api/backups/clone",
                data={"target": "hotspare-a"},
                headers={
                    "X-Van-Dashboard": "1",
                    "Origin": "https://example.invalid",
                },
            )
            backup_cross_origin = client.post(
                "/api/backups/exfat",
                headers={
                    "X-Van-Dashboard": "1",
                    "Origin": "https://example.invalid",
                },
            )
            capacity_cross_origin = client.post(
                "/api/backups/settings/clone-card-size",
                data={"nominal_gb": "64"},
                headers={
                    "X-Van-Dashboard": "1",
                    "Origin": "https://example.invalid",
                },
            )
            stop_cross_origin = client.post(
                "/api/backups/borg/stop",
                headers={
                    "X-Van-Dashboard": "1",
                    "Origin": "https://example.invalid",
                },
            )
        finally:
            dashboard.backups = original

        self.assertEqual(status.status_code, 200)
        self.assertEqual(status.headers["Cache-Control"], "no-store")
        self.assertEqual(started.status_code, 202)
        self.assertEqual(started.headers["Cache-Control"], "no-store")
        self.assertEqual(capacity.status_code, 200)
        self.assertEqual(capacity.headers["Cache-Control"], "no-store")
        self.assertEqual(
            capacity.json["backups"]["settings"]["clone_card_nominal_gb"], 128
        )
        self.assertEqual(bad_capacity.status_code, 400)
        self.assertEqual(capacity_extra.status_code, 400)
        self.assertEqual(borg.status_code, 202)
        self.assertEqual(borg.headers["Cache-Control"], "no-store")
        self.assertEqual(exfat.status_code, 202)
        self.assertEqual(exfat.headers["Cache-Control"], "no-store")
        self.assertEqual(borg_stop.status_code, 202)
        self.assertEqual(borg_stop.headers["Cache-Control"], "no-store")
        self.assertEqual(exfat_stop.status_code, 202)
        self.assertEqual(exfat_stop.headers["Cache-Control"], "no-store")
        self.assertEqual(unknown.status_code, 400)
        self.assertEqual(extra.status_code, 400)
        self.assertEqual(borg_extra.status_code, 400)
        self.assertEqual(stop_extra.status_code, 400)
        self.assertEqual(stop_query.status_code, 400)
        self.assertEqual(query.status_code, 400)
        self.assertEqual(cross_origin.status_code, 403)
        self.assertEqual(backup_cross_origin.status_code, 403)
        self.assertEqual(stop_cross_origin.status_code, 403)
        self.assertEqual(capacity_cross_origin.status_code, 403)
        self.assertEqual(
            calls,
            [
                ("status",),
                ("clone", "hotspare-a"),
                ("capacity", "128"),
                ("capacity", "512"),
                ("borg",),
                ("exfat",),
                ("stop", "borg"),
                ("stop", "exfat"),
                ("clone", "/dev/sda"),
            ],
        )

    def test_ignition_monitor_routes_are_authoritative_narrow_and_csrf_protected(self):
        calls = []
        active = {
            "service": {
                "active_state": "active",
                "sub_state": "running",
                "unit_file_state": "enabled",
                "running": True,
                "enabled": True,
            },
            "monitor": {
                "version": 1,
                "status": "active",
                "active": True,
                "deadline": None,
                "remaining_seconds": 0,
                "checked_at": 123,
            },
        }

        class FakeIgnitionMonitor:
            def status(self):
                calls.append(("status",))
                return active

            def disable(self, minutes):
                calls.append(("disable", minutes))
                return active

            def enable(self):
                calls.append(("enable",))
                return active

        original = dashboard.ignition_monitor_control
        dashboard.ignition_monitor_control = FakeIgnitionMonitor()
        try:
            client = dashboard.app.test_client()
            status = client.get("/api/ignition-monitor")
            disabled = client.post(
                "/api/ignition-monitor/disable",
                data={"minutes": "90"},
                headers={"X-Van-Dashboard": "1"},
            )
            enabled = client.post(
                "/api/ignition-monitor/enable",
                headers={"X-Van-Dashboard": "1"},
            )
            bad_duration = client.post(
                "/api/ignition-monitor/disable", data={"minutes": "2h"}
            )
            extra = client.post(
                "/api/ignition-monitor/disable",
                data={"minutes": "90", "command": "anything"},
            )
            enable_input = client.post(
                "/api/ignition-monitor/enable", data={"minutes": "90"}
            )
            query = client.get("/api/ignition-monitor?command=anything")
            cross_origin = client.post(
                "/api/ignition-monitor/disable",
                data={"minutes": "90"},
                headers={
                    "X-Van-Dashboard": "1",
                    "Origin": "https://example.invalid",
                },
            )
        finally:
            dashboard.ignition_monitor_control = original

        self.assertEqual(status.status_code, 200)
        self.assertEqual(status.headers["Cache-Control"], "no-store")
        self.assertEqual(disabled.status_code, 200)
        self.assertEqual(enabled.status_code, 200)
        self.assertEqual(bad_duration.status_code, 400)
        self.assertEqual(extra.status_code, 400)
        self.assertEqual(enable_input.status_code, 400)
        self.assertEqual(query.status_code, 400)
        self.assertEqual(cross_origin.status_code, 403)
        self.assertEqual(calls, [("status",), ("disable", 90), ("enable",)])

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

    def test_compute_route_has_bounded_ranges(self):
        calls = []
        detail_calls = []

        class FakeComputeMonitor:
            def report(self, hours):
                calls.append(hours)
                return {
                    "ok": True,
                    "range_hours": hours,
                    "status": {"available": True, "queued": 0, "running": 0},
                    "summary": {"jobs": 2, "mac_cpu_seconds": 3.5},
                    "tasks": [],
                    "jobs": [],
                }

            def jobs_for_task(self, hours, task):
                calls.append(("task", hours, task))
                if task == "unavailable":
                    raise dashboard.ComputeMetricsError("queue unavailable")
                return {
                    "ok": True,
                    "range_hours": hours,
                    "task": task,
                    "matching_jobs": 1,
                    "truncated": False,
                    "jobs": [{"id": "20260722T015900Z-deadbeef", "task": task}],
                }

            def job_details(self, job_id):
                detail_calls.append(job_id)
                if job_id == "not-a-job":
                    raise ValueError("invalid compute job id")
                if job_id.endswith("feedface"):
                    raise FileNotFoundError(job_id)
                if job_id.endswith("0badc0de"):
                    raise dashboard.ComputeMetricsError("unsafe result path")
                return {
                    "ok": True,
                    "job": {
                        "id": job_id,
                        "state": "failed",
                        "failure_classification": "task",
                    },
                    "diagnostics": {"worker_error": None},
                    "stderr": {"available": True, "excerpt": "test failed"},
                    "stdout": {"available": False, "excerpt": ""},
                }

        original = dashboard.compute_monitor
        dashboard.compute_monitor = FakeComputeMonitor()
        try:
            client = dashboard.app.test_client()
            default = client.get("/api/compute")
            day = client.get("/api/compute?hours=24")
            task_jobs = client.get("/api/compute/jobs?hours=24&task=repo-tests")
            missing_task = client.get("/api/compute/jobs?hours=24")
            duplicate_task = client.get(
                "/api/compute/jobs?hours=24&task=repo-tests&task=other"
            )
            invalid_task_range = client.get(
                "/api/compute/jobs?hours=25&task=repo-tests"
            )
            invalid_task_name = client.get(
                "/api/compute/jobs?hours=24&task=" + ("x" * 65)
            )
            unavailable_task_jobs = client.get(
                "/api/compute/jobs?hours=24&task=unavailable"
            )
            invalid = client.get("/api/compute?hours=25")
            extra = client.get("/api/compute?hours=24&command=anything")
            details = client.get(
                "/api/compute/jobs/20260722T015900Z-deadbeef"
            )
            bad_job = client.get("/api/compute/jobs/not-a-job")
            missing_job = client.get(
                "/api/compute/jobs/20260722T015900Z-feedface"
            )
            detail_extra = client.get(
                "/api/compute/jobs/20260722T015900Z-deadbeef?output=all"
            )
            unavailable_details = client.get(
                "/api/compute/jobs/20260722T015900Z-0badc0de"
            )
        finally:
            dashboard.compute_monitor = original

        self.assertEqual(default.status_code, 200)
        self.assertEqual(default.headers["Cache-Control"], "no-store")
        self.assertEqual(default.json["range_hours"], 168)
        self.assertEqual(day.status_code, 200)
        self.assertEqual(task_jobs.status_code, 200)
        self.assertEqual(task_jobs.headers["Cache-Control"], "no-store")
        self.assertEqual(task_jobs.json["task"], "repo-tests")
        self.assertEqual(missing_task.status_code, 400)
        self.assertEqual(duplicate_task.status_code, 400)
        self.assertEqual(invalid_task_range.status_code, 400)
        self.assertEqual(invalid_task_name.status_code, 400)
        self.assertEqual(unavailable_task_jobs.status_code, 503)
        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(extra.status_code, 400)
        self.assertEqual(details.status_code, 200)
        self.assertEqual(details.headers["Cache-Control"], "no-store")
        self.assertEqual(
            details.json["job"]["failure_classification"], "task"
        )
        self.assertEqual(bad_job.status_code, 400)
        self.assertEqual(missing_job.status_code, 404)
        self.assertEqual(detail_extra.status_code, 400)
        self.assertEqual(unavailable_details.status_code, 503)
        self.assertEqual(
            calls,
            [168, 24, ("task", 24, "repo-tests"), ("task", 24, "unavailable")],
        )
        self.assertEqual(
            detail_calls,
            [
                "20260722T015900Z-deadbeef",
                "not-a-job",
                "20260722T015900Z-feedface",
                "20260722T015900Z-0badc0de",
            ],
        )

    def test_compute_task_filter_degrades_when_metrics_reader_is_older(self):
        class LegacyComputeMonitor:
            pass

        original = dashboard.compute_monitor
        dashboard.compute_monitor = LegacyComputeMonitor()
        try:
            response = dashboard.app.test_client().get(
                "/api/compute/jobs?hours=24&task=repo-tests"
            )
        finally:
            dashboard.compute_monitor = original

        self.assertEqual(response.status_code, 503)
        self.assertIn("matching van_compute metrics release", response.json["message"])

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

            def set_hue(self, entity, hue):
                calls.append(("hue", entity, hue))
                return {**status, "state": "on", "on_count": 1}

            def set_color_temperature(self, entity, kelvin):
                calls.append(("temperature", entity, kelvin))
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
            hue = client.post(
                "/api/lights/hue",
                data={"entity": "light.wiz_kitchen", "hue": "225"},
            )
            temperature = client.post(
                "/api/lights/color-temperature",
                data={"entity": "light.wiz_kitchen", "kelvin": "3200"},
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
            bad_hue = client.post(
                "/api/lights/hue",
                data={"entity": "light.wiz_kitchen", "hue": "361"},
            )
            bad_temperature = client.post(
                "/api/lights/color-temperature",
                data={"entity": "light.wiz_kitchen", "kelvin": "warm"},
            )
            extra_color = client.post(
                "/api/lights/hue",
                data={
                    "entity": "light.wiz_kitchen",
                    "hue": "120",
                    "command": "anything",
                },
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
        self.assertEqual(hue.status_code, 200)
        self.assertEqual(temperature.status_code, 200)
        self.assertEqual(unknown_target.status_code, 400)
        self.assertEqual(unknown_entity.status_code, 400)
        self.assertEqual(bad_value.status_code, 400)
        self.assertEqual(bad_hue.status_code, 400)
        self.assertEqual(bad_temperature.status_code, 400)
        self.assertEqual(extra_color.status_code, 400)
        self.assertEqual(extra.status_code, 400)
        self.assertEqual(
            calls,
            [
                ("status",),
                ("power", "all", True),
                ("brightness", "light.wiz_kitchen", 42),
                ("hue", "light.wiz_kitchen", 225),
                ("temperature", "light.wiz_kitchen", 3200),
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

            def abort(self):
                calls.append(("abort", None))
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
            profile_update = client.post(
                "/api/ubnt-wifi/profile",
                data={
                    "profile": "Known Camp",
                    "password": "replacement-password",
                    "bssid": "00:11:22:33:44:66",
                    "output_power_dbm": "18",
                    "rate_module": "ewma_ht",
                    "rate_auto": "false",
                    "rate_mcs": "4",
                    "apply_now": "false",
                },
            )
            resume = client.post("/api/ubnt-wifi/resume")
            abort = client.post("/api/ubnt-wifi/abort")
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
            bad_profile_update = client.post(
                "/api/ubnt-wifi/profile",
                data={
                    "profile": "Known Camp",
                    "password": "short",
                    "bssid": "anything",
                    "output_power_dbm": "99",
                    "rate_module": "shell",
                    "rate_auto": "maybe",
                    "rate_mcs": "99",
                    "apply_now": "maybe",
                },
            )
        finally:
            dashboard.ubnt_wifi = original

        self.assertEqual(status.status_code, 200)
        self.assertEqual(status.headers["Cache-Control"], "no-store")
        self.assertEqual(scan.status_code, 202)
        self.assertEqual(connect.status_code, 202)
        self.assertEqual(provision.status_code, 202)
        self.assertEqual(profile_update.status_code, 202)
        self.assertEqual(resume.status_code, 202)
        self.assertEqual(abort.status_code, 202)
        self.assertEqual(unknown_security.status_code, 400)
        self.assertEqual(extra_scan_input.status_code, 400)
        self.assertEqual(extra_connect_input.status_code, 400)
        self.assertEqual(bad_profile_update.status_code, 400)
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
                (
                    "update-profile",
                    {
                        "profile": "Known Camp",
                        "password": "replacement-password",
                        "bssid": "00:11:22:33:44:66",
                        "output_power_dbm": 18,
                        "rate_module": "ewma_ht",
                        "rate_auto": False,
                        "rate_mcs": 4,
                        "apply_now": False,
                    },
                ),
                ("resume", {}),
                ("abort", None),
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
            ],
        )

    def test_disk_status_and_actions_are_whitelisted_and_csrf_protected(self):
        calls = []
        status = {
            "checked_at": 1000,
            "disks": [
                {
                    "label": "movingparts",
                    "controllable": True,
                    "attached": True,
                    "mounted": True,
                }
            ],
            "operation": {"status": "idle"},
        }

        class FakeDiskManager:
            def status(self):
                calls.append("status")
                return status

            def start_action(self, label, action):
                if label != "movingparts":
                    raise ValueError("unknown controllable disk label")
                if action not in ("mount", "eject"):
                    raise ValueError("unknown disk action")
                calls.append((label, action))
                return {**status, "operation": {"status": "running"}}

        original = dashboard.disk_manager
        dashboard.disk_manager = FakeDiskManager()
        try:
            client = dashboard.app.test_client()
            current = client.get("/api/disks")
            query_rejected = client.get("/api/disks?label=movingparts")
            accepted = client.post(
                "/api/disks/action",
                data={"label": "movingparts", "action": "eject"},
                headers={"X-Van-Dashboard": "1"},
            )
            raw_device = client.post(
                "/api/disks/action",
                data={"label": "/dev/sda", "action": "eject"},
                headers={"X-Van-Dashboard": "1"},
            )
            unknown_action = client.post(
                "/api/disks/action",
                data={"label": "movingparts", "action": "delete"},
                headers={"X-Van-Dashboard": "1"},
            )
            extra_input = client.post(
                "/api/disks/action",
                data={"label": "movingparts", "action": "eject", "command": "anything"},
                headers={"X-Van-Dashboard": "1"},
            )
            cross_origin = client.post(
                "/api/disks/action",
                data={"label": "movingparts", "action": "eject"},
                headers={
                    "X-Van-Dashboard": "1",
                    "Origin": "https://example.invalid",
                },
            )
        finally:
            dashboard.disk_manager = original

        self.assertEqual(current.status_code, 200)
        self.assertEqual(current.headers["Cache-Control"], "no-store")
        self.assertEqual(current.json["disk_status"]["disks"][0]["label"], "movingparts")
        self.assertEqual(query_rejected.status_code, 400)
        self.assertEqual(accepted.status_code, 202)
        self.assertEqual(accepted.headers["Cache-Control"], "no-store")
        self.assertEqual(accepted.json["message"], "Unmount started for movingparts")
        self.assertEqual(raw_device.status_code, 400)
        self.assertEqual(unknown_action.status_code, 400)
        self.assertEqual(extra_input.status_code, 400)
        self.assertEqual(cross_origin.status_code, 403)
        self.assertEqual(calls, ["status", ("movingparts", "eject")])

    def test_system_power_route_requires_confirmation_and_fixed_actions(self):
        calls = []

        class FakeSystemPower:
            def snapshot(self):
                return {"status": "idle", "action": None}

            def start_action(self, action):
                if action not in ("reboot", "power-down"):
                    raise ValueError("unknown system power action")
                calls.append(action)
                return {"status": "running", "action": action}

        original = dashboard.system_power
        dashboard.system_power = FakeSystemPower()
        try:
            client = dashboard.app.test_client()
            status = client.get("/api/system-power")
            status_input = client.get("/api/system-power?command=anything")
            accepted = client.post(
                "/api/system-power",
                data={"action": "reboot", "confirmation": "reboot"},
                headers={"X-Van-Dashboard": "1"},
            )
            unconfirmed = client.post(
                "/api/system-power",
                data={"action": "power-down", "confirmation": "reboot"},
                headers={"X-Van-Dashboard": "1"},
            )
            unknown = client.post(
                "/api/system-power",
                data={"action": "shell-command", "confirmation": "shell-command"},
                headers={"X-Van-Dashboard": "1"},
            )
            extra = client.post(
                "/api/system-power",
                data={
                    "action": "reboot",
                    "confirmation": "reboot",
                    "command": "anything",
                },
                headers={"X-Van-Dashboard": "1"},
            )
            cross_origin = client.post(
                "/api/system-power",
                data={"action": "reboot", "confirmation": "reboot"},
                headers={
                    "X-Van-Dashboard": "1",
                    "Origin": "https://example.invalid",
                },
            )
        finally:
            dashboard.system_power = original

        self.assertEqual(status.status_code, 200)
        self.assertEqual(status.headers["Cache-Control"], "no-store")
        self.assertEqual(status.json["system_power"]["status"], "idle")
        self.assertEqual(status_input.status_code, 400)
        self.assertEqual(accepted.status_code, 202)
        self.assertEqual(accepted.headers["Cache-Control"], "no-store")
        self.assertIn("safely unmounting disks", accepted.json["message"])
        self.assertEqual(unconfirmed.status_code, 400)
        self.assertEqual(unknown.status_code, 400)
        self.assertEqual(extra.status_code, 400)
        self.assertEqual(cross_origin.status_code, 403)
        self.assertEqual(calls, ["reboot"])

    def test_dashboard_restart_route_is_fixed_confirmed_and_csrf_protected(self):
        calls = []

        class FakeDashboardRestart:
            def restart(self):
                calls.append("restart")
                return {"scheduled_at": 123}

        original = dashboard.dashboard_restart
        dashboard.dashboard_restart = FakeDashboardRestart()
        try:
            client = dashboard.app.test_client()
            accepted = client.post(
                "/api/dashboard-service/restart",
                data={"confirmation": "restart-dashboard"},
                headers={"X-Van-Dashboard": "1"},
            )
            unconfirmed = client.post(
                "/api/dashboard-service/restart",
                data={"confirmation": "no"},
                headers={"X-Van-Dashboard": "1"},
            )
            extra = client.post(
                "/api/dashboard-service/restart",
                data={"confirmation": "restart-dashboard", "service": "anything"},
                headers={"X-Van-Dashboard": "1"},
            )
            cross_origin = client.post(
                "/api/dashboard-service/restart",
                data={"confirmation": "restart-dashboard"},
                headers={
                    "X-Van-Dashboard": "1",
                    "Origin": "https://example.invalid",
                },
            )
        finally:
            dashboard.dashboard_restart = original

        self.assertEqual(accepted.status_code, 202)
        self.assertEqual(accepted.headers["Cache-Control"], "no-store")
        self.assertEqual(
            accepted.json["message"], "Dashboard service restart scheduled"
        )
        self.assertEqual(accepted.json["dashboard_restart"], {"scheduled_at": 123})
        self.assertEqual(unconfirmed.status_code, 400)
        self.assertEqual(extra.status_code, 400)
        self.assertEqual(cross_origin.status_code, 403)
        self.assertEqual(calls, ["restart"])

    def test_telemetry_summary_route_is_read_only_and_uncached(self):
        class FakeTelemetrySummary:
            def snapshot(self):
                return {
                    "available": True,
                    "value": 12.6,
                    "unit": "V",
                    "source": "voltage_mon",
                    "observed_at": "2026-07-26T19:24:52-06:00",
                    "detail": "Last voltage_mon reading",
                }

        class FakeTelemetryService:
            def status(self):
                return {
                    "available": True,
                    "running": False,
                }

        originals = dashboard.telemetry_summary, dashboard.telemetry_service_status
        dashboard.telemetry_summary = FakeTelemetrySummary()
        dashboard.telemetry_service_status = FakeTelemetryService().status
        try:
            client = dashboard.app.test_client()
            status = client.get("/api/telemetry-summary")
            rejected = client.get("/api/telemetry-summary?command=anything")
        finally:
            dashboard.telemetry_summary, dashboard.telemetry_service_status = originals

        self.assertEqual(status.status_code, 200)
        self.assertEqual(status.headers["Cache-Control"], "no-store")
        self.assertEqual(status.json["battery"]["value"], 12.6)
        self.assertEqual(status.json["battery"]["source"], "voltage_mon")
        self.assertFalse(status.json["service"]["running"])
        self.assertIn(status.json["check"]["status"], ("idle", "complete", "error"))
        self.assertEqual(rejected.status_code, 400)

    def test_telemetry_service_route_is_fixed_and_csrf_protected(self):
        calls = []
        down = {
            "available": True,
            "running": False,
        }
        up = {
            **down,
            "running": True,
        }

        class FakeTelemetryService:
            def status(self):
                calls.append("status")
                return down

            def toggle(self):
                calls.append("toggle")
                return "start", up

        original = dashboard.toggle_telemetry_service
        dashboard.toggle_telemetry_service = FakeTelemetryService().toggle
        try:
            client = dashboard.app.test_client()
            accepted = client.post(
                "/api/telemetry-service",
                headers={"X-Van-Dashboard": "1"},
            )
            extra = client.post(
                "/api/telemetry-service",
                data={"action": "shell-command"},
                headers={"X-Van-Dashboard": "1"},
            )
            cross_origin = client.post(
                "/api/telemetry-service",
                headers={
                    "X-Van-Dashboard": "1",
                    "Origin": "https://example.invalid",
                },
            )
        finally:
            dashboard.toggle_telemetry_service = original

        self.assertEqual(accepted.status_code, 200)
        self.assertEqual(accepted.headers["Cache-Control"], "no-store")
        self.assertTrue(accepted.json["service"]["running"])
        self.assertEqual(accepted.json["message"], "Telemetry service started")
        self.assertEqual(extra.status_code, 400)
        self.assertEqual(cross_origin.status_code, 403)
        self.assertEqual(calls, ["toggle"])

    def test_voltage_check_route_is_narrow_nonblocking_and_csrf_protected(self):
        calls = []

        class FakeVoltageCheck:
            running = False

            def start(self):
                calls.append("start")
                if self.running:
                    return False
                self.running = True
                return True

            def snapshot(self):
                return {
                    "status": "running" if self.running else "idle",
                    "started_at": 123 if self.running else None,
                    "completed_at": None,
                    "error": None,
                }

        original = dashboard.voltage_check
        dashboard.voltage_check = FakeVoltageCheck()
        try:
            client = dashboard.app.test_client()
            accepted = client.post(
                "/api/telemetry-voltage-check",
                headers={"X-Van-Dashboard": "1"},
            )
            duplicate = client.post(
                "/api/telemetry-voltage-check",
                headers={"X-Van-Dashboard": "1"},
            )
            extra = client.post(
                "/api/telemetry-voltage-check",
                data={"command": "anything"},
                headers={"X-Van-Dashboard": "1"},
            )
            cross_origin = client.post(
                "/api/telemetry-voltage-check",
                headers={
                    "X-Van-Dashboard": "1",
                    "Origin": "https://example.invalid",
                },
            )
        finally:
            dashboard.voltage_check = original

        self.assertEqual(accepted.status_code, 202)
        self.assertEqual(accepted.headers["Cache-Control"], "no-store")
        self.assertEqual(accepted.json["check"]["status"], "running")
        self.assertEqual(duplicate.status_code, 409)
        self.assertEqual(extra.status_code, 400)
        self.assertEqual(cross_origin.status_code, 403)
        self.assertEqual(calls, ["start", "start"])

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
