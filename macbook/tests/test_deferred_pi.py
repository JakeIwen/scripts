"""`deferred.zsh add-agent --host vanpi`: the durable launch/collect state machine.

Every case runs the real CLI and the job's real run script against a fake
pi_agent.py and a fake ssh (deferred_pi_fixture), so no Pi run is started.
"""
import json
from pathlib import Path
import re
import stat
import time

from macbook.tests.deferred_fixture import WarningTopic
from macbook.tests.deferred_pi_fixture import PiHarness, YESTERDAY

TOKEN_RUN = re.compile(r"^\d{4}-\d{6}-[0-9a-f]{4}-(d[0-9a-f]{16})$")


class AddPiJobTests(PiHarness):
    def test_vanpi_defaults_and_listing(self):
        job = self.add_pi()
        self.assertEqual(sorted(p.name for p in job.iterdir()), ["agent.env", "deferred_pi_remote.py", "deferred_pi_state.py", "job", "prompt.txt", "run"])
        self.assertEqual(stat.S_IMODE((job / "run").stat().st_mode), 0o700)
        self.assertEqual(self.meta(job)["max_attempts"], "1")
        record = self.listed(job)
        self.assertEqual((record["host"], record["model"], record["effort"]), ("vanpi", "sol", "medium"))
        self.assertEqual((record["remote_state"], record["remote_run"]), ("unlaunched", None))
        self.assertTrue(record["agent"])
        explicit = self.add_pi("--attempts", "1", "--model", "astra", "--effort", "high", name="three")
        self.assertEqual(self.meta(explicit)["max_attempts"], "1")
        self.assertEqual(self.listed(explicit)["model"], "astra")

    def test_attempts_other_than_one_are_refused_for_vanpi(self):
        for args in (("--host", "vanpi", "--attempts", "2"), ("--attempts", "3")):
            prompt = str(self.prompt) if "--host" in args else "vanpi:/home/pi/brief.md"
            with self.subTest(args=args):
                result = self.deferred("add-agent", *args, YESTERDAY, "many", prompt, check=False)
                self.assertEqual(result.returncode, 1)
                self.assertIn("at most one Pi run", result.stderr)
        self.assertEqual(sorted((self.root / "jobs").glob("*")), [])
        mac = self.deferred("add-agent", "--attempts", "3", "--cwd", str(self.tmp), YESTERDAY, "macjob", str(self.prompt))
        self.assertEqual(self.meta(Path(mac.stdout.strip()))["max_attempts"], "3")

    def test_mac_and_script_jobs_list_their_host_fields(self):
        mac = Path(self.deferred("add-agent", "--cwd", str(self.tmp), YESTERDAY, "macjob", str(self.prompt)).stdout.strip())
        record = self.listed(mac)
        self.assertEqual((record["host"], record["model"], record["effort"], record["remote_state"], record["remote_run"]),
                         ("mac", "haiku", "medium", None, None))
        plain = self.add(YESTERDAY, "plain", "exit 0")
        record = self.listed(plain)
        self.assertEqual([record[k] for k in ("host", "model", "effort", "remote_state", "remote_run")], [None] * 5)

    def test_add_rejects_bad_vanpi_input(self):
        cases = (
            ("--model", "haiku"),
            ("--run-timeout", "0"),
            ("--cwd", "two\nlines"),
        )
        # Like the other add option checks (`--attempts`, `--timeout`), these exit 1 through die;
        # status 2 is only for a malformed command line.
        for args in cases:
            with self.subTest(args=args):
                self.assertEqual(self.add_pi(*args, check=False).returncode, 1)
        result = self.deferred("add-agent", "--host", "elsewhere", YESTERDAY, "bad", str(self.prompt), check=False)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.deferred("add-agent", "--bogus", YESTERDAY, "bad", str(self.prompt),
                                       check=False).returncode, 2)
        for prompt in ("vanpi:relative/path.md", "vanpi:/two\nlines.md", "vanpi:/" + "a" * 1100):
            with self.subTest(prompt=prompt):
                result = self.deferred("add-agent", YESTERDAY, "bad", prompt, check=False)
                self.assertEqual(result.returncode, 1)
        result = self.deferred("add-agent", "--host", "mac", YESTERDAY, "bad", "vanpi:/x.md", check=False)
        self.assertEqual(result.returncode, 1)
        self.assertIn("--host mac", result.stderr)
        result = self.deferred("add-agent", "--run-timeout", "5", "--cwd", str(self.tmp), YESTERDAY, "bad",
                               str(self.prompt), check=False)
        self.assertEqual(result.returncode, 1)
        self.assertIn("only to --host vanpi", result.stderr)
        self.assertFalse((self.root / "jobs").exists() and any((self.root / "jobs").iterdir()))

    def test_vanpi_prompt_path_implies_vanpi_and_stores_no_prompt(self):
        job = Path(self.deferred("add-agent", YESTERDAY, "remote", "vanpi:/home/pi/brief.md").stdout.strip())
        self.assertNotIn("prompt.txt", [p.name for p in job.iterdir()])
        self.assertEqual(self.listed(job)["host"], "vanpi")


