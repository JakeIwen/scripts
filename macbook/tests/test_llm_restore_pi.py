"""Saved Pi SSH tabs through the shared and Mac adapter entry points."""

from contextlib import redirect_stdout, redirect_stderr
from dataclasses import asdict
import importlib.util
import io
import json
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


lr = load("saved_pi_restore", ROOT / "shared/sh/llm_restore.py")
pi = load("saved_pi_adapter", ROOT / "macbook/scripts/llm_restore_pi.py")


def session(tool="claude", sid="agent", host="vanpi", cwd="/pi/work"):
    return lr.Session(tool, sid, cwd, "Pi title", "cd /pi/work && claude --resume agent", host=host)


def item(s):
    return asdict(lr.PlanItem(s, 0))


class PiSavedTabs(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.calls = []

    def entry(self, action, items=(), flags=()):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err), patch("sys.stdin", io.StringIO(json.dumps(items))):
            result = pi.main([action, "vanpi", lr.PI_LLM_RESTORE, "transport.py", *flags])
        return result, out.getvalue(), err.getvalue()

    def fake_process(self, pid, parent, comm, client="192.0.2.1 50000 192.0.2.2 22", tmux=False, tty=1):
        path = self.root / str(pid)
        path.mkdir()
        (path / "stat").write_text(f"{pid} ({comm}) S {parent} 0 0 {tty} 0 0")
        (path / "comm").write_text(comm)
        env = "SSH_CONNECTION=" + client + "\0" if client else ""
        (path / "environ").write_bytes((env + ("TMUX=socket\0" if tmux else "")).encode())
        (path / "cwd").symlink_to("/pi/" + str(pid))

    def probe_run(self, listed):
        """Run the actual stdin probe against generated /proc data, not a canned result."""
        namespace = {"__name__": "probe_test"}
        exec(compile(pi.PROBE, "<probe>", "exec"), namespace)
        namespace["Path"] = lambda value: self.root if value == "/proc" else Path(value)
        result = subprocess.CompletedProcess([], 0, json.dumps({"sessions": listed, "warnings": []}), "")
        out = io.StringIO()
        with patch.object(namespace["subprocess"], "run", return_value=result), redirect_stdout(out):
            namespace["main"](lr.PI_LLM_RESTORE)
        return out.getvalue()

    def test_save_matches_socket_endpoints_and_excludes_tmux_other_clients_and_occupied_shells(self):
        self.fake_process(1, 0, "sshd", client="", tty=0)
        (self.root / "1/environ").unlink()  # sshd env need not be readable
        self.fake_process(10, 1, "bash")
        self.fake_process(11, 10, "claude")
        self.fake_process(12, 1, "bash", client="192.0.2.9 50001 192.0.2.2 22")
        self.fake_process(13, 1, "bash", tmux=True)
        self.fake_process(14, 1, "bash")
        self.fake_process(15, 1, "bash", tty=0)
        self.fake_process(16, 10, "codex", tmux=True)
        self.fake_process(17, 12, "codex", client="192.0.2.9 50001 192.0.2.2 22")
        listed = [{**asdict(session()), "pid": 11}, {**asdict(session("codex", "tmux")), "pid": 16},
                  {**asdict(session("codex", "other-client")), "pid": 17}]
        probe_output = self.probe_run(listed)
        def run(cmd, **kwargs):
            self.calls.append((cmd, kwargs))
            if cmd[0] == "/usr/bin/ssh":
                self.assertEqual(kwargs["input"], pi.PROBE)
                self.assertEqual(cmd[-1], "python3 -I - " + shlex.quote(lr.PI_LLM_RESTORE))
                return subprocess.CompletedProcess(cmd, 0, probe_output, "")
            self.assertEqual(cmd[0], "/usr/sbin/lsof")
            return subprocess.CompletedProcess(cmd, 0, "p9\nn192.0.2.1:50000->192.0.2.2:22\n", "")
        with patch.object(pi.subprocess, "run", side_effect=run):
            code, out, err = self.entry("save")
        sessions = json.loads(out)["sessions"]
        self.assertEqual(code, 0)
        self.assertEqual([(s["tool"], s["id"], s["host"]) for s in sessions],
                         [("claude", "agent", "vanpi"), ("shell", "/pi/14", "vanpi")])
        self.assertEqual(sum(cmd[0] == "/usr/bin/ssh" for cmd, _ in self.calls), 1)
        self.assertEqual(err, "")

    def test_ipv6_and_ipv4_mapped_endpoints_match(self):
        records = [{"session": asdict(session()), "client": ["2001:db8::1", "50000"]},
                   {"session": asdict(session("codex", "mapped")), "client": ["::ffff:192.0.2.1", "50001"]}]
        results = [subprocess.CompletedProcess([], 0, json.dumps({"records": records}), ""),
                   subprocess.CompletedProcess([], 0, "n[2001:0db8::1]:50000->[2001:db8::2]:22\nn192.0.2.1:50001->192.0.2.2:22\n", "")]
        with patch.object(pi.subprocess, "run", side_effect=results):
            _, out, _ = self.entry("save")
        self.assertEqual(len(json.loads(out)["sessions"]), 2)

    def test_apply_agents_go_to_pi_emit_and_open_only_returned_commands(self):
        a, b = item(session()), item(session("codex", "already-open"))
        response = json.dumps(asdict(session())) + "\n"
        results = [subprocess.CompletedProcess([], 0, response, "skip already open\n"),
                   subprocess.CompletedProcess([], 0, "", "")]
        with patch.object(pi.subprocess, "run", side_effect=results) as run:
            code, out, err = self.entry("apply", [a, b])
        ssh = run.call_args_list[0]
        self.assertIn("--apply - --emit", ssh.args[0][-1])
        sent = [json.loads(line) for line in ssh.kwargs["input"].splitlines()]
        self.assertEqual([i["session"]["host"] for i in sent], [None, None])
        self.assertEqual(run.call_args_list[1].args[0], ["/usr/bin/python3", "-I", "-B", "transport.py",
                                                       pi.pi_tab_command(session().command, "vanpi")])
        self.assertEqual(code, 0)
        self.assertIn("skip already open", err)

    def test_shell_skip_if_open_and_quote_cwd(self):
        cwd = "/pi/it's work"
        current = [{"session": asdict(session("shell", "open", cwd="/pi/open")),
                    "client": ["192.0.2.1", "50000"]}]
        results = [subprocess.CompletedProcess([], 0, json.dumps({"records": current}), ""),
                   subprocess.CompletedProcess([], 0, "n192.0.2.1:50000->192.0.2.2:22\n", ""),
                   subprocess.CompletedProcess([], 0, "", "")]
        shells = [item(session("shell", "open", cwd="/pi/open")), item(session("shell", "new", cwd=cwd))]
        with patch.object(pi.subprocess, "run", side_effect=results) as run:
            _, out, _ = self.entry("apply", shells)
        self.assertIn("already open", out)
        tabs = run.call_args.args[0]
        self.assertEqual(len(tabs), 5)
        remote = shlex.split(shlex.split(tabs[-1])[3])
        self.assertEqual(remote[2], "cd -- " + shlex.quote(cwd) + "; exec bash -il")

    def test_dry_run_opens_nothing_and_remote_apply_is_dry(self):
        results = [subprocess.CompletedProcess([], 0, json.dumps({"records": []}), ""),
                   subprocess.CompletedProcess([], 1, "", ""),
                   subprocess.CompletedProcess([], 0, "", "would open claude agent\n")]
        with patch.object(pi.subprocess, "run", side_effect=results) as run:
            _, out, err = self.entry("apply", [item(session()), item(session("shell", "s"))], ["--dry-run"])
        self.assertIn("--dry-run", run.call_args.args[0][-1])
        self.assertEqual(run.call_count, 3)
        self.assertIn("would open  shell", out)
        self.assertIn("would open claude", err)

    def test_unreachable_pi_warns_without_opening_tabs(self):
        error = subprocess.CalledProcessError(255, ["ssh"], stderr="offline\nremote diagnostic")
        with patch.object(pi.subprocess, "run", side_effect=error) as run:
            code, out, err = self.entry("apply", [item(session())])
            save_code, save_out, _ = self.entry("save")
        self.assertEqual((code, save_code), (0, 0))
        self.assertIn("offline", err)
        self.assertEqual(json.loads(save_out)["sessions"], [])
        self.assertIn("offline", json.loads(save_out)["warnings"][0])
        self.assertNotIn("\n", json.loads(save_out)["warnings"][0])
        self.assertEqual(run.call_count, 2)


