import pathlib
import unittest


class LightingTileStaticTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = pathlib.Path(__file__).resolve().parents[3]
        dashboard = root / "pi" / "apps" / "van_dashboard"
        cls.template = (dashboard / "templates" / "van_dashboard.html").read_text()
        cls.javascript = (dashboard / "static" / "van_dashboard.js").read_text()
        cls.stylesheet = (dashboard / "static" / "van_dashboard.css").read_text()

    def test_main_tile_has_only_the_requested_room_controls(self):
        for room in ("cab", "rear", "kitchen"):
            self.assertIn(f'data-lighting-quick-room="{room}"', self.template)
            self.assertIn(f'data-light-target="group:{room}"', self.template)
            self.assertIn(f'data-light-group-brightness="{room}"', self.template)
        self.assertEqual(self.template.count("data-lighting-quick-room="), 3)
        self.assertNotIn('data-lighting-quick-room="exterior"', self.template)
        self.assertNotIn('data-lighting-quick-room="solder"', self.template)
        self.assertNotIn('data-lighting-quick-room="extra"', self.template)

    def test_room_brightness_uses_reported_available_entities(self):
        self.assertIn(
            "const LIGHTING_QUICK_GROUPS = new Set(['cab', 'rear', 'kitchen'])",
            self.javascript,
        )
        self.assertIn("function renderLightingQuick(next)", self.javascript)
        self.assertIn("function changeLightGroupBrightness(groupId, brightness)", self.javascript)
        self.assertIn(".filter((light) => light.available)", self.javascript)
        self.assertIn("post('lights/brightness', { entity, brightness })", self.javascript)
        self.assertIn("await refreshLighting(false)", self.javascript)

    def test_quick_controls_have_compact_tile_styles(self):
        self.assertIn(".lighting-quick-row", self.stylesheet)
        self.assertIn(".lighting-room-power", self.stylesheet)
        self.assertIn(".lighting-room-slider", self.stylesheet)
        self.assertIn(".lighting-quick-level", self.stylesheet)

    def test_expanded_bulbs_offer_capability_driven_color_controls(self):
        self.assertIn("light.supports_hue", self.javascript)
        self.assertIn("light.supports_color_temperature", self.javascript)
        self.assertIn("data-light-hue=", self.javascript)
        self.assertIn("data-light-temperature=", self.javascript)
        self.assertIn("post('lights/hue', { entity, hue })", self.javascript)
        self.assertIn(
            "post('lights/color-temperature', { entity, kelvin })",
            self.javascript,
        )
        self.assertIn(".lighting-color-controls", self.stylesheet)
        self.assertIn(".lighting-hue-slider", self.stylesheet)
        self.assertIn(".lighting-temperature-slider", self.stylesheet)


if __name__ == "__main__":
    unittest.main()