class LaunchAndCollectTests(PiHarness):
    def test_finished_run_is_delivered_without_the_deferred_preamble(self):
        self.control("next-state", "done")
        job = self.add_pi("--cwd", "~/dev/obd things", "--effort", "high")
        self.deferred("run-due")
        self.assertFalse(job.exists())
        [run] = self.calls("run")
        self.assertEqual(run[run.index("--agent") + 1], "sol")
        self.assertEqual(run[run.index("--effort") + 1], "high")
        self.assertEqual(run[run.index("--cd") + 1], "~/dev/obd things")
        self.assertIn("--no-wait", run)
        self.assertRegex(run[run.index("--name") + 1], r"^d[0-9a-f]{16}$")
        [run_id] = self.runs()
        self.assertEqual((self.fake / "prompts" / run_id).read_text(), "Check the Pi thing.\n")
        self.assertEqual(WarningTopic.paths, ["/agent"])
        title, _, body = WarningTopic.received[0]
        self.assertEqual(title, "pijob")
        self.assertTrue(body.startswith("OK\nall good"))
        self.assertIn(f"Run {run_id} on vanpi", body)
        self.assertNotIn("NEEDS LOOK", (self.fake / "prompts" / run_id).read_text())

    def test_running_run_is_collected_by_a_later_pass_even_after_ready_turns_false(self):
        flag = self.tmp / "ready"
        flag.write_text("")
        job = self.add_pi("--ready", f"test -e '{flag}'")
        self.deferred("run-due")
        record = self.listed(job)
        [run_id] = self.runs()
        # The launch pass that left the run on the Pi keeps its attempt; collecting spends none.
        self.assertEqual((record["remote_state"], record["remote_run"], record["attempts"]), ("running", run_id, 1))
        self.assertIn(f"status {run_id}", self.last_log(job))
        self.assertEqual(self.last_log(job).splitlines()[-1],
                         f"Pi run {run_id} is still running on vanpi; a later check collects it.")
        flag.unlink()
        self.set_run(run_id, state="done")
        self.deferred("run-due")
        self.assertFalse(job.exists())
        self.assertEqual(len(self.calls("run")), 1)
        self.assertEqual(len(WarningTopic.received), 1)

    def test_pi_offline_at_the_due_time_stays_unlaunched(self):
        self.offline()
        job = self.add_pi()
        self.deferred("run-due")
        record = self.listed(job)
        self.assertEqual((record["remote_state"], record["attempts"], record["state"]), ("unlaunched", 0, "pending"))
        self.assertEqual(self.calls("run"), [])
        self.assertIsNone(self.record(job, "launching.json"))
        self.assertEqual(WarningTopic.received, [])

    def test_exit_zero_with_an_empty_report_is_not_success(self):
        job = self.add_pi()
        self.deferred("run-due")
        [run_id] = self.runs()
        self.set_run(run_id, state="done")
        self.control("status-mode", "empty")
        self.deferred("run-due")
        record = self.listed(job)
        self.assertEqual((record["remote_state"], record["state"], record["attempts"]), ("running", "pending", 1))
        self.assertEqual(WarningTopic.received, [])
        self.clear("status-mode")
        self.deferred("run-due")
        self.assertFalse(job.exists())

    def test_finished_before_the_deadline_but_collected_after_it_is_a_success(self):
        job = self.add_pi("--run-timeout", "1")
        self.deferred("run-due")
        [run_id] = self.runs()
        run = self.record(job, "remote-run.json")
        (job / "remote-run.json").write_text(json.dumps({**run, "launched": run["launched"] - 3600}))
        self.set_run(run_id, state="done")
        self.deferred("run-due")
        self.assertFalse(job.exists())
        self.assertEqual(self.calls("stop"), [])

    def test_overdue_run_is_stopped_and_confirmed_before_it_counts_as_timed_out(self):
        job = self.add_pi("--run-timeout", "1")
        self.deferred("run-due")
        run = self.record(job, "remote-run.json")
        (job / "remote-run.json").write_text(json.dumps({**run, "launched": run["launched"] - 120}))
        self.deferred("run-due")
        self.assertEqual(len(self.calls("stop")), 1)
        record = self.listed(job)
        self.assertEqual((record["remote_state"], record["state"]), ("failed", "failed"))
        self.assertIn("timed out", self.last_log(job))

    def test_a_stop_that_returns_while_the_run_still_shows_running_is_not_stopped(self):
        self.control("stop-mode", "ignore")
        job = self.add_pi("--run-timeout", "1")
        self.quick_stop_polling(job)
        self.deferred("run-due")
        run = self.record(job, "remote-run.json")
        (job / "remote-run.json").write_text(json.dumps({**run, "launched": run["launched"] - 120}))
        self.deferred("run-due")
        record = self.listed(job)
        self.assertEqual((record["remote_state"], record["state"], record["attempts"]), ("running", "pending", 1))
        self.assertIn("not confirmed", self.last_log(job))
        self.deferred("run-due")
        self.assertEqual(len(self.calls("stop")), 2)

    def test_a_slow_ending_overdue_run_is_confirmed_by_polling_in_one_pass(self):
        self.control("stop-lag", "2")
        job = self.add_pi("--run-timeout", "1")
        self.quick_stop_polling(job, window="5")
        self.deferred("run-due")
        run = self.record(job, "remote-run.json")
        (job / "remote-run.json").write_text(json.dumps({**run, "launched": run["launched"] - 120}))
        self.deferred("run-due")
        self.assertEqual(len(self.calls("stop")), 1)
        record = self.listed(job)
        self.assertEqual((record["remote_state"], record["state"]), ("failed", "failed"))
        self.assertIn("timed out", self.last_log(job))

    def test_remote_failure_is_terminal_and_a_retry_starts_a_new_run(self):
        self.control("next-state", "done")
        self.control("next-exit", "1")
        job = self.add_pi()
        self.deferred("run-due")
        [first] = self.runs()
        record = self.listed(job)
        self.assertEqual((record["state"], record["remote_state"], record["remote_run"]), ("failed", "failed", first))
        self.assertIn(f"Run {first} on vanpi failed: exited 1", self.last_log(job))
        self.assertEqual(self.alerts(), [f"Deferred job failed: {job.name}"])
        status_calls = len(self.calls("status"))
        self.control("next-exit", "0")
        time.sleep(1.1)  # a new run id needs a new second
        self.deferred("retry", job.name)
        self.deferred("run-due")
        self.assertFalse(job.exists())
        self.assertEqual(len(self.runs()), 2)
        self.assertNotIn(["status", first], self.calls("status")[status_calls:])

    def test_agent_reported_error_counts_as_failure(self):
        self.control("next-state", "done")
        self.control("next-error", "error_during_execution")
        job = self.add_pi()
        self.deferred("run-due")
        self.assertEqual(self.listed(job)["remote_state"], "failed")
        self.assertIn("reported an error", self.last_log(job))

    def test_delivery_failure_keeps_the_report_and_sends_it_again(self):
        self.control("next-state", "done")
        job = self.add_pi()
        secrets = Path(self.env["DEFERRED_SECRETS"])
        good = secrets.read_text()
        secrets.write_text("export UNRELATED=1\n")
        self.deferred("run-due")
        self.assertEqual(self.listed(job)["remote_state"], "finished")
        self.assertEqual(self.meta(job)["state"], "pending")
        secrets.write_text(good)
        calls = len(self.calls())
        self.deferred("run-due")
        self.assertFalse(job.exists())
        self.assertEqual(len(self.calls()), calls)
        self.assertEqual([t for t, _, _ in WarningTopic.received], ["pijob"])


