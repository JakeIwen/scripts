"""`deferred.zsh` vanpi jobs: a launched Pi run is always collected, the pass
budget, accounting after a crash, reconciliation coverage, and what listing and
cancel may trust. Fake pi_agent.py and ssh as in deferred_pi_fixture.
"""
import datetime
import json
from pathlib import Path

import deferred_pi_run
from macbook.tests.deferred_fixture import WarningTopic
from macbook.tests.deferred_pi_fixture import PiHarness, YESTERDAY

IN_THREE_DAYS = (datetime.date.today() + datetime.timedelta(days=3)).isoformat()
WEEK = 7 * 86400


class CollectionTests(PiHarness):
    def test_a_run_started_by_run_now_on_a_failed_job_is_still_collected(self):
        self.control("next-state", "done")
        self.control("next-exit", "1")
        job = self.add_pi()
        self.deferred("run-due")
        self.assertEqual(self.meta(job)["state"], "failed")
        self.control("next-state", "running")
        self.control("next-exit", "0")
        result = self.deferred("run-now", job.name, check=False)
        self.assertEqual(result.returncode, 4)
        self.assertIn("a later check collects it", result.stdout)
        record = self.listed(job)
        self.assertEqual((record["state"], record["remote_state"], record["last_not_ready"]), ("failed", "running", None))
        self.set_run(record["remote_run"], state="done")
        self.deferred("run-due")
        self.assertFalse(job.exists())
        self.assertEqual(WarningTopic.paths[-1], "/agent")

    def test_run_now_before_the_due_time_does_not_postpone_collection(self):
        job = self.add_pi(due=IN_THREE_DAYS)
        self.assertEqual(self.deferred("run-now", job.name, check=False).returncode, 4)
        [run_id] = self.runs()
        self.set_run(run_id, state="done")
        self.deferred("run-due")
        self.assertFalse(job.exists())
        self.assertEqual(len(self.calls("run")), 1)

    def test_rescheduling_a_running_job_into_the_future_still_collects_and_times_it_out(self):
        job = self.add_pi("--run-timeout", "1")
        self.deferred("run-due")
        job = self.tmp / "root" / "jobs" / self.deferred("reschedule", job.name, IN_THREE_DAYS).stdout.strip()
        self.age_launch(job, 120)
        self.deferred("run-due")
        self.assertEqual(len(self.calls("stop")), 1)
        record = self.listed(job)
        self.assertEqual((record["remote_state"], record["state"]), ("failed", "failed"))
        self.assertIn("timed out", self.last_log(job))

    def test_a_run_launched_after_give_up_is_collected_not_failed(self):
        job = self.add_pi("--give-up", YESTERDAY)
        self.deferred("run-due")
        record = self.listed(job)
        self.assertEqual((record["state"], record["remote_state"]), ("pending", "running"))
        self.assertEqual(WarningTopic.received, [])
        self.set_run(record["remote_run"], state="done")
        self.deferred("run-due")
        self.assertFalse(job.exists())

    def test_a_lost_launch_response_always_prints_the_recovery_commands(self):
        self.control("launch-mode", "lost")
        job = self.add_pi("--give-up", YESTERDAY)
        self.deferred("run-due")
        token = self.record(job, "launching.json")["token"]
        self.assertIn(f"grep -e -{token}", self.last_log(job))
        self.assertEqual(self.meta(job)["state"], "pending")

    def test_past_the_collection_deadline_the_job_fails_with_recovery_commands(self):
        job = self.add_pi("--run-timeout", "1")
        self.deferred("run-due")
        [run_id] = self.runs()
        self.age_launch(job, 60 + WEEK + 60)
        self.offline()
        self.deferred("run-due")
        record = self.listed(job)
        self.assertEqual((record["state"], record["remote_state"]), ("failed", "failed"))
        self.assertIn("collection deadline", self.last_log(job))
        self.assertIn(f"stop {run_id}", self.last_log(job))


