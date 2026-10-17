"""Session restore planning, argument rewriting and duplicate guards."""

from dataclasses import asdict
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[2] / "shared" / "sh" / "llm_restore.py"
spec = importlib.util.spec_from_file_location("llm_restore", SCRIPT)
lr = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = lr  # dataclasses resolve annotations through it
spec.loader.exec_module(lr)

A, B, C = (f"0000000{i}-0000-0000-0000-000000000000" for i in "abc")


def session(tool, sid, cwd="/w", **extra):
    return lr.Session(tool=tool, id=sid, cwd=cwd, title=extra.pop("title", ""), command=f"cmd {sid}", **extra)


def codex_tui(*argv, cwd="/w"):
    return lr.codex_tui(lr.Proc(1, ["codex", *argv], cwd))


class ClaudeOptions(unittest.TestCase):
    def test_keeps_permissions_and_settings_drops_resume_and_prompt(self):
        argv = ["claude", "--permission-mode", "bypassPermissions", "--settings", '{"env":{"A":"b c"}}',
                "--resume", A, "--remote-control", "My name", "fix the bug"]
        self.assertEqual(lr.claude_options(argv), [
            "--permission-mode", "bypassPermissions", "--settings", '{"env":{"A":"b c"}}',
            "--remote-control", "My name"])

    def test_bare_resume_picker_and_continue_are_dropped(self):
        self.assertEqual(lr.claude_options(["claude", "--resume", "--model", "opus", "-c"]), ["--model", "opus"])

    def test_variadic_add_dir_kept_session_id_dropped(self):
        argv = ["claude", "--add-dir", "/a", "/b", "--session-id", A, "--dangerously-skip-permissions"]
        self.assertEqual(lr.claude_options(argv), ["--add-dir", "/a", "/b", "--dangerously-skip-permissions"])

    def test_attached_values_follow_the_kept_list(self):
        self.assertEqual(lr.claude_options(["claude", "--model=opus", "--resume=" + A]), ["--model=opus"])


class CodexTui(unittest.TestCase):
    def test_resume_keeps_global_options_and_requested_id(self):
        tui = codex_tui("--no-alt-screen", "--ask-for-approval", "never", "resume", A)
        self.assertEqual((tui.options, tui.requested), (["--no-alt-screen", "--ask-for-approval", "never"], A))

    def test_prompt_and_cd_dropped(self):
        tui = codex_tui("-C", "/x", "-m", "gpt", "do it")
        self.assertEqual((tui.options, tui.requested), (["-m", "gpt"], None))

    def test_daemon_and_exec_are_not_tuis(self):
        self.assertIsNone(codex_tui("app-server", "--listen", "unix://"))
        self.assertIsNone(codex_tui("-m", "gpt", "exec", "hi"))

    def test_command_bypasses_shell_wrappers_and_quotes(self):
        self.assertEqual(lr.shell_command("/a b", ["codex", "resume", A]),
                         f"cd -- '/a b' && command codex resume {A}")


class CodexAssignment(unittest.TestCase):
    threads = {A: {"cwd": "/one", "updated": 3}, B: {"cwd": "/two", "updated": 2}, C: {"cwd": "/two", "updated": 1}}

    def tui(self, pid, cwd, requested=None):
        return lr.CodexTui(pid, cwd, [], requested)

    def test_embedded_tui_uses_its_own_rollout(self):
        self.assertEqual(lr.assign_codex_threads([self.tui(1, "/x")], {1: {C}}, self.threads), {1: C})

    def test_daemon_threads_match_by_id_then_folder(self):
        tuis = [self.tui(1, "/two", C), self.tui(2, "/two"), self.tui(3, "/one")]
        got = lr.assign_codex_threads(tuis, {99: {A, B, C}}, self.threads)
        self.assertEqual(got, {1: C, 2: B, 3: A})

    def test_ambiguous_tuis_stay_unassigned(self):
        tuis = [self.tui(1, "/elsewhere"), self.tui(2, "/elsewhere")]
        self.assertEqual(lr.assign_codex_threads(tuis, {99: {A, B}}, self.threads), {})

    def test_single_leftover_pairs_by_elimination(self):
        self.assertEqual(lr.assign_codex_threads([self.tui(1, "/moved")], {99: {A}}, self.threads), {1: A})


class Plan(unittest.TestCase):
    def snaps(self):
        return [
            {"boot": "now", "taken_at": 30, "sessions": [session("claude", A)]},
            {"boot": "prev", "taken_at": 20, "sessions": [session("claude", A), session("codex", B),
                                                          session("claude", C, managed="llm-switchboard")]},
            {"boot": "older", "taken_at": 10, "sessions": [session("claude", C)]},
        ]

    def test_current_plus_previous_boot_without_duplicates_or_managed(self):
        plan, sources = lr.restore_plan("now", self.snaps(), [])
        self.assertEqual([s.id for s in plan], [A, B])
        self.assertEqual(sources, ["now", "prev"])

    def test_consumed_previous_boot_is_dropped(self):
        plan, sources = lr.restore_plan("now", self.snaps(), ["prev"])
        self.assertEqual(([s.id for s in plan], sources), ([A], ["now"]))

    def test_first_run_after_reboot_uses_last_boot(self):
        plan, sources = lr.restore_plan("new", self.snaps(), [])
        self.assertEqual(([s.id for s in plan], sources), ([A], ["now"]))

    def test_older_snapshot_records_still_load(self):
        old = {**asdict(session("claude", A)), "pid": 123}
        self.assertEqual(lr.Session.from_json(old), session("claude", A))


