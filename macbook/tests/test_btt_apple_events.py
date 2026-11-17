"""btt_apple_events: size routing and the osascript transport (BTT itself is never contacted)."""
import json
import unittest
from unittest.mock import patch

import btt_apple_events as events


class RoutingTests(unittest.TestCase):
    def setUp(self):
        self.sent = []
        self.send = events.routed(lambda command, **p: self.sent.append(("socket", command)) or "r",
                                  lambda command, **p: self.sent.append(("large", command)))

    def test_small_writes_and_reads_use_the_socket_big_writes_do_not(self):
        big = "x" * (events.SOCKET_LIMIT + 1)
        self.send("update_menu_item", uuid="u", json="{}", persist="true")
        self.send("get_trigger", uuid=big)
        self.send("add_new_trigger", json=big, parent_uuid="p")
        self.assertEqual(self.sent, [("socket", "update_menu_item"), ("socket", "get_trigger"),
                                     ("large", "add_new_trigger")])


class TransportTests(unittest.TestCase):
    def run_worker(self, request):
        return events.subprocess.run(["/usr/bin/osascript", "-l", "JavaScript", str(events.WORKER), request],
                                     capture_output=True, text=True, timeout=30)

    def test_failure_message_never_echoes_the_payload(self):
        failed = type("R", (), {"returncode": 1, "stdout": "", "stderr": "secret"})()
        with patch.object(events.subprocess, "run", return_value=failed):
            with self.assertRaises(RuntimeError) as caught:
                events.write_large("add_new_trigger", json="private text", parent_uuid="p")
        self.assertNotIn("private", str(caught.exception))
        self.assertNotIn("secret", str(caught.exception))

    def test_success_needs_the_workers_ok_and_the_file_is_removed_and_private(self):
        seen = {}
        def fake_run(argv, **kwargs):
            import os, stat
            seen["mode"] = stat.S_IMODE(os.stat(argv[-1]).st_mode)
            seen["request"] = json.loads(open(argv[-1]).read())
            seen["path"] = argv[-1]
            return type("R", (), {"returncode": 0, "stdout": "ok\n", "stderr": ""})()
        with patch.object(events.subprocess, "run", side_effect=fake_run):
            events.write_large("update_menu_item", uuid="u", json="{}", persist=True)
        self.assertEqual(seen["mode"], 0o600)
        self.assertEqual(seen["request"]["parameters"]["persist"], "True")
        self.assertFalse(__import__("os").path.exists(seen["path"]))
        with patch.object(events.subprocess, "run", return_value=type(
                "R", (), {"returncode": 0, "stdout": "", "stderr": ""})()):
            with self.assertRaises(RuntimeError):
                events.write_large("add_new_trigger", json="{}")

    def test_worker_refuses_an_unknown_command_without_contacting_btt(self):
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".json") as handle:
            json.dump({"command": "delete_trigger", "parameters": {"uuid": "x"}}, handle)
            handle.flush()
            result = self.run_worker(handle.name)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Unsupported", result.stderr)


if __name__ == "__main__":
    unittest.main()
