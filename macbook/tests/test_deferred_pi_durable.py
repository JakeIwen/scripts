"""`deferred.zsh` vanpi jobs: the scheduler reads the outcome of a pass from the
durable state, not from the run's exit status; a replacement run gets a usable
launch window; the records must agree on which attempt they describe; the
collection deadline does not depend on the job's copied `run`; the launch
window is checked at the launch; alerts are recorded only once sent. Fake
pi_agent.py and ssh as in deferred_pi_fixture.
"""
import datetime
import json
from pathlib import Path
import time

import deferred_pi_state
from macbook.tests.deferred_fixture import WarningTopic
from macbook.tests.deferred_pi_fixture import PiHarness

PAST_DEADLINE = 120 * 60 + deferred_pi_state.COLLECT_GRACE + 60  # default --run-timeout + the grace
STAMP = "%Y-%m-%dT%H:%M"
NO_OP_RUN = "#!/bin/zsh\nexit 0\n"


def minute_end(seconds_left):
    """Waits until SECONDS_LEFT seconds remain in the current minute; returns the next minute's stamp."""
    remaining = 60 - time.time() % 60
    if remaining < seconds_left + 0.5:
        time.sleep(remaining + 0.1)
        remaining = 60 - time.time() % 60
    time.sleep(remaining - seconds_left)
    return time.strftime(STAMP, time.localtime(time.time() + seconds_left + 1))


class IdentityTests(PiHarness):
    def test_a_token_only_remote_failure_beside_a_launch_marker_is_not_confirmed_over(self):
        self.control("launch-mode", "lost-nothing")
        job = self.add_pi()
        self.deferred("run-due")
        token = self.record(job, "launching.json")["token"]
        (job / "remote-failed.json").write_text(json.dumps({"v": 1, "token": token, "kind": "remote", "reason": "x"}))
        self.assertFalse(self.listed(job)["valid"])
        self.assertEqual(self.deferred("cancel", job.name, check=False).returncode, 6)
        self.assertTrue(job.exists())

    def test_rearm_refuses_any_id_when_the_records_disagree_about_identity(self):
        job = self.add_pi()
        self.deferred("run-due")
        run = self.record(job, "remote-run.json")
        other_token = "d" + "1" * 16
        other = "1111-111111-1111-" + other_token
        (job / "remote-failed.json").write_text(json.dumps(
            {"v": 1, "run_id": other, "token": other_token, "kind": "remote", "reason": "x"}))
        self.set_meta(job, state="failed")
        before = {path.name: path.read_bytes() for path in job.iterdir()}
        for given in (other, other_token, run["run_id"], run["token"]):
            for verb in ("retry", "run-now"):
                with self.subTest(given=given, verb=verb):
                    result = self.deferred(verb, job.name, "--relaunch", given, check=False)
                    self.assertEqual(result.returncode, 7)
                    for word in (run["run_id"], other, f"cancel --abandon {job.name}"):
                        self.assertIn(word, result.stderr)
        self.assertEqual({path.name: path.read_bytes() for path in job.iterdir()}, before)
        self.assertEqual(len(self.calls("run")), 1)
        self.deferred("cancel", "--abandon", job.name)
        self.assertFalse(job.exists())