class PassBudgetTests(PiHarness):
    def test_add_agent_refuses_a_vanpi_timeout_below_the_floor(self):
        floor = deferred_pi_run.MIN_PASS_TIMEOUT
        result = self.deferred("add-agent", "--host", "vanpi", "--timeout", str(floor - 1), YESTERDAY, "short",
                               str(self.prompt), check=False)
        self.assertEqual(result.returncode, 1)
        self.assertIn(f"at least {floor} seconds", result.stderr)
        self.deferred("add-agent", "--host", "vanpi", "--timeout", str(floor), YESTERDAY, "enough", str(self.prompt))

    def test_a_slow_ready_check_ends_the_pass_before_launching(self):
        job = self.add_pi("--ready", "sleep 30")
        self.set_meta(job, timeout=deferred_pi_run.MIN_PASS_TIMEOUT + 6)
        self.deferred("run-due")
        meta = self.meta(job)
        self.assertEqual((meta["state"], meta["attempts"]), ("pending", "0"))
        self.assertEqual(self.calls("run"), [])
        self.assertIn("did not finish", self.last_log(job))

    def test_a_pass_with_little_time_left_keeps_its_run_for_a_later_check(self):
        job = self.add_pi()
        self.deferred("run-due")
        # The status read would outlast this pass's whole budget, so it is cut short, not killed.
        self.set_meta(job, timeout=70)
        self.control("status-delay", "100")
        self.deferred("run-due")
        record = self.listed(job)
        self.assertEqual((record["state"], record["remote_state"], record["attempts"]), ("pending", "running", 1))
        self.assertEqual(WarningTopic.received, [])
        self.clear("status-delay")
        self.set_run(record["remote_run"], state="done")
        self.deferred("run-due")
        self.assertFalse(job.exists())


class AccountingTests(PiHarness):
    def test_a_blank_report_is_read_again_then_fails_without_relaunching(self):
        self.control("next-state", "done")
        self.control("next-final", "")
        job = self.add_pi("--attempts", "3")
        for check in (1, 2):
            self.deferred("run-due")
            record = self.listed(job)
            self.assertEqual((record["state"], record["remote_state"]), ("pending", "running"))
            self.assertIn(f"check {check} of 3", self.last_log(job))
        self.deferred("run-due")
        record = self.listed(job)
        self.assertEqual((record["state"], record["remote_state"]), ("failed", "failed"))
        self.assertIn("finished without a report", self.last_log(job))
        self.deferred("run-due")
        self.assertEqual(len(self.calls("run")), 1)
        self.assertEqual(self.record(job, "remote-failed.json")["kind"], "no-report")

    def test_a_failure_recorded_before_a_crash_is_accounted_not_relaunched(self):
        self.control("next-state", "done")
        self.control("next-exit", "1")
        job = self.add_pi()
        self.deferred("run-due")
        self.assertEqual(self.record(job, "remote-failed.json")["attempt"], 1)
        self.set_meta(job, state="pending")  # the scheduler stopped before saving state=failed
        self.deferred("run-due")
        self.assertEqual(len(self.calls("run")), 1)
        self.assertEqual(self.meta(job)["state"], "failed")

    def test_superseded_runs_and_abandoned_tokens_reach_the_scheduler_log(self):
        self.control("next-state", "done")
        self.control("next-exit", "1")
        job = self.add_pi()
        self.deferred("run-due")
        [first] = self.runs()
        self.control("next-exit", "0")
        self.control("next-state", "running")
        self.control("launch-mode", "lost-nothing")
        self.deferred("retry", job.name)
        self.deferred("run-due")
        self.assertIn(f"run {first} ended", self.scheduler_log())
        self.deferred("run-due")
        token = self.listed(job)["remote_token"]
        self.deferred("retry", job.name, "--relaunch", token)
        self.assertIn(f"launch token {token} abandoned by retry", self.scheduler_log())


class ReconcileCoverageTests(PiHarness):
    def test_a_listing_that_does_not_reach_the_launch_is_search_incomplete(self):
        self.control("launch-mode", "lost")
        job = self.add_pi()
        self.deferred("run-due")
        self.control("extra-runs", "5000")
        self.clear("launch-mode")
        self.deferred("run-due")
        record = self.listed(job)
        self.assertEqual(record["remote_state"], "launch-unknown")
        self.assertEqual(self.record(job, "launch-unknown.json")["reason"], "search-incomplete")
        self.assertIn("search incomplete", self.last_log(job))
        self.assertIn("status -n 100000", self.last_log(job))
        self.assertNotIn("no run with that token", self.last_log(job))

    def test_a_full_first_listing_is_widened_and_finds_the_run(self):
        self.control("launch-mode", "lost")
        job = self.add_pi()
        self.deferred("run-due")
        self.control("extra-runs", "700")
        self.deferred("run-due")
        [run_id] = self.runs()
        self.assertEqual((self.listed(job)["remote_state"], self.listed(job)["remote_run"]), ("running", run_id))
        self.assertIn(["status", "-n", "2000"], self.calls("status"))


