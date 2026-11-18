"""`deferred.zsh` vanpi jobs: a job starts at most one Pi run by itself; another
run needs retry or run-now, with --relaunch naming the attempt when it may
still be active; the scheduler owns the collection deadline and the alerts;
the state reader trusts only consistent records; reconciliation proves its
coverage. Fake pi_agent.py and ssh as in deferred_pi_fixture.
"""
import json
import os
from pathlib import Path
import time

import deferred_pi_remote
import deferred_pi_run
import deferred_pi_state
from macbook.tests.deferred_fixture import WarningTopic
from macbook.tests.deferred_pi_fixture import PiHarness

PAST_DEADLINE = 120 * 60 + deferred_pi_state.COLLECT_GRACE + 60  # default --run-timeout + the grace


def alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


class OneAutomaticRunTests(PiHarness):
    def test_a_lost_launch_past_its_collection_deadline_never_relaunches_by_itself(self):
        self.control("launch-mode", "lost-nothing")
        job = self.add_pi()
        self.deferred("run-due")
        token = self.record(job, "launching.json")["token"]
        self.offline()
        self.age_record(job, "launching.json", "at", PAST_DEADLINE)
        self.deferred("run-due")
        record = self.listed(job)
        self.assertEqual((record["state"], record["remote_state"], record["relaunch_id"]), ("failed", "failed", token))
        self.assertIn("collection deadline", self.last_log(job))
        self.offline(False)
        self.clear("launch-mode")
        self.deferred("run-due")
        self.deferred("run-due")
        self.assertEqual(len(self.calls("run")), 1)
        for verb in ("retry", "run-now"):
            refused = self.deferred(verb, job.name, check=False)
            self.assertEqual(refused.returncode, 7)
            self.assertIn(f"{verb} {job.name} --relaunch {token}", refused.stderr)
        self.assertEqual(self.deferred("reschedule", job.name, "2099-01-01", check=False).returncode, 8)
        self.deferred("retry", job.name, "--relaunch", token)
        self.deferred("run-due")
        self.assertEqual(len(self.calls("run")), 2)

    def test_a_consumed_or_stale_relaunch_id_is_refused(self):
        self.control("launch-mode", "lost-nothing")
        job = self.add_pi()
        self.deferred("run-due")
        self.deferred("run-due")
        token = self.listed(job)["relaunch_id"]
        self.deferred("retry", job.name, "--relaunch", token)
        self.clear("launch-mode")
        self.control("next-state", "done")
        self.control("next-exit", "1")
        self.deferred("run-due")
        record = self.listed(job)
        self.assertEqual((record["state"], record["remote_state"], record["relaunch_id"]), ("failed", "failed", None))
        for stale in (token, record["remote_run"]):
            for verb in ("retry", "run-now"):
                with self.subTest(stale=stale, verb=verb):
                    result = self.deferred(verb, job.name, "--relaunch", stale, check=False)
                    self.assertEqual(result.returncode, 7)
                    self.assertIn("names no current attempt", result.stderr)
        self.assertEqual((self.meta(job)["state"], len(self.calls("run"))), ("failed", 2))
        self.deferred("retry", job.name)  # a confirmed end needs no id
        self.assertEqual(self.listed(job)["remote_state"], "unlaunched")

    def test_a_clean_finish_with_no_report_found_by_the_stop_is_no_report_not_timed_out(self):
        self.control("next-final", "")
        self.control("stop-exit", "0")
        job = self.add_pi("--run-timeout", "1")
        self.quick_stop_polling(job)
        self.deferred("run-due")
        self.age_launch(job, 120)
        for check in (1, 2):
            self.deferred("run-due")
            self.assertIn(f"check {check} of 3", self.last_log(job))
        self.deferred("run-due")
        self.assertEqual(self.record(job, "remote-failed.json")["kind"], "no-report")
        self.assertEqual((len(self.calls("stop")), len(self.calls("run"))), (1, 1))
        refused = self.deferred("reschedule", job.name, "2099-01-01", check=False)
        self.assertIn(f"use retry {job.name}", refused.stderr)


