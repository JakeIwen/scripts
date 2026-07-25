import json
import pathlib
import unittest
from types import SimpleNamespace

from pi.apps.van_dashboard import van_dashboard as dashboard


class LightingPowerSwitchTests(unittest.TestCase):
    @staticmethod
    def states(ext_state="off", solder_state="on"):
        values = [
            {
                "entity_id": entity,
                "state": "off",
                "brightness": None,
            }
            for _group_id, _group_label, lights in dashboard.LIGHT_GROUPS
            for entity, _label in lights
        ]
        values.extend(
            (
                {
                    "entity_id": "switch.ext_flood",
                    "state": ext_state,
                    "brightness": None,
                },
                {
                    "entity_id": "switch.solder_flood",
                    "state": solder_state,
                    "brightness": None,
                },
            )
        )
        return values

    def test_status_attaches_switches_only_to_exterior_and_solder(self):
        values = self.states()

        def command(args, timeout):
            self.assertEqual(args, [dashboard.TUYA_LIGHT, "list"])
            return SimpleNamespace(returncode=0, stdout=json.dumps(values), stderr="")

        groups = {
            group["id"]: group
            for group in dashboard.LightingController(command=command).status()["groups"]
        }
        self.assertEqual(groups["exterior"]["power_switch"]["entity_id"], "switch.ext_flood")
        self.assertEqual(groups["exterior"]["power_switch"]["state"], "off")
        self.assertEqual(groups["solder"]["power_switch"]["entity_id"], "switch.solder_flood")
        self.assertEqual(groups["solder"]["power_switch"]["state"], "on")
        for group_id in ("cab", "rear", "kitchen", "extra"):
            self.assertIsNone(groups[group_id]["power_switch"])

    def test_power_action_uses_only_the_fixed_switch_allowlist(self):
        values = self.states()
        calls = []

        def command(args, timeout):
            calls.append(list(args))
            if args[:2] == [dashboard.TUYA_TOGGLE, "switch.ext_flood"]:
                values[-2]["state"] = args[2]
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(values) if args == [dashboard.TUYA_LIGHT, "list"] else "",
                stderr="",
            )

        controller = dashboard.LightingController(command=command)
        status = controller.set_power("switch.ext_flood", True)
        exterior = next(group for group in status["groups"] if group["id"] == "exterior")
        self.assertEqual(exterior["power_switch"]["state"], "on")
        self.assertEqual(
            calls,
            [
                [dashboard.TUYA_TOGGLE, "switch.ext_flood", "on"],
                [dashboard.TUYA_LIGHT, "list"],
            ],
        )
        with self.assertRaisesRegex(ValueError, "unknown lighting target"):
            controller.set_power("switch.starlink", True)

    def test_route_accepts_fixed_switch_and_rejects_other_switches(self):
        values = self.states()

        def command(args, timeout):
            if args[:2] == [dashboard.TUYA_TOGGLE, "switch.solder_flood"]:
                values[-1]["state"] = args[2]
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(values) if args == [dashboard.TUYA_LIGHT, "list"] else "",
                stderr="",
            )

        original = dashboard.lighting
        dashboard.lighting = dashboard.LightingController(command=command)
        try:
            client = dashboard.app.test_client()
            accepted = client.post(
                "/api/lights/power",
                data={"target": "switch.solder_flood", "value": "false"},
            )
            rejected = client.post(
                "/api/lights/power",
                data={"target": "switch.starlink", "value": "false"},
            )
        finally:
            dashboard.lighting = original

        self.assertEqual(accepted.status_code, 200)
        solder = next(
            group for group in accepted.json["lighting"]["groups"] if group["id"] == "solder"
        )
        self.assertEqual(solder["power_switch"]["state"], "off")
        self.assertEqual(rejected.status_code, 400)

    def test_helper_and_ui_include_only_the_two_power_switches(self):
        root = pathlib.Path(__file__).resolve().parents[3]
        helper = (root / "pi" / "scripts" / "tuya_light.sh").read_text()
        javascript = (
            root / "pi" / "apps" / "van_dashboard" / "static" / "van_dashboard.js"
        ).read_text()
        stylesheet = (
            root / "pi" / "apps" / "van_dashboard" / "static" / "van_dashboard.css"
        ).read_text()
        self.assertIn('or .entity_id == "switch.ext_flood"', helper)
        self.assertIn('or .entity_id == "switch.solder_flood"', helper)
        self.assertNotIn('or .entity_id == "switch.starlink"', helper)
        self.assertIn("Turn switch ${switchEnabled ? 'off' : 'on'}", javascript)
        self.assertIn("lighting-switch-action", javascript)
        self.assertIn(".lighting-group-actions", stylesheet)
        self.assertIn(".lighting-switch-action.good", stylesheet)


if __name__ == "__main__":
    unittest.main()