class LostLaunchTests(PiHarness):
    def test_lost_response_adopts_the_run_that_carries_the_token(self):
        self.control("launch-mode", "lost")
        job = self.add_pi()
        self.deferred("run-due")
        self.assertEqual(self.listed(job)["remote_state"], "launching")
        token = self.record(job, "launching.json")["token"]
        self.clear("launch-mode")
        self.deferred("run-due")
        [run_id] = self.runs()
        self.assertTrue(run_id.endswith("-" + token))
        record = self.listed(job)
        self.assertEqual((record["remote_state"], record["remote_run"]), ("running", run_id))
        self.assertEqual(len(self.calls("run")), 1)

    def test_absent_token_holds_without_relaunching_until_retry(self):
        self.control("launch-mode", "lost-nothing")
        job = self.add_pi()
        self.deferred("run-due")
        token = self.record(job, "launching.json")["token"]
        self.deferred("run-due")
        record = self.listed(job)
        self.assertEqual((record["remote_state"], record["state"], record["attempts"]), ("launch-unknown", "failed", 1))
        self.assertEqual(record["relaunch_id"], token)
        self.assertIn(token, self.last_log(job))
        calls, ssh = len(self.calls()), (self.fake / "ssh-calls").read_text()
        self.deferred("run-due")
        self.assertEqual((len(self.calls()), (self.fake / "ssh-calls").read_text()), (calls, ssh))
        self.clear("launch-mode")
        refused = self.deferred("retry", job.name, check=False)
        self.assertEqual(refused.returncode, 7)
        self.assertIn(f"retry {job.name} --relaunch {token}", refused.stderr)
        self.assertEqual(self.listed(job)["remote_state"], "launch-unknown")
        self.assertEqual(self.deferred("retry", job.name, "--relaunch", "d" + "0" * 16, check=False).returncode, 7)
        self.assertIn("Cleared", self.deferred("retry", job.name, "--relaunch", token).stdout)
        self.assertIn(f"attempt {token} abandoned", (job / "runs.log").read_text())
        self.deferred("run-due")
        self.assertEqual(len(self.calls("run")), 2)
        [run_id] = self.runs()
        self.assertFalse(run_id.endswith("-" + token))

    def test_token_matched_twice_holds(self):
        self.control("launch-mode", "lost")
        job = self.add_pi()
        self.deferred("run-due")
        [run_id] = self.runs()
        # Any other month-day makes a second id for the same token, whatever today's date.
        twin = re.sub(r"^\d{4}", "0101" if not run_id.startswith("0101") else "0202", run_id)
        self.assertNotEqual(twin, run_id)
        (self.fake / "runs" / (twin + ".json")).write_text((self.fake / "runs" / (run_id + ".json")).read_text())
        self.deferred("run-due")
        record = self.listed(job)
        self.assertEqual((record["remote_state"], record["state"]), ("launch-unknown", "failed"))
        self.assertIn("2 runs match", self.last_log(job))

    def test_run_now_also_clears_the_hold(self):
        self.control("launch-mode", "lost-nothing")
        job = self.add_pi()
        self.deferred("run-due")
        self.deferred("run-due")
        record = self.listed(job)
        self.assertEqual(record["remote_state"], "launch-unknown")
        self.clear("launch-mode")
        self.control("next-state", "done")
        self.assertEqual(self.deferred("run-now", job.name, check=False).returncode, 7)
        self.assertEqual(self.calls("run")[1:], [])
        self.assertEqual(self.deferred("run-now", "--relaunch", record["remote_token"], job.name,
                                       check=False).returncode, 0)
        self.assertFalse(job.exists())


