"""Seek brokering: passthrough, ordering fences, identity and freshness refusals, ack timing."""
import os
import time
import unittest

from media_scrub_fixture import SAMPLE, ScrubCase, token


class SeekPassthrough(ScrubCase):
    def test_seek_reaches_the_pi_and_is_acked_with_the_next_sample(self):
        self.start_watcher()
        code, result = self.seek(position=123.0, seq=4, page="pg")
        self.assertEqual((code, result["ok"], result["superseded"]), (0, True, False), result)
        self.assertEqual(result["message"], "sought")
        sent = self.seeks()
        self.assertEqual(len(sent), 1)
        self.assertEqual((sent[0]["position"], sent[0]["source"], sent[0]["media_id"]),
                         (123.0, "vlc", "vlc:item:7"))
        self.assertIsInstance(sent[0]["req"], int)
        status = self.wait_for(lambda: self.acked_status(), 5, "ack")
        self.assertEqual(status["seek_ack"], {"page": "pg", "seq": 4, "ok": True, "message": "sought"})
        self.assertEqual(status["position"], 123.0)

    def acked_status(self):
        status = self.status(wait=0)
        return status if status["seek_ack"] else None

    def test_pi_refusal_comes_back_as_a_failed_ack(self):
        self.configure(result_ok=False)
        self.start_watcher()
        code, result = self.seek()
        self.assertEqual((code, result["ok"], result["reason"]), (1, False, "failed"))
        ack = self.wait_for(self.acked_status, 5, "ack")["seek_ack"]
        self.assertEqual((ack["ok"], ack["message"]), (False, "refused"))

    def test_no_watcher_means_exit_1_with_a_reason(self):
        code, result = self.seek()
        self.assertEqual((code, result["ok"], result["reason"]), (1, False, "unavailable"))


class AckTiming(ScrubCase):
    def test_ack_is_never_published_before_a_post_result_sample(self):
        self.configure(hold_after_seek=1.5, stray_req=999)
        self.start_watcher()
        code, result = self.seek(position=500.0, seq=1)
        self.assertEqual(code, 0, result)
        held = time.monotonic()
        while time.monotonic() - held < 1.0:           # the Pi sends no sample for 1.5 s
            self.assertIsNone(self.status(wait=0)["seek_ack"])
            time.sleep(0.1)
        status = self.wait_for(lambda: self.status(wait=0)["seek_ack"] and self.status(wait=0), 5, "ack")
        self.assertEqual(status["seek_ack"]["seq"], 1)      # the stray result never acked anything
        self.assertEqual(status["position"], 500.0)         # and the ack came with the new sample

    def test_older_ack_is_replaced_by_the_newer_one(self):
        self.start_watcher()
        self.seek(position=10.0, seq=1)
        self.seek(position=20.0, seq=2)
        status = self.wait_for(lambda: self.status(wait=0)["position"] == 20.0 and self.status(wait=0), 5, "sample")
        self.assertEqual(status["seek_ack"]["seq"], 2)


class Ordering(ScrubCase):
    def test_reversed_cli_arrival_refuses_the_lower_seq(self):
        self.start_watcher()
        self.assertEqual(self.seek(position=50.0, seq=5)[0], 0)
        code, result = self.seek(position=40.0, seq=3)
        self.assertEqual((code, result["superseded"], result["reason"]), (1, True, "superseded"))
        code, result = self.seek(position=45.0, seq=5)       # equal is stale too
        self.assertEqual((code, result["superseded"]), (1, True))
        self.assertEqual([s["position"] for s in self.seeks()], [50.0])
        self.assertEqual(self.seek(position=60.0, seq=1, page="other")[0], 0)   # fenced per page
        self.assertEqual(self.seek(position=70.0, seq=6)[0], 0)

    def test_latest_pending_wins_and_the_replaced_one_is_superseded(self):
        self.configure(seek_delay=1.5)
        self.start_watcher()
        first = self.popen_cli("seek", "--b64", token(seq=1, position=10.0))
        self.wait_for(lambda: self.seeks(), 5, "first seek on the Pi")
        second = self.popen_cli("seek", "--b64", token(seq=2, position=20.0))
        self.wait_for(lambda: (self.read_state("snapshot.json") or {}).get("broker", {}).get("pending") == ["p1", 2],
                      5, "second seek pending")
        third = self.popen_cli("seek", "--b64", token(seq=3, position=30.0))
        outcomes = {}
        for name, process in (("first", first), ("second", second), ("third", third)):
            out, _ = process.communicate(timeout=15)
            outcomes[name] = (process.returncode, out)
        self.assertEqual(outcomes["first"][0], 0, outcomes)
        self.assertEqual(outcomes["second"][0], 1, outcomes)
        self.assertIn('"superseded": true', outcomes["second"][1])
        self.assertEqual(outcomes["third"][0], 0, outcomes)
        self.assertEqual([s["position"] for s in self.seeks()], [10.0, 30.0])


class Refusals(ScrubCase):
    def test_identity_mismatch_is_refused_locally(self):
        self.start_watcher()
        code, result = self.seek(media_id="vlc:item:8")
        self.assertEqual((code, result["reason"]), (1, "identity"))
        code, result = self.seek(source="vantv", seq=2)
        self.assertEqual((code, result["reason"]), (1, "identity"))
        self.assertEqual(self.seeks(), [])

    def test_stale_sample_is_refused_and_not_seekable(self):
        self.configure(stop_samples_after=2)
        self.start_watcher()
        self.wait_for(lambda: self.status(wait=0)["age"] > 2.1, 6, "the sample to go stale")
        self.assertFalse(self.status(wait=0)["seekable"])
        code, result = self.seek()
        self.assertEqual((code, result["reason"]), (1, "stale"))
        self.assertEqual(self.seeks(), [])

    def test_unseekable_player_is_refused(self):
        self.configure(sample=dict(SAMPLE, can_seek=False))
        self.start_watcher()
        code, result = self.seek()
        self.assertEqual((code, result["reason"]), (1, "not_seekable"))

    def test_socket_that_is_not_ours_is_not_trusted(self):
        os.symlink("/etc/hosts", os.path.join(self.state, "ctl.sock"))
        code, result = self.seek()
        self.assertEqual((code, result["ok"]), (1, False))


if __name__ == "__main__":
    unittest.main()
