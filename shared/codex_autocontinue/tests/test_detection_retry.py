import json
import random
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from shared.python import tool_settings
from shared.codex_autocontinue.backoff import delay
from shared.codex_autocontinue.detector import (
    ERROR_WINDOW_CHARACTERS, CapacityDetector, SubmissionDetector,
)
from shared.codex_autocontinue.retry_state import RetryState


ERROR = "■ Selected model is at capacity. Please try a different model.".encode("utf-8")


def state_osc(state):
    return ("\x1b]7501;state={}:app=codex\x1b\\".format(state)).encode("ascii")


def settings(**overrides):
    values = tool_settings.load_declaration("codex-autocontinue").defaults()
    values.update(overrides)
    return values


class DetectorTests(unittest.TestCase):
    def test_utf8_ansi_and_all_chunk_boundaries(self):
        data = b"\x1b[31m" + ERROR[:3] + b"\x1b[0m" + ERROR[3:] + b"\r\n"
        for split in range(len(data) + 1):
            detector = CapacityDetector()
            count = detector.feed(data[:split]).capacity_errors
            self.assertEqual(count + detector.feed(data[split:]).capacity_errors, 1, split)
        detector = CapacityDetector()
        self.assertEqual(sum(detector.feed(bytes([byte])).capacity_errors for byte in data), 1)

    def test_redraws_latch_until_a_new_turn(self):
        detector = CapacityDetector()
        self.assertEqual(detector.feed(ERROR).capacity_errors, 1)
        self.assertEqual(detector.feed(b"\r\x1b[2K" + ERROR + b"\r\n" + ERROR).capacity_errors, 0)
        detector.new_turn()
        self.assertEqual(detector.feed(b"\x1b[3;1H" + ERROR).capacity_errors, 1)

    def test_all_alternate_screen_modes_suppress_old_errors(self):
        for mode in (b"47", b"1047", b"1049", b"1049;25"):
            detector = CapacityDetector()
            stream = b"\x1b[?" + mode + b"h" + ERROR + b"\x1b[?" + mode + b"l"
            self.assertEqual(sum(detector.feed(bytes([byte])).capacity_errors for byte in stream), 0)
            self.assertFalse(detector.alternate_screen)
            self.assertEqual(detector.feed(ERROR).capacity_errors, 1)

    def test_overlay_roundtrip_does_not_rearm(self):
        detector = CapacityDetector()
        self.assertEqual(detector.feed(ERROR).capacity_errors, 1)
        events = detector.feed(b"\x1b[?1049h" + ERROR + b"\x1b[?1049l\r" + ERROR)
        self.assertEqual(events.capacity_errors, 0)

    def test_composer_quotes_other_errors_and_pasted_wrap_do_not_trigger(self):
        for prefix in ("› ", "> ", "• ", "The error was: "):
            self.assertEqual(CapacityDetector().feed(prefix.encode() + ERROR).capacity_errors, 0)
        for text in ("■ Usage limit reached.", "■ Authentication failed.", "Selected model is at capacity."):
            self.assertEqual(CapacityDetector().feed(text.encode()).capacity_errors, 0)
        detector = CapacityDetector()
        detector.user_activity()
        self.assertEqual(detector.feed(b"\r\n" + ERROR).capacity_errors, 0)
        detector.new_turn()
        self.assertEqual(detector.feed(b"\r\n" + ERROR).capacity_errors, 1)

    def test_osc_and_dcs_contents_do_not_match(self):
        for start, end in ((b"\x1b]0;", b"\a"), (b"\x1bP", b"\x1b\\")):
            detector = CapacityDetector()
            self.assertEqual(detector.feed(start + ERROR + end).capacity_errors, 0)
            self.assertEqual(detector.feed(ERROR).capacity_errors, 1)

    def test_state_gated_fragmented_error_at_every_chunk_boundary_in_both_modes(self):
        fragmented = b"\x1b[31m" + b"\x1b[15;11H".join(ERROR.split()) + b"\x1b[0m"
        for screen in (b"", b"\x1b[?1049h"):
            data = screen + state_osc("working") + state_osc("error") + fragmented
            for split in range(len(data) + 1):
                detector = CapacityDetector()
                count = detector.feed(data[:split]).capacity_errors
                self.assertEqual(count + detector.feed(data[split:]).capacity_errors, 1, split)
            detector = CapacityDetector()
            self.assertEqual(sum(detector.feed(bytes([byte])).capacity_errors for byte in data), 1)

    def test_state_gated_redraws_never_trigger_without_a_fresh_error_signal(self):
        for screen in (b"", b"\x1b[?1049h"):
            detector = CapacityDetector()
            detector.feed(screen + state_osc("working"))
            self.assertEqual(detector.feed(ERROR).capacity_errors, 0)
            self.assertEqual(detector.feed(state_osc("error") + ERROR).capacity_errors, 1)
            self.assertEqual(detector.feed(b"\r" + ERROR).capacity_errors, 0)
            detector.new_turn()
            redraw = (b"\x1b[?1049h\x1b[2J" + ERROR
                      + b"\x1b[?1049l\x1b[1;1H" + ERROR)
            self.assertEqual(detector.feed(redraw).capacity_errors, 0)
            self.assertEqual(detector.feed(state_osc("error") + ERROR).capacity_errors, 1)

    def test_next_state_or_bounded_output_expires_the_error_window(self):
        for ending in (state_osc("idle"), state_osc("working"), state_osc("unknown"),
                       b"x" * ERROR_WINDOW_CHARACTERS,
                       b"\x1b[2;1H" * ERROR_WINDOW_CHARACTERS,
                       b"\x1b7" * ERROR_WINDOW_CHARACTERS,
                       b"\x1b]0;" + b"x" * ERROR_WINDOW_CHARACTERS + b"\a"):
            detector = CapacityDetector()
            detector.feed(state_osc("error"))
            self.assertEqual(detector.feed(ending + ERROR).capacity_errors, 0)
            self.assertEqual(detector.feed(state_osc("error") + ERROR).capacity_errors, 1)

    def test_state_gate_does_not_accept_other_errors_or_osc_contents(self):
        detector = CapacityDetector()
        data = (state_osc("error") + b"Authentication failed."
                + b"\x1b]0;" + ERROR + b"\a" + b"\x1bP" + ERROR + b"\x1b\\"
                + state_osc("idle") + ERROR)
        self.assertEqual(detector.feed(data).capacity_errors, 0)

    def test_user_activity_and_submitted_quotes_remain_suppressed_with_state_signals(self):
        detector = CapacityDetector()
        detector.feed(b"\x1b[?1049h" + state_osc("working") + state_osc("error"))
        detector.user_activity()
        self.assertEqual(detector.feed(ERROR).capacity_errors, 0)
        self.assertEqual(detector.feed(state_osc("error") + ERROR).capacity_errors, 0)
        detector.new_turn()
        self.assertEqual(detector.feed(state_osc("error") + ERROR).capacity_errors, 1)

    def test_success_requires_done_signal_in_both_modes_once_signals_are_seen(self):
        for screen in (b"", b"\x1b[?1049h"):
            detector = CapacityDetector()
            for data in (screen + state_osc("working"), b"Ask Codex to do anything",
                         b"\r\nPartial assistant output", state_osc("error")):
                self.assertEqual(detector.feed(data).completed_turns, 0)
            done = state_osc("done")
            self.assertEqual(sum(detector.feed(bytes([byte])).completed_turns for byte in done), 1)
            self.assertEqual(detector.feed(done).completed_turns, 0)
            detector.new_turn()
            self.assertEqual(detector.feed(b"\r\n  Worked for 1m 3s").completed_turns, 0)
            self.assertEqual(detector.feed(done).completed_turns, 1)

    def test_no_state_signal_keeps_inline_completion_fallback(self):
        for line in (b"Worked for <1s", b"  Worked for 1m 3s"):
            detector = CapacityDetector()
            self.assertEqual(detector.feed(b"Partial assistant output").completed_turns, 0)
            self.assertEqual(detector.feed(b"\r\n" + line).completed_turns, 1)
            self.assertEqual(detector.feed(b"\r\n" + line).completed_turns, 0)

    def test_other_apps_state_signals_do_not_disable_inline_fallback(self):
        detector = CapacityDetector()
        detector.feed(b"\x1b]7501;state=working:app=other\a")
        self.assertEqual(detector.feed(ERROR).capacity_errors, 1)

    def test_submit_ignores_bracketed_paste_newlines_and_cursor_reply(self):
        detector = SubmissionDetector()
        self.assertFalse(detector.feed(b"\x1b[1;1R"))
        self.assertFalse(detector.feed(b"\x1b[20"))
        self.assertFalse(detector.feed(b"0~hello\r\nworld\x1b[201~"))
        self.assertTrue(detector.feed(b"\r"))

    def test_submitted_quote_is_marked_even_after_bracketed_paste(self):
        detector = SubmissionDetector()
        data = b"\x1b[200~quote\n" + ERROR + b"\x1b[201~\r"
        self.assertTrue(detector.feed(data))
        self.assertTrue(detector.submitted_capacity_quote)
        self.assertTrue(detector.feed(b"continue\r"))
        self.assertFalse(detector.submitted_capacity_quote)