class PromptPathTests(PiHarness):
    def test_paths_with_spaces_quotes_and_substitutions_reach_the_pi_as_data(self):
        folder = self.tmp / "we ird"
        folder.mkdir()
        brief = folder / "it's $(touch PWNED) `touch PWNED2` \"q\".md"
        brief.write_text("Remote brief.\n")
        self.control("next-state", "done")
        job = Path(self.deferred("add-agent", YESTERDAY, "remote", f"vanpi:{brief}").stdout.strip())
        self.deferred("run-due")
        self.assertFalse(job.exists())
        [run_id] = self.runs()
        self.assertEqual((self.fake / "prompts" / run_id).read_text(), "Remote brief.\n")
        for place in (self.tmp, folder, job.parent, Path(self.env["HOME"])):
            self.assertEqual(sorted(place.glob("PWNED*")), [])

    def test_unusable_remote_prompt_is_an_ordinary_failure(self):
        blank = self.tmp / "blank.md"
        blank.write_text("  \n")
        for name, path in (("missing", self.tmp / "missing.md"), ("blank", blank), ("folder", self.tmp)):
            with self.subTest(name=name):
                job = Path(self.deferred("add-agent", YESTERDAY, name, f"vanpi:{path}").stdout.strip())
                self.deferred("run-due")
                self.assertEqual((self.meta(job)["state"], self.listed(job)["remote_state"]), ("failed", "unlaunched"))
                self.assertIn("cannot be used", self.last_log(job))
        self.assertEqual(self.calls("run"), [])

    def test_unreachable_pi_postpones_reading_the_prompt(self):
        self.offline()
        job = Path(self.deferred("add-agent", YESTERDAY, "remote", f"vanpi:{self.prompt}").stdout.strip())
        self.deferred("run-due")
        self.assertEqual((self.meta(job)["state"], self.meta(job)["attempts"]), ("pending", "0"))


