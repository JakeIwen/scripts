"""`deferred.zsh` vanpi jobs: which attempt a person's command acts on. run-now
that starts a new attempt saves the job as pending before the attempt runs, so
its end is accounted even when a check is interrupted; refusals print one
command that works. Fake pi_agent.py and ssh as in
deferred_pi_fixture.
"""
import datetime
import json
import re

import deferred_pi_state
from macbook.tests.deferred_pi_fixture import PiHarness

PAST_DEADLINE = 120 * 60 + deferred_pi_state.COLLECT_GRACE + 60  # default --run-timeout + the grace
TOMORROW = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()


class RunNowAttemptTests(PiHarness):
    def test_a_run_now_replacement_whose_failure_was_saved_but_not_accounted_is_still_warned(self):
        self.control("next-state", "done")
        self.control("next-exit", "1")
        job = self.add_pi()
        self.deferred("run-due")
        title = f"Deferred job failed: {job.name}"
        self.assertEqual(self.alerts(), [title])
        self.control("next-state", "running")
        self.control("next-exit", "0")
        self.assertEqual(self.deferred("run-now", job.name, check=False).returncode, 4)
        self.assertEqual(self.meta(job)["state"], "pending")
        record = self.listed(job)
        # A collect pass saved the new run's failure and was stopped before the scheduler accounted it.
        (job / "remote-failed.json").write_text(json.dumps({
            "v": 1, "run_id": record["remote_run"], "token": record["remote_token"], "kind": "remote",
            "reason": "exited 1"}))
        self.deferred("run-due")
        self.assertEqual(self.alerts(), [title] * 2)
        self.assertEqual(self.meta(job)["state"], "failed")
        self.assertEqual(len(self.calls("run")), 2)

    def test_run_now_of_a_job_not_yet_due_while_vanpi_is_offline_says_when_it_becomes_eligible(self):
        job = self.add_pi(due=TOMORROW)
        self.offline()
        result = self.deferred("run-now", job.name, check=False)
        self.assertEqual((result.returncode, self.meta(job)["state"]), (4, "pending"))
        self.assertIn(f"the checker retries from its due time {TOMORROW} 00:00", result.stdout)
        self.assertNotIn("the next check retries", result.stdout)

    def test_a_relaunch_refusal_prints_one_command_that_works(self):
        job = self.add_pi()
        self.deferred("run-due")
        run_id = self.listed(job)["remote_run"]
        self.age_launch(job, PAST_DEADLINE)
        self.deferred("run-due")
        refused = self.deferred("retry", job.name, check=False)
        self.assertEqual(refused.returncode, 7)
        command = re.search(r"deferred\.zsh (retry .*)$", refused.stderr, re.M).group(1).split()
        self.assertEqual(command, ["retry", job.name, "--relaunch", run_id])
        self.deferred(*command)
        self.assertEqual(self.meta(job)["state"], "pending")