class SchedulerAlertTests(PiHarness):
    def test_a_new_run_started_by_run_now_on_a_failed_job_alerts_when_it_fails(self):
        self.control("next-state", "done")
        self.control("next-exit", "1")
        job = self.add_pi()
        self.deferred("run-due")
        self.assertEqual(self.alerts(), [f"Deferred job failed: {job.name}"])
        self.control("next-state", "running")
        self.control("next-exit", "0")
        self.assertEqual(self.deferred("run-now", job.name, check=False).returncode, 4)
        run_id = self.listed(job)["remote_run"]
        self.set_run(run_id, state="done", exit="1")
        self.deferred("run-due")
        self.deferred("run-due")
        self.assertEqual(self.alerts(), [f"Deferred job failed: {job.name}"] * 2)
        self.assertIn(run_id, WarningTopic.received[-1][2])

    def test_a_broken_collector_alerts_once_and_the_scheduler_still_enforces_the_deadline(self):
        job = self.add_pi()
        self.deferred("run-due")
        run_id = self.listed(job)["remote_run"]
        (job / "deferred_pi_remote.py").unlink()
        self.deferred("run-due")
        self.deferred("run-due")
        self.assertEqual(self.alerts(), [f"Deferred job collector failed: {job.name}"])
        self.assertIn(f"stop {run_id}", WarningTopic.received[0][2])
        record = self.listed(job)
        self.assertEqual((record["state"], record["remote_state"]), ("pending", "running"))
        self.age_launch(job, PAST_DEADLINE)
        self.deferred("run-due")
        record = self.listed(job)
        self.assertEqual((record["state"], record["remote_state"], record["relaunch_id"]), ("failed", "failed", run_id))
        self.assertEqual(self.record(job, "remote-failed.json")["kind"], "collection-deadline")
        self.assertEqual(self.alerts()[-1], f"Deferred job failed: {job.name}")

    def test_an_undelivered_report_is_sent_again_and_alerted_once_as_a_delivery_problem(self):
        self.control("next-state", "done")
        job = self.add_pi("--topic-var", "MISSING_TOPIC_URL")
        self.deferred("run-due")
        self.deferred("run-due")
        self.assertEqual(self.alerts(), [f"Deferred job result not delivered: {job.name}"])
        body = WarningTopic.received[0][2]
        self.assertIn("report is saved", body)
        self.assertNotIn("ended badly", body)
        self.assertEqual((self.meta(job)["state"], len(self.calls("run"))), ("pending", 1))
        secrets = Path(self.env["DEFERRED_SECRETS"])
        url = secrets.read_text().split("NTFY_MACBOOK_URL=", 1)[1].splitlines()[0]
        secrets.write_text(secrets.read_text() + f"export MISSING_TOPIC_URL={url}\n")
        self.deferred("run-due")
        self.assertFalse(job.exists())
        self.assertEqual(len(self.calls("run")), 1)


class ReadyProbeTests(PiHarness):
    def test_a_ready_probe_that_times_out_leaves_no_child_running(self):
        pidfile = self.tmp / "child.pid"
        job = self.add_pi("--ready", f"/bin/sh -c 'echo $$ > {pidfile}; exec /bin/sleep 120'; true")
        self.set_meta(job, timeout=deferred_pi_run.MIN_PASS_TIMEOUT + 8)
        self.deferred("run-due")
        pid = int(pidfile.read_text())
        self.addCleanup(lambda: alive(pid) and os.kill(pid, 9))
        deadline = time.monotonic() + 5
        while alive(pid) and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertFalse(alive(pid))
        self.assertIn("did not finish", self.last_log(job))
        self.assertEqual(self.calls("run"), [])


