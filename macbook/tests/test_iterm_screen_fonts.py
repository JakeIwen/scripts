"""Display matching, settle/resize decisions and config validation for the iTerm font follower."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

SOURCE = Path(__file__).resolve().parents[1] / "scripts/iterm_screen_fonts.py"
SPEC = importlib.util.spec_from_file_location("iterm_screen_fonts", SOURCE)
fonts = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = fonts  # dataclasses resolve annotations through sys.modules
SPEC.loader.exec_module(fonts)

# What macOS reports on m4mac: the laptop panel (its hidden mirror folded in)
# with the Q24h above it.
SCREENS_JSON = json.dumps([
    {"name": "Built-in Retina Display", "x": 0, "y": 0, "w": 1728, "h": 1117},
    {"name": "Q24h-10", "x": 0, "y": 1117, "w": 2560, "h": 1440},
])


class DisplayMatchingTests(unittest.TestCase):
    def setUp(self):
        self.screens = fonts.parse_screens(SCREENS_JSON)

    def test_windows_map_to_the_display_holding_them(self):
        self.assertEqual(fonts.display_kind_for((0, 0, 864, 1084), self.screens), fonts.BUILT_IN)
        self.assertEqual(fonts.display_kind_for((1280, 1117, 1280, 1410), self.screens), fonts.EXTERNAL)

    def test_a_window_straddling_both_belongs_to_the_larger_share(self):
        mostly_external = (100, 1000, 800, 900)
        self.assertEqual(fonts.display_kind_for(mostly_external, self.screens), fonts.EXTERNAL)
        mostly_laptop = (100, 200, 800, 1000)
        self.assertEqual(fonts.display_kind_for(mostly_laptop, self.screens), fonts.BUILT_IN)

    def test_a_window_off_every_display_has_no_kind(self):
        self.assertIsNone(fonts.display_kind_for((5000, 5000, 400, 300), self.screens))


class WindowTrackerTests(unittest.TestCase):
    def setUp(self):
        self.tracker = fonts.WindowTracker(settle_polls=2)

    def observe(self, kind, sessions):
        return self.tracker.observe("w1", kind, set(sessions))

    def test_resizes_every_session_once_the_window_settles(self):
        self.assertEqual(self.observe(fonts.EXTERNAL, {"a", "b"}), (None, set()))
        self.assertEqual(self.observe(fonts.EXTERNAL, {"a", "b"}), (fonts.EXTERNAL, {"a", "b"}))
        self.assertEqual(self.observe(fonts.EXTERNAL, {"a", "b"}), (None, set()))

    def test_a_new_tab_on_a_settled_window_gets_the_size_alone(self):
        self.observe(fonts.EXTERNAL, {"a"})
        self.observe(fonts.EXTERNAL, {"a"})
        self.assertEqual(self.observe(fonts.EXTERNAL, {"a", "b"}), (fonts.EXTERNAL, {"b"}))

    def test_moving_to_the_other_display_resizes_everything_after_settling(self):
        for _ in range(2):
            self.observe(fonts.EXTERNAL, {"a", "b"})
        self.assertEqual(self.observe(fonts.BUILT_IN, {"a", "b"}), (None, set()))
        self.assertEqual(self.observe(fonts.BUILT_IN, {"a", "b"}), (fonts.BUILT_IN, {"a", "b"}))

    def test_passing_over_the_boundary_mid_drag_changes_nothing(self):
        for _ in range(2):
            self.observe(fonts.EXTERNAL, {"a"})
        self.observe(fonts.BUILT_IN, {"a"})
        self.assertEqual(self.observe(fonts.EXTERNAL, {"a"}), (None, set()))
        self.assertEqual(self.observe(fonts.EXTERNAL, {"a"}), (None, set()))

    def test_reset_reapplies_to_settled_windows(self):
        for _ in range(2):
            self.observe(fonts.EXTERNAL, {"a"})
        self.tracker.reset()
        self.observe(fonts.EXTERNAL, {"a"})
        self.assertEqual(self.observe(fonts.EXTERNAL, {"a"}), (fonts.EXTERNAL, {"a"}))


class FontAndConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "sizes.json"

    def test_font_size_changes_and_name_stays(self):
        self.assertEqual(fonts.font_with_size("Monaco 12", 15), "Monaco 15")
        self.assertEqual(fonts.font_with_size("JetBrainsMonoNF-Regular 13", 12.5), "JetBrainsMonoNF-Regular 12.5")

    def test_missing_config_uses_defaults_and_a_partial_one_overrides(self):
        self.assertEqual(fonts.load_sizes(self.path), fonts.DEFAULT_SIZES)
        self.path.write_text(json.dumps({fonts.EXTERNAL: 18}))
        sizes = fonts.load_sizes(self.path)
        self.assertEqual(sizes[fonts.EXTERNAL], 18)
        self.assertEqual(sizes[fonts.BUILT_IN], fonts.DEFAULT_SIZES[fonts.BUILT_IN])

    def test_bad_config_is_rejected(self):
        for bad in ({"externall": 15}, {fonts.EXTERNAL: True}, {fonts.EXTERNAL: 500}, [15]):
            self.path.write_text(json.dumps(bad))
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                fonts.load_sizes(self.path)


if __name__ == "__main__":
    unittest.main()