class RecentlyClosed(unittest.TestCase):
    def test_sessions_closed_at_shutdown_stay_listed(self):
        before = lr.merge_seen([], [session("claude", A), session("codex", B)], now=1000.0)
        after = lr.merge_seen(before, [], now=1060.0)  # every terminal closed
        self.assertEqual([(s.id, s.last_seen) for s in after], [(A, 1000.0), (B, 1000.0)])

    def test_closed_sessions_expire_after_the_window(self):
        listed = lr.merge_seen([session("claude", A, last_seen=1000.0)], [], now=1000.0 + lr.RECENT_S)
        self.assertEqual(listed, [])

    def test_open_session_is_refreshed_with_its_current_command(self):
        stale = session("claude", A, last_seen=1000.0)
        fresh = lr.Session("claude", A, "/new", "renamed", "cmd new")
        self.assertEqual(lr.merge_seen([stale], [fresh], now=1500.0), [lr.replace(fresh, last_seen=1500.0)])


class Choose(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        for name, value in (("CLAUDE_PROJECTS", root / "projects"), ("CODEX_HOME", root / "codex"),
                            ("SWITCHBOARD_SESSIONS", root / "sb.json")):
            patcher = patch.object(lr, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        for sid in (A, B, C):
            (root / "projects/-w").mkdir(parents=True, exist_ok=True)
            (root / f"projects/-w/{sid}.jsonl").write_text("{}\n")
        (root / "sb.json").write_text(json.dumps({"sessions": {C: {"enabled": True}}}))

    def test_skips_open_kept_recent_and_missing(self):
        missing = "0000000d-0000-0000-0000-000000000000"
        plan = [session("claude", s) for s in (A, B, C, missing)]
        todo, skipped = lr.choose(plan, [session("claude", A)], {"claude:" + B: 995.0}, False, 1000.0)
        self.assertEqual(todo, [])
        self.assertEqual([line.split(": ", 1)[1] for line in skipped], [
            "already open", "launched under 120s ago (--force to reopen)",
            "kept running by llm-switchboard", "transcript not found"])

    def test_force_overrides_only_the_recent_launch_guard(self):
        todo, _ = lr.choose([session("claude", A), session("claude", B)], [session("claude", A)],
                            {"claude:" + B: 995.0}, True, 1000.0)
        self.assertEqual([s.id for s in todo], [B])

    def test_open_session_of_other_tool_does_not_block(self):
        todo, _ = lr.choose([session("claude", A)], [session("codex", A)], {}, False, 1000.0)
        self.assertEqual(len(todo), 1)


class PiTabs(unittest.TestCase):
    def test_command_survives_local_and_remote_shell_parsing(self):
        command = """cd -- '/home/pi/it'"'"'s' && command claude --settings '{"env":{"A":"b c"}}' --resume x"""
        local = shlex.split(lr.pi_tab_command(command))
        self.assertEqual(local[:3], ["ssh", "-t", lr.PI_HOST])
        remote = shlex.split(local[3])
        self.assertEqual(remote[:2], ["bash", "-lic"])
        self.assertEqual(remote[2], command + "; exec bash -il")


class State(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        for name, value in (("STATE", self.root), ("LOCK", self.root / "lock"),
                            ("RESTORE_SCRIPT", self.root / "restore")):
            patcher = patch.object(lr, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_busy_lock_is_reported_not_raised(self):
        with lr.state_lock() as outer, lr.state_lock(wait=False) as inner:
            self.assertEqual((outer, inner), (True, False))
        with lr.state_lock(wait=False) as again:
            self.assertTrue(again)

    def test_emit_prints_sessions_to_open_and_reports_on_stderr(self):
        plan = [session("claude", A), session("claude", B)]
        stdin = "\n".join(json.dumps(lr.asdict(s)) for s in plan) + "\n"
        out, err = io.StringIO(), io.StringIO()
        with patch.object(lr, "live_sessions", return_value=([session("claude", A)], [])), \
                patch.object(lr, "_session_exists", return_value=True), \
                patch.object(lr, "_switchboard_kept", return_value=set()), \
                patch.object(lr, "LAUNCHED", self.root / "launched.json"), \
                patch.object(lr, "CONSUMED", self.root / "consumed.json"), \
                patch.object(lr, "BOOTS", self.root / "boots"), \
                patch.object(lr, "boot_id", return_value="now"), \
                patch.object(lr, "open_terminals") as opened, \
                patch("sys.stdin", io.StringIO(stdin)), patch("sys.stdout", out), patch("sys.stderr", err):
            lr.apply("prev", dry_run=False, force=False, emit=True)
        opened.assert_not_called()
        self.assertEqual([lr.Session.from_json(json.loads(line)) for line in out.getvalue().splitlines()],
                         [session("claude", B)])
        self.assertIn("already open", err.getvalue())
        self.assertIn("claude:" + B, json.loads((self.root / "launched.json").read_text()))
        self.assertEqual(json.loads((self.root / "consumed.json").read_text()), ["prev"])

    def test_script_feeds_its_sessions_back_to_apply(self):
        plan = [session("claude", A, title="it's \"quoted\""), session("codex", B)]
        lr.write_restore_script(plan, ["prev", "now"], ["codex pid 1: unknown"])
        script = self.root / "restore"
        self.assertTrue(os.access(script, os.X_OK))
        fake = self.root / "fake"
        fake.write_text(re.sub(r"^exec .*<<'SESSIONS'$", "exec /bin/cat <<'SESSIONS'",
                               script.read_text(), flags=re.M))
        out = subprocess.run(["/bin/sh", str(fake)], capture_output=True, text=True, check=True).stdout
        self.assertEqual([lr.Session.from_json(json.loads(line)) for line in out.splitlines()], plan)
        self.assertIn("--apply prev,now", script.read_text())


if __name__ == "__main__":
    unittest.main()
