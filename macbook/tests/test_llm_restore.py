"""Session restore planning, argument rewriting and duplicate guards."""

import importlib.util
import io
import json
import os
import re
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[2] / "shared" / "sh" / "llm_restore.py"
spec = importlib.util.spec_from_file_location("llm_restore", SCRIPT)
lr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lr)

A, B, C = (f"0000000{i}-0000-0000-0000-000000000000" for i in "abc")


def session(tool, sid, cwd="/w", **extra):
    return {"tool": tool, "id": sid, "cwd": cwd, "title": "", "command": f"cmd {sid}", "managed": None, **extra}


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


class CodexOptions(unittest.TestCase):
    def test_tui_resume_keeps_global_options(self):
        argv = ["codex", "--no-alt-screen", "--ask-for-approval", "never", "resume", A]
        self.assertEqual(lr.codex_options(argv), ["--no-alt-screen", "--ask-for-approval", "never"])
        self.assertEqual(lr.codex_requested_id(argv), A)

    def test_prompt_and_cd_dropped(self):
        self.assertEqual(lr.codex_options(["codex", "-C", "/x", "-m", "gpt", "do it"]), ["-m", "gpt"])

    def test_daemon_and_exec_are_not_tuis(self):
        self.assertIsNone(lr.codex_options(["codex", "app-server", "--listen", "unix://"]))
        self.assertIsNone(lr.codex_options(["codex", "-m", "gpt", "exec", "hi"]))

    def test_command_bypasses_shell_wrappers_and_quotes(self):
        self.assertEqual(lr.shell_command("/a b", ["codex", "resume", A]),
                         f"cd -- '/a b' && command codex resume {A}")


class CodexAssignment(unittest.TestCase):
    threads = {A: {"cwd": "/one", "updated": 3}, B: {"cwd": "/two", "updated": 2}, C: {"cwd": "/two", "updated": 1}}

    def tui(self, pid, cwd, *argv):
        return {"pid": pid, "cwd": cwd, "argv": ["codex", *argv]}

    def test_embedded_tui_uses_its_own_rollout(self):
        self.assertEqual(lr.assign_codex_threads([self.tui(1, "/x")], {1: {C}}, self.threads), {1: C})

    def test_daemon_threads_match_by_id_then_folder(self):
        tuis = [self.tui(1, "/two", "resume", C), self.tui(2, "/two"), self.tui(3, "/one")]
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
        self.assertEqual([s["id"] for s in plan], [A, B])
        self.assertEqual(sources, ["now", "prev"])

    def test_consumed_previous_boot_is_dropped(self):
        plan, sources = lr.restore_plan("now", self.snaps(), ["prev"])
        self.assertEqual(([s["id"] for s in plan], sources), ([A], ["now"]))

    def test_first_run_after_reboot_uses_last_boot(self):
        plan, sources = lr.restore_plan("new", self.snaps(), [])
        self.assertEqual(([s["id"] for s in plan], sources), ([A], ["now"]))


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
        plan = [session("claude", s) for s in (A, B, C, missing)] + [session("claude", "e")]
        todo, skipped = lr.choose(plan, [session("claude", A)], {"claude:" + B: 995.0}, False, 1000.0)
        self.assertEqual([s["id"] for s in todo], [])
        self.assertEqual([line.split(": ", 1)[1] for line in skipped], [
            "already open", "launched under 120s ago (--force to reopen)",
            "kept running by llm-switchboard", "transcript not found", "transcript not found"])

    def test_force_overrides_only_the_recent_launch_guard(self):
        todo, _ = lr.choose([session("claude", A), session("claude", B)], [session("claude", A)],
                            {"claude:" + B: 995.0}, True, 1000.0)
        self.assertEqual([s["id"] for s in todo], [B])

    def test_open_session_of_other_tool_does_not_block(self):
        todo, _ = lr.choose([session("claude", A)], [session("codex", A)], {}, False, 1000.0)
        self.assertEqual(len(todo), 1)


class GeneratedScript(unittest.TestCase):
    def test_script_feeds_its_sessions_back_to_apply(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(lr, "STATE", Path(tmp)), \
                patch.object(lr, "RESTORE_SCRIPT", Path(tmp) / "restore"):
            plan = [session("claude", A, title="it's \"quoted\""), session("codex", B)]
            lr.write_restore_script(plan, ["prev", "now"], ["codex pid 1: unknown"])
            script = Path(tmp) / "restore"
            self.assertTrue(os.access(script, os.X_OK))
            fake = Path(tmp) / "fake"
            fake.write_text(re.sub(r"^exec .*<<'SESSIONS'$", "exec /bin/cat <<'SESSIONS'",
                                   script.read_text(), flags=re.M))
            out = subprocess.run(["/bin/sh", str(fake)], capture_output=True, text=True, check=True).stdout
            self.assertEqual([json.loads(line) for line in io.StringIO(out)], plan)
            self.assertIn("--apply prev,now", script.read_text())


if __name__ == "__main__":
    unittest.main()
