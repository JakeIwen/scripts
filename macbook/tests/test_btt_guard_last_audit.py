"""Public CLI access to the saved BetterTouchTool guard audit."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest

from macbook.bettertouchtool.btt_guard import cli, storage


class LastAuditTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.state = Path(temporary.name) / "state"

    def invoke(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = cli.main(["--state-dir", str(self.state), "last-audit"])
        return code, json.loads(output.getvalue())

    def test_missing_report_is_explicitly_empty(self):
        code, output = self.invoke()
        self.assertEqual((code, output), (0, {"status": "none"}))

    def test_saved_report_is_printed_without_changes(self):
        report = {
            "status": "drift",
            "checkpoint_id": "cp-123",
            "checked_at": 12.5,
            "btt_version": "private-version",
            "findings": [{"kind": "missing", "uuid": "private-id", "detail": "private-detail"}],
        }
        storage.write_json(self.state / "last-audit.json", report)
        code, output = self.invoke()
        self.assertEqual(code, 0)
        self.assertEqual(output, report)

    def test_malformed_report_is_unreadable(self):
        self.state.mkdir(mode=0o700)
        path = self.state / "last-audit.json"
        path.write_text("not json", encoding="utf-8")
        path.chmod(0o600)
        code, output = self.invoke()
        self.assertEqual(code, 2)
        self.assertEqual(output["status"], "unreadable")
        self.assertTrue(output["detail"])

    def test_symlink_report_is_unreadable(self):
        self.state.mkdir(mode=0o700)
        target = self.state / "target.json"
        target.write_text("{}", encoding="utf-8")
        target.chmod(0o600)
        (self.state / "last-audit.json").symlink_to(target)
        code, output = self.invoke()
        self.assertEqual(code, 2)
        self.assertEqual(output["status"], "unreadable")


if __name__ == "__main__":
    unittest.main()
