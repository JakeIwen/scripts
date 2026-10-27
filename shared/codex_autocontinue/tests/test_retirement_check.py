from contextlib import nullcontext
from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from shared.codex_autocontinue.retirement_actions import (
    NOTIFIER, PI_DISABLE_COMMAND, disable_mac, disable_pi, notify)
from shared.codex_autocontinue.retirement_check import Dependencies, run
from shared.codex_autocontinue.retirement_state import (
    PRStatus, ProbeOutcome, ProbeResult, RetirementState)
from shared.codex_autocontinue.retirement_store import RetirementStore
from shared.python import tool_settings


class MemoryStore:
    def __init__(self):
        self.data = RetirementState().as_dict()
        self.writes = 0

    def locked(self):
        return nullcontext()

    def load(self):
        return RetirementState.from_dict(json.loads(json.dumps(self.data)))

    def save(self, state):
        self.data = state.as_dict()
        self.writes += 1


class RetirementDecisionTests(unittest.TestCase):
    def setUp(self):
        self.store = MemoryStore()
        self.effects = []
        self.lines = []
        self.probe = ProbeResult(ProbeOutcome.NEGATIVE, "0.164.0", 1, 1)
        self.pr = PRStatus("open")
        self.dependencies = Dependencies(
            probe=lambda: self.probe, pr_status=lambda: self.pr,
            disable_mac=lambda: self.effects.append("mac"),
            disable_pi=lambda: self.effects.append("pi"),
            notify=lambda message: self.effects.append(message), now=lambda: "fixed-time")

    def check(self, **kwargs):
        return run(self.store, self.dependencies, emit=self.lines.append, **kwargs)

    def test_shipped_disables_both_and_notifies_once(self):
        self.probe = ProbeResult(ProbeOutcome.SHIPPED, "0.165.0", 2, 0)
        self.assertEqual(self.check(), 0)
        self.assertEqual(self.effects[:2], ["mac", "pi"])
        self.assertEqual(len(self.effects), 3)
        self.assertIn("Codex 0.165.0 now retries capacity errors itself", self.effects[-1])
        self.assertIn("Pi: ok", self.effects[-1])
        self.assertIn("remove _codex_tui", self.effects[-1])
        self.assertTrue(self.store.data["retired"])
        self.assertTrue(self.store.data["retirement_notified"])
        self.dependencies = replace(self.dependencies,
                                    probe=lambda: self.fail("retired job must not probe again"))
        self.assertEqual(self.check(), 0)
        self.assertEqual(len(self.effects), 3)
        self.assertEqual(len(self.lines), 2)

    def test_pi_failure_is_reported_and_retried_without_second_notification(self):
        def unavailable():
            raise RuntimeError("unreachable")
        self.probe = ProbeResult(ProbeOutcome.SHIPPED, "new", 2, 0)
        self.dependencies = replace(self.dependencies, disable_pi=unavailable)
        self.assertEqual(self.check(), 0)
        self.assertIn("Pi: failed", self.effects[-1])
        self.assertFalse(self.store.data["pi_disabled"])
        self.dependencies = replace(self.dependencies,
                                    disable_pi=lambda: self.effects.append("pi-retry"))
        self.assertEqual(self.check(), 0)
        self.assertEqual(self.effects[-1], "pi-retry")
        self.assertEqual(len(self.effects), 3)
        self.assertTrue(self.store.data["pi_disabled"])

    def test_mac_failure_does_not_claim_retirement_or_notify(self):
        def failed():
            raise ValueError("settings invalid")
        self.probe = ProbeResult(ProbeOutcome.SHIPPED, "new", 2, 0)
        self.dependencies = replace(self.dependencies, disable_mac=failed)
        self.assertEqual(self.check(), 1)
        self.assertFalse(self.store.data["retired"])
        self.assertEqual(self.effects, [])
        self.assertIn("settings invalid", self.lines[-1])

    def test_closed_unmerged_once_and_weekly_probe_continues(self):
        self.pr = PRStatus("closed", False)
        for _ in range(2):
            self.assertEqual(self.check(), 0)
        self.assertEqual(self.effects, [
            "Codex PR #31058 closed unmerged; weekly probe continues."])
        self.assertFalse(self.store.data["retired"])
        self.assertEqual(self.store.data["last_run"]["probe"]["turn_requests"], 1)

    def test_pr_merge_is_not_authoritative_and_network_failure_is_not_fatal(self):
        for pr in (PRStatus("closed", True, "merged-time"), PRStatus(error="offline")):
            self.pr = pr
            self.assertEqual(self.check(), 0)
        self.assertEqual(self.effects, [])

    def test_three_probe_errors_notify_once_until_clean_run(self):
        self.probe = ProbeResult(ProbeOutcome.ERROR, detail="timeout")
        self.pr = PRStatus("closed", False)
        for _ in range(2):
            self.assertEqual(self.check(), 1)
        self.assertEqual(self.effects, [])
        for _ in range(3):
            self.assertEqual(self.check(), 1)
        self.assertEqual(len(self.effects), 1)
        self.assertIn("failed 3 consecutive times", self.effects[-1])
        self.assertEqual(self.store.data["probe_error_streak"], 5)
        self.probe = ProbeResult(ProbeOutcome.NEGATIVE, "old", 1, 1)
        self.pr = PRStatus("open")
        self.assertEqual(self.check(), 0)
        self.assertEqual(self.store.data["probe_error_streak"], 0)
        self.assertFalse(self.store.data["probe_error_notified"])
        self.probe = ProbeResult(ProbeOutcome.ERROR)
        for _ in range(3):
            self.check()
        self.assertEqual(len(self.effects), 2)

    def test_dry_run_changes_nothing_and_prints_proposed_actions(self):
        self.probe = ProbeResult(ProbeOutcome.SHIPPED, "new", 2, 0)
        before = json.dumps(self.store.data)
        self.assertEqual(self.check(dry_run=True), 0)
        self.assertEqual(self.effects, [])
        self.assertEqual(self.store.writes, 0)
        self.assertEqual(json.dumps(self.store.data), before)
        self.assertIn("would disable Mac", self.lines[-1])
        self.assertIn("would disable Pi", self.lines[-1])
        self.assertIn("would notify retirement_notified", self.lines[-1])

    def test_notification_claim_precedes_delivery_even_when_delivery_fails(self):
        self.probe = ProbeResult(ProbeOutcome.SHIPPED, "new", 2, 0)
        calls = []
        def failed_delivery(message):
            calls.append(message)
            self.assertTrue(self.store.data["retirement_notified"])
            raise RuntimeError("delivery outcome unknown")
        self.dependencies = replace(self.dependencies, notify=failed_delivery)
        self.check()
        self.assertIn("not retried", self.lines[-1])
        self.check()
        self.assertEqual(len(calls), 1)


class RetirementStoreTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "private/retirement.json"
        self.store = RetirementStore(self.path)

    def test_state_survives_reopen_with_private_permissions(self):
        with self.store.locked():
            state = self.store.load()
            state.closed_unmerged_notified = True
            self.store.save(state)
            self.store.save(state)
        self.assertTrue(RetirementStore(self.path).load().closed_unmerged_notified)
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.path.parent.stat().st_mode & 0o777, 0o700)
        self.assertEqual(self.path.with_suffix(".lock").stat().st_mode & 0o777, 0o600)
        self.assertEqual(sorted(path.name for path in self.path.parent.iterdir()),
                         ["retirement.json", "retirement.lock"])

    def test_second_process_style_reopen_does_not_renotify(self):
        calls = []
        dependencies = Dependencies(
            probe=lambda: ProbeResult(ProbeOutcome.SHIPPED, "test", 2, 0),
            pr_status=lambda: PRStatus("open"), disable_mac=lambda: calls.append("mac"),
            disable_pi=lambda: calls.append("pi"), notify=lambda message: calls.append(message))
        self.assertEqual(run(self.store, dependencies, emit=lambda line: None), 0)
        self.assertEqual(run(RetirementStore(self.path), dependencies,
                             emit=lambda line: None), 0)
        self.assertEqual(len(calls), 3)

    def test_overlapping_run_is_refused(self):
        with self.store.locked():
            with self.assertRaises(BlockingIOError):
                with RetirementStore(self.path).locked():
                    self.fail("overlapping check acquired lock")

    def test_invalid_state_fails_closed_without_overwriting(self):
        self.path.parent.mkdir()
        self.path.write_text('{"schema": 99}')
        lines = []
        dependencies = Dependencies(probe=lambda: self.fail("must not probe invalid state"))
        self.assertEqual(run(self.store, dependencies, emit=lines.append), 1)
        self.assertEqual(self.path.read_text(), '{"schema": 99}')
        self.assertEqual(len(lines), 1)

    def test_dry_run_does_not_create_state_directory(self):
        dependencies = Dependencies(probe=lambda: ProbeResult(ProbeOutcome.NEGATIVE),
                                    pr_status=lambda: PRStatus())
        self.assertEqual(run(self.store, dependencies, dry_run=True, emit=lambda line: None), 0)
        self.assertFalse(self.path.parent.exists())


class RetirementActionTests(unittest.TestCase):
    def test_settings_api_preserves_other_overrides(self):
        with tempfile.TemporaryDirectory() as temporary:
            with patch.dict(os.environ, {"JACOBR_CONFIG_DIR": temporary}):
                tool_settings.save("codex-autocontinue", {"max_attempts": 5})
                disable_mac()
                settings = tool_settings.load("codex-autocontinue")
                self.assertIs(settings["enabled"], False)
                self.assertEqual(settings["max_attempts"], 5)

    def test_remote_and_phone_commands_are_bounded_and_use_documented_paths(self):
        calls = []
        def runner(command, **kwargs):
            calls.append((command, kwargs))
            return subprocess.CompletedProcess(command, 0)
        disable_pi(runner=runner)
        self.assertEqual(calls[0][0], ["/usr/bin/ssh", "-o", "BatchMode=yes", "-o",
                                     "ConnectTimeout=10", "vanpi.lan", PI_DISABLE_COMMAND])
        self.assertEqual(calls[0][1]["timeout"], 30)
        notify("one line", runner=runner)
        self.assertTrue(NOTIFIER.is_file())
        self.assertEqual(calls[1][0], ["/usr/bin/python3", str(NOTIFIER), "--send",
                                     "Codex autocontinue", "one line", "default"])
        self.assertEqual(calls[1][1]["timeout"], 50)

    def test_adapter_failures_are_not_silenced(self):
        def runner(command, **kwargs):
            return subprocess.CompletedProcess(command, 1)
        with self.assertRaises(RuntimeError):
            disable_pi(runner=runner)
        with self.assertRaises(RuntimeError):
            notify("message", runner=runner)

    def test_executable_help_works_outside_checkout(self):
        entry = Path(__file__).resolve().parents[1] / "retirement-check"
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "json.py").write_text('raise RuntimeError("unsafe cwd import")')
            result = subprocess.run([str(entry), "--help"], cwd=directory,
                                    capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--dry-run", result.stdout)
