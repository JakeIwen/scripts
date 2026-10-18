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
from pi.scripts.price_check.search_watch.browser_refresh import (
    MAX_FAILURES, RETRY_SECONDS, claim_refresh, notify_exhausted,
    record_failure, request_refresh, retry_status,
)
from pi.scripts.price_check.search_watch.store import SearchStore


class BrowserRefreshWorkerTests(unittest.TestCase):
    REQUEST = {
        "search_id": 1, "request_id": "a" * 32,
        "url": "https://www.ebay.com/sch/i.html?_nkw=x",
        "attempt": 1,
    }
    NEEDS_RENEWAL = {"ok": True, "renewal_needed": True}
    PAGE = {"final_url": REQUEST["url"], "html": '<ul class="srp-results">Example</ul>'}
    RECEIPT = {"ok": True, "result_count": 2, "notification_failed": False}

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

    def test_rejected_browser_results_report_failure_without_replacing_headers(self):
        with mock.patch.object(worker, "remote", side_effect=[self.REQUEST, self.NEEDS_RENEWAL, {"ok": False}, {"ok": True}]) as remote, \
             mock.patch.object(worker.subprocess, "run", return_value=SimpleNamespace(
                 stdout=json.dumps(self.PAGE))):
            with self.assertRaises(ValueError):
                worker.refresh("pi@vanpi.lan")
        self.assertEqual(remote.call_args_list[-1].args, ("pi@vanpi.lan", "failed", self.REQUEST))

    def test_success_submits_browser_page_and_leaves_local_credentials_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "pi/secrets").mkdir(parents=True)
            headers = root / "pi/secrets/.ebay_headers"
            headers.write_text("Cookie: private=value")
            before = headers.read_bytes(), headers.stat().st_mtime_ns
            output = io.StringIO()
            with mock.patch.object(worker, "ROOT", root), \
                 mock.patch.object(worker, "remote", side_effect=[self.REQUEST, self.NEEDS_RENEWAL, self.RECEIPT]) as remote, \
                 mock.patch.object(worker.subprocess, "run", return_value=SimpleNamespace(
                     stdout=json.dumps(self.PAGE))), contextlib.redirect_stdout(output):
                self.assertTrue(worker.refresh("pi@vanpi.lan"))
            self.assertEqual(remote.call_args.args, ("pi@vanpi.lan", "results", {**self.REQUEST, **self.PAGE}))
            self.assertEqual((headers.read_bytes(), headers.stat().st_mtime_ns), before)
            self.assertNotIn("private=value", output.getvalue())
            self.assertNotIn(self.PAGE["html"], output.getvalue())

    def test_successful_recheck_skips_browser_and_result_submission(self):
        with mock.patch.object(worker, "remote", side_effect=[
            self.REQUEST, {"ok": True, "renewal_needed": False},
        ]) as remote, mock.patch.object(worker.subprocess, "run") as run, \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertFalse(worker.refresh("pi@vanpi.lan"))
        self.assertEqual(remote.call_args.args, ("pi@vanpi.lan", "recheck", self.REQUEST))
        run.assert_not_called()

    def test_browser_page_cannot_override_the_claim(self):
        with mock.patch.object(worker, "remote", side_effect=[self.REQUEST, self.NEEDS_RENEWAL, {"ok": True}]) as remote, \
             mock.patch.object(worker.subprocess, "run", return_value=SimpleNamespace(
                 stdout=json.dumps({**self.PAGE, "search_id": 42}))):
            with self.assertRaises(ValueError):
                worker.refresh("pi@vanpi.lan")
        self.assertEqual(remote.call_args.args, ("pi@vanpi.lan", "failed", self.REQUEST))

    def test_failed_or_invalid_recheck_does_not_start_browser(self):
        for response in ({"ok": False}, {"ok": True, "renewal_needed": "true"},
                         worker.RefreshFailure("Pi recheck failed")):
            with self.subTest(response=response), \
                 mock.patch.object(worker, "remote", side_effect=[self.REQUEST, response, {"ok": True}]), \
                 mock.patch.object(worker.subprocess, "run") as run:
                with self.assertRaises((ValueError, worker.RefreshFailure)):
                    worker.refresh("pi@vanpi.lan")
                run.assert_not_called()

    def test_worker_failure_does_not_log_captured_secrets(self):
        with tempfile.TemporaryDirectory() as directory:
            output = io.StringIO()
            error = subprocess.CalledProcessError(1, ["example"], output="Cookie: private=value")
            with mock.patch.object(worker, "ROOT", Path(directory)), \
                 mock.patch.object(worker, "refresh", side_effect=error), \
                 mock.patch("sys.argv", ["deal_watch_refresh.py", "--once"]), \
                 contextlib.redirect_stdout(output):
                self.assertEqual(worker.main(), 1)
            self.assertNotIn("private=value", output.getvalue())

    def test_bridge_failure_reports_its_cause_without_the_payload(self):
        error = subprocess.CalledProcessError(1, ["ssh"], output=json.dumps({
            "ok": False, "cause": "SearchCookieError", "headers": "Cookie: private=value",
        }))
        with mock.patch.object(worker.subprocess, "run", side_effect=error):
            with self.assertRaises(worker.RefreshFailure) as failed:
                worker.remote("pi@vanpi.lan", "install", {"headers": "Cookie: private=value"})
        self.assertIn("SearchCookieError", str(failed.exception))
        self.assertNotIn("private=value", str(failed.exception))

    def test_running_worker_retries_at_six_minutes_and_stops_after_fifty_failures(self):
        with tempfile.TemporaryDirectory() as directory:
            now = [1_700_000_000]
            claims = []
            alert = mock.Mock()
            with SearchStore(Path(directory) / "prices.sqlite3", clock=lambda: now[0]) as store:
                watch = store.add_watch("ebay", self.REQUEST["url"])
                request_refresh(store, watch)

                def remote(_host, command, payload=None):
                    if command == "claim":
                        request = claim_refresh(store)
                        notify_exhausted(store, alert)
                        if request:
                            claims.append(now[0])
                        return request
                    if command == "status":
                        return retry_status(store)
                    if command == "recheck":
                        return self.NEEDS_RENEWAL
                    if command == "failed":
                        record_failure(store, payload)
                        notify_exhausted(store, alert)
                        return {"ok": True}
                    self.fail(f"unexpected bridge command {command}")

                def sleep(seconds):
                    now[0] += seconds

                failure = subprocess.CalledProcessError(1, ["browser"], stderr="Cookie: private=value")
                output = io.StringIO()
                with mock.patch.object(worker, "remote", side_effect=remote), \
                     mock.patch.object(worker.subprocess, "run", side_effect=failure), \
                     contextlib.redirect_stdout(output):
                    self.assertEqual(worker.run_worker("example", sleep=sleep), 1)
                self.assertEqual(len(claims), MAX_FAILURES)
                self.assertTrue(all(b - a == RETRY_SECONDS for a, b in zip(claims, claims[1:])))
                alert.assert_called_once()
                self.assertEqual(alert.call_args.args[1], MAX_FAILURES)
                self.assertIsNone(claim_refresh(store))
                self.assertNotIn("private=value", output.getvalue())


if __name__ == "__main__":
    unittest.main()
