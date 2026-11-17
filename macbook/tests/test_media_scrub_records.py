"""Wire-record validation, status derivation and the token's refusal paths."""
import base64
import json
import unittest

from media_scrub import config
from media_scrub.records import (RecordError, backoff_seconds, build_status, parse_token,
                                 status_text, validate_sample, validate_seek_result)

from media_scrub_fixture import SAMPLE, ScrubCase, token

NOW = 1000.0


def snapshot(age=0.5, **sample_overrides):
    sample = dict(SAMPLE, **sample_overrides)
    return {"sample": sample, "received_at": NOW - age, "error": None, "seek_ack": None}


def wire(**overrides):
    return dict(SAMPLE, type="sample", v=1, t=1.0, **overrides)


class StatusDerivation(unittest.TestCase):
    def test_fresh_playing_sample_is_ok_and_seekable(self):
        status = build_status(snapshot(), NOW)
        self.assertTrue(status["ok"])
        self.assertTrue(status["seekable"])
        self.assertEqual(status["age"], 0.5)

    def test_staleness_thresholds(self):
        self.assertFalse(build_status(snapshot(age=2.5), NOW)["seekable"])
        self.assertTrue(build_status(snapshot(age=2.5), NOW)["ok"])
        stale = build_status(snapshot(age=3.5), NOW)
        self.assertFalse(stale["ok"])
        self.assertFalse(stale["seekable"])
        self.assertEqual(stale["error"], "no fresh sample")

    def test_negative_age_from_a_clock_step_fails_closed(self):
        status = build_status(snapshot(age=-5.0), NOW)
        self.assertFalse(status["ok"])
        self.assertFalse(status["seekable"])
        self.assertEqual(status["error"], "clock stepped back")
        self.assertEqual(status_text(status), "Status unavailable")

    def test_seekable_needs_every_condition(self):
        for override in ({"can_seek": False}, {"source": "none"}, {"media_id": None},
                         {"duration": 0.0}, {"source": "unknown"}):
            self.assertFalse(build_status(snapshot(**override), NOW)["seekable"], override)

    def test_sample_or_snapshot_error_makes_status_not_ok(self):
        self.assertFalse(build_status(snapshot(error="Van TV status: timeout"), NOW)["ok"])
        broken = snapshot()
        broken["error"] = "ssh session ended"
        self.assertEqual(build_status(broken, NOW)["error"], "ssh session ended")

    def test_no_snapshot(self):
        status = build_status(None, NOW)
        self.assertFalse(status["ok"])
        self.assertIsNone(status["age"])
        self.assertIsNone(status["source"])

    def test_text_forms(self):
        text = status_text(build_status(snapshot(position=65.0, duration=5400.0), NOW))
        self.assertEqual(text, "Movie \n0:01:05 / 1:30:00")
        empty = status_text(build_status(snapshot(source="none", state="STOPPED", title=None), NOW))
        self.assertEqual(empty, "")
        self.assertEqual(status_text(build_status(None, NOW)), "Status unavailable")
        newline = status_text(build_status(snapshot(title="a\nb"), NOW))
        self.assertTrue(newline.startswith("a b \n"))


class Records(unittest.TestCase):
    def test_sample_validation(self):
        self.assertEqual(validate_sample(wire())["media_id"], "vlc:item:7")
        for bad in ({"source": "tv"}, {"position": "1"}, {"position": float("nan")},
                    {"can_seek": 1}, {"duration": -1}, {"title": 5}, {"rate": True}):
            with self.assertRaises(RecordError, msg=bad):
                validate_sample(wire(**bad))
        with self.assertRaises(RecordError):
            validate_sample(dict(wire(), v=2))
        with self.assertRaises(RecordError):
            validate_sample([])

    def test_seek_result_validation(self):
        self.assertEqual(validate_seek_result({"type": "seek_result", "req": 3, "ok": True,
                                               "message": "x"})["req"], 3)
        for bad in ({"req": "3"}, {"req": True}, {"ok": "yes"}, {"message": None}):
            line = {"type": "seek_result", "req": 3, "ok": True, "message": "x"}
            line.update(bad)
            with self.assertRaises(RecordError, msg=bad):
                validate_seek_result(line)

    def test_token_validation(self):
        self.assertEqual(parse_token(token(position=3))["position"], 3.0)
        raw = lambda data: base64.b64encode(data).decode()
        cases = ["", "!!!", raw(b"not json"), raw(b"[]"), raw(b'{"page":"p"}'),
                 raw(json.dumps({"page": "p", "seq": 0, "position": float("nan"),
                                 "source": "vlc", "media_id": "m"}).encode()),
                 token(page="has space"), token(page="x" * 65), token(seq=-1), token(seq=1.5),
                 token(seq=True), token(position=-1), token(position="5"), token(source="none"),
                 token(media_id=""), token(media_id="m" * 513)]
        for bad in cases:
            with self.assertRaises(RecordError, msg=bad[:40]):
                parse_token(bad)
        extra = json.loads(base64.b64decode(token()))
        extra["extra"] = 1
        with self.assertRaises(RecordError):
            parse_token(base64.b64encode(json.dumps(extra).encode()).decode())

    def test_backoff_doubles_to_the_cap(self):
        self.assertEqual([backoff_seconds(n) for n in (1, 2, 3, 4, 5, 6, 7)],
                         [2, 4, 8, 16, 32, 60, 60])
        self.assertEqual(config.BACKOFF_CAP_SECONDS, 60)


class SeekCliRefusals(ScrubCase):
    def test_bad_tokens_exit_2_and_start_nothing(self):
        for bad in ("!!!", "aGVsbG8=", token(position=-1), token(source="x"), token(seq=-3)):
            result = self.run_cli("seek", "--b64", bad)
            self.assertEqual(result.returncode, 2, bad)
            self.assertFalse(json.loads(result.stdout)["ok"])
        self.assertEqual(self.run_cli("seek").returncode, 2)
        self.assertEqual(self.run_cli("bogus").returncode, 2)
        self.assertIsNone(self.watcher_pid())
        self.assertEqual(self.serve_pids(), [])


if __name__ == "__main__":
    unittest.main()