class RetryTests(unittest.TestCase):
    def test_linear_formula_with_symmetric_jitter(self):
        values = settings(jitter=0)
        self.assertEqual([delay(i, values, random.Random(1)) for i in range(1, 13)],
                         [30 * i for i in range(1, 13)])
        values["jitter"] = 0.2
        samples = [delay(attempt, values, random.Random(seed))
                   for attempt in range(1, 13) for seed in range(50)]
        for index, actual in enumerate(samples):
            nominal = 30 * (index // 50 + 1)
            self.assertGreaterEqual(actual, nominal * 0.8)
            self.assertLessEqual(actual, nominal * 1.2)
        self.assertTrue(any(actual < 30 * (index // 50 + 1) for index, actual in enumerate(samples)))
        with self.assertRaises(ValueError):
            delay(0, values, random.Random())

    def test_wait_then_type_then_delayed_enter(self):
        state = RetryState()
        values = settings(base_seconds=2, jitter=0, submit_pause_seconds=0.2)
        state.capacity_error(10, values, random.Random())
        self.assertEqual(state.title(10, values), "codex: retry 1/12 in 2s")
        self.assertEqual(state.tick(11.9, values), b"")
        self.assertEqual(state.tick(12, values), b"continue")
        self.assertEqual(state.tick(12.19, values), b"")
        self.assertEqual(state.tick(12.21, values), b"\r")
        self.assertEqual(state.attempts, 1)
        self.assertEqual(state.title(13, values), "")

    def test_cancel_in_wait_or_between_text_and_enter(self):
        for typing in (False, True):
            state = RetryState()
            values = settings(base_seconds=1, jitter=0)
            state.capacity_error(0, values, random.Random())
            if typing:
                self.assertEqual(state.tick(1, values), b"continue")
            state.cancel()
            self.assertEqual(state.tick(100, values), b"")
            self.assertEqual(state.attempts, 0)

    def test_only_completed_turn_resets_streak_and_attempt_budget(self):
        state = RetryState()
        values = settings(base_seconds=1, step_seconds=0, jitter=0, max_attempts=2)
        for now in (0, 5):
            state.capacity_error(now, values, random.Random())
            self.assertEqual(state.tick(now + 3, values), b"continue")
            self.assertEqual(state.tick(now + 4, values), b"\r")
        state.capacity_error(10, values, random.Random())
        self.assertEqual(state.tick(20, values), b"")
        self.assertTrue(state.bell_pending)
        self.assertEqual(state.title(20, values), "codex: gave up after 2")
        state.bell_pending = False
        state.capacity_error(10000, values, random.Random())
        self.assertFalse(state.bell_pending)
        self.assertEqual(state.attempts, 2)
        state.completed_turn()
        state.capacity_error(10001, values, random.Random())
        self.assertEqual(state.attempts, 0)
        self.assertFalse(state.gave_up)
        self.assertEqual(state.tick(10002, values), b"continue")

    def test_disabled_setting_cancels_wait(self):
        values = settings(base_seconds=1, jitter=0)
        state = RetryState()
        state.capacity_error(0, values, random.Random())
        values["enabled"] = False
        self.assertEqual(state.tick(20, values), b"")
        self.assertIsNone(state.due)

    def test_reloader_uses_values_file_without_global_test_overrides(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict("os.environ", {"JACOBR_CONFIG_DIR": directory}):
                reloader = tool_settings.Reloader("codex-autocontinue")
                path = Path(directory) / "codex-autocontinue.json"
                path.write_text(json.dumps({"enabled": False}))
                self.assertFalse(reloader.poll().values["enabled"])
                path.write_text("{")
                result = reloader.poll()
                self.assertIsNotNone(result.error)
                self.assertFalse(result.values["enabled"])
