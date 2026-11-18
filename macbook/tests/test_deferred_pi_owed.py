"""`deferred.zsh` vanpi jobs: what a job still owes after a pass. A launch a
person authorized with retry or run-now that was not ready yet waits as pending
for the checker, which starts it once; a scheduled warning ntfy could not take
is sent at a later check, a bounded number of times. Fake pi_agent.py and ssh
as in deferred_pi_fixture.
"""
from macbook.tests.deferred_pi_fixture import PiHarness

OWED_SEND_LIMIT = 48  # deferred_pi_schedule.zsh pi_alert_send_limit: about two days of hourly checks


class AuthorizedLaunchWaitsTests(PiHarness):
    def failed_job(self, *args):
        """A job whose one automatic run ended badly (confirmed over)."""
        self.control("next-state", "done")
        self.control("next-exit", "1")
        job = self.add_pi(*args)
        self.deferred("run-due")
        self.assertEqual(self.meta(job)["state"], "failed")
        self.control("next-state", "running")
        self.control("next-exit", "0")
        return job

    def assert_waits_then_launches_once(self, job, unblock):
        self.assertEqual(self.meta(job)["state"], "pending")
        launched = len(self.calls("run"))
        unblock()
        self.deferred("run-due")
        self.assertEqual(len(self.calls("run")), launched + 1)
        record = self.listed(job)
        self.assertEqual(record["remote_state"], "running")
        self.set_run(record["remote_run"], state="done", exit="1")
        self.deferred("run-due")
        self.deferred("run-due")
        self.assertEqual(len(self.calls("run")), launched + 1)
        self.assertEqual(self.meta(job)["state"], "failed")

    def test_run_now_while_vanpi_is_unreachable_waits_as_pending_and_launches_once(self):
        job = self.failed_job()
        self.offline()
        result = self.deferred("run-now", job.name, check=False)
        self.assertEqual((result.returncode, self.meta(job)["state"]), (4, "pending"))
        self.assertIn("the next check retries", result.stdout)
        self.deferred("run-due")  # still unreachable: nothing launches, the job keeps waiting
        self.assertEqual(len(self.calls("run")), 1)
        self.assert_waits_then_launches_once(job, lambda: self.offline(False))

    def test_run_now_relaunch_with_a_false_ready_condition_waits_and_launches_once(self):
        flag = self.tmp / "ready.flag"
        self.control("launch-mode", "lost-nothing")
        job = self.add_pi("--ready", f"test -e {flag}")
        flag.touch()
        self.deferred("run-due")
        self.deferred("run-due")
        token = self.listed(job)["relaunch_id"]
        self.assertEqual((self.meta(job)["state"], self.listed(job)["remote_state"]), ("failed", "launch-unknown"))
        self.clear("launch-mode")
        flag.unlink()
        result = self.deferred("run-now", job.name, "--relaunch", token, check=False)
        self.assertEqual((result.returncode, self.meta(job)["state"]), (4, "pending"))
        self.assertIn("the next check retries", result.stdout)
        self.assert_waits_then_launches_once(job, flag.touch)

    def test_a_rearmed_attempt_that_fails_terminally_stays_failed(self):
        job = self.failed_job()
        self.control("launch-mode", "refuse")
        self.assertEqual(self.deferred("run-now", job.name, check=False).returncode, 3)
        self.assertEqual(self.meta(job)["state"], "failed")
        self.clear("launch-mode")
        self.deferred("run-due")
        self.assertEqual(len(self.calls("run")), 2)  # the first run and the refused one; nothing more


class OwedWarningTests(PiHarness):
    def assert_sent_later_exactly_once(self, title):
        self.assertEqual(self.alerts(), [])
        self.assertIn(f"warning undelivered: {title}", self.scheduler_log())
        self.warnings_down(False)
        self.deferred("run-due")
        self.deferred("run-due")
        self.assertEqual(self.alerts(), [title])

    def test_a_failed_run_warning_the_notifier_missed_is_sent_at_a_later_check(self):
        self.control("next-state", "done")
        self.control("next-exit", "1")
        job = self.add_pi()
        self.warnings_down()
        self.deferred("run-due")
        self.assertEqual(self.meta(job)["state"], "failed")
        self.assert_sent_later_exactly_once(f"Deferred job failed: {job.name}")

    def test_a_held_launch_warning_the_notifier_missed_is_sent_at_a_later_check(self):
        self.control("launch-mode", "lost-nothing")
        job = self.add_pi()
        self.deferred("run-due")
        self.warnings_down()
        self.deferred("run-due")
        self.assertEqual((self.meta(job)["state"], self.listed(job)["remote_state"]), ("failed", "launch-unknown"))
        self.assert_sent_later_exactly_once(f"Deferred job failed: {job.name}")

    def test_a_give_up_warning_the_notifier_missed_is_sent_at_a_later_check(self):
        job = self.add_pi()
        self.set_meta(job, give_up=f"{self.meta(job)['due'][:10]}T00:01")
        self.warnings_down()
        self.deferred("run-due")
        self.assertEqual((self.meta(job)["state"], self.calls("run")), ("failed", []))
        self.assert_sent_later_exactly_once(f"Deferred job gave up: {job.name}")

    def test_an_owed_warning_is_dropped_after_the_bound_with_a_log_line(self):
        self.control("next-state", "done")
        self.control("next-exit", "1")
        job = self.add_pi()
        self.warnings_down()
        self.deferred("run-due")
        title = f"Deferred job failed: {job.name}"
        for _ in range(OWED_SEND_LIMIT - 1):
            self.deferred("run-due")
        self.assertEqual(self.scheduler_log().count(f"warning undelivered: {title}"), OWED_SEND_LIMIT)
        self.assertIn(f"dropped warning for {job.name} after {OWED_SEND_LIMIT} failed sends: {title}",
                      self.scheduler_log())
        self.warnings_down(False)
        self.deferred("run-due")
        self.assertEqual(self.alerts(), [])
        self.assertEqual(self.scheduler_log().count(f"warning undelivered: {title}"), OWED_SEND_LIMIT)

    def test_run_now_reports_its_failure_at_the_terminal_and_owes_no_warning(self):
        self.control("next-state", "done")
        self.control("next-exit", "1")
        job = self.add_pi()
        self.assertEqual(self.deferred("run-now", job.name, check=False).returncode, 3)
        self.deferred("run-due")
        self.deferred("run-due")
        self.assertEqual(self.alerts(), [])
