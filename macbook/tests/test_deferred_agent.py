"""Deferred 'not ready' exit status, give-up window and add-agent jobs.

A fake `claude` on PATH prints canned JSON and the ntfy topics are loopback
servers, so no real session or topic is used.
"""
import datetime
import json
from pathlib import Path
import stat
import time

from macbook.tests.deferred_fixture import DeferredHarness, WarningTopic

# The job environment is `env -i`, so the fake finds its state directory from a baked-in path.
FAKE_CLAUDE = r"""#!/bin/sh
FAKE_DIR=@FAKE_DIR@
echo "$PWD" > "$FAKE_DIR/claude-cwd"
echo "$@" > "$FAKE_DIR/claude-args"
cat > "$FAKE_DIR/claude-stdin"
echo x >> "$FAKE_DIR/claude-calls"
cat "$FAKE_DIR/claude-output"
exit "$(cat "$FAKE_DIR/claude-exit" 2>/dev/null || echo 0)"
"""


# Due yesterday: past due, but inside the default seven-day give-up window.
YESTERDAY = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()


class NotReadyTests(DeferredHarness):
    def test_not_ready_keeps_attempts_and_sends_no_alert(self):
        job = self.add(YESTERDAY, "waiting", "exit 75", "--attempts", "1")
        for _ in range(3):
            self.deferred("run-due")
        meta = self.meta(job)
        self.assertEqual((meta["attempts"], meta["state"], meta["due"]), ("0", "pending", YESTERDAY + "T00:00"))
        self.assertEqual(WarningTopic.received, [])
        self.assertIn("not ready " + YESTERDAY + "-waiting", (self.root / "deferred.log").read_text())

    def test_not_ready_after_give_up_is_an_ordinary_failure(self):
        job = self.add("2020-01-01", "late", "exit 75", "--attempts", "2", "--give-up", "2020-01-02")
        self.deferred("run-due")
        self.assertEqual(self.meta(job)["attempts"], "1")
        self.assertEqual([t for t, _, _ in WarningTopic.received], ["Deferred job failed: 2020-01-01-late"])

    def test_give_up_defaults_to_seven_days_after_due(self):
        job = self.add(YESTERDAY, "default-window", "exit 75", "--attempts", "1")
        # An old-format record (no give_up key) loads and gets the same default.
        record = [l for l in (job / "job").read_text().splitlines() if not l.startswith("give_up=")]
        (job / "job").write_text("\n".join(record) + "\n")
        self.assertNotIn("give_up", self.meta(job))
        self.deferred("run-due")
        self.assertEqual(self.meta(job)["attempts"], "0")
        late = self.add("2020-01-01", "past-window", "exit 75", "--attempts", "1", "--give-up", "2020-01-01")
        self.deferred("run-due")
        self.assertEqual(self.meta(late)["state"], "failed")

    def test_old_format_record_still_runs_and_lists(self):
        marker = self.tmp / "ran"
        job = self.add("2020-01-01", "old", f"touch {marker}")
        record = [l for l in (job / "job").read_text().splitlines() if not l.startswith("give_up=")]
        (job / "job").write_text("\n".join(record) + "\n")
        listed = json.loads(self.deferred("list", "--json").stdout)["jobs"]
        self.assertTrue(listed[0]["valid"])
        self.deferred("run-due")
        self.assertTrue(marker.exists())
        self.assertFalse(job.exists())

    def test_give_up_before_due_is_refused(self):
        result = self.deferred("add", "--give-up", "2020-01-01", "2020-02-01", "bad",
                               str(self.script("exit 0")), check=False)
        self.assertEqual(result.returncode, 1)
        self.assertIn("--give-up", result.stderr)

    def test_run_now_maps_not_ready_to_exit_4_without_spending_an_attempt(self):
        job = self.add("2099-01-01", "manual-wait", "exit 75")
        result = self.deferred("run-now", job.name, check=False)
        self.assertEqual(result.returncode, 4)
        self.assertIn(f"not ready {job.name}", result.stdout)
        self.assertEqual(self.meta(job)["attempts"], "0")
        self.assertEqual(WarningTopic.received, [])


class TimeoutTests(DeferredHarness):
    def test_timeout_stops_the_whole_process_tree(self):
        marker = self.tmp / "late-work"
        job = self.add("2020-01-01", "tree", f"/bin/sh -c 'sleep 3; touch {marker}' &\nwait",
                       "--timeout", "1", "--attempts", "1")
        self.deferred("run-due")
        self.assertEqual(self.meta(job)["state"], "failed")
        time.sleep(4)
        self.assertFalse(marker.exists())


