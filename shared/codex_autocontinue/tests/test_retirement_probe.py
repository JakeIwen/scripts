from io import BytesIO
import json
from pathlib import Path
import re
import subprocess
import tempfile
import unittest
import urllib.error
import urllib.request

from shared.codex_autocontinue.fake_server import Scenario
from shared.codex_autocontinue.retirement_probe import (
    PR_URL, classify_probe, fetch_pr_status, run_probe)
from shared.codex_autocontinue.retirement_state import ProbeOutcome


CODEX = Path.home() / ".local/bin/codex"


class RetirementProbeTests(unittest.TestCase):
    def test_default_retries_and_isolated_environment_with_successful_second_request(self):
        calls = []
        def runner(command, **kwargs):
            calls.append((command, kwargs))
            env = kwargs["env"]
            root = Path(env["HOME"])
            self.assertNotEqual(root, Path.home())
            self.assertEqual(env["NO_PROXY"], "127.0.0.1,localhost")
            self.assertEqual(env["no_proxy"], env["NO_PROXY"])
            self.assertEqual(env["CODEX_HOME"], str(root / "codex-home"))
            self.assertNotIn("OPENAI_API_KEY", env)
            self.assertNotIn("CODEX_API_KEY", env)
            self.assertNotIn("CODEX_HOME_OLD", env)
            self.assertNotIn("HTTPS_PROXY", env)
            self.assertNotIn("OTEL_EXPORTER_OTLP_ENDPOINT", env)
            config = (Path(env["CODEX_HOME"]) / "config.toml").read_text()
            self.assertNotIn("request_max_retries", config)
            self.assertNotIn("stream_max_retries", config)
            self.assertIn('wire_api = "responses"', config)
            self.assertIn("check_for_update_on_startup = false", config)
            self.assertEqual(config.count("enabled = false"), 2)
            if command[-1] == "--version":
                return subprocess.CompletedProcess(command, 0, "codex-cli test-version\n", "")
            self.assertIn("exec", command)
            self.assertIn("--skip-git-repo-check", command)
            self.assertEqual(kwargs["timeout"], 480)
            base_url = json.loads(re.search(r"base_url = (.+)", config).group(1))
            request = urllib.request.Request(base_url + "/responses", method="POST",
                                             data=b'{"input":"hello"}')
            with urllib.request.urlopen(request) as response:
                self.assertIn(b"server_is_overloaded", response.read())
            with urllib.request.urlopen(request) as response:
                self.assertIn(b"response.completed", response.read())
            return subprocess.CompletedProcess(command, 0, "fixture succeeded", "")
        probe = run_probe(codex="/installed/codex", runner=runner, environ={
            "OPENAI_API_KEY": "never-forward", "CODEX_API_KEY": "never-forward",
            "CODEX_HOME_OLD": "/private", "HTTPS_PROXY": "http://outside",
            "OTEL_EXPORTER_OTLP_ENDPOINT": "http://outside"})
        self.assertEqual(probe.outcome, ProbeOutcome.SHIPPED)
        self.assertEqual(probe.turn_requests, 2)
        self.assertEqual(probe.version, "test-version")
        self.assertEqual(len(calls), 2)
        self.assertFalse(Path(calls[0][1]["env"]["HOME"]).exists())

    def test_missing_codex_and_timeout_are_errors_not_negative_results(self):
        result = run_probe(codex="/no-such-codex")
        self.assertEqual(result.outcome, ProbeOutcome.ERROR)
        self.assertEqual(result.turn_requests, 0)
        def timeout(command, **kwargs):
            if command[-1] == "--version":
                return subprocess.CompletedProcess(command, 0, "codex-cli test", "")
            raise subprocess.TimeoutExpired(command, kwargs["timeout"])
        result = run_probe(runner=timeout)
        self.assertEqual(result.outcome, ProbeOutcome.ERROR)
        self.assertIn("timed out", result.detail)

    def test_server_start_failure_is_a_probe_error(self):
        def unavailable(**kwargs):
            raise OSError("loopback unavailable")
        result = run_probe(server_factory=unavailable)
        self.assertEqual(result.outcome, ProbeOutcome.ERROR)
        self.assertIn("loopback unavailable", result.detail)

    def test_only_second_request_and_success_are_authoritative(self):
        for requests, code in ((0, 0), (1, 0), (2, 1)):
            result = classify_probe("test", requests, subprocess.CompletedProcess([], code, "", ""))
            self.assertEqual(result.outcome, ProbeOutcome.ERROR)
        unrelated = classify_probe("test", 1, subprocess.CompletedProcess([], 1, "", "bad config"))
        self.assertEqual(unrelated.outcome, ProbeOutcome.ERROR)

    def test_pr_status_is_unauthenticated_informational_and_bounded(self):
        def opener(request, **kwargs):
            self.assertEqual(request.full_url, PR_URL)
            self.assertNotIn("Authorization", request.headers)
            self.assertEqual(kwargs["timeout"], 15)
            return BytesIO(json.dumps({"state": "closed", "merged": True,
                                       "merged_at": "merge-time"}).encode())
        status = fetch_pr_status(opener)
        self.assertEqual((status.state, status.merged, status.merged_at),
                         ("closed", True, "merge-time"))
        def offline(*args, **kwargs):
            raise urllib.error.URLError("offline")
        self.assertIn("offline", fetch_pr_status(offline).error)
        self.assertTrue(fetch_pr_status(lambda *args, **kwargs: BytesIO(b"[]")).error)
        self.assertTrue(fetch_pr_status(lambda *args, **kwargs: BytesIO(b"not JSON")).error)


@unittest.skipUnless(CODEX.is_file(),
                     "Installed ~/.local/bin/codex absent; real-binary capacity probe requires it")
class InstalledCodexRetirementProbeTests(unittest.TestCase):
    def test_current_codex_fails_once_without_native_capacity_retry(self):
        result = run_probe(codex=CODEX, timeout=8, scenario=Scenario.ALWAYS_FAIL)
        self.assertEqual(result.outcome, ProbeOutcome.NEGATIVE, result)
        self.assertEqual(result.turn_requests, 1, result)
        self.assertIsNotNone(result.returncode)
        self.assertNotEqual(result.returncode, 0, result)
        self.assertNotEqual(result.version, "unknown")
