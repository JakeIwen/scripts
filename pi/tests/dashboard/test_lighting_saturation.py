import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from werkzeug.datastructures import MultiDict

from pi.apps.van_dashboard import runtime
from pi.apps.van_dashboard import van_dashboard_common as dashboard_common
from pi.apps.van_dashboard import van_dashboard_home as dashboard_home
from pi.apps.van_dashboard.van_dashboard import create_app


class LightingSaturationTests(unittest.TestCase):
    def setUp(self):
        self.values = [{
            "entity_id": "light.wiz_kitchen",
            "state": "on",
            "brightness": 128,
            "color_mode": "rgbww",
            "supported_color_modes": ["rgbww"],
            "hs_color": [210.04, 35.26],
            "color_temp_kelvin": None,
            "min_color_temp_kelvin": None,
            "max_color_temp_kelvin": None,
        }]
        self.calls = []
        self.controller = dashboard_home.LightingController(command=self.command)
        self.client = create_app().test_client()
        self.runtime_patch = patch.object(runtime, "lighting", self.controller)
        self.runtime_patch.start()
        self.addCleanup(self.runtime_patch.stop)

    def command(self, args, timeout):
        self.calls.append(list(args))
        if args[:3] == [dashboard_common.TUYA_LIGHT, "hue", "light.wiz_kitchen"]:
            self.values[0]["hs_color"] = [int(args[3]), int(args[4])]
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(self.values) if args == [dashboard_common.TUYA_LIGHT, "list"] else "",
            stderr="",
        )

    @staticmethod
    def kitchen(status):
        group = next(group for group in status["groups"] if group["id"] == "kitchen")
        return group["lights"][0]

    def test_status_reports_rounded_saturation_only_with_hue(self):
        response = self.client.get("/api/lights")
        self.assertEqual(response.status_code, 200)
        light = self.kitchen(response.json["lighting"])
        self.assertEqual(light["hue"], 210.0)
        self.assertEqual(light["saturation"], 35.3)
        for modes, hs_color in ((["rgbww"], None), (["brightness"], [210, 35])):
            with self.subTest(modes=modes, hs_color=hs_color):
                self.values[0].update(supported_color_modes=modes, hs_color=hs_color)
                light = self.kitchen(self.controller.status())
                self.assertIsNone(light["hue"])
                self.assertIsNone(light["saturation"])

    def test_controller_passes_saturation_explicitly_and_refreshes(self):
        for saturation in (0, 35, 100):
            with self.subTest(saturation=saturation):
                self.calls.clear()
                status = self.controller.set_hue("light.wiz_kitchen", 210, saturation)
                self.assertEqual(self.kitchen(status)["saturation"], saturation)
                self.assertEqual(self.calls, [
                    [dashboard_common.TUYA_LIGHT, "hue", "light.wiz_kitchen", "210", str(saturation)],
                    [dashboard_common.TUYA_LIGHT, "list"],
                ])

    def test_controller_rejects_invalid_saturation_before_running_commands(self):
        for saturation in (-1, 101, 35.5, "35", True, None):
            with self.subTest(saturation=saturation):
                with self.assertRaisesRegex(ValueError, "saturation must be from 0 to 100"):
                    self.controller.set_hue("light.wiz_kitchen", 210, saturation)
        self.assertEqual(self.calls, [])

    def test_hue_route_accepts_optional_saturation_and_preserves_legacy_message(self):
        for saturation in (None, 0, 35, 100):
            with self.subTest(saturation=saturation):
                data = {"entity": "light.wiz_kitchen", "hue": "210"}
                if saturation is not None:
                    data["saturation"] = str(saturation)
                response = self.client.post("/api/lights/hue", data=data)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(
                    self.kitchen(response.json["lighting"])["saturation"],
                    100 if saturation is None else saturation,
                )
                self.assertEqual(
                    response.json["message"],
                    "Hue set to 210°" if saturation is None
                    else f"Hue set to 210°, saturation {saturation}%",
                )

    def test_hue_route_rejects_invalid_saturation_without_running_commands(self):
        for saturation in ("-1", "101", "35.5", "true", "", "blue"):
            with self.subTest(saturation=saturation):
                response = self.client.post("/api/lights/hue", data={
                    "entity": "light.wiz_kitchen", "hue": "210", "saturation": saturation,
                })
                self.assertEqual(response.status_code, 400)
                self.assertIn("saturation must be from 0 to 100", response.json["message"])
        self.assertEqual(self.calls, [])

    def test_hue_route_rejects_duplicate_or_extra_fields(self):
        fields = [("entity", "light.wiz_kitchen"), ("hue", "210"), ("saturation", "35")]
        for extra in (fields[0], fields[1], fields[2], ("command", "anything")):
            with self.subTest(extra=extra):
                response = self.client.post("/api/lights/hue", data=MultiDict(fields + [extra]))
                self.assertEqual(response.status_code, 400)
        response = self.client.post(
            "/api/lights/hue", data={"entity": "light.wiz_kitchen", "saturation": "35"}
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()
