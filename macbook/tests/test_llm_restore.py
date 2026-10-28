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


class Launcher(unittest.TestCase):
    """Restored sessions start the way the shell's claudep/codexp start them."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.run_path = Path(tmp.name) / "run"
        self.run_path.write_text("#!/bin/sh\n")

    def launchers(self, is_mac, executable=True):
        self.run_path.chmod(0o755 if executable else 0o644)
        with patch.object(lr, "IS_MAC", is_mac), patch.object(lr, "CODEX_RETRY", self.run_path):
            return lr.launcher(lr.CLAUDE), lr.launcher(lr.CODEX)

    def test_mac_codex_runs_under_the_retry_wrapper(self):
        self.assertEqual(self.launchers(True), ([], [str(self.run_path), "--"]))

    def test_pi_unsets_display_for_both_and_wraps_codex(self):
        env = ["env", "-u", "DISPLAY"]
        self.assertEqual(self.launchers(False), (env, [*env, str(self.run_path), "--"]))

    def test_plain_codex_without_an_installed_wrapper(self):
        self.assertEqual(self.launchers(True, executable=False), ([], []))
        self.assertEqual(self.launchers(False, executable=False)[1], ["env", "-u", "DISPLAY"])


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


class Display(unittest.TestCase):
    def test_bypass_claude_resumes_print_as_claudepr(self):
        mac = lr.shell_command("/w x", ["claude", "--permission-mode", "bypassPermissions", "--resume", A])
        pi = lr.shell_command("/w", ["env", "-u", "DISPLAY", "claude", "--permission-mode", "bypassPermissions",
                                     "--model", "opus", "--resume", A])
        codex = lr.shell_command("/w", ["codex", "resume", A])
        prompting = lr.shell_command("/w", ["claude", "--resume", A])
        self.assertEqual([lr.display_command(c) for c in (mac, pi, codex, prompting)], [
            f"cd -- '/w x' && claudepr {A}", f"cd -- /w && claudepr {A} --model opus", codex, prompting])

    def test_claude_name_is_the_last_rename_else_its_latest_title(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(lr, "CLAUDE_PROJECTS", Path(tmp)):
            (Path(tmp) / "-w").mkdir()
            transcript = Path(tmp) / f"-w/{A}.jsonl"
            events = [{"type": "ai-title", "aiTitle": "First"}, {"type": "ai-title", "aiTitle": "Fix\nthe  tabs"}]
            transcript.write_text("".join(json.dumps(e) + "\n" for e in events))
            self.assertEqual(lr.session_name(session("claude", A)), "Fix the tabs")
            with transcript.open("a") as f:
                f.write(json.dumps({"type": "custom-title", "customTitle": "Mine"}) + "\n")
                f.write(json.dumps({"type": "ai-title", "aiTitle": "Later"}) + "\n")
            self.assertEqual(lr.session_name(session("claude", A)), "Mine")
            self.assertEqual((lr.session_name(session("claude", B)), lr.session_name(session("codex", A))), ("", ""))


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
        self.assertEqual([i.session.id for i in plan], [A, B])
        self.assertEqual(sources, ["now", "prev"])

    def test_consumed_previous_boot_is_dropped(self):
        plan, sources = lr.restore_plan("now", self.snaps(), ["prev"])
        self.assertEqual(([i.session.id for i in plan], sources), ([A], ["now"]))

    def test_first_run_after_reboot_uses_last_boot(self):
        plan, sources = lr.restore_plan("new", self.snaps(), [])
        self.assertEqual(([i.session.id for i in plan], sources), ([A], ["now"]))

    def test_entries_record_how_long_before_the_snapshot_they_closed(self):
        snap = {"boot": "now", "taken_at": 5000.0, "sessions": [
            session("claude", A, last_seen=5000.0), session("codex", B, last_seen=2600.0)]}
        plan, _ = lr.restore_plan("now", [snap], [])
        self.assertEqual([i.closed_for for i in plan], [0.0, 2400.0])
        self.assertTrue(plan[0].describe().startswith("open at"))
        self.assertTrue(plan[1].describe().startswith("closed 40m before the last snapshot"))

    def test_window_flags_parse_to_minutes_within_retention(self):
        self.assertEqual((lr._window(1)("45"), lr._window(60)("1.5"), lr._window(60)("24")), (45, 90, 1440))
        with self.assertRaises(lr.argparse.ArgumentTypeError):
            lr._window(60)("25")

    def test_older_snapshot_records_still_load(self):
        old = {**asdict(session("claude", A)), "pid": 123}
        self.assertEqual(lr.Session.from_json(old), session("claude", A))


class RecentlyClosed(unittest.TestCase):
    def test_sessions_closed_at_shutdown_stay_listed(self):
        before = lr.merge_seen([], [session("claude", A), session("codex", B)], now=1000.0)
        after = lr.merge_seen(before, [], now=1060.0)  # every terminal closed
        self.assertEqual([(s.id, s.last_seen) for s in after], [(A, 1000.0), (B, 1000.0)])

    def test_closed_sessions_expire_after_the_window(self):
        listed = lr.merge_seen([session("claude", A, last_seen=1000.0)], [], now=1000.0 + lr.RETAIN_S)
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

    def test_limit_takes_the_most_recently_closed_and_skips_open_ones(self):
        missing = "0000000d-0000-0000-0000-000000000000"
        plan = [session("claude", s, last_seen=t) for s, t in ((B, 10.0), (missing, 40.0), (A, 50.0), (C, 30.0))]
        with patch.object(lr, "_switchboard_kept", return_value=set()):
            todo, skipped = lr.choose(plan, [session("claude", A)], {}, False, 1000.0, limit=1)
        self.assertEqual([s.id for s in todo], [C])
        self.assertEqual([line.split(": ", 1)[1] for line in skipped], ["already open", "transcript not found"])

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


class OpenListing(unittest.TestCase):
    def test_json_lists_detected_sessions_with_their_pids(self):
        detected = [(session("claude", A, title="one"), 123),
                    (session("codex", B, title="two"), 456)]
        output = io.StringIO()
        with patch.object(lr, "located_live_sessions", return_value=(detected, ["warning"])), \
                patch("sys.stdout", output):
            self.assertEqual(lr.list_open_sessions(json_output=True), 0)
        self.assertEqual(json.loads(output.getvalue()), {
            "sessions": [
                {"tool": "claude", "id": A, "cwd": "/w", "title": "one", "pid": 123},
                {"tool": "codex", "id": B, "cwd": "/w", "title": "two", "pid": 456},
            ],
            "warnings": ["warning"],
        })


class TrackedCrontabs(unittest.TestCase):
    """Reinstalling a repo crontab must keep the line --cron on would write."""
    HOSTS = {"macbook/crontab": ("/Users/jacobr/dev/scripts/shared/sh/llm_restore.py", "/Users/jacobr"),
             "pi/crontab_pi": ("/home/pi/scripts/llm_restore.py", "/home/pi")}

    def test_each_tracked_crontab_carries_the_current_block(self):
        for name, (tool, home) in self.HOSTS.items():
            block = lr.cron_block(tool, f"{home}/.local/state/llm-restore/cron.log")
            lines = (SCRIPT.parents[2] / name).read_text().splitlines()
            with self.subTest(crontab=name):
                self.assertIn(block[0], lines)
                start = lines.index(block[0])
                self.assertEqual(lines[start:start + len(block)], block)


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

    def cron(self, state, crontab):
        written, out = [], io.StringIO()
        with patch.object(lr, "CRON_OFF", self.root / "cron-off"), \
                patch.object(lr, "CRON_LOG", self.root / "cron.log"), \
                patch.object(lr, "BOOTS", self.root / "boots"), \
                patch.object(lr, "CONSUMED", self.root / "consumed.json"), \
                patch.object(lr, "boot_id", return_value="now"), \
                patch.object(lr, "_crontab", return_value=crontab), \
                patch.object(lr.subprocess, "run", side_effect=lambda cmd, input, **_: written.append(input.splitlines())), \
                patch.object(lr, "snapshot") as snap, patch("sys.stdout", out):
            lr.cron(state, 30)
        return written, out.getvalue(), snap

    def test_cron_off_pauses_without_editing_the_crontab(self):
        written, out, snap = self.cron("off", ["a", *lr.cron_block(), "b"])
        self.assertEqual(written, [])
        snap.assert_not_called()
        self.assertTrue((self.root / "cron-off").exists())
        self.assertIn("OFF (paused", out)
        self.assertNotIn("--cron on fixes it", out)

    def test_cron_on_resumes_and_rewrites_an_old_block_in_place(self):
        (self.root / "cron-off").touch()
        written, out, snap = self.cron("on", ["a", lr.CRON_BEGIN, "old line", lr.CRON_END, "b"])
        self.assertEqual(written, [["a", *lr.cron_block(), "b"]])
        snap.assert_called_once()
        self.assertFalse((self.root / "cron-off").exists())
        self.assertIn("ON (every minute)", out)

    def test_status_reports_a_missing_line_and_on_adds_it(self):
        written, out, _ = self.cron(None, ["a"])
        self.assertEqual(written, [])
        self.assertIn("line is missing; --cron on fixes it", out)
        written, _, _ = self.cron("on", ["a"])
        self.assertEqual(written, [["a", *lr.cron_block()]])

    def cron_json(self, state, crontab, snapshots=None):
        written, out = [], io.StringIO()
        with patch.object(lr, "CRON_OFF", self.root / "cron-off"), \
                patch.object(lr, "CRON_LOG", self.root / "cron.log"), \
                patch.object(lr, "_crontab", return_value=crontab), \
                patch.object(lr, "boot_snapshots", return_value=snapshots or []), \
                patch.object(lr.subprocess, "run", side_effect=lambda cmd, input, **_: written.append(input.splitlines())), \
                patch.object(lr, "snapshot"), patch("sys.stdout", out):
            lr.cron(state, 30, json_output=True)
        return written, json.loads(out.getvalue())

    def test_json_status_reports_snapshot_and_pause_state(self):
        snap = {"taken_at": 123.5, "sessions": [
            session("claude", A, last_seen=123.5), session("codex", B, last_seen=100.0)]}
        _, status = self.cron_json(None, ["a", *lr.cron_block()], [snap])
        self.assertEqual(status, {"enabled": True, "installed": True, "up_to_date": True,
                                  "last_snapshot": 123.5, "sessions_open": 1})
        (self.root / "cron-off").touch()
        _, paused = self.cron_json(None, ["a", *lr.cron_block()], [snap])
        self.assertFalse(paused["enabled"])
        self.assertEqual(paused["sessions_open"], 1)
        _, empty = self.cron_json(None, ["a"], [])
        self.assertEqual(empty["last_snapshot"], None)
        self.assertEqual(empty["sessions_open"], None)

    def test_json_on_and_off_print_the_resulting_state(self):
        (self.root / "cron-off").touch()
        written, on = self.cron_json("on", ["a", lr.CRON_BEGIN, "old", lr.CRON_END, "b"])
        self.assertEqual(written, [["a", *lr.cron_block(), "b"]])
        self.assertEqual(on, {"enabled": True, "installed": True, "up_to_date": True,
                              "last_snapshot": None, "sessions_open": None})
        written, off = self.cron_json("off", ["a", *lr.cron_block(), "b"])
        self.assertEqual(written, [])
        self.assertFalse(off["enabled"])

    def test_json_without_cron_is_rejected(self):
        with patch.object(sys, "argv", ["llm_restore.py", "--json"]):
            with self.assertRaises(SystemExit) as raised:
                lr.main()
        self.assertEqual(raised.exception.code, 2)

    def test_only_the_scheduled_snapshot_skips_while_paused(self):
        def run(*argv):
            with patch.object(sys, "argv", ["llm_restore.py", "--snapshot", *argv]):
                lr.main()
        with patch.object(lr, "CRON_OFF", self.root / "cron-off"), patch.object(lr, "snapshot", return_value=0) as snap:
            run("--scheduled")
            snap.assert_called_once_with(quiet=True)
            (self.root / "cron-off").touch()
            run("--scheduled")
            snap.assert_called_once()
            run()
            snap.assert_called_with(quiet=False)

    def apply(self, items, opts, emit=True):
        stdin = "\n".join(json.dumps(lr.asdict(i)) for i in items) + "\n"
        out, err = io.StringIO(), io.StringIO()
        with patch.object(lr, "live_sessions", return_value=([session("claude", A)], [])), \
                patch.object(lr, "_session_exists", return_value=True), \
                patch.object(lr, "CLAUDE_PROJECTS", self.root / "projects"), \
                patch.object(lr, "_switchboard_kept", return_value=set()), \
                patch.object(lr, "LAUNCHED", self.root / "launched.json"), \
                patch.object(lr, "CONSUMED", self.root / "consumed.json"), \
                patch.object(lr, "BOOTS", self.root / "boots"), \
                patch.object(lr, "boot_id", return_value="now"), \
                patch.object(lr, "open_terminals") as opened, \
                patch("sys.stdin", io.StringIO(stdin)), patch("sys.stdout", out), patch("sys.stderr", err):
            lr.apply("prev", opts, emit)
        return out.getvalue(), err.getvalue(), opened

    def test_the_report_names_each_session_and_its_claudepr_command(self):
        (self.root / "projects/-w").mkdir(parents=True)
        (self.root / f"projects/-w/{B}.jsonl").write_text(json.dumps({"type": "ai-title", "aiTitle": "Tabs"}) + "\n")
        resume = lr.shell_command("/w", ["claude", "--permission-mode", "bypassPermissions", "--resume", B])
        item = lr.PlanItem(lr.Session("claude", B, "/w", "w-1", resume, last_seen=1000.0), 60.0)
        out, err, _ = self.apply([item], lr.RestoreOptions(dry_run=True), emit=False)
        self.assertIn(f"would open  claude w-1  (Tabs, last seen {lr._clock(1000.0)})\n"
                      f"      cd -- /w && claudepr {B}\n", out)
        self.assertNotIn("before the last snapshot", out)

    def test_minutes_window_limits_what_reopens(self):
        items = [lr.PlanItem(session("claude", B), 20 * 60), lr.PlanItem(session("claude", C), 50 * 60)]
        _, err, _ = self.apply(items, lr.RestoreOptions(dry_run=True))
        self.assertEqual(err.count("would open"), 1)
        self.assertIn("1 closed more than 30 minutes", err)
        _, err, _ = self.apply(items, lr.RestoreOptions(dry_run=True, minutes=60))
        self.assertEqual(err.count("would open"), 2)

    def test_last_ignores_the_window_opens_each_session_once_and_keeps_the_old_boot(self):
        b_old, b_new = session("claude", B, last_seen=100.0), session("claude", B, last_seen=900.0)
        items = [lr.PlanItem(session("claude", A, last_seen=1000.0), 0.0), lr.PlanItem(b_new, 100 * 60),
                 lr.PlanItem(b_old, 900 * 60), lr.PlanItem(session("claude", C, last_seen=50.0), 950 * 60)]
        out, err, _ = self.apply(items, lr.RestoreOptions(last=2))
        self.assertEqual([lr.Session.from_json(json.loads(line)).id for line in out.splitlines()], [B, C])
        self.assertIn("already open", err)
        self.assertNotIn("widen with", err)
        self.assertFalse((self.root / "consumed.json").exists())
        out, _, _ = self.apply(items, lr.RestoreOptions(last=2))
        self.assertEqual(out, "")  # just launched: a rerun doesn't open them twice

    def test_last_is_its_own_window_flag(self):
        self.assertEqual(lr.RestoreOptions(last=3).flags(), ["--last", "3"])
        for bad in ("0", "-1", "x"):
            with self.assertRaises(lr.argparse.ArgumentTypeError):
                lr._count(bad)
        with patch.object(sys, "argv", ["llm_restore.py", "--last", "2", "-H", "1"]), \
                patch("sys.stderr", io.StringIO()), self.assertRaises(SystemExit):
            lr.main()

    def test_emit_prints_sessions_to_open_and_reports_on_stderr(self):
        items = [lr.PlanItem(session("claude", A), 0.0), lr.PlanItem(session("claude", B), 0.0)]
        out, err, opened = self.apply(items, lr.RestoreOptions())
        opened.assert_not_called()
        self.assertEqual([lr.Session.from_json(json.loads(line)) for line in out.splitlines()],
                         [session("claude", B)])
        self.assertIn("already open", err)
        self.assertIn("claude:" + B, json.loads((self.root / "launched.json").read_text()))
        self.assertEqual(json.loads((self.root / "consumed.json").read_text()), ["prev"])

    def test_script_feeds_its_sessions_back_to_apply(self):
        plan = [lr.PlanItem(session("claude", A, title="it's \"quoted\""), 0.0),
                lr.PlanItem(session("codex", B, title="Reply with:\nA [report](/x)\n"), 90.0)]
        lr.write_restore_script(plan, ["prev", "now"], ["codex pid 1: unknown"])
        script = self.root / "restore"
        self.assertTrue(os.access(script, os.X_OK))
        fake = self.root / "fake"
        fake.write_text(re.sub(r"^exec .*<<'SESSIONS'$", "exec /bin/cat <<'SESSIONS'",
                               script.read_text(), flags=re.M))
        out = subprocess.run(["/bin/sh", str(fake)], capture_output=True, text=True, check=True).stdout
        self.assertEqual([lr.PlanItem.from_json(json.loads(line)) for line in out.splitlines()], plan)
        self.assertIn("--apply prev,now", script.read_text())


if __name__ == "__main__":
    unittest.main()
