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


class DurableSuccessTests(PiHarness):
    def test_a_run_that_exits_zero_while_its_pi_run_is_running_keeps_the_job(self):
        job = self.add_pi()
        self.deferred("run-due")
        run_id = self.listed(job)["remote_run"]
        (job / "run").write_text(NO_OP_RUN)
        self.deferred("run-due")
        self.deferred("run-due")
        self.assertTrue(job.exists())
        record = self.listed(job)
        self.assertEqual((record["remote_state"], record["remote_run"]), ("running", run_id))
        self.assertEqual(self.alerts(), [f"Deferred job collector failed: {job.name}"])
        self.assertIn(f"stop {run_id}", WarningTopic.received[0][2])
        self.assertNotIn(f"done {job.name}", self.scheduler_log())
        result = self.deferred("run-now", job.name, check=False)
        self.assertEqual(result.returncode, 3)
        self.assertTrue(job.exists())

    def test_a_launch_pass_that_exits_zero_with_nothing_on_record_fails_the_job(self):
        job = self.add_pi()
        (job / "run").write_text(NO_OP_RUN)
        self.deferred("run-due")
        self.assertTrue(job.exists())
        self.assertEqual((self.meta(job)["state"], self.listed(job)["remote_state"]), ("failed", "unlaunched"))
        self.deferred("run-due")
        self.assertEqual(self.alerts(), [f"Deferred job collector failed: {job.name}"])
        self.assertEqual(self.calls("run"), [])


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
