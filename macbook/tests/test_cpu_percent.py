"""cpu_percent turns kernel tick counters into one whole-machine busy percentage."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import cpu_percent as cpu

SCRIPT = Path(cpu.__file__)


class ArithmeticTests(unittest.TestCase):
    def test_busy_share_of_the_tick_delta(self):
        self.assertEqual(cpu.busy_percent((100, 1000), (400, 2000)), 70.0)
        self.assertEqual(cpu.busy_percent((0, 0), (500, 1000)), 50.0)

    def test_no_elapsed_ticks_or_backwards_counters_have_no_value(self):
        self.assertIsNone(cpu.busy_percent((10, 100), (10, 100)))
        self.assertIsNone(cpu.busy_percent((10, 100), (5, 200)))
        self.assertIsNone(cpu.busy_percent((10, 100), (20, 50)))

    def test_clamped_and_formatted_as_whole_percent(self):
        self.assertEqual(cpu.busy_percent((0, 0), (0, 10)), 100.0)
        self.assertEqual(cpu.busy_percent((0, 0), (11, 10)), 0.0)  # idle ahead of total
        self.assertEqual(cpu.format_percent(36.6), "37%")
        self.assertEqual(cpu.format_percent(104), "100%")
        self.assertEqual(cpu.format_percent(-3), "0%")
        self.assertEqual(cpu.format_percent(None), "–%")


class StateTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name) / "nested" / "sample.json"

    def run_measure(self, readings, now=1000.0):
        queue, sleeps = list(readings), []
        clock_values = iter([now, now + 0.25, now + 0.25])
        value = cpu.measure(self.path, reader=lambda: queue.pop(0),
                            clock=lambda: next(clock_values), sleeper=sleeps.append)
        return value, sleeps

    def test_first_run_takes_two_samples_and_saves_the_second(self):
        value, sleeps = self.run_measure([(0, 0), (50, 100)])
        self.assertEqual(value, 50.0)
        self.assertEqual(sleeps, [cpu.FALLBACK_GAP])
        self.assertEqual(json.loads(self.path.read_text())["total"], 100)

    def test_recent_saved_sample_is_used_without_sleeping(self):
        cpu.save_sample(self.path, (100, 1000), 990.0)
        value, sleeps = self.run_measure([(200, 1400)])
        self.assertEqual(value, 75.0)
        self.assertEqual(sleeps, [])
        self.assertEqual(json.loads(self.path.read_text())["idle"], 200)

    def test_stale_or_future_sample_is_ignored(self):
        for taken in (1000.0 - cpu.MAX_SAMPLE_AGE - 1, 2000.0):
            cpu.save_sample(self.path, (100, 1000), taken)
            self.assertIsNone(cpu.load_previous(self.path, 1000.0))

    def test_corrupt_or_incomplete_state_is_ignored(self):
        self.path.parent.mkdir(parents=True)
        for text in ("", "not json", "[1]", '{"idle": 1}', '{"idle":"x","total":1,"time":1000}'):
            self.path.write_text(text)
            self.assertIsNone(cpu.load_previous(self.path, 1000.0), text)
        value, sleeps = self.run_measure([(0, 0), (10, 100)])
        self.assertEqual((value, sleeps), (90.0, [cpu.FALLBACK_GAP]))

    def test_save_leaves_no_temp_files(self):
        cpu.save_sample(self.path, (1, 2), 5.0)
        self.assertEqual([p.name for p in self.path.parent.iterdir()], ["sample.json"])


@unittest.skipUnless(sys.platform == "darwin", "reads the macOS Mach CPU counters")
class RealCounterTests(unittest.TestCase):
    def test_real_read_is_a_plausible_busy_percentage(self):
        first, second = cpu.read_ticks(), cpu.read_ticks()
        self.assertGreater(first[1], 0)
        self.assertLessEqual(first[0], first[1])
        value = cpu.busy_percent(first, second)
        self.assertTrue(value is None or 0 <= value <= 100)

    def test_script_prints_one_percent_line(self):
        with tempfile.TemporaryDirectory() as home:  # keep the sample out of the real ~/.local/state
            result = subprocess.run([sys.executable, "-I", "-B", str(SCRIPT)], env={"HOME": home},
                                    capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0)
        self.assertRegex(result.stdout, r"^(\d{1,3}|–)%\n$")


if __name__ == "__main__":
    unittest.main()
