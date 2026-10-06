import contextlib
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from macbook.scripts import deal_watch_refresh as worker


class BrowserRefreshWorkerTests(unittest.TestCase):
    REQUEST = {
        "search_id": 1, "request_id": "a" * 32,
        "url": "https://www.ebay.com/sch/i.html?_nkw=x",
    }

    @mock.patch.object(worker, "remote", return_value=None)
    @mock.patch.object(worker.subprocess, "run")
    def test_idle_poll_does_not_open_browser(self, run, _remote):
        self.assertFalse(worker.refresh("pi@vanpi.lan"))
        run.assert_not_called()

    @mock.patch.object(worker.subprocess, "run", return_value=SimpleNamespace(stdout='{"ok":true}'))
    def test_headers_travel_only_in_ssh_stdin(self, run):
        worker.remote("pi@vanpi.lan", "install", {"headers": "Cookie: private=value"})
        self.assertNotIn("private=value", " ".join(run.call_args.args[0]))
        self.assertIn("private=value", run.call_args.kwargs["input"])

    def test_rejected_remote_install_does_not_replace_local_secret(self):
        with mock.patch.object(worker, "remote", side_effect=[self.REQUEST, {"ok": False}]), \
             mock.patch.object(worker.subprocess, "run", return_value=SimpleNamespace(
                 stdout=json.dumps({"headers": "Cookie: private=value"}))), \
             mock.patch.object(worker, "sync_local_headers") as sync:
            with self.assertRaises(ValueError):
                worker.refresh("pi@vanpi.lan")
            sync.assert_not_called()

    def test_success_updates_private_local_copy_without_logging_cookie(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "pi/secrets").mkdir(parents=True)
            output = io.StringIO()
            with mock.patch.object(worker, "ROOT", root), \
                 mock.patch.object(worker, "remote", side_effect=[self.REQUEST, {"ok": True}]), \
                 mock.patch.object(worker.subprocess, "run", return_value=SimpleNamespace(
                     stdout=json.dumps({"headers": "Cookie: private=value"}))), \
                 contextlib.redirect_stdout(output):
                self.assertTrue(worker.refresh("pi@vanpi.lan"))
            destination = root / "pi/secrets/.ebay_headers"
            self.assertEqual(destination.stat().st_mode & 0o777, 0o600)
            self.assertIn("private=value", destination.read_text())
            self.assertNotIn("private=value", output.getvalue())

    def test_worker_failure_does_not_log_captured_secrets(self):
        with tempfile.TemporaryDirectory() as directory:
            output = io.StringIO()
            error = subprocess.CalledProcessError(1, ["example"], output="Cookie: private=value")
            with mock.patch.object(worker, "ROOT", Path(directory)), \
                 mock.patch.object(worker, "refresh", side_effect=error), \
                 mock.patch("sys.argv", ["deal_watch_refresh.py"]), \
                 contextlib.redirect_stdout(output):
                self.assertEqual(worker.main(), 1)
            self.assertNotIn("private=value", output.getvalue())


if __name__ == "__main__":
    unittest.main()
