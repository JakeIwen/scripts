"""Strict runtime-definition capture tests for BTT Guard checkpoints."""
from copy import deepcopy
import unittest

from macbook.bettertouchtool.btt_guard.definitions import (
    build_definitions,
    validate_definitions,
    verify_created_appearance,
)


PRESET = "preset-uuid"


def record(
    uuid,
    *,
    parent=None,
    trigger_type=773,
    order=0,
    action_type=366,
    category=0,
    payload=None,
    config=None,
    app_scope=None,
):
    return {
        "uuid": uuid,
        "parent": parent,
        "preset": PRESET,
        "app_scope": ["BT.G"] if app_scope is None else app_scope,
        "trigger_type": trigger_type,
        "enabled": True,
        "order": order,
        "action_type": action_type,
        "action_category": category,
        "payload": payload or {},
        "config": config or {},
    }


def snapshot_fixture():
    records = {
        "root": record(
            "root",
            trigger_type=767,
            config={"BTTMenuVisibility": 2, "BTTMenuDisableDrag": False},
        ),
        "button": record("button", parent="root", order=3),
        "shell": record(
            "shell",
            parent="button",
            trigger_type=-1,
            action_type=206,
            payload={
                "launch_path": "private shell command",
                "additional_action_string": "private shell config",
                "action_data": {"setting": "private"},
            },
        ),
        "delay": record(
            "delay",
            parent="button",
            trigger_type=-1,
            order=1,
            action_type=129,
            payload={"launch_path": "1.5"},
        ),
        "keyboard": record(
            "keyboard",
            parent="button",
            trigger_type=-1,
            category=1,
            action_type=-1,
            payload={"launch_path": "17", "shortcut": "-1"},
        ),
        "trigger": record("trigger", parent="button", trigger_type=777, order=4),
        "named": record(
            "named",
            trigger_type=643,
            order=2,
            action_type=206,
            payload={
                "gesture_config": "private trigger name",
                "launch_path": "private named command",
            },
        ),
    }
    return {
        "schema_version": 1,
        "captured_at": 1.0,
        "btt_version": "fixture",
        "database_name": "fixture",
        "roots": ["root"],
        "presets": {PRESET: True},
        "records": records,
        "findings": [],
    }


def runtime_exports():
    root = {
        "BTTUUID": "root",
        "BTTTriggerType": 767,
        "BTTEnabled": 1,
        "BTTEnabled2": 0,
        "BTTOrder": 0,
        "BTTLastUpdatedAt": 123,
        "BTTFloatingMenuRenderedPreview": "discard",
        "BTTTriggerNameReadOnly": "discard",
        "BTTMenuConfig": {
            "BTTMenuVisibility": 2,
            "BTTMenuCloseAfterAction": 0,
            "BTTMenuItemFontColor": "retain appearance",
        },
        "BTTMenuItems": [
            {
                "BTTUUID": "button",
                "BTTTriggerType": 773,
                "BTTOrder": 3,
                "BTTMenuName": "retain label",
                "BTTTriggerName": "descriptive button name",
                "BTTMenuConfig": {"BTTMenuItemScriptActive": False},
                "BTTMenuItemActions": [
                    {
                        "BTTUUID": "shell",
                        "BTTOrder": 118,
                        "BTTPredefinedActionType": 206,
                        "BTTShellTaskActionScript": "private shell command",
                        "BTTShellTaskActionConfig": "private shell config",
                        "BTTAdditionalActionData": '{"setting":"private"}',
                        "BTTIsPureAction": True,
                    },
                    {
                        "BTTUUID": "delay",
                        "BTTTriggerType": -1,
                        "BTTOrder": 119,
                        "BTTPredefinedActionType": 129,
                        "BTTDelayNextActionBy": 1.5,
                    },
                    {
                        "BTTUUID": "keyboard",
                        "BTTTriggerType": -1,
                        "BTTOrder": 500,
                        "BTTActionCategory": 1,
                        "BTTPredefinedActionType": 264,
                        "BTTLayoutIndependentActionChar": "17",
                    },
                ],
                "BTTMenuItemTriggers": [
                    {"BTTUUID": "trigger", "BTTTriggerType": 777, "BTTOrder": 4}
                ],
            }
        ],
    }
    named = {
        "BTTUUID": "named",
        "BTTTriggerType": 643,
        "BTTOrder": 2,
        "BTTPredefinedActionType": 206,
        "BTTTriggerName": "private trigger name",
        "BTTShellTaskActionScript": "private named command",
    }
    return [root, named]


