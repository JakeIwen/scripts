"""`deferred.zsh` vanpi jobs: a launched Pi run is always collected, the pass
budget, accounting after a crash, reconciliation coverage, and what listing and
cancel may trust. Fake pi_agent.py and ssh as in deferred_pi_fixture.
"""
import datetime
import json
from pathlib import Path

import deferred_pi_remote
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
        # The new attempt is saved pending before it runs, so the checker accounts however it ends.
        self.assertEqual((record["state"], record["remote_state"], record["last_not_ready"]), ("pending", "running", None))
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

    def test_a_launched_job_cannot_be_rescheduled_and_is_still_collected_and_timed_out(self):
        job = self.add_pi("--run-timeout", "1")
        self.deferred("run-due")
        refused = self.deferred("reschedule", job.name, IN_THREE_DAYS, check=False)
        self.assertEqual(refused.returncode, 8)
        self.assertIn(f"use retry {job.name}", refused.stderr)
        self.assertEqual(self.meta(job)["due"], YESTERDAY + "T00:00")
        self.age_launch(job, 120)
        self.deferred("run-due")
        self.assertEqual(len(self.calls("stop")), 1)
        record = self.listed(job)
        self.assertEqual((record["remote_state"], record["state"]), ("failed", "failed"))
        self.assertIn("timed out", self.last_log(job))

    def test_an_unlaunched_job_past_give_up_fails_without_launching(self):
        job = self.add_pi("--give-up", YESTERDAY)
        self.deferred("run-due")
        record = self.listed(job)
        self.assertEqual((record["state"], record["remote_state"]), ("failed", "unlaunched"))
        self.assertEqual(self.calls(), [])
        self.assertEqual(self.alerts(), [f"Deferred job gave up: {job.name}"])
        self.deferred("run-due")
        self.assertEqual((self.calls(), len(self.alerts())), ([], 1))

    def test_a_launched_run_is_collected_after_give_up_passes(self):
        job = self.add_pi()
        self.deferred("run-due")
        self.set_meta(job, give_up=YESTERDAY + "T00:01")
        self.set_run(self.listed(job)["remote_run"], state="done")
        self.deferred("run-due")
        self.assertFalse(job.exists())
        self.assertEqual(self.alerts(), [])

    def test_a_lost_launch_response_always_prints_the_recovery_commands(self):
        self.control("launch-mode", "lost")
        job = self.add_pi()
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
    def test_a_job_with_no_ready_launches_at_exactly_the_timeout_floor(self):
        floor = deferred_pi_run.MIN_PASS_TIMEOUT
        result = self.deferred("add-agent", "--host", "vanpi", "--timeout", str(floor - 1), YESTERDAY, "short",
                               str(self.prompt), check=False)
        self.assertEqual(result.returncode, 1)
        self.assertIn(f"at least {floor} seconds", result.stderr)
        job = Path(self.deferred("add-agent", "--host", "vanpi", "--timeout", str(floor), YESTERDAY, "enough",
                                 str(self.prompt)).stdout.strip())
        self.deferred("run-due")
        self.assertEqual(len(self.calls("run")), 1)
        self.assertEqual(self.listed(job)["remote_state"], "running")

    def test_a_ready_command_needs_its_allowance_above_the_floor(self):
        floor = deferred_pi_run.MIN_PASS_TIMEOUT + deferred_pi_run.READY_ALLOWANCE
        result = self.add_pi("--ready", "true", "--timeout", str(floor - 1), name="short", check=False)
        self.assertEqual(result.returncode, 1)
        self.assertIn(f"at least {floor} seconds", result.stderr)
        job = self.add_pi("--ready", "true", "--timeout", str(floor), name="enough")
        self.deferred("run-due")
        self.assertEqual((len(self.calls("run")), self.listed(job)["remote_state"]), (1, "running"))

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
        job = self.add_pi()
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
        self.assertEqual(self.record(job, "remote-failed.json")["kind"], "remote")
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
        self.assertIn(f"attempt {first} ended (remote); cleared by retry", self.scheduler_log())
        self.deferred("run-due")
        self.deferred("run-due")
        token = self.listed(job)["remote_token"]
        self.deferred("retry", job.name, "--relaunch", token)
        self.assertIn(f"attempt {token} abandoned by retry --relaunch", self.scheduler_log())


class ReconcileCoverageTests(PiHarness):
    def test_a_listing_that_does_not_reach_the_launch_is_search_incomplete(self):
        self.control("launch-mode", "lost")
        job = self.add_pi()
        self.deferred("run-due")
        self.control("extra-runs", str(deferred_pi_remote.LIST_LIMITS[-1] + 1))
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
        for index, cwd in enumerate(("~/dev", "/home/pi/dev", f"{home}x/dev")):
            with self.subTest(cwd=cwd):
                self.add_pi("--cwd", cwd, name=f"ok{index}")

    def test_names_of_up_to_60_characters_are_accepted_and_their_full_ids_resolve(self):
        for verb, letter in (("add", "a"), ("add-agent", "g")):
            with self.subTest(verb=verb):
                source = self.script("exit 0") if verb == "add" else self.prompt
                job = Path(self.deferred(verb, "2099-01-01", letter * 60, str(source)).stdout.strip())
                self.assertEqual(len(job.name), 71)
                self.assertEqual(self.listed(job)["name"], letter * 60)
                self.deferred("cancel", job.name)
                self.assertFalse(job.exists())
        result = self.deferred("add", "2099-01-01", "n" * 61, str(self.script("exit 0")), check=False)
        self.assertEqual(result.returncode, 1)

    def test_an_existing_over_long_id_stays_manageable(self):
        job = self.add("2099-01-01", "short", "exit 0")
        name = "o" * 55
        old = job.with_name(f"2099-01-01-{name}")
        job.rename(old)
        self.set_meta(old, name=name)
        self.assertIn(old.name, self.deferred("run-now", old.name).stdout)
        self.assertFalse(old.exists())
