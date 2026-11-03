"""Claude attention notifications lead with the conversation name from the transcript."""
import importlib.util
import json
from pathlib import Path
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]


def load(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


TITLE_SCRIPT = ROOT / "shared/sh/claude_session_title.py"
claude_session_title = load("session_title", TITLE_SCRIPT)


def row(kind, field, value):
    return json.dumps({"type": kind, field: value, "sessionId": "s"}, ensure_ascii=False, separators=(",", ":"))


class NotificationNameTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.deployment = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.deployment.cleanup)
        target = Path(cls.deployment.name)
        # Exercise the Pi's installed flat layout, not a test-only import path.
        shutil.copy2(TITLE_SCRIPT, target / TITLE_SCRIPT.name)
        shutil.copy2(ROOT / "pi/scripts/claude_ntfy_notify.py", target)
        cls.notifiers = {
            "mac": load("ntfy_mac", "macbook/scripts/claude_ntfy_notify.py"),
            "pi": load("ntfy_pi", target / "claude_ntfy_notify.py"),
        }

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)

    def transcript(self, lines, raw=b""):
        path = Path(self.dir.name) / "s.jsonl"
        path.write_bytes(("\n".join(lines) + "\n").encode() + raw)
        return str(path)

    def event(self, path, message="Claude needs your attention"):
        return {"hook_event_name": "Notification", "message": message,
                "cwd": "/home/me/proj", "transcript_path": path}

    def each(self):
        for label, module in self.notifiers.items():
            with self.subTest(notifier=label):
                yield module

    def test_name_leads_body_before_reason_and_path(self):
        path = self.transcript([row("ai-title", "aiTitle", "Generated")])
        for module in self.each():
            title, message, priority = module.notification(self.event(path))
            self.assertEqual(message, "Generated: Claude needs your attention\n\n/home/me/proj")
            self.assertEqual((title, priority), ("proj: attention", "high"))

    def test_latest_user_rename_beats_generated_title(self):
        path = self.transcript([
            row("custom-title", "customTitle", "Old name"),
            row("agent-name", "agentName", "Old name"),
            row("ai-title", "aiTitle", "Generated"),
            row("custom-title", "customTitle", "Café ✓ renamed"),
            row("agent-name", "agentName", "Café ✓ renamed"),
            row("ai-title", "aiTitle", "Generated later"),
        ])
        for module in self.each():
            self.assertTrue(module.notification(self.event(path))[1].startswith("Café ✓ renamed: "))

    def test_agent_name_used_without_custom_title(self):
        path = self.transcript([row("ai-title", "aiTitle", "Gen"), row("agent-name", "agentName", "Named")])
        self.assertEqual(claude_session_title.transcript_title(path), "Named")

    def test_missing_or_corrupt_transcript_keeps_original_message(self):
        corrupt = self.transcript(['{"type":"custom-title","customTitle":'], raw=b"\xff\xfe not json\n")
        for module in self.each():
            for path in (corrupt, str(Path(self.dir.name) / "absent.jsonl"), ""):
                self.assertEqual(module.notification(self.event(path))[1],
                                 "Claude needs your attention\n\n/home/me/proj")

    def test_long_multiline_name_is_collapsed_and_shortened(self):
        path = self.transcript([row("custom-title", "customTitle", "word\n" * 50)])
        name = claude_session_title.transcript_title(path)
        self.assertLessEqual(len(name), 60)
        self.assertNotIn("\n", name)
        self.assertTrue(name.endswith("…"))

    def test_title_far_from_end_of_large_transcript_is_found(self):
        filler = [json.dumps({"type": "user", "text": "x" * 1000})] * 2000  # about 2 MB
        path = self.transcript([row("custom-title", "customTitle", "Early")] + filler)
        self.assertEqual(claude_session_title.transcript_title(path), "Early")

    def test_hook_spawns_send_with_named_message_and_nothing_real_is_sent(self):
        path = self.transcript([row("custom-title", "customTitle", "Phone name")])
        real_popen = subprocess.Popen
        for module in self.each():
            sends = []

            def capture_send(argv, **kwargs):
                if "--send" in argv:
                    sends.append(argv)
                    return None
                return real_popen(argv, **kwargs)

            with mock.patch.object(module.sys, "stdin", mock.Mock(read=lambda: json.dumps(self.event(path)))), \
                 mock.patch.object(module.subprocess, "Popen", side_effect=capture_send):
                self.assertEqual(module.main(), 0)
            self.assertEqual(len(sends), 1)
            argv = sends[0]
            self.assertEqual(argv[2:4], ["--send", "proj: attention"])
            self.assertTrue(argv[4].startswith("Phone name: Claude needs your attention"))

    def test_spaced_json_metadata_is_supported(self):
        path = self.transcript([json.dumps({"type": "custom-title", "customTitle": "Spaced"})])
        self.assertEqual(claude_session_title.transcript_title(path), "Spaced")

    def test_cli_falls_back_to_session_id(self):
        session = "12345678-1234-1234-1234-123456789abc"
        project = Path(self.dir.name) / ".claude/projects/test-project"
        project.mkdir(parents=True)
        (project / f"{session}.jsonl").write_text(row("custom-title", "customTitle", "Found by ID"))
        result = subprocess.run(
            [sys.executable, "-I", str(TITLE_SCRIPT)],
            input=json.dumps({"session_id": session}), text=True, capture_output=True,
            env={**os.environ, "HOME": self.dir.name}, check=True,
        )
        self.assertEqual(result.stdout.strip(), "Found by ID")

    def test_title_cli_failure_keeps_notification(self):
        for module in self.each():
            with mock.patch.object(module.subprocess, "run", side_effect=subprocess.TimeoutExpired("title", 5)):
                message = module.notification(self.event("missing"))[1]
            self.assertEqual(message, "Claude needs your attention\n\n/home/me/proj")

    def test_send_cli_is_unchanged(self):
        for module in self.each():
            with mock.patch.object(module.sys, "argv", ["n", "--send", "t", "m", "high"]), \
                 mock.patch.object(module, "send", return_value=0) as send:
                self.assertEqual(module.main(), 0)
            send.assert_called_once_with("t", "m", "high")

    def test_non_notification_event_sends_nothing(self):
        for module in self.each():
            with mock.patch.object(module.sys, "stdin", mock.Mock(read=lambda: '{"hook_event_name":"Stop"}')), \
                 mock.patch.object(module.subprocess, "Popen") as popen:
                self.assertEqual(module.main(), 0)
            popen.assert_not_called()


if __name__ == "__main__":
    unittest.main()
