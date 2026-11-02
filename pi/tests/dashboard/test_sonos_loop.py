"""Sonos seek and A-B loop behavior against a scripted speaker; no live devices."""

import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from flask import Flask

from pi.apps.van_dashboard import sonos_loop as loop_module
from pi.apps.van_dashboard.http import reject_cross_origin_mutations
from pi.apps.van_dashboard.routes import sonos as routes
from pi.apps.van_dashboard.sonos_loop import SonosLoop
from pi.apps.van_dashboard.van_dashboard_sonos import (
    SonosController,
    SonosNotSeekable,
    seek_clock,
    track_seconds,
)


class FakeClock:
    def __init__(self):
        self.value = 1_000.0

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


class ScriptedSpeaker:
    """Reports a settable position; seek moves it like a real speaker."""

    def __init__(self, position=83, duration=225):
        self.position = position
        self.duration = duration
        self.title = "Night Bell - Arizona"
        self.seeks = []
        self.fail_reads = False
        self.fail_seeks = False
        self.sought = threading.Event()

    def get_current_track_info(self):
        if self.fail_reads:
            raise TimeoutError("speaker did not answer")
        return {
            "uri": "x-sonos-vli:RINCON_1:2,spotify:session",
            "title": self.title,
            "artist": "Kerala Dust",
            "position": seek_clock(self.position),
            "duration": seek_clock(self.duration),
        }

    def seek(self, position):
        if self.fail_seeks:
            raise RuntimeError("UPnP Error 711")
        self.seeks.append(position)
        self.position = track_seconds(position)
        self.sought.set()


def make_loop(speaker, clock=None, start_worker=False):
    sonos = SimpleNamespace(coordinator=lambda: speaker)
    return SonosLoop(sonos, clock=clock or FakeClock(), start_worker=start_worker)


class ClockTextTests(unittest.TestCase):
    def test_round_trips_soco_clock_text(self):
        self.assertEqual(track_seconds("0:01:23"), 83)
        self.assertEqual(track_seconds("1:02:03"), 3723)
        self.assertEqual(seek_clock(3723), "1:02:03")
        self.assertEqual(seek_clock(83), "0:01:23")
        self.assertIsNone(track_seconds("NOT_IMPLEMENTED"))
        self.assertIsNone(track_seconds(""))


class SeekTests(unittest.TestCase):
    def controller(self, speaker):
        controller = SonosController(store=None)
        controller.coordinator = lambda: speaker
        return controller

    def test_seeks_relative_to_a_fresh_position_and_clamps_to_the_track(self):
        speaker = ScriptedSpeaker(position=83, duration=225)
        controller = self.controller(speaker)
        self.assertEqual(controller.seek(10), 93)
        self.assertEqual(controller.seek(-100), 0)
        speaker.position = 220
        self.assertEqual(controller.seek(10), 224)
        self.assertEqual(speaker.seeks, ["0:01:33", "0:00:00", "0:03:44"])

    def test_refuses_sources_without_a_clock(self):
        speaker = ScriptedSpeaker()
        speaker.get_current_track_info = lambda: {"position": "NOT_IMPLEMENTED", "duration": ""}
        with self.assertRaises(SonosNotSeekable):
            self.controller(speaker).seek(10)


class LoopTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.speaker = ScriptedSpeaker(position=83)
        self.loop = make_loop(self.speaker, self.clock)

    def play_to(self, position, seconds=1.0):
        self.clock.advance(seconds)
        self.speaker.position = position

    def test_rejects_short_loops_and_loops_past_the_track(self):
        with self.assertRaises(ValueError):
            self.loop.start(80, 81)
        with self.assertRaises(ValueError):
            self.loop.start(200, 230)
        self.assertFalse(self.loop.state()["active"])
        self.assertEqual(self.speaker.seeks, [])

    def test_starting_inside_the_loop_keeps_playing_where_it_is(self):
        self.assertEqual(self.loop.start(80, 101), "Looping 1:20–1:41")
        self.assertEqual(self.speaker.seeks, [])
        self.assertEqual(
            self.loop.state(),
            {"active": True, "start": 80, "end": 101, "title": "Night Bell - Arizona", "notice": None},
        )

    def test_starting_outside_the_loop_jumps_to_its_start(self):
        self.loop.start(30, 45)
        self.assertEqual(self.speaker.seeks, ["0:00:30"])

    def test_polls_fast_near_the_end_and_seeks_back_once_it_is_reached(self):
        self.loop.start(80, 90)
        self.play_to(84)
        self.assertEqual(self.loop.tick(), loop_module.SLOW_POLL_SECONDS)
        self.play_to(88)
        self.assertEqual(self.loop.tick(), loop_module.NEAR_POLL_SECONDS)
        self.play_to(89, 0.2)
        self.assertEqual(self.loop.tick(), loop_module.NEAR_POLL_SECONDS)
        self.play_to(90, 0.2)
        self.loop.tick()
        self.assertEqual(self.speaker.seeks, ["0:01:20"])
        self.assertTrue(self.loop.state()["active"])

    def test_a_stale_position_right_after_a_seek_does_not_seek_twice(self):
        self.loop.start(80, 90)
        self.play_to(90)
        self.loop.tick()
        self.speaker.position = 90  # the speaker still reports the old second
        self.clock.advance(0.2)
        self.loop.tick()
        self.assertEqual(self.speaker.seeks, ["0:01:20"])

    def test_a_paused_track_near_the_end_polls_slowly(self):
        self.loop.start(80, 90)
        self.play_to(89)
        self.assertEqual(self.loop.tick(), loop_module.NEAR_POLL_SECONDS)
        self.play_to(89, 3)
        self.assertEqual(self.loop.tick(), loop_module.SLOW_POLL_SECONDS)

    def test_a_loop_to_the_track_end_seeks_before_the_queue_advances(self):
        self.speaker.position = 215
        self.loop.start(210, 225)
        self.play_to(224)
        self.loop.tick()
        self.assertEqual(self.speaker.seeks, ["0:03:30"])

    def test_a_new_track_ends_the_loop_with_a_notice(self):
        self.loop.start(80, 90)
        self.speaker.title = "Next song"
        self.assertIsNone(self.loop.tick())
        state = self.loop.state()
        self.assertFalse(state["active"])
        self.assertEqual(state["notice"], "Loop stopped: the track changed")
        self.assertEqual(self.speaker.seeks, [])

    def test_repeated_read_failures_end_the_loop(self):
        self.loop.start(80, 90)
        self.speaker.fail_reads = True
        for _ in range(loop_module.MAX_POLL_FAILURES - 1):
            self.assertEqual(self.loop.tick(), loop_module.SLOW_POLL_SECONDS)
        self.assertIsNone(self.loop.tick())
        self.assertIn("stopped answering", self.loop.state()["notice"])

    def test_a_failed_seek_ends_the_loop_without_retrying(self):
        self.loop.start(80, 90)
        self.speaker.fail_seeks = True
        self.play_to(90)
        self.assertIsNone(self.loop.tick())
        self.assertIn("could not seek", self.loop.state()["notice"])

    def test_stopping_clears_the_loop_and_its_notice(self):
        self.loop.start(80, 90)
        self.assertEqual(self.loop.stop(), "Loop stopped")
        self.assertIsNone(self.loop.tick())
        self.assertEqual(self.loop.state()["notice"], None)
        self.play_to(95)
        self.assertEqual(self.speaker.seeks, [])


class LoopWorkerTests(unittest.TestCase):
    def test_worker_seeks_back_and_exits_once_stopped(self):
        speaker = ScriptedSpeaker(position=83)
        loop = SonosLoop(SimpleNamespace(coordinator=lambda: speaker))
        loop.start(80, 90)
        speaker.position = 90
        loop.wake.set()
        self.assertTrue(speaker.sought.wait(5))
        worker = loop.worker
        loop.stop()
        worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertIsNone(loop.worker)
        self.assertEqual(speaker.seeks[0], "0:01:20")


class RouteTests(unittest.TestCase):
    def setUp(self):
        self.speaker = ScriptedSpeaker(position=83)
        controller = SonosController(store=None)
        controller.coordinator = lambda: self.speaker
        controller.snapshot = lambda: {"ok": True}
        self.loop = make_loop(self.speaker)
        app = Flask(__name__)
        app.before_request(reject_cross_origin_mutations)
        app.register_blueprint(routes.bp)
        self.client = app.test_client()
        for name, value in (("sonos", controller), ("sonos_loop", self.loop)):
            patcher = patch.object(routes, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_seek_validates_the_offset_before_touching_the_speaker(self):
        for offset in ("", "ten", "0", "601"):
            response = self.client.post("/api/speakers/seek", data={"offset": offset})
            self.assertEqual(response.status_code, 400, offset)
        self.assertEqual(self.speaker.seeks, [])
        response = self.client.post("/api/speakers/seek", data={"offset": "-10"})
        self.assertEqual(response.get_json(), {"ok": True, "message": "Sonos at 1:13", "position": 73})

    def test_seek_reports_an_unseekable_source_as_a_conflict(self):
        self.speaker.get_current_track_info = lambda: {"position": "", "duration": ""}
        response = self.client.post("/api/speakers/seek", data={"offset": "10"})
        self.assertEqual(response.status_code, 409)

    def test_loop_routes_start_report_and_stop(self):
        response = self.client.post("/api/speakers/loop", data={"start": "80", "end": "101"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["loop"]["end"], 101)
        self.assertTrue(self.client.get("/api/speakers").get_json()["loop"]["active"])
        bad = self.client.post("/api/speakers/loop", data={"start": "80", "end": "81"})
        self.assertEqual(bad.status_code, 400)
        stopped = self.client.post("/api/speakers/loop/stop")
        self.assertFalse(stopped.get_json()["loop"]["active"])


if __name__ == "__main__":
    unittest.main()