class TrustedStateTests(PiHarness):
    def test_listing_never_runs_a_script_jobs_files_even_with_an_agent_env(self):
        marker = self.tmp / "ran"
        env = self.tmp / "agent.env"
        env.write_text("agent_host=vanpi\n")
        script = self.tmp / "job.py"
        script.write_text(f"#!/usr/bin/python3\nopen({str(marker)!r}, 'w').close()\n")
        job = Path(self.deferred("add", "--file", str(env), "2099-01-01", "plain", str(script)).stdout.strip())
        record = self.listed(job)
        self.assertFalse(marker.exists())
        self.assertEqual((record["agent"], record["host"], record["remote_state"]), (False, None, None))

    def test_listing_and_cancel_never_run_a_vanpi_jobs_own_copy(self):
        marker = self.tmp / "ran"
        job = self.add_pi()
        (job / "run").write_text(f"#!/usr/bin/python3\nopen({str(marker)!r}, 'w').close()\n")
        self.assertEqual(self.listed(job)["remote_state"], "unlaunched")
        self.deferred("cancel", job.name)
        self.assertFalse(marker.exists())

    def test_cancel_refuses_without_the_job_run_or_agent_env(self):
        for missing in ("run", "agent.env"):
            with self.subTest(missing=missing):
                job = self.add_pi(name=f"no-{missing.replace('.', '-')}")
                self.deferred("run-due")
                (job / missing).unlink()
                self.offline()
                result = self.deferred("cancel", job.name, check=False)
                self.assertEqual(result.returncode, 6)
                self.assertTrue(job.exists())
                self.offline(False)

    def test_unreadable_state_is_an_invalid_row_that_only_abandon_removes(self):
        job = self.add_pi()
        self.deferred("run-due")
        [run_id] = self.runs()
        healthy = self.add_pi(name="healthy")
        (job / "remote-run.json").write_text('{"run_id": "%s", broken' % run_id)
        records = {record["id"]: record for record in json.loads(self.deferred("list", "--json").stdout)["jobs"]}
        self.assertFalse(records[job.name]["valid"])
        self.assertIn("vanpi run state unreadable", records[job.name]["problem"])
        self.assertTrue(records[healthy.name]["valid"])
        result = self.deferred("cancel", job.name, check=False)
        self.assertEqual(result.returncode, 6)
        self.assertIn(run_id, result.stderr)
        result = self.deferred("cancel", "--abandon", job.name)
        self.assertFalse(job.exists())
        self.assertIn(f"stop {run_id}", result.stdout)
        self.assertIn(run_id, self.scheduler_log())
        self.assertEqual(self.calls("stop"), [])

    def test_unreadable_state_is_flagged_once_and_not_run(self):
        job = self.add_pi()
        self.deferred("run-due")
        (job / "remote-run.json").write_text("{}")
        self.deferred("run-due")
        self.deferred("run-due")
        self.assertEqual([t for t, _, _ in WarningTopic.received], [f"Deferred job not run: {job.name}"])
        self.assertEqual(len(self.calls("status")), 1)


class AddCheckTests(PiHarness):
    def test_a_vanpi_cwd_under_this_macs_home_is_refused(self):
        home = self.env["HOME"]
        for cwd in (home, f"{home}/dev"):
            with self.subTest(cwd=cwd):
                result = self.add_pi("--cwd", cwd, check=False)
                self.assertEqual(result.returncode, 1)
                self.assertIn("~/", result.stderr)
        for cwd in ("~/dev", "/home/pi/dev", f"{home}x/dev"):
            with self.subTest(cwd=cwd):
                self.add_pi("--cwd", cwd, name="ok" + str(abs(hash(cwd)) % 1000))

    def test_names_whose_id_the_other_verbs_reject_are_refused(self):
        long_name = "n" * 50
        for verb in ("add", "add-agent"):
            with self.subTest(verb=verb):
                source = self.script("exit 0") if verb == "add" else self.prompt
                result = self.deferred(verb, "2099-01-01", long_name, str(source), check=False)
                self.assertEqual(result.returncode, 1)
                self.assertIn("at most 60 characters", result.stderr)
        job = self.add("2099-01-01", "n" * 49, "exit 0")
        self.assertEqual(len(job.name), 60)
        self.deferred("cancel", job.name)

    def test_an_existing_over_long_id_stays_manageable(self):
        job = self.add("2099-01-01", "short", "exit 0")
        name = "o" * 55
        old = job.with_name(f"2099-01-01-{name}")
        job.rename(old)
        self.set_meta(old, name=name)
        self.assertIn(old.name, self.deferred("run-now", old.name).stdout)
        self.assertFalse(old.exists())