class SharedSavedTabs(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        for name, value in (("IS_MAC", True), ("STATE", self.root), ("LOCK", self.root / "lock"),
                            ("SAVED", self.root / "saved"), ("LAUNCHED", self.root / "launched.json"),
                            ("CONSUMED", self.root / "consumed.json")):
            p = patch.object(lr, name, value)
            p.start()
            self.addCleanup(p.stop)

    def test_old_saved_set_without_host_still_applies_locally(self):
        old = item(session(host=None))
        del old["session"]["host"]
        with patch("sys.stdin", io.StringIO(json.dumps(old))), patch("sys.stdout", io.StringIO()), \
                patch.object(lr, "live_sessions", return_value=([], [])), \
                patch.object(lr, "_session_exists", return_value=True), patch.object(lr, "session_name", return_value=""), \
                patch.object(lr, "_switchboard_kept", return_value=set()), patch.object(lr, "pi_request") as remote, \
                patch.object(lr, "open_terminals") as opened, patch.object(lr, "boot_id", return_value="boot"), \
                patch.object(lr, "regenerate"):
            self.assertEqual(lr.apply("-", lr.RestoreOptions(), False), 0)
        remote.assert_not_called()
        self.assertIsNone(opened.call_args.args[0][0].host)

    def test_apply_splits_host_items_before_local_chooser_and_transcript_lookup(self):
        local, remote = session(host=None), session()
        records = [item(local), item(remote), item(session("shell", "shell"))]
        with patch("sys.stdin", io.StringIO("\n".join(json.dumps(i) for i in records))), \
                patch("sys.stdout", io.StringIO()), patch.object(lr, "live_sessions", return_value=([], [])), \
                patch.object(lr, "choose", return_value=([local], [])) as choose, \
                patch.object(lr, "session_name", return_value="") as names, \
                patch.object(lr, "pi_request", return_value={}) as host, patch.object(lr, "open_terminals") as opened, \
                patch.object(lr, "boot_id", return_value="boot"), patch.object(lr, "regenerate"):
            lr.apply("-", lr.RestoreOptions(), False)
        self.assertEqual(choose.call_args.args[0], [local])
        names.assert_called_once_with(local)
        self.assertEqual(len(host.call_args.args[2]), 2)
        self.assertEqual(opened.call_args.args[0], [local])

    def test_restore_keeps_local_sessions_when_pi_is_unreachable(self):
        records = [item(session(host=None)), item(session())]
        with patch("sys.stdin", io.StringIO("\n".join(json.dumps(i) for i in records))), \
                patch("sys.stdout", io.StringIO()), patch("sys.stderr", io.StringIO()) as err, \
                patch.object(lr, "live_sessions", return_value=([], [])), \
                patch.object(lr, "_session_exists", return_value=True), patch.object(lr, "session_name", return_value=""), \
                patch.object(lr, "_switchboard_kept", return_value=set()), \
                patch.object(lr.subprocess, "run", side_effect=subprocess.CalledProcessError(255, ["helper"])), \
                patch.object(lr, "open_terminals") as opened, patch.object(lr, "boot_id", return_value="boot"), \
                patch.object(lr, "regenerate"):
            self.assertEqual(lr.apply("-", lr.RestoreOptions(), False), 0)
        self.assertIn("warning:", err.getvalue())
        self.assertEqual(opened.call_args.args[0], [session(host=None)])

    def test_save_keeps_local_sessions_when_helper_or_pi_fails(self):
        for failure in (subprocess.CalledProcessError(255, ["helper"]), OSError("missing helper")):
            with self.subTest(failure=failure), patch.object(lr, "live_sessions", return_value=([session(host=None)], [])), \
                    patch.object(lr.subprocess, "run", side_effect=failure), patch("sys.stdout", io.StringIO()), \
                    patch("sys.stderr", io.StringIO()) as err:
                self.assertEqual(lr.save_set("offline", True), 0)
            self.assertIn("warning:", err.getvalue())
            lines = (self.root / "saved/offline.sh").read_text().splitlines()
            self.assertEqual(sum(line.startswith("{") for line in lines), 1)

    def test_save_embeds_host_records_and_header(self):
        response = json.dumps({"sessions": [asdict(session()), asdict(session("shell", "s"))], "warnings": []})
        with patch.object(lr, "live_sessions", return_value=([], [])), patch("sys.stdout", io.StringIO()), \
                patch.object(lr.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, response, "")) as run:
            self.assertEqual(lr.save_set("pi", False), 0)
        self.assertEqual(run.call_args.args[0][4], "save")
        script = (self.root / "saved/pi.sh").read_text()
        self.assertIn("vanpi", script)
        self.assertEqual(sum(line.startswith("{") for line in script.splitlines()), 2)


if __name__ == "__main__":
    unittest.main()