class CoverageTests(PiHarness):
    def lost_launch_with_a_twin(self):
        self.control("launch-mode", "lost")
        job = self.add_pi()
        self.deferred("run-due")
        [run_id] = self.runs()
        # Another month-day gives an older run with the same token, whatever today's date.
        twin = ("0101" if not run_id.startswith("0101") else "0202") + run_id[4:]
        (self.fake / "runs" / (twin + ".json")).write_text((self.fake / "runs" / (run_id + ".json")).read_text())
        return job

    def test_one_match_in_a_full_listing_is_not_adopted_until_coverage_is_proven(self):
        job = self.lost_launch_with_a_twin()
        self.control("between-runs", str(deferred_pi_remote.LIST_LIMITS[0] + 100))
        cancel = self.deferred("cancel", job.name, check=False)
        self.assertEqual(cancel.returncode, 6)
        self.assertTrue(job.exists())
        self.assertEqual(self.calls("stop"), [])
        self.deferred("run-due")
        self.assertEqual(self.listed(job)["remote_state"], "launch-unknown")
        self.assertEqual(self.record(job, "launch-unknown.json")["reason"], "ambiguous")
        self.assertIn(["status", "-n", str(deferred_pi_remote.LIST_LIMITS[1])], self.calls("status"))

    def test_coverage_counts_listed_rows_not_recognised_ids(self):
        self.control("launch-mode", "lost")
        job = self.add_pi()
        self.deferred("run-due")
        self.control("foreign-runs", str(deferred_pi_remote.LIST_LIMITS[0]))
        self.deferred("run-due")
        [run_id] = self.runs()
        record = self.listed(job)
        self.assertEqual((record["remote_state"], record["remote_run"]), ("running", run_id))


class StrictStateTests(PiHarness):
    def damaged_jobs(self):
        """One launched job per contradiction, each with the damage applied."""
        other = "1111-111111-1111-d" + "1" * 16
        damage = (
            lambda job, run: (job / "remote-failed.json").write_text(json.dumps(
                {"v": 1, "run_id": other, "token": "d" + "1" * 16, "kind": "remote", "reason": "x"})),
            lambda job, run: (job / "remote-run.json").write_text(json.dumps({**run, "launched": "soon"})),
            lambda job, run: (job / "launch-unknown.json").write_text(json.dumps(
                {"v": 1, "token": run["token"], "at": run["launched"], "reason": "absent", "detail": "x"})),
            lambda job, run: ((job / "report.txt").write_text("OK"), (job / "remote-run.json").unlink()),
        )
        jobs = []
        for index, apply in enumerate(damage):
            job = self.add_pi(name=f"damaged{index}")
            self.deferred("run-due")
            apply(job, self.record(job, "remote-run.json"))
            jobs.append(job)
        return jobs

    def test_contradictory_records_are_invalid_and_never_trusted(self):
        jobs = self.damaged_jobs()
        calls = len(self.calls())
        self.deferred("run-due")
        self.assertEqual(len(self.calls()), calls)
        rows = {row["id"]: row for row in json.loads(self.deferred("list", "--json").stdout)["jobs"]}
        for job in jobs:
            with self.subTest(job=job.name):
                self.assertFalse(rows[job.name]["valid"])
                self.assertIn("vanpi run state unreadable", rows[job.name]["problem"])
                self.assertEqual(self.deferred("cancel", job.name, check=False).returncode, 6)
                self.assertEqual(self.deferred("run-now", job.name, check=False).returncode, 7)
                self.assertTrue(job.exists())
        self.assertEqual(self.calls("stop"), [])
        self.assertEqual(len(self.calls("run")), len(jobs))
        self.deferred("cancel", "--abandon", jobs[0].name)
        self.assertFalse(jobs[0].exists())

    def test_a_record_that_lost_kind_and_host_beside_pi_state_is_not_a_mac_job(self):
        launched = self.add_pi(name="launched")
        self.deferred("run-due")
        [run_id] = self.runs()
        unlaunched = self.add_pi(name="unlaunched", due="2099-01-01")
        for job in (launched, unlaunched):
            meta = {key: value for key, value in self.meta(job).items() if key not in ("kind", "host")}
            (job / "job").write_text("".join(f"{key}={value}\n" for key, value in meta.items()))
        for job in (launched, unlaunched):
            with self.subTest(job=job.name):
                self.assertFalse(self.listed(job)["valid"])
                result = self.deferred("cancel", job.name, check=False)
                self.assertEqual(result.returncode, 6)
                self.assertTrue(job.exists())
        self.assertIn(run_id, self.deferred("cancel", launched.name, check=False).stderr)
        result = self.deferred("cancel", "--abandon", launched.name)
        self.assertIn(f"stop {run_id}", result.stdout)
        self.assertFalse(launched.exists())
        self.assertEqual(self.calls("stop"), [])
