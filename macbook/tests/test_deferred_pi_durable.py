"""`deferred.zsh` vanpi jobs: the scheduler reads the outcome of a pass from the
durable state, not from the run's exit status; a replacement run gets a usable
launch window; the records must agree on which attempt they describe; the
collection deadline does not depend on the job's copied `run`; the launch
window is checked at the launch; alerts are recorded only once sent. Fake
pi_agent.py and ssh as in deferred_pi_fixture.
"""
import datetime
import json
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


class RetryWindowTests(PiHarness):
    def test_a_retry_after_a_genuine_collection_deadline_launches_the_replacement(self):
        due = datetime.date.today() - datetime.timedelta(days=15)
        job = self.add_pi(due=due.isoformat())
        give_up = time.mktime(due.timetuple()) + 7 * 86400  # the default window: due + 7 days
        self.assertEqual(self.deferred("run-now", job.name, check=False).returncode, 4)
        run_id = self.listed(job)["remote_run"]
        # As if launched an hour before the original give-up time, and never collected since.
        self.set_meta(job, give_up="")
        self.age_launch(job, int(self.record(job, "remote-run.json")["launched"] - (give_up - 3600)))
        self.offline()
        self.deferred("run-due")
        record = self.listed(job)
        self.assertEqual((record["state"], record["relaunch_id"]), ("failed", run_id))
        self.assertEqual(self.record(job, "remote-failed.json")["kind"], "collection-deadline")
        self.offline(False)
        self.deferred("retry", job.name, "--relaunch", run_id)
        self.assertGreater(self.meta(job).get("give_up", ""), time.strftime(STAMP))
        self.deferred("run-due")
        self.assertEqual(len(self.calls("run")), 2)
        self.assertEqual(self.listed(job)["remote_state"], "running")

    def test_a_refused_relaunch_clears_nothing_and_keeps_the_window(self):
        self.control("launch-mode", "lost-nothing")
        job = self.add_pi()
        self.deferred("run-due")
        self.deferred("run-due")
        token = self.listed(job)["relaunch_id"]
        before = {path.name: path.read_bytes() for path in job.iterdir() if path.name != "last.log"}
        result = self.deferred("retry", job.name, "--relaunch", "d" + "f" * 16, check=False)
        self.assertEqual(result.returncode, 7)
        self.assertIn(f"retry {job.name} --relaunch {token}", result.stderr)
        self.assertEqual({path.name: path.read_bytes() for path in job.iterdir() if path.name != "last.log"}, before)


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


class DeadlineWithoutRunTests(PiHarness):
    def test_the_collection_deadline_is_recorded_when_the_run_file_is_gone(self):
        job = self.add_pi()
        self.deferred("run-due")
        run_id = self.listed(job)["remote_run"]
        (job / "run").unlink()
        self.deferred("run-due")
        self.deferred("run-due")
        self.assertEqual(self.alerts(), [f"Deferred job collector failed: {job.name}"])
        self.age_launch(job, PAST_DEADLINE)
        self.deferred("run-due")
        self.assertEqual(self.record(job, "remote-failed.json")["kind"], "collection-deadline")
        self.assertEqual(self.meta(job)["state"], "failed")
        self.assertEqual(self.alerts()[-1], f"Deferred job failed: {job.name}")
        self.assertIn(f"stop {run_id}", WarningTopic.received[-1][2])
        self.deferred("run-due")
        self.assertEqual(len(self.alerts()), 2)


class LaunchWindowTests(PiHarness):
    def test_the_launch_window_is_checked_at_the_launch_not_at_the_scan(self):
        # Sorted first, so its ready probe holds the checker across the cutoff for the second.
        first = self.add_pi("--ready", "/bin/sleep 6", name="a-ready")
        second = self.add_pi(name="b-late")
        cutoff = minute_end(3)
        for job in (first, second):
            self.set_meta(job, give_up=cutoff)
        self.deferred("run-due")
        self.assertEqual(self.calls("run"), [])
        for job in (first, second):
            with self.subTest(job=job.name):
                self.assertEqual(self.meta(job)["state"], "failed")
                self.assertEqual(self.listed(job)["remote_state"], "unlaunched")
        self.assertIn("past its give-up time", self.last_log(first))
        self.assertEqual(self.meta(second)["attempts"], "0")  # its pass never started
        self.assertEqual(self.alerts(), [f"Deferred job gave up: {first.name}", f"Deferred job gave up: {second.name}"])


class AlertRetryTests(PiHarness):
    def test_an_alert_that_could_not_be_sent_is_sent_at_the_next_check(self):
        job = self.add_pi()
        self.deferred("run-due")
        (job / "deferred_pi_remote.py").unlink()
        self.warnings_down()
        self.deferred("run-due")
        self.assertEqual(self.alerts(), [])
        self.assertIn("warning undelivered", self.scheduler_log())
        self.warnings_down(False)
        self.deferred("run-due")
        self.deferred("run-due")
        self.assertEqual(self.alerts(), [f"Deferred job collector failed: {job.name}"])
