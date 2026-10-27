import shutil
import tempfile
import unittest

from shared.codex_autocontinue.demo_environment import create_environment
from shared.codex_autocontinue.fake_server import ResponsesServer, Scenario
from shared.codex_autocontinue.tests.pty_driver import LAUNCHER, PTYDriver


CODEX = shutil.which("codex")


@unittest.skipUnless(CODEX, "Codex binary is absent; real-TUI integration requires codex on PATH")
class RealCodexTests(unittest.TestCase):
    alternate_screen = False

    def session(self, scenario=Scenario.FAIL_THEN_SUCCEED, failures=1, **overrides):
        temporary = tempfile.TemporaryDirectory(prefix="codex-capacity-test-")
        self.addCleanup(temporary.cleanup)
        server = ResponsesServer(scenario, failures=failures).start()
        self.addCleanup(server.stop)
        values = {"enabled": True, "base_seconds": 0.15, "step_seconds": 0,
                  "jitter": 0, "submit_pause_seconds": 0.2}
        values.update(overrides)
        fixture = create_environment(temporary.name, server.base_url, values)
        command = fixture.command(CODEX, alternate_screen=self.alternate_screen)
        # The state signal must not depend on iTerm being the outer terminal.
        environment = {key: value for key, value in fixture.environ.items()
                       if not key.startswith(("ITERM_", "TERM_PROGRAM", "LC_TERMINAL"))}
        driver = PTYDriver([str(LAUNCHER), "--"] + command, environment, fixture.project)
        self.addCleanup(driver.close)
        driver.until(lambda: "›".encode() in driver.output, timeout=15)
        self.assertEqual(b"\x1b[?1049h" in driver.output, self.alternate_screen)
        return server, driver

    def test_capacity_failure_is_resumed_by_submitted_continue(self):
        server, driver = self.session(max_attempts=1)
        driver.submit("Say hello from the offline fixture")
        driver.until(lambda: len(server.turn_requests()) >= 2, timeout=20)
        records = server.turn_requests()
        self.assertEqual(len(records), 2)
        self.assertFalse(records[0].contains_continue)
        self.assertTrue(records[1].contains_continue, records[1])
        driver.until(lambda: b"7501;state=done:app=codex" in driver.output)
        driver.pause(0.3)
        self.assertEqual(len(server.turn_requests()), 2)
        # A successful turn resets the single-retry budget; mere time passing must not.
        server.failures = 3
        driver.submit("A new task after the successful turn")
        driver.until(lambda: len(server.turn_requests()) >= 4)
        self.assertTrue(server.turn_requests()[3].contains_continue)
        driver.until(lambda: bytes(driver.output).count(b"7501;state=done:app=codex") >= 2)
        driver.quit_codex()
        self.assertEqual(driver.process.returncode, 0, bytes(driver.output[-3000:]))
        self.assertIn(b"7501;state=done:app=codex", driver.output)
        self.assertGreaterEqual(bytes(driver.output).count(b"codex: retry 1/1"), 2)
        self.assertIn(b"\x1b[23;0t", driver.output)

    def test_repeated_errors_give_up_and_transcript_overlay_never_retries(self):
        server, driver = self.session(Scenario.ALWAYS_FAIL, max_attempts=2)
        driver.submit("Exercise the retry limit")
        driver.until(lambda: b"codex: gave up after 2" in driver.output)
        self.assertEqual(len(server.turn_requests()), 3)
        driver.send(b"\x14")
        driver.pause(0.5)
        driver.send(b"q")
        driver.pause(0.5)
        self.assertEqual(len(server.turn_requests()), 3)
        driver.quit_codex()
        self.assertEqual(driver.process.returncode, 0)

    def test_partial_output_does_not_reset_the_attempt_budget(self):
        server, driver = self.session(Scenario.MID_STREAM, failures=3, max_attempts=1)
        driver.submit("Fail after some streamed text")
        driver.until(lambda: b"codex: gave up after 1" in driver.output)
        self.assertEqual(len(server.turn_requests()), 2)
        self.assertTrue(server.turn_requests()[1].contains_continue)
        self.assertNotIn(b"7501;state=done:app=codex", driver.output)
        driver.pause(0.5)
        self.assertEqual(len(server.turn_requests()), 2)
        driver.quit_codex()
        self.assertEqual(driver.process.returncode, 0)

    def test_a_real_keystroke_cancels_the_retry(self):
        server, driver = self.session(base_seconds=1)
        driver.submit("Wait for me to take over")
        driver.until(lambda: b"codex: retry" in driver.output)
        driver.send(b"x")
        driver.pause(1.4)
        self.assertEqual(len(server.turn_requests()), 1)
        self.assertFalse(server.turn_requests()[0].contains_continue)
        driver.quit_codex()
        self.assertEqual(driver.process.returncode, 0)

    def test_transcript_overlay_cancels_pending_retry_without_replaying_old_errors(self):
        server, driver = self.session(Scenario.ALWAYS_FAIL, base_seconds=1)
        driver.submit("Wait while I open the transcript")
        driver.until(lambda: b"codex: retry" in driver.output)
        driver.send(b"\x14")
        driver.pause(1.4)
        self.assertEqual(len(server.turn_requests()), 1)
        driver.send(b"q")
        driver.pause(1.4)
        self.assertEqual(len(server.turn_requests()), 1)
        self.assertFalse(server.turn_requests()[0].contains_continue)
        driver.quit_codex()
        self.assertEqual(driver.process.returncode, 0)


class FullScreenRealCodexTests(RealCodexTests):
    alternate_screen = True
