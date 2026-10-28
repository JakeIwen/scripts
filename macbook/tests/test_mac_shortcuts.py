"""Check the photo share-sheet plist without sending photos or running SSH."""
from collections import defaultdict
from pathlib import Path
import plistlib
import tempfile
import unittest
from uuid import UUID

from macbook.shortcuts import mac_shortcuts as spec
from macbook.shortcuts.build_shortcuts import build_shortcuts, create_workflows
from macbook.shortcuts.mac_workflows import create_workflow
from macbook.shortcuts.pi_shortcuts import CLIENT_VERSION, LAN_PREFIX, MINIMUM_CLIENT_VERSION
from macbook.shortcuts.workflows import create_workflows as create_pi_workflows


def parameters(action):
    return action["WFWorkflowActionParameters"]


def kind(action):
    return action["WFWorkflowActionIdentifier"].removeprefix("is.workflow.actions.")


class MacShortcutTests(unittest.TestCase):
    def setUp(self):
        self.workflow = create_workflow()
        self.actions = self.workflow["WFWorkflowActions"]

    def assert_text_output(self, value, source, name):
        self.assertEqual(value, {
            "Value": {
                "string": "￼",
                "attachmentsByRange": {"{0, 1}": {
                    "Type": "ActionOutput",
                    "OutputUUID": parameters(source)["UUID"],
                    "OutputName": name,
                }},
            },
            "WFSerializationType": "WFTextTokenString",
        })

    def test_share_sheet_envelope_and_direct_run_photo_picker(self):
        self.assertEqual(self.workflow["WFWorkflowName"], "To Mac LLM")
        self.assertEqual(self.workflow["WFWorkflowTypes"], ["ActionExtension"])
        self.assertEqual(self.workflow["WFWorkflowInputContentItemClasses"], ["WFImageContentItem"])
        self.assertTrue(self.workflow["WFWorkflowHasShortcutInputVariables"])
        self.assertEqual(self.workflow["WFWorkflowNoInputBehavior"], {
            "Name": "WFWorkflowNoInputBehaviorAskForInput",
            "Parameters": {"ItemClass": "WFImageContentItem"},
        })
        self.assertEqual(self.workflow["WFWorkflowClientVersion"], CLIENT_VERSION)
        self.assertEqual(self.workflow["WFWorkflowMinimumClientVersion"], MINIMUM_CLIENT_VERSION)
        self.assertEqual(self.workflow["WFWorkflowMinimumClientVersionString"],
                         str(MINIMUM_CLIENT_VERSION))
        self.assertEqual(self.workflow["WFWorkflowIcon"], {
            "WFWorkflowIconStartColor": spec.MAC_COLOR,
            "WFWorkflowIconGlyphNumber": spec.MAC_GLYPH,
        })

    def test_action_sequence_and_unique_uuids(self):
        self.assertEqual([kind(item) for item in self.actions], [
            "getipaddress", "conditional", "gettext", "conditional", "gettext",
            "conditional", "repeat.each", "runsshscript", "notification", "repeat.each",
        ])
        identifiers = [parameters(item)["UUID"] for item in self.actions]
        self.assertEqual(len(set(identifiers)), len(self.actions))
        for identifier in identifiers:
            self.assertEqual(str(UUID(identifier)).upper(), identifier)

    def test_conditional_and_repeat_groups_balance_separately(self):
        groups = defaultdict(list)
        for item in self.actions:
            values = parameters(item)
            if "GroupingIdentifier" in values:
                groups[values["GroupingIdentifier"]].append(item)
        conditional_group = parameters(self.actions[1])["GroupingIdentifier"]
        repeat_group = parameters(self.actions[6])["GroupingIdentifier"]
        self.assertNotEqual(conditional_group, repeat_group)
        self.assertEqual(set(groups), {conditional_group, repeat_group})
        for group, identifier, modes in (
            (conditional_group, "conditional", [0, 1, 2]),
            (repeat_group, "repeat.each", [0, 2]),
        ):
            with self.subTest(group=identifier):
                self.assertEqual(str(UUID(group)).upper(), group)
                self.assertEqual([kind(item) for item in groups[group]], [identifier] * len(modes))
                self.assertEqual([parameters(item)["WFControlFlowMode"] for item in groups[group]],
                                 modes)

    def test_host_selection_uses_local_ipv4_and_shared_lan_prefix(self):
        ip_address, branch, lan, _, remote = self.actions[:5]
        self.assertEqual(parameters(ip_address)["WFIPAddressSourceOption"], "Local")
        self.assertEqual(parameters(ip_address)["WFIPAddressTypeOption"], "IPv4")
        self.assertEqual(parameters(branch)["WFCondition"], 8)
        self.assertEqual(parameters(branch)["WFConditionalActionString"], LAN_PREFIX)
        self.assertEqual(parameters(branch)["WFInput"], {
            "Type": "Variable",
            "Variable": {
                "Value": {
                    "Type": "ActionOutput", "OutputUUID": parameters(ip_address)["UUID"],
                    "OutputName": "IP Address",
                },
                "WFSerializationType": "WFTextTokenAttachment",
            },
        })
        self.assertEqual(parameters(lan)["WFTextActionText"], "m4mac.local")
        self.assertEqual(parameters(remote)["WFTextActionText"], "100.82.91.77")

    def test_repeat_passes_each_shared_photo_to_key_authenticated_ssh(self):
        end_if, repeat, ssh = self.actions[5:8]
        self.assertEqual(parameters(repeat)["WFInput"], {
            "Value": {"Type": "ExtensionInput"},
            "WFSerializationType": "WFTextTokenAttachment",
        })
        self.assert_text_output(parameters(ssh)["WFSSHHost"], end_if, "If Result")
        self.assertEqual(parameters(ssh)["WFInput"], {
            "Value": {"Type": "Variable", "VariableName": "Repeat Item"},
            "WFSerializationType": "WFTextTokenAttachment",
        })
        self.assertEqual(parameters(ssh)["WFSSHAuthenticationType"], "SSH Key")
        self.assertEqual(parameters(ssh)["WFSSHUser"], "jacobr")
        self.assertEqual(parameters(ssh)["WFSSHScript"], spec.MAC_INBOX_COMMAND)
        self.assertEqual(spec.MAC_INBOX_COMMAND,
                         "/usr/bin/python3 -I /Users/jacobr/dev/scripts/macbook/scripts/llm_photo_inbox.py")
        self.assertNotIn("WFSSHPassword", parameters(ssh))

    def test_notification_uses_ssh_result_without_sound(self):
        ssh, notification = self.actions[7:9]
        self.assertEqual(parameters(notification)["WFNotificationActionTitle"], "Mac LLM")
        self.assert_text_output(parameters(notification)["WFNotificationActionBody"],
                                ssh, "Shell Script Result")
        self.assertIs(parameters(notification)["WFNotificationActionSound"], False)

    def test_combined_build_keeps_pi_helper_first_and_includes_mac_workflow(self):
        workflows = create_workflows()
        names = [item["WFWorkflowName"] for item in workflows]
        pi_names = [item["WFWorkflowName"] for item in create_pi_workflows()]
        self.assertEqual(names, pi_names + ["To Mac LLM"])
        self.assertEqual(names[0], "Pi")
        with tempfile.TemporaryDirectory() as directory:
            paths = build_shortcuts(directory, workflows, sign=False)
            self.assertEqual([path.name for path in paths],
                             [name + ".unsigned.shortcut" for name in names])
            self.assertEqual(len(paths), 16)
            for path, expected in zip(paths, workflows):
                data = path.read_bytes()
                self.assertTrue(data.startswith(b"bplist00"))
                self.assertEqual(plistlib.loads(data), expected)
            self.assertEqual({path.name for path in Path(directory).iterdir()},
                             {path.name for path in paths})


if __name__ == "__main__":
    unittest.main()