def fixture():
    return snapshot_fixture(), runtime_exports()


class GuardDefinitionTests(unittest.TestCase):
    def test_flattens_exact_graph_and_preserves_full_definitions(self):
        snapshot, exports = fixture()
        result = build_definitions(exports, snapshot, {PRESET: "Current Master"})

        self.assertEqual(set(result), set(snapshot["records"]))
        self.assertEqual(result["root"]["BTTAppBundleIdentifier"], "BT.G")
        self.assertEqual(result["named"]["BTTAppBundleIdentifier"], "BT.G")
        self.assertTrue(
            all(node["BTTAppBundleIdentifier"] == "BT.G" for node in result.values())
        )
        self.assertEqual(
            result["root"]["BTTMenuConfig"]["BTTMenuItemFontColor"],
            "retain appearance",
        )
        self.assertEqual(result["button"]["BTTMenuName"], "retain label")
        self.assertEqual(
            result["button"]["BTTTriggerName"], "descriptive button name"
        )
        self.assertEqual(result["button"]["BTTTriggerParentUUID"], "root")
        self.assertEqual(result["shell"]["BTTTriggerType"], -1)
        self.assertEqual(result["shell"]["BTTOrder"], 0)
        self.assertEqual(result["delay"]["BTTOrder"], 1)
        self.assertEqual(result["keyboard"]["BTTOrder"], 0)
        self.assertEqual(result["trigger"]["BTTOrder"], 4)
        self.assertNotIn("BTTMenuItems", result["root"])
        self.assertNotIn("BTTMenuItemActions", result["button"])
        self.assertNotIn("BTTLastUpdatedAt", result["root"])
        self.assertNotIn("BTTFloatingMenuRenderedPreview", result["root"])
        self.assertNotIn("BTTTriggerNameReadOnly", result["root"])
        self.assertNotIn("BTTIsPureAction", result["shell"])
        self.assertEqual(result["root"]["BTTEnabled2"], 0)
        self.assertEqual(
            result["shell"]["BTTTriggerClass"], "BTTTriggerTypeFloatingMenu"
        )
        self.assertEqual(
            result["named"]["BTTTriggerClass"], "BTTTriggerTypeOtherTriggers"
        )
        self.assertTrue(
            all(node["BTTTriggerBelongsToPreset"] == "Current Master" for node in result.values())
        )

    def test_rejects_duplicate_unexpected_and_missing_runtime_ids(self):
        snapshot, exports = fixture()
        duplicate = deepcopy(exports)
        duplicate[0]["BTTAdditionalActions"] = [
            deepcopy(duplicate[0]["BTTMenuItems"][0]["BTTMenuItemActions"][0])
        ]
        with self.assertRaisesRegex(ValueError, "Duplicate runtime BTT UUID"):
            build_definitions(duplicate, snapshot, {PRESET: "Master"})

        unexpected = deepcopy(exports)
        unexpected[0]["BTTMenuItems"].append(
            {"BTTUUID": "dynamic-note", "BTTTriggerType": 773}
        )
        with self.assertRaisesRegex(ValueError, "unexpected UUID: dynamic-note"):
            build_definitions(unexpected, snapshot, {PRESET: "Master"})

        missing = deepcopy(exports)
        missing[0]["BTTMenuItems"][0]["BTTMenuItemTriggers"] = []
        with self.assertRaisesRegex(ValueError, "missing UUID: trigger"):
            build_definitions(missing, snapshot, {PRESET: "Master"})

    def test_rejects_saved_and_runtime_operational_disagreement(self):
        snapshot, exports = fixture()
        exports[0]["BTTMenuConfig"]["BTTMenuVisibility"] = 1
        with self.assertRaisesRegex(ValueError, "operational configuration"):
            build_definitions(exports, snapshot, {PRESET: "Master"})

        snapshot, exports = fixture()
        exports[0]["BTTMenuConfig"]["BTTMenuOffsetX"] = 10
        with self.assertRaisesRegex(ValueError, "BTTMenuOffsetX"):
            build_definitions(exports, snapshot, {PRESET: "Master"})

        snapshot, exports = fixture()
        exports[0]["BTTEnabled"] = 0
        with self.assertRaisesRegex(ValueError, "enabled state"):
            build_definitions(exports, snapshot, {PRESET: "Master"})

    def test_rejects_payload_action_parent_and_dense_order_mismatches(self):
        snapshot, exports = fixture()
        exports[0]["BTTMenuItems"][0]["BTTMenuItemActions"][0][
            "BTTShellTaskActionScript"
        ] = "changed"
        with self.assertRaisesRegex(ValueError, "payload differs"):
            build_definitions(exports, snapshot, {PRESET: "Master"})

        snapshot, exports = fixture()
        exports[0]["BTTMenuItems"][0]["BTTMenuItemActions"][0][
            "BTTPredefinedActionType"
        ] = 248
        with self.assertRaisesRegex(ValueError, "action type"):
            build_definitions(exports, snapshot, {PRESET: "Master"})

        snapshot, exports = fixture()
        exports[0]["BTTMenuItems"][0]["BTTTriggerParentUUID"] = "wrong"
        with self.assertRaisesRegex(ValueError, "parent differs"):
            build_definitions(exports, snapshot, {PRESET: "Master"})

        snapshot, exports = fixture()
        exports[0]["BTTMenuItems"][0]["BTTMenuItemActions"][0]["BTTOrder"] = 200
        with self.assertRaisesRegex(ValueError, "action order"):
            build_definitions(exports, snapshot, {PRESET: "Master"})

    def test_unknown_payload_mapping_or_missing_class_derivation_fails(self):
        snapshot, exports = fixture()
        snapshot["records"]["named"]["action_type"] = 386
        exports[1]["BTTPredefinedActionType"] = 386
        with self.assertRaisesRegex(ValueError, "Unsupported launch-path action mapping"):
            build_definitions(exports, snapshot, {PRESET: "Master"})

        snapshot, exports = fixture()
        snapshot["records"]["trigger"]["trigger_type"] = 999
        exports[0]["BTTMenuItems"][0]["BTTMenuItemTriggers"][0][
            "BTTTriggerType"
        ] = 999
        with self.assertRaisesRegex(ValueError, "unfamiliar BTT type"):
            build_definitions(exports, snapshot, {PRESET: "Master"})

    def test_missing_current_preset_name_fails(self):
        snapshot, exports = fixture()
        with self.assertRaisesRegex(ValueError, "Missing current preset name"):
            build_definitions(exports, snapshot, {})

    def test_application_scope_is_authoritative_and_preservable(self):
        snapshot, exports = fixture()
        exports[0]["BTTAppBundleIdentifier"] = "private.bundle"
        with self.assertRaisesRegex(ValueError, "application scope differs"):
            build_definitions(exports, snapshot, {PRESET: "Master"})

        snapshot, exports = fixture()
        snapshot["records"]["root"]["app_scope"] = []
        with self.assertRaisesRegex(ValueError, "no preservable application scope"):
            build_definitions(exports, snapshot, {PRESET: "Master"})

        snapshot, exports = fixture()
        snapshot["records"]["button"]["app_scope"] = []
        with self.assertRaisesRegex(ValueError, "no preservable application scope"):
            build_definitions(exports, snapshot, {PRESET: "Master"})

        snapshot, exports = fixture()
        snapshot["records"]["button"]["app_scope"] = ["BT.G", "private.bundle"]
        with self.assertRaisesRegex(ValueError, "Multiple application scopes"):
            build_definitions(exports, snapshot, {PRESET: "Master"})

    def test_legacy_top_level_named_trigger_preserves_observed_empty_scope(self):
        snapshot, exports = fixture()
        snapshot["records"]["named"]["app_scope"] = []
        definitions = build_definitions(exports, snapshot, {PRESET: "Master"})
        self.assertNotIn("BTTAppBundleIdentifier", definitions["named"])
        validate_definitions(definitions, snapshot)

        conflicting = deepcopy(exports)
        conflicting[1]["BTTAppBundleIdentifier"] = "BT.G"
        with self.assertRaisesRegex(ValueError, "application scope differs"):
            build_definitions(conflicting, snapshot, {PRESET: "Master"})

    def test_checkpoint_definition_validator_rejects_semantic_mismatches(self):
        snapshot, exports = fixture()
        definitions = build_definitions(exports, snapshot, {PRESET: "Master"})
        validate_definitions(definitions, snapshot)
        incomplete = deepcopy(definitions)
        del incomplete["trigger"]
        with self.assertRaisesRegex(ValueError, "UUIDs do not match"):
            validate_definitions(incomplete, snapshot)
        mutations = [
            ("button", "BTTTriggerType", 774, "trigger type differs"),
            ("shell", "BTTOrder", 9, "order differs"),
            ("shell", "BTTShellTaskActionScript", "changed", "payload differs"),
            ("button", "BTTTriggerParentUUID", "wrong", "parent differs"),
            ("button", "BTTTriggerBelongsToPreset", "", "preset name is missing"),
            ("button", "BTTTriggerClass", "WrongClass", "trigger class differs"),
            ("button", "BTTAppBundleIdentifier", "private.bundle", "application scope differs"),
        ]
        for uuid, key, value, message in mutations:
            with self.subTest(key=key):
                broken = deepcopy(definitions)
                broken[uuid][key] = value
                with self.assertRaisesRegex(ValueError, message):
                    validate_definitions(broken, snapshot)
        wrong_config = deepcopy(definitions)
        wrong_config["root"]["BTTMenuConfig"]["BTTMenuVisibility"] = 1
        with self.assertRaisesRegex(ValueError, "operational configuration"):
            validate_definitions(wrong_config, snapshot)
        nested = deepcopy(definitions)
        nested["button"]["BTTMenuItems"] = []
        with self.assertRaisesRegex(ValueError, "not flat"):
            validate_definitions(nested, snapshot)

    def test_created_appearance_checks_presence_with_image_externalization(self):
        expected = {
            "created": {
                "BTTUUID": "created",
                "BTTMenuName": "Expected name",
                "BTTMenuConfig": {
                    "BTTMenuAttributedText": "private attributed text",
                    "BTTMenuItemIconType": 1,
                    "BTTMenuItemImage": "embedded bytes",
                    "BTTMenuItemIconTypeDark": 1,
                    "BTTMenuItemImageDark": "embedded dark bytes",
                },
            }
        }
        runtime = {
            "created": {
                "BTTUUID": "created",
                "BTTTriggerName": "Runtime name representation",
                "BTTMenuConfig": {
                    "BTTMenuItemText": "runtime text representation",
                    "BTTMenuItemIconType": 7,
                    "BTTMenuItemIconPresetPath": "/private/externalized-light",
                    "BTTMenuItemIconTypeDark": 7,
                    "BTTMenuItemIconPresetPathDark": "/private/externalized-dark",
                },
            }
        }
        verify_created_appearance(expected, runtime, ["created"])

        no_text = deepcopy(runtime)
        no_text["created"].pop("BTTTriggerName")
        with self.assertRaisesRegex(ValueError, "meaningful name"):
            verify_created_appearance(expected, no_text, ["created"])
        no_icon = deepcopy(runtime)
        del no_icon["created"]["BTTMenuConfig"]["BTTMenuItemIconPresetPath"]
        with self.assertRaisesRegex(ValueError, "lost its icon"):
            verify_created_appearance(expected, no_icon, ["created"])
        with self.assertRaisesRegex(ValueError, "iterable of IDs"):
            verify_created_appearance(expected, runtime, "created")


if __name__ == "__main__":
    unittest.main()
