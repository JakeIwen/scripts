import json
import os
import tempfile
import threading
import unittest
from email.message import Message
from types import SimpleNamespace

from automation import van_dashboard as dashboard


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
        self.tempdir.cleanup()

    def test_parked_running_parked_transition(self):
        status = self.manager.set_active(True)
        self.assertTrue(status["active"])
        self.assertEqual(self.entity_state, "on")
        self.assertTrue(os.path.isfile(self.manager.active_marker))

        self.manager.tick()
        self.assertEqual(len(self.wakes), 1)
        self.assertEqual(self.entity_state, "on")
        self.assertTrue(any(call[0] == dashboard.NTFY_SEND for call in self.calls))
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
        reloaded = dashboard.StateStore(self.store.path)
        self.assertTrue(reloaded.get("cop_alert"))


class DashboardRouteTests(unittest.TestCase):
    def test_index_and_manifest(self):
        client = dashboard.app.test_client()
        page = client.get("/")
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"COP ALERT", page.data)
        self.assertIn(b"Starlink", page.data)
        self.assertIn(b"MWAN3", page.data)
        self.assertIn(b"ext_led", page.data)
        self.assertNotIn(b">Internet Connectivity<", page.data)
        self.assertNotIn(b">Reachable<", page.data)
        self.assertNotIn(b"mwan-chip paused", page.data)
        self.assertNotIn(" · paused".encode(), page.data)
        self.assertIn(b"Run speed test", page.data)
        self.assertIn(b'id="sonos-track"', page.data)
        self.assertIn(b'id="sonos-progress"', page.data)
        self.assertIn(b'data-transport="play_pause"', page.data)
        self.assertIn(b"Group volume", page.data)
        self.assertIn(b"data-group-mute", page.data)
        self.assertIn(b"data-speaker-mute", page.data)
        self.assertIn(b"bookUrl.port='8787'", page.data)
        manifest = client.get("/manifest.webmanifest")
        self.assertEqual(manifest.status_code, 200)
        self.assertEqual(manifest.json["name"], "Van Dashboard")

    def test_connectivity_and_speedtest_status_routes(self):
        client = dashboard.app.test_client()
        connectivity = client.get("/api/connectivity")
        self.assertEqual(connectivity.status_code, 200)
        self.assertEqual(connectivity.headers["Cache-Control"], "no-store")
        self.assertIn("router", connectivity.json["connectivity"])
        speedtest = client.get("/api/speedtest")
        self.assertEqual(speedtest.status_code, 200)
        self.assertIn(speedtest.json["speedtest"]["status"], ("idle", "complete", "error"))

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
