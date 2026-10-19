"""Attribution of uncommitted files to recent Claude and Codex sessions."""

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[2] / "shared" / "sh" / "uncommitted_owners.py"
spec = importlib.util.spec_from_file_location("uncommitted_owners", SCRIPT)
uo = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = uo
spec.loader.exec_module(uo)
CLAUDE_ID = "11111111-1111-4111-8111-111111111111"
CODEX_ID = "22222222-2222-4222-8222-222222222222"
CHILD_ID = "33333333-3333-4333-8333-333333333333"


def event(path, value):
    with path.open("a") as stream:
        stream.write(json.dumps(value) + "\n")


class UncommittedOwners(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.repo = self.root / "repo"
        self.home = self.root / "home"
        self.repo.mkdir()
        self.home.mkdir()
        self.commit_time = int(time.time()) - 500
        self.after = self.commit_time + 100
        self.before = self.commit_time - 100
        self._git("init", "-q")
        self._git("config", "user.name", "Test User")
        self._git("config", "user.email", "test@example.invalid")
        names = ["claude_edit.txt", "codex_patch.txt", "mention.txt", "old.txt",
                 "subagent.txt", "unattributed.txt", "nested/name_only.py",
                 "nested/suppressed.py", "nested/token.txt", "rename-old.txt"]
        for name in names:
            path = self.repo / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("baseline\n")
        (self.repo / ".gitignore").write_text("ignored.txt\n")
        self._git("add", ".")
        commit_env = {
            **os.environ,
            "GIT_AUTHOR_DATE": f"{self.commit_time} +0000",
            "GIT_COMMITTER_DATE": f"{self.commit_time} +0000",
        }
        self._git("commit", "-q", "-m", "baseline", env=commit_env)
        for name in names[:-1]:
            (self.repo / name).write_text("baseline\ndirty\n")
        self._git("mv", "rename-old.txt", "rename-new.txt")
        untracked = self.repo / "new" / "untracked.txt"
        untracked.parent.mkdir()
        untracked.write_text("unowned\n")
        (self.repo / "ignored.txt").write_text("ignored\n")
        self._write_claude()
        self._write_codex()
        self.fake_restore = self.root / "fake_llm_restore.py"
        listing = {"sessions": [{"tool": "codex", "id": CODEX_ID, "cwd": str(self.repo),
                                  "title": "Codex parent", "pid": os.getpid()}], "warnings": []}
        self.fake_restore.write_text(f"print({json.dumps(json.dumps(listing))})\n")

    def _git(self, *args, env=None):
        return subprocess.run(["git", *args], cwd=self.repo, env=env, check=True,
                              capture_output=True, text=True)

    def _write_claude(self):
        project = self.home / ".claude" / "projects" / "fixture"
        project.mkdir(parents=True)
        transcript = project / f"{CLAUDE_ID}.jsonl"
        event(transcript, {
            "type": "user", "timestamp": self.after, "cwd": str(self.repo),
            "message": {"content": [{"type": "text", "text": "Please edit fixture files"}]},
        })
        event(transcript, {"type": "ai-title", "aiTitle": "Fixture edits"})
        event(transcript, {
            "type": "assistant", "timestamp": self.after + 1, "cwd": str(self.repo),
            "message": {"content": [{"type": "tool_use", "name": "Edit", "input": {
                "file_path": str(self.repo / "claude_edit.txt"), "old_string": "baseline",
                "new_string": "dirty"}}]},
        })
        event(transcript, {
            "type": "assistant", "timestamp": self.after + 2, "cwd": str(self.repo),
            "message": {"content": [{"type": "tool_use", "name": "Bash",
                                      "input": {"command": "cat mention.txt"}}]},
        })
        event(transcript, {
            "type": "assistant", "timestamp": self.after + 2.2, "cwd": str(self.repo),
            "message": {"content": [{"type": "tool_use", "name": "Bash", "input": {
                "command": "cd nested && python3 - <<'PY'\nfrom pathlib import Path\nPath('name_only.py')\nPath('suppressed.py')\nPath('mytoken.txt')\nPY"}}]},
        })
        event(transcript, {
            "type": "assistant", "timestamp": self.before, "cwd": str(self.repo),
            "message": {"content": [{"type": "tool_use", "name": "Write",
                                      "input": {"file_path": "old.txt", "content": "old"}}]},
        })
        event(transcript, {
            "type": "user", "timestamp": self.after + 2.5, "cwd": str(self.repo),
            "message": {"content": [{"type": "tool_result", "content": " M unattributed.txt"}]},
        })
        event(transcript, {
            "type": "assistant", "timestamp": self.after + 3, "cwd": str(self.repo),
            "message": {"content": [{"type": "tool_use", "name": "Edit", "input": {
                "file_path": "rename-new.txt", "old_string": "x", "new_string": "y"}},
                {"type": "text", "text": "Claude fixture complete"}]},
        })
        os.utime(transcript, (time.time(), time.time()))

    def _codex_path(self, session_id):
        folder = self.home / ".codex" / "sessions" / "2026" / "10" / "19"
        folder.mkdir(parents=True, exist_ok=True)
        return folder / f"rollout-2026-10-19T00-00-00-{session_id}.jsonl"

    def _write_codex(self):
        parent = self._codex_path(CODEX_ID)
        event(parent, {"type": "session_meta", "timestamp": self.after,
                       "payload": {"id": CODEX_ID, "session_id": CODEX_ID,
                                   "cwd": str(self.repo), "thread_source": "user"}})
        event(parent, {"type": "response_item", "timestamp": self.after + 1,
                       "payload": {"type": "message", "role": "user",
                                   "content": [{"type": "input_text", "text": "Patch the fixtures"}]}})
        patch = ('const patch = "*** Begin Patch\\n*** Update File: codex_patch.txt\\n'
                 '*** End Patch"; text(await tools.apply_patch(patch));')
        event(parent, {"type": "response_item", "timestamp": self.after + 2,
                       "payload": {"type": "custom_tool_call", "name": "exec", "input": patch}})
        event(parent, {"type": "response_item", "timestamp": self.after + 2.5,
                       "payload": {"type": "custom_tool_call", "name": "exec",
                                   "input": "text(await tools.exec_command({cmd:'cat nested/suppressed.py'}));"}})
        event(parent, {"type": "response_item", "timestamp": self.after + 3,
                       "payload": {"type": "message", "role": "assistant",
                                   "content": [{"type": "output_text", "text": "Codex fixture complete"}]}})
        child = self._codex_path(CHILD_ID)
        source = {"subagent": {"thread_spawn": {"parent_thread_id": CODEX_ID, "depth": 1}}}
        event(child, {"type": "session_meta", "timestamp": self.after,
                      "payload": {"id": CHILD_ID, "session_id": CODEX_ID,
                                  "parent_thread_id": CODEX_ID, "cwd": str(self.repo),
                                  "thread_source": "subagent", "source": source}})
        sub_patch = ('const patch = "*** Begin Patch\\n*** Update File: subagent.txt\\n'
                     '*** End Patch"; text(await tools.apply_patch(patch));')
        event(child, {"type": "response_item", "timestamp": self.after + 4,
                      "payload": {"type": "custom_tool_call", "name": "exec", "input": sub_patch}})
        for path in (parent, child):
            os.utime(path, (time.time(), time.time()))

    def run_tool(self, *extra):
        environment = {
            **os.environ,
            "UNCOMMITTED_OWNERS_HOME": str(self.home),
            "UNCOMMITTED_OWNERS_LLM_RESTORE": str(self.fake_restore),
        }
        return subprocess.run([sys.executable, str(SCRIPT), str(self.repo), "--days", "2", *extra],
                              env=environment, capture_output=True, text=True, check=True)

    def test_attributes_edits_mentions_subagents_and_unowned_files(self):
        result = json.loads(self.run_tool("--json").stdout)
        sessions = {item["session_id"]: item for item in result["sessions"]}
        self.assertEqual(set(sessions), {CLAUDE_ID, CODEX_ID})
        claude_files = {item["path"]: item for item in sessions[CLAUDE_ID]["files"]}
        self.assertEqual(claude_files["claude_edit.txt"]["strength"], "edit")
        self.assertEqual(claude_files["mention.txt"]["strength"], "mention")
        self.assertEqual(claude_files["nested/name_only.py"]["strength"], "name")
        self.assertNotIn("nested/suppressed.py", claude_files)
        self.assertEqual(claude_files["rename-new.txt"]["strength"], "edit")
        self.assertEqual(claude_files["rename-new.txt"]["previous_path"], "rename-old.txt")
        codex_files = {item["path"]: item for item in sessions[CODEX_ID]["files"]}
        self.assertEqual(codex_files["codex_patch.txt"]["strength"], "edit")
        self.assertEqual(codex_files["nested/suppressed.py"]["strength"], "mention")
        self.assertEqual(codex_files["subagent.txt"]["strength"], "edit")
        self.assertNotIn(CHILD_ID, sessions)
        self.assertTrue(sessions[CODEX_ID]["open"])
        self.assertFalse(sessions[CLAUDE_ID]["open"])
        self.assertIn("command claude --permission-mode bypassPermissions --resume",
                      sessions[CLAUDE_ID]["resume_command"])
        unattributed = {item["path"]: item for item in result["unattributed"]}
        self.assertEqual(set(unattributed), {"nested/token.txt", "new/untracked.txt", "old.txt",
                                             "unattributed.txt"})
        self.assertIsNone(unattributed["new/untracked.txt"]["additions"])
        self.assertEqual(unattributed["old.txt"]["additions"], 1)
        self.assertNotIn("ignored.txt", unattributed)

    def test_codex_titles_correct_swapped_terminal_pids(self):
        archive = uo.SessionOwner("codex", CODEX_ID, title="Archive resolved conversations")
        satellite = uo.SessionOwner("codex", CHILD_ID, title="Set up Mac satellite")
        open_codex = {
            CODEX_ID: {"id": CODEX_ID, "pid": 101, "title": archive.title},
            CHILD_ID: {"id": CHILD_ID, "pid": 202, "title": satellite.title},
        }
        with patch.object(uo, "_claude_open", return_value={}), \
                patch.object(uo, "_codex_open", return_value=(open_codex, True)), \
                patch.object(uo, "_tty", side_effect={101: "ttys1", 202: "ttys2"}.get), \
                patch.object(uo, "_iterm_locations", return_value={
                    "ttys1": "Set up Mac satellite | jacobr (codex)",
                    "ttys2": "Archive resolved conversations | jacobr (codex)",
                }), patch.object(uo.sys, "platform", "darwin"):
            uo.enrich_open_state([archive, satellite])
        self.assertEqual((archive.pid, archive.location),
                         (202, "Archive resolved conversations | jacobr (codex)"))
        self.assertEqual((satellite.pid, satellite.location),
                         (101, "Set up Mac satellite | jacobr (codex)"))

    def test_text_lists_open_before_closed_then_unattributed(self):
        output = self.run_tool().stdout
        self.assertLess(output.index("Open sessions"), output.index("Closed sessions"))
        self.assertLess(output.index("Closed sessions"), output.index("Unattributed files"))
        self.assertIn("Codex parent", output)
        self.assertIn("resume: cd --", output)


if __name__ == "__main__":
    unittest.main()
