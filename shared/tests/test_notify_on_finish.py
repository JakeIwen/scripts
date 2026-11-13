"""Exercise the +n hook through its public CLI without sending phone alerts."""

import concurrent.futures
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "sh/notify_on_finish.py"
# Task entries as Claude Code 2.1.321 labels them in the Stop payload.
AGENT = {"id": "a1", "type": "subagent", "status": "running", "description": "worker"}
CLOUD = {"id": "r1", "type": "cloud session", "status": "running", "description": "remote"}
SHELL = {"id": "b1", "type": "shell", "status": "running", "command": "make"}


class NotifyOnFinishTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.state = self.root / "state"
        self.log = self.root / "sent.jsonl"
        self.sender = self.root / "sender.py"
        self.sender.write_text(
            "import json, os, sys\n"
            "from pathlib import Path\n"
            "with Path(os.environ['NOTIFY_ON_FINISH_LOG']).open('a') as handle:\n"
            "    handle.write(json.dumps(sys.argv[1:]) + '\\n')\n",
            encoding="utf-8",
        )
        self.env = {**os.environ, "NOTIFY_ON_FINISH_STATE_DIR": str(self.state),
                    "NOTIFY_ON_FINISH_SENDER": str(self.sender),
                    "NOTIFY_ON_FINISH_LOG": str(self.log)}

    def invoke(self, event):
        stdin = json.dumps(event)
        result = subprocess.run([sys.executable, "-I", str(SCRIPT), "--hook"],
                                input=stdin, capture_output=True, text=True,
                                env=self.env, cwd=self.root, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout) if result.stdout else None

    def arm(self, prompt="do x +n", session="session-1", cwd=None):
        return self.invoke({"hook_event_name": "UserPromptSubmit", "session_id": session,
                            "prompt": prompt, "cwd": str(cwd or self.root)})

    def stop(self, message="**Done** with `task`", session="session-1", **fields):
        return self.invoke({"hook_event_name": "Stop", "session_id": session,
                            "last_assistant_message": message, **fields})

    def deliveries(self):
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text().splitlines() if line]

    def wait_for_delivery(self):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if self.deliveries():
                return self.deliveries()
            time.sleep(0.01)
        self.fail("detached sender did not write its log")

    def test_standalone_tokens_arm_without_blocking_bare_prompt(self):
        for index, prompt in enumerate(("+n", "do x +n", "+n do x", "do\n+n\t")):
            with self.subTest(prompt=prompt):
                output = self.arm(prompt, session=str(index))
                self.assertNotIn("decision", output)
                context = output["hookSpecificOutput"]
                self.assertEqual(context["hookEventName"], "UserPromptSubmit")
                self.assertIn("Do not send", context["additionalContext"])
                self.assertIn(f"/usr/bin/python3 {SCRIPT.resolve()} hold", context["additionalContext"])
                self.assertIn("last tool call", context["additionalContext"])
                self.assertIn("task stalls on Jacob mid-turn", context["additionalContext"])
        flags = list(self.state.glob("*.json"))
        self.assertEqual(len(flags), 4)
        saved = json.loads(flags[0].read_text())
        self.assertEqual(saved["cwd"], str(self.root))
        self.assertLess(abs(time.time() - saved["time"]), 5)
        self.assertEqual(self.deliveries(), [])

    def test_substrings_do_not_arm(self):
        for prompt in ("+nx", "a+n", "do +n,", "(+n)", "do x", ""):
            with self.subTest(prompt=prompt):
                self.assertIsNone(self.arm(prompt))
        self.assertFalse(self.state.exists())

    def test_stop_sends_once_clears_flag_and_strips_markdown(self):
        self.arm()
        self.assertIsNone(self.stop("\n  **Done** with `task` and _checks_\nNext line"))
        self.assertEqual(self.wait_for_delivery(), [
            ["--send", self.root.name, "Done with task and checks", "default"],
        ])
        self.assertEqual(list(self.state.glob("*.json")), [])
        self.assertIsNone(self.stop())
        self.assertEqual(len(self.deliveries()), 1)

    def test_stop_without_flag_does_not_send(self):
        self.assertIsNone(self.stop())
        self.assertEqual(self.deliveries(), [])
        self.assertFalse(self.state.exists())

    def test_flags_are_session_scoped_and_session_can_rearm(self):
        self.arm(session="requested")
        self.stop(session="unrequested")
        self.assertEqual(self.deliveries(), [])
        self.stop(session="requested")
        self.wait_for_delivery()
        self.arm(session="requested")
        self.stop(session="requested")
        deadline = time.monotonic() + 3
        while len(self.deliveries()) < 2 and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(len(self.deliveries()), 2)

    def test_concurrent_stops_claim_one_notification(self):
        self.arm()
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda _: self.stop(), range(4)))
        self.wait_for_delivery()
        self.assertEqual(len(self.deliveries()), 1)
        self.assertEqual(list(self.state.glob("*.json")), [])

    def test_git_root_names_project_not_nested_cwd(self):
        repo = self.root / "project"
        repo.mkdir()
        subprocess.run(["/usr/bin/git", "init", "-q", str(repo)], check=True)
        nested = repo / "nested"
        nested.mkdir()
        self.arm(cwd=nested)
        self.stop()
        self.assertEqual(self.wait_for_delivery()[0][1], "project")

    def test_message_preview_caps_length_and_preserves_identifier_underscores(self):
        self.arm()
        self.stop("test_data " + "x" * 250)
        preview = self.wait_for_delivery()[0][2]
        self.assertTrue(preview.startswith("test_data "))
        self.assertEqual(len(preview), 200)

    def test_empty_or_missing_message_falls_back(self):
        self.arm()
        self.stop(message=None)
        self.assertEqual(self.wait_for_delivery()[0][2], "turn finished")

    def test_flags_older_than_two_days_are_pruned_not_sent(self):
        self.arm()
        old = list(self.state.glob("*.json"))[0]
        timestamp = time.time() - 2 * 86400 - 10
        os.utime(old, (timestamp, timestamp))
        self.stop()
        self.assertFalse(old.exists())
        self.assertEqual(self.deliveries(), [])

    def test_invalid_inputs_pass_without_blocking(self):
        for event in ([], None, {}, {"session_id": "s", "hook_event_name": "Other"},
                      {"session_id": "s", "hook_event_name": "UserPromptSubmit", "prompt": []}):
            with self.subTest(event=event):
                self.assertIsNone(self.invoke(event))
        result = subprocess.run([sys.executable, "-I", str(SCRIPT), "--hook"],
                                input="not json", capture_output=True, text=True,
                                env=self.env, cwd=self.root, timeout=5)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")

    def test_unwritable_state_fails_open_without_false_context(self):
        self.state.write_text("not a directory")
        self.assertIsNone(self.arm())
        self.assertEqual(self.deliveries(), [])

    def test_corrupt_flag_is_consumed_without_blocking(self):
        self.arm()
        flag = list(self.state.glob("*.json"))[0]
        flag.write_text("not json")
        self.assertIsNone(self.stop())
        self.assertEqual(list(self.state.glob("*.json")), [])
        self.assertEqual(self.deliveries(), [])

    def hold(self, session="session-1", codex=False):
        env = dict(self.env)
        env.pop("CLAUDE_CODE_SESSION_ID", None)
        env.pop("CODEX_THREAD_ID", None)
        if session is not None:
            env["CODEX_THREAD_ID" if codex else "CLAUDE_CODE_SESSION_ID"] = session
        if codex:
            env["CLAUDE_CODE_SESSION_ID"] = "inherited-claude-session"
        return subprocess.run([sys.executable, "-I", str(SCRIPT), "hold"],
                              env=env, cwd=self.root, capture_output=True,
                              text=True, timeout=5)

    def test_held_stop_keeps_flag_then_next_stop_sends(self):
        self.arm()
        result = self.hold()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIsNone(self.stop(background_tasks=[SHELL]))
        self.assertEqual(self.deliveries(), [])
        flag = list(self.state.glob("*.json"))[0]
        self.assertFalse(json.loads(flag.read_text()).get("held", False))
        self.assertIsNone(self.stop(background_tasks=[]))
        self.assertEqual(len(self.wait_for_delivery()), 1)
        self.assertEqual(list(self.state.glob("*.json")), [])

    def test_each_notification_turn_can_hold_again(self):
        # A repeated hold replaces the remembered snapshot.
        self.arm()
        for shell in ("b1", "b2"):
            self.assertEqual(self.hold().returncode, 0)
            self.stop(background_tasks=[dict(SHELL, id=shell)])
            self.assertEqual(self.deliveries(), [])
            self.assertEqual(len(list(self.state.glob("*.json"))), 1)
        self.stop(background_tasks=[SHELL])  # b1 is no longer the held task
        self.assertEqual(len(self.wait_for_delivery()), 1)

    def test_hold_uses_codex_thread_id(self):
        self.arm(session="codex-session")
        result = self.hold(session="codex-session", codex=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.stop(session="codex-session", turn_id="t1")
        self.assertEqual(self.deliveries(), [])
        self.assertEqual(len(list(self.state.glob("*.json"))), 1)
        self.stop(session="codex-session", turn_id="t2")
        self.assertEqual(len(self.wait_for_delivery()), 1)

    def test_entry_without_an_id_makes_the_snapshot_unknown(self):
        # Re-check finding (a): a running shell with no id shortened the list.
        self.arm()
        self.assertEqual(self.hold().returncode, 0)
        self.stop(background_tasks=[SHELL])
        nameless = {key: value for key, value in SHELL.items() if key != "id"}
        self.stop(stop_hook_active=True, background_tasks=[nameless])
        self.stop(background_tasks=[dict(SHELL, status=None)])
        self.assertEqual(len(self.flags()), 1)
        self.assertEqual(self.deliveries(), [])

    def test_claude_hold_without_a_snapshot_waits_for_a_readable_one(self):
        # Re-check finding (b): two list-less Stops released the hold.
        self.arm()
        self.assertEqual(self.hold().returncode, 0)
        self.stop()
        self.stop()
        self.assertEqual(len(self.flags()), 1)
        self.assertEqual(self.deliveries(), [])
        # The first readable list that shows nothing running sends.
        self.stop("Finished.", background_tasks=[])
        self.assertEqual(self.wait_for_delivery()[0][2], "Finished.")

    def test_pending_hold_captures_the_first_readable_snapshot(self):
        # Re-check finding (c): a continuation's running shell didn't hold.
        self.arm()
        self.assertEqual(self.hold().returncode, 0)
        self.stop()
        self.stop(stop_hook_active=True, background_tasks=[SHELL])
        self.stop(background_tasks=[SHELL])
        self.assertEqual(len(self.flags()), 1)
        self.assertEqual(self.deliveries(), [])
        self.stop("Shell done.", background_tasks=[])
        self.assertEqual(self.wait_for_delivery()[0][2], "Shell done.")

    def test_hold_with_nothing_running_sends_at_that_stop(self):
        # Re-check finding 2: an empty snapshot left an empty hold forever.
        self.arm()
        self.assertEqual(self.hold().returncode, 0)
        self.stop("Already done.", background_tasks=[])
        self.assertEqual(self.wait_for_delivery()[0][2], "Already done.")
        self.assertEqual(self.flags(), [])

    def flags(self):
        return list(self.state.glob("*.json"))

    def test_hold_survives_the_continuation_of_a_stop_another_hook_blocked(self):
        # The audit's repro: the leftover-shells guard blocks the held Stop,
        # and the continuation's Stop used to send while the work still ran.
        self.arm()
        self.assertEqual(self.hold().returncode, 0)
        workers = [AGENT]
        self.stop("Workers launched; waiting.", stop_hook_active=False, background_tasks=workers)
        self.stop("Stopped shell bx1; keeping workers.", stop_hook_active=True,
                  background_tasks=workers)
        self.stop("Still waiting.", stop_hook_active=True, background_tasks=workers)
        self.assertEqual(len(self.flags()), 1)
        self.assertEqual(self.deliveries(), [])
        # The task-notification turn after the work ends sends, once.
        self.stop("All workers finished.", stop_hook_active=False, background_tasks=[])
        self.assertEqual(self.wait_for_delivery()[0][2], "All workers finished.")
        self.assertEqual(self.flags(), [])
        self.stop(stop_hook_active=True, background_tasks=[])
        self.assertEqual(len(self.deliveries()), 1)

    def test_held_work_finished_inside_the_cleanup_continuation_sends(self):
        # Review finding: the continuation finished the work and no later
        # task-notification turn came, so the alert was never sent.
        self.arm()
        self.assertEqual(self.hold().returncode, 0)
        self.stop("Waiting on the build shell.", stop_hook_active=False,
                  background_tasks=[SHELL])
        self.assertEqual(len(self.flags()), 1)
        self.stop("Build done; nothing left.", stop_hook_active=True, background_tasks=[])
        self.assertEqual(self.wait_for_delivery()[0][2], "Build done; nothing left.")
        self.assertEqual(self.flags(), [])

    def test_codex_hold_keeps_through_continuations_and_sends_at_the_next_stop(self):
        # Codex lists no background work: a held turn's continuations keep
        # the flag and the next Stop outside a continuation sends.
        self.arm(session="codex-session")
        self.assertEqual(self.hold(session="codex-session", codex=True).returncode, 0)
        for active in (False, True, True):
            self.stop(session="codex-session", turn_id="t1", stop_hook_active=active)
        self.assertEqual(len(self.flags()), 1)
        self.assertEqual(self.deliveries(), [])
        self.stop("Done.", session="codex-session", turn_id="t2", stop_hook_active=False)
        self.assertEqual(len(self.wait_for_delivery()), 1)
        self.assertEqual(self.flags(), [])

    def test_hold_over_a_shell_outlasts_a_blocked_continuation(self):
        # Re-check finding: the continuation sent while the held shell still ran.
        self.arm()
        self.assertEqual(self.hold().returncode, 0)
        self.stop("Build running.", stop_hook_active=False, background_tasks=[SHELL])
        self.stop("Cleanup after a block.", stop_hook_active=True, background_tasks=[SHELL])
        self.assertEqual(len(self.flags()), 1)
        self.assertEqual(self.deliveries(), [])

    def test_hold_keeps_through_a_continuation_with_no_task_list(self):
        self.arm()
        self.assertEqual(self.hold().returncode, 0)
        self.stop("Build running.", stop_hook_active=False, background_tasks=[SHELL])
        self.stop("Cleanup.", stop_hook_active=True)
        self.stop("Cleanup.", stop_hook_active=False, background_tasks="not a list")
        self.assertEqual(len(self.flags()), 1)
        self.assertEqual(self.deliveries(), [])

    def test_held_shell_that_ends_sends_once(self):
        self.arm()
        self.assertEqual(self.hold().returncode, 0)
        self.stop("Build running.", stop_hook_active=False, background_tasks=[SHELL])
        self.stop("Still building.", stop_hook_active=False, background_tasks=[SHELL])
        self.assertEqual(self.deliveries(), [])
        self.stop("Build finished.", stop_hook_active=False, background_tasks=[])
        self.assertEqual(self.wait_for_delivery()[0][2], "Build finished.")
        self.stop(stop_hook_active=False, background_tasks=[])
        self.assertEqual(len(self.deliveries()), 1)
        self.assertEqual(self.flags(), [])

    def test_hold_over_a_shell_and_a_subagent_waits_for_both(self):
        self.arm()
        self.assertEqual(self.hold().returncode, 0)
        self.stop("Both running.", stop_hook_active=False, background_tasks=[SHELL, AGENT])
        self.stop("Shell done.", stop_hook_active=False, background_tasks=[AGENT])
        self.assertEqual(self.deliveries(), [])
        self.assertEqual(len(self.flags()), 1)
        # Only tasks live at the held Stop hold it: a shell started later
        # (b2) does not, and shells never defer on their own.
        self.stop("Worker done.", stop_hook_active=False, background_tasks=[dict(SHELL, id="b2")])
        self.assertEqual(self.wait_for_delivery()[0][2], "Worker done.")
        self.assertEqual(self.flags(), [])

    def test_running_cloud_session_defers_like_a_local_worker(self):
        # Review finding: Claude Code labels remote_agent tasks "cloud session".
        self.arm()
        self.assertEqual(self.hold().returncode, 0)
        self.stop("Launched.", stop_hook_active=False, background_tasks=[AGENT, CLOUD])
        self.stop("Local worker done.", stop_hook_active=False, background_tasks=[CLOUD])
        self.stop("Cleanup.", stop_hook_active=True, background_tasks=[CLOUD])
        self.assertEqual(len(self.flags()), 1)
        self.assertEqual(self.deliveries(), [])
        self.stop("Cloud session done.", stop_hook_active=False, background_tasks=[])
        self.assertEqual(self.wait_for_delivery()[0][2], "Cloud session done.")

    def test_unheld_continuation_without_background_work_sends_as_before(self):
        self.arm()
        self.stop(stop_hook_active=True, background_tasks=[])
        self.assertEqual(len(self.wait_for_delivery()), 1)

    def test_running_subagents_and_workflows_defer_until_the_last_ends(self):
        self.arm()
        agent = {"id": "a1", "type": "subagent", "status": "running", "description": "worker"}
        workflow = {"id": "w1", "type": "workflow", "status": "pending", "description": "flow"}
        self.stop("Launched.", stop_hook_active=False, background_tasks=[agent, workflow])
        self.stop("Blocked continuation.", stop_hook_active=True, background_tasks=[agent, workflow])
        self.stop("One worker done.", stop_hook_active=False, background_tasks=[workflow])
        self.assertEqual(len(self.flags()), 1)
        self.assertEqual(self.deliveries(), [])
        self.stop("Everything finished.", stop_hook_active=False, background_tasks=[])
        self.assertEqual(self.wait_for_delivery()[0][2], "Everything finished.")
        self.assertEqual(self.flags(), [])

    def test_shells_monitors_internal_and_malformed_tasks_do_not_defer(self):
        lists = (
            [{"id": "b1", "type": "shell", "status": "running", "command": "npm run dev"},
             {"id": "m1", "type": "monitor", "status": "running"},
             {"id": "d1", "type": "dream", "status": "running"},
             {"id": "t1", "type": "teammate", "status": "running"},
             {"id": "s1", "type": "auto-mode scan", "status": "pending"},
             {"id": "n1", "type": "a label added later", "status": "running"},
             {"id": "a1", "type": "subagent", "status": "completed"}],
            "subagent", [None, "subagent"], {"type": "subagent", "status": "running"},
        )
        for index, tasks in enumerate(lists):
            with self.subTest(tasks=tasks):
                self.arm(session=f"s{index}")
                self.stop(session=f"s{index}", background_tasks=tasks)
                deadline = time.monotonic() + 3
                while len(self.deliveries()) <= index and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertEqual(len(self.deliveries()), index + 1)

    def test_hold_without_flag_or_session_errors_clearly(self):
        result = self.hold()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("no +n flag exists", result.stderr)
        result = self.hold(session=None)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("CLAUDE_CODE_SESSION_ID or CODEX_THREAD_ID", result.stderr)

    def test_sender_is_detached_and_cannot_stall_stop(self):
        self.sender.write_text("import time\ntime.sleep(1.5)\n" + self.sender.read_text())
        self.arm()
        started = time.monotonic()
        self.stop()
        self.assertLess(time.monotonic() - started, 1)
        self.wait_for_delivery()


if __name__ == "__main__":
    unittest.main()