class AddAgentTests(DeferredHarness):
    def setUp(self):
        super().setUp()
        self.fake = self.tmp / "fake"
        self.fake.mkdir()
        bin_dir = self.tmp / "home" / ".local" / "bin"
        bin_dir.mkdir(parents=True)
        claude = bin_dir / "claude"
        claude.write_text(FAKE_CLAUDE.replace("@FAKE_DIR@", str(self.fake)))
        claude.chmod(0o755)
        self.env.update(HOME=str(self.tmp / "home"))
        self.work = self.tmp / "work dir"
        self.work.mkdir()
        self.prompt = self.tmp / "prompt.md"
        self.prompt.write_text("Check the thing.\n")
        self.set_claude({"type": "result", "is_error": False, "session_id": "sess-123", "result": "OK\nall fine"})

    def set_claude(self, payload, code=0):
        (self.fake / "claude-output").write_text(json.dumps(payload))
        (self.fake / "claude-exit").write_text(str(code))

    def add_agent(self, *args, due=YESTERDAY, name="followup"):
        out = self.deferred("add-agent", "--cwd", str(self.work), *args, due, name, str(self.prompt)).stdout
        return Path(out.strip())

    def test_job_is_self_contained_and_private(self):
        job = self.add_agent()
        self.assertEqual(sorted(p.name for p in job.iterdir()), ["agent.env", "job", "prompt.txt", "run"])
        self.assertEqual(stat.S_IMODE((job / "run").stat().st_mode), 0o700)
        self.assertEqual(self.meta(job)["description"], "agent follow-up: followup")

    def test_failing_ready_command_means_not_ready(self):
        job = self.add_agent("--ready", "exit 1")
        self.deferred("run-due")
        self.assertEqual(self.meta(job)["attempts"], "0")
        self.assertFalse((self.fake / "claude-calls").exists())
        self.assertEqual(WarningTopic.received, [])

    def test_ready_job_runs_claude_in_cwd_and_reports_with_resume_line(self):
        job = self.add_agent("--ready", "true", "--model", "sonnet", "--effort", "high")
        self.deferred("run-due")
        self.assertFalse(job.exists())
        self.assertEqual(Path((self.fake / "claude-cwd").read_text().strip()).resolve(), self.work.resolve())
        args = (self.fake / "claude-args").read_text()
        for expected in ("-p", "--output-format json", "--model sonnet", "--effort high",
                         "--permission-mode bypassPermissions"):
            self.assertIn(expected, args)
        stdin = (self.fake / "claude-stdin").read_text()
        self.assertIn("NEEDS LOOK", stdin)
        self.assertIn("at most 4 short lines", stdin)
        self.assertTrue(stdin.rstrip().endswith("Check the thing."))
        self.assertEqual(WarningTopic.paths, ["/agent"])
        title, _, body = WarningTopic.received[0]
        self.assertEqual(title, "followup")
        self.assertIn("OK\nall fine", body)
        self.assertIn(f"Open it: cd '{self.work.resolve()}' && claude --resume sess-123", body)

    def test_delivery_retry_skips_the_ready_check(self):
        flag = self.tmp / "ready-flag"
        flag.write_text("")
        job = self.add_agent("--ready", f"test -e '{flag}'", "--attempts", "3")
        good = Path(self.env["DEFERRED_SECRETS"]).read_text()
        Path(self.env["DEFERRED_SECRETS"]).write_text("export UNRELATED=1\n")
        self.assertEqual(self.deferred("run-now", job.name, check=False).returncode, 3)
        self.assertTrue((job / "claude.json").exists())
        flag.unlink()
        Path(self.env["DEFERRED_SECRETS"]).write_text(good)
        self.assertEqual(self.deferred("run-now", job.name, check=False).returncode, 0)
        self.assertFalse(job.exists())
        self.assertEqual(len((self.fake / "claude-calls").read_text().split()), 1)
        self.assertEqual([t for t, _, _ in WarningTopic.received], ["followup"])

    def test_defaults_are_haiku_and_medium(self):
        self.add_agent()
        self.deferred("run-due")
        args = (self.fake / "claude-args").read_text()
        self.assertIn("--model haiku", args)
        self.assertIn("--effort medium", args)

    def test_missing_session_id_and_long_result(self):
        self.set_claude({"type": "result", "is_error": False, "result": "OK " + "x" * 2000})
        self.add_agent()
        self.deferred("run-due")
        body = WarningTopic.received[0][2]
        self.assertIn("No session id", body)
        self.assertLess(len(body), 800)

    def test_claude_error_is_an_ordinary_failure(self):
        self.set_claude({"type": "result", "is_error": True, "session_id": "s", "result": "boom"})
        job = self.add_agent("--attempts", "1")
        self.deferred("run-due")
        self.assertEqual(self.meta(job)["state"], "failed")
        self.assertEqual([t for t, _, _ in WarningTopic.received], ["Deferred job gave up: " + YESTERDAY + "-followup"])
        self.assertEqual(WarningTopic.paths, ["/warn"])

    def test_claude_nonzero_exit_is_a_failure(self):
        self.set_claude({"type": "result", "is_error": False, "result": "x"}, code=2)
        job = self.add_agent("--attempts", "1")
        self.deferred("run-due")
        self.assertEqual(self.meta(job)["state"], "failed")

    def test_add_agent_rejects_bad_input(self):
        for args in (("--cwd", str(self.tmp / "nope")), ("--topic-var", "lower"), ("--effort", "huge")):
            with self.subTest(args=args):
                result = self.deferred("add-agent", *args, "+1d", "x", str(self.prompt), check=False)
                self.assertNotEqual(result.returncode, 0)
        result = self.deferred("add-agent", "+1d", "x", str(self.tmp / "missing"), check=False)
        self.assertNotEqual(result.returncode, 0)