class CancelTests(PiHarness):
    def test_unlaunched_job_cancels_without_contacting_the_pi(self):
        job = self.add_pi()
        self.deferred("cancel", job.name)
        self.assertFalse(job.exists())
        self.assertEqual(self.calls(), [])

    def test_running_job_is_stopped_and_confirmed_before_deletion(self):
        job = self.add_pi()
        self.deferred("run-due")
        [run_id] = self.runs()
        self.deferred("cancel", job.name)
        self.assertFalse(job.exists())
        self.assertEqual(self.calls("stop"), [["stop", run_id]])

    def test_unreachable_pi_refuses_and_abandon_overrides(self):
        job = self.add_pi()
        self.deferred("run-due")
        [run_id] = self.runs()
        self.offline()
        result = self.deferred("cancel", job.name, check=False)
        self.assertEqual(result.returncode, 6)
        self.assertIn(run_id, result.stderr)
        self.assertIn("--abandon", result.stderr)
        self.assertTrue(job.exists())
        result = self.deferred("cancel", "--abandon", job.name)
        self.assertFalse(job.exists())
        self.assertIn(f"stop {run_id}", result.stdout)

    def test_unconfirmed_stop_refuses(self):
        self.control("stop-mode", "ignore")
        job = self.add_pi()
        self.quick_stop_polling(job)
        self.deferred("run-due")
        result = self.deferred("cancel", job.name, check=False)
        self.assertEqual(result.returncode, 6)
        self.assertTrue(job.exists())
        self.assertIn("still shows running", result.stderr)

    def test_a_slow_ending_run_is_confirmed_within_one_cancel(self):
        self.control("stop-lag", "3")
        job = self.add_pi()
        self.quick_stop_polling(job, window="5")
        self.deferred("run-due")
        self.deferred("cancel", job.name)
        self.assertFalse(job.exists())
        self.assertEqual(len(self.calls("stop")), 1)
        self.assertGreaterEqual(len(self.calls("status")), 4)

    def test_a_run_still_running_for_the_whole_window_is_refused(self):
        self.control("stop-lag", "100000")
        job = self.add_pi()
        self.quick_stop_polling(job)
        self.deferred("run-due")
        result = self.deferred("cancel", job.name, check=False)
        self.assertEqual(result.returncode, 6)
        self.assertTrue(job.exists())
        self.assertGreater(len(self.calls("status")), 3)

    def test_unreadable_status_while_polling_never_confirms_the_stop(self):
        self.control("stop-lag", "0")
        self.control("empty-reads", "100000")
        job = self.add_pi()
        self.quick_stop_polling(job)
        self.deferred("run-due")
        result = self.deferred("cancel", job.name, check=False)
        self.assertEqual(result.returncode, 6)
        self.assertTrue(job.exists())

    def test_polling_goes_on_past_an_unreadable_status(self):
        self.control("stop-lag", "1")
        self.control("empty-reads", "2")
        job = self.add_pi()
        self.quick_stop_polling(job, window="5")
        self.deferred("run-due")
        self.deferred("cancel", job.name)
        self.assertFalse(job.exists())
        self.assertGreaterEqual(len(self.calls("status")), 5)

    def test_launching_job_without_an_id_refuses_when_unreachable(self):
        self.control("launch-mode", "lost")
        job = self.add_pi()
        self.deferred("run-due")
        token = self.record(job, "launching.json")["token"]
        self.offline()
        result = self.deferred("cancel", job.name, check=False)
        self.assertEqual(result.returncode, 6)
        self.assertIn(token, result.stderr)
        self.offline(False)
        self.deferred("cancel", job.name)  # reconciled by token, stopped, confirmed
        self.assertFalse(job.exists())
        self.assertEqual(len(self.calls("stop")), 1)

    def test_finished_and_failed_jobs_cancel_without_a_stop(self):
        self.control("next-state", "done")
        self.control("next-exit", "1")
        job = self.add_pi()
        self.deferred("run-due")
        self.deferred("cancel", job.name)
        self.assertFalse(job.exists())
        self.assertEqual(self.calls("stop"), [])
