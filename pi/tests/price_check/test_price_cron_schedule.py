import subprocess
import tempfile
import unittest
from pathlib import Path

from pi.scripts.price_check.cron_schedule import (
    PRICE_CHECK_COMMAND,
    CronScheduleError,
    CronScheduleManager,
)


BASE_CRONTAB = (
    "MAILTO=\n"
    "5 4 * * * /usr/local/bin/other-job\n"
    f"0 10,15,20 * * * {PRICE_CHECK_COMMAND}\n"
)


class FakeCommands:
    def __init__(self):
        self.current = BASE_CRONTAB
        self.description = "At minute 0 past hours 10, 15 and 20"
        self.rate_limited = False
        self.reject_candidate = False
        self.fail_install = False
        self.fail_restore = False
        self.calls = []

    @staticmethod
    def result(args, returncode=0, stdout="", stderr=""):
        return subprocess.CompletedProcess(args, returncode, stdout, stderr)

    def __call__(self, args, **_kwargs):
        self.calls.append(list(args))
        if args[0] == "/test/parse_cron.sh":
            if self.rate_limited:
                return self.result(args, returncode=75, stderr="cron parser rate limited")
            if self.description:
                return self.result(args, stdout=f"{self.description}\n")
            return self.result(args, returncode=1, stderr="unresolved")
        if args == ["/test/crontab", "-l"]:
            return self.result(args, stdout=self.current)
        if args[:2] == ["/test/crontab", "-n"]:
            if self.reject_candidate:
                return self.result(args, returncode=1, stderr="bad minute")
            return self.result(args)
        if args[0] == "/test/crontab" and len(args) == 2:
            path = Path(args[1])
            if path.name == "candidate.crontab" and self.fail_install:
                self.current = "damaged during failed install\n"
                return self.result(args, returncode=1, stderr="install failed")
            if path.name == "previous.crontab" and self.fail_restore:
                return self.result(args, returncode=1, stderr="restore failed")
            self.current = path.read_text(encoding="utf-8")
            return self.result(args)
        raise AssertionError(f"unexpected command: {args}")


class CronScheduleManagerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.commands = FakeCommands()
        self.manager = CronScheduleManager(
            crontab_bin="/test/crontab",
            parser=Path("/test/parse_cron.sh"),
            runner=self.commands,
            temporary_directory=self.temporary.name,
        )

    def tearDown(self):
        self.temporary.cleanup()

    def test_status_returns_raw_expression_and_cronp_description(self):
        status = self.manager.status()
        self.assertEqual(status["expression"], "0 10,15,20 * * *")
        self.assertEqual(status["description"], self.commands.description)
        self.assertIsNone(status["error"])
        self.assertIsNone(status["error_code"])

    def test_status_keeps_expression_when_cronp_is_unavailable(self):
        self.commands.description = ""
        status = self.manager.status()
        self.assertEqual(status["expression"], "0 10,15,20 * * *")
        self.assertEqual(status["description"], "")
        self.assertIn("could not resolve", status["error"])
        self.assertEqual(status["error_code"], "parse")

    def test_rate_limit_is_tagged_for_dashboard_retry(self):
        self.commands.rate_limited = True
        status = self.manager.status()
        preview = self.manager.preview("30 8,16 * * 1-5")
        self.assertEqual(status["error_code"], "rate_limit")
        self.assertEqual(preview["error_code"], "rate_limit")
        self.assertEqual(preview["expression"], "30 8,16 * * 1-5")

    def test_preview_returns_parse_error_without_reading_crontab(self):
        preview = self.manager.preview("not a cron")
        self.assertEqual(preview["error_code"], "parse")
        self.assertIn("five fields", preview["error"])
        self.assertEqual(self.commands.calls, [])

    def test_update_preserves_unrelated_crontab_lines(self):
        result = self.manager.update("30 8,16 * * 1-5")
        self.assertEqual(result["expression"], "30 8,16 * * 1-5")
        self.assertIn("5 4 * * * /usr/local/bin/other-job\n", self.commands.current)
        self.assertIn(
            f"30 8,16 * * 1-5 {PRICE_CHECK_COMMAND}\n", self.commands.current
        )
        self.assertTrue(
            any(call[:2] == ["/test/crontab", "-n"] for call in self.commands.calls)
        )

    def test_unresolved_cronp_never_attempts_crontab_edit(self):
        self.commands.description = ""
        with self.assertRaisesRegex(CronScheduleError, "crontab was not changed"):
            self.manager.update("30 8,16 * * 1-5")
        edits = [
            call
            for call in self.commands.calls
            if call[0] == "/test/crontab" and call[1:] != ["-l"]
        ]
        self.assertEqual(edits, [])
        self.assertEqual(self.commands.current, BASE_CRONTAB)

    def test_dry_run_rejection_does_not_install(self):
        self.commands.reject_candidate = True
        with self.assertRaisesRegex(CronScheduleError, "was not changed"):
            self.manager.update("99 8 * * *")
        installs = [
            call
            for call in self.commands.calls
            if call[0] == "/test/crontab"
            and len(call) == 2
            and call[1] not in ("-l", "-n")
        ]
        self.assertEqual(installs, [])
        self.assertEqual(self.commands.current, BASE_CRONTAB)

    def test_install_failure_restores_previous_crontab(self):
        self.commands.fail_install = True
        with self.assertRaisesRegex(CronScheduleError, "previous crontab restored"):
            self.manager.update("30 8,16 * * 1-5")
        self.assertEqual(self.commands.current, BASE_CRONTAB)

    def test_restore_failure_is_reported_to_the_user(self):
        self.commands.fail_install = True
        self.commands.fail_restore = True
        with self.assertRaisesRegex(CronScheduleError, "automatic restore also failed"):
            self.manager.update("30 8,16 * * 1-5")
        self.assertNotEqual(self.commands.current, BASE_CRONTAB)

    def test_rejects_non_five_field_expression_before_cronp(self):
        with self.assertRaisesRegex(CronScheduleError, "exactly five fields"):
            self.manager.update("@daily")
        self.assertEqual(self.commands.calls, [])


if __name__ == "__main__":
    unittest.main()
