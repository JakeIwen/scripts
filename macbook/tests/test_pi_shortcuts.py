"""Check generated workflows offline, without importing or running SSH commands."""
from contextlib import redirect_stderr, redirect_stdout
import io
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from macbook.shortcuts.build_shortcuts import build_shortcuts, main


ROOT = Path(__file__).resolve().parents[2]
MODULE = "macbook.shortcuts.build_shortcuts"
EXPECTED_COMMANDS = {
    "Brown Noise": "sh ~/sns.sh brown_noise",
    "Pink Noise": "sh ~/sns.sh pink_noise",
    "Discover Wkly": "sh ~/sns.sh discover_weekly",
    "PartyMode": "sh ~/sns.sh partymode",
    "Unjoin All": "sh ~/sns.sh unjoin_all",
    "Sonos Tasks": "sh ~/sns.sh --help",
    "Disks On": "~/scripts/policyctl disks on",
    "Disks Off": "~/scripts/policyctl disks off",
    "Torrents On": "~/scripts/policyctl torrents on",
    "Torrents Off": "~/scripts/policyctl torrents off",
    "slink on": "s toggle_starlink on",
    "slink off": "s toggle_starlink off",
    "toggle flood": "s tuya_toggle ext_flood",
    "rball": "s rball",
}


def parameters(action):
    return action["WFWorkflowActionParameters"]


def kind(action):
    return action["WFWorkflowActionIdentifier"].removeprefix("is.workflow.actions.")


class PiShortcutTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.paths = build_shortcuts(self.directory, sign=False)
        self.workflows = {}
        for path in self.paths:
            data = path.read_bytes()
            self.assertTrue(data.startswith(b"bplist00"))
            workflow = plistlib.loads(data)
            self.workflows[workflow["WFWorkflowName"]] = workflow

    def assert_output(self, value, source, name):
        self.assertEqual(value, {
            "Value": {
                "Type": "ActionOutput", "OutputUUID": parameters(source)["UUID"],
                "OutputName": name,
            },
            "WFSerializationType": "WFTextTokenAttachment",
        })

    def test_inventory_metadata_and_password_free_key_auth(self):
        self.assertEqual(set(self.workflows), {"Pi", *EXPECTED_COMMANDS})
        self.assertEqual(len(self.paths), 15)
        self.assertTrue(all(path.name.endswith(".unsigned.shortcut") for path in self.paths))
        for name, workflow in self.workflows.items():
            with self.subTest(name=name):
                self.assertEqual(workflow["WFWorkflowMinimumClientVersion"], 900)
                self.assertEqual(workflow["WFWorkflowMinimumClientVersionString"], "900")
                self.assertEqual(workflow["WFWorkflowClientVersion"], "4711")
                self.assertEqual(workflow["WFWorkflowTypes"], [])
                self.assertEqual(workflow["WFWorkflowImportQuestions"], [])
                self.assertEqual(workflow["WFWorkflowOutputContentItemClasses"], [])
                self.assertFalse(workflow["WFWorkflowHasOutputFallback"])
                for action in workflow["WFWorkflowActions"]:
                    self.assertNotIn("WFSSHPassword", parameters(action))
                    if kind(action) == "runsshscript":
                        self.assertEqual(parameters(action)["WFSSHAuthenticationType"], "SSH Key")
                        self.assertEqual(parameters(action)["WFSSHUser"], "pi")
                        self.assertNotIn("WFSSHPort", parameters(action))

    def test_helper_routes_local_ip_and_returns_chosen_ssh_output(self):
        helper = self.workflows["Pi"]
        actions = helper["WFWorkflowActions"]
        self.assertEqual([kind(item) for item in actions], [
            "getipaddress", "conditional", "runsshscript", "conditional",
            "runsshscript", "conditional", "output",
        ])
        ip_address, branch, lan, otherwise, remote, end_if, stop = actions
        self.assertEqual(parameters(ip_address)["WFIPAddressSourceOption"], "Local")
        self.assertEqual(parameters(ip_address)["WFIPAddressTypeOption"], "IPv4")
        self.assertEqual(parameters(branch)["WFCondition"], 8)
        self.assertEqual(parameters(branch)["WFConditionalActionString"], "192.168.6.")
        self.assertEqual(parameters(branch)["WFInput"]["Type"], "Variable")
        self.assert_output(parameters(branch)["WFInput"]["Variable"], ip_address, "IP Address")
        self.assertEqual([parameters(item)["WFControlFlowMode"] for item in
                          (branch, otherwise, end_if)], [0, 1, 2])
        self.assertEqual(len({parameters(item)["GroupingIdentifier"] for item in
                              (branch, otherwise, end_if)}), 1)
        self.assertEqual([parameters(item)["WFSSHHost"] for item in (lan, remote)],
                         ["192.168.6.103", "100.82.91.76"])
        for ssh in (lan, remote):
            self.assertEqual(parameters(ssh)["WFSSHScript"], {
                "Value": {"string": "￼", "attachmentsByRange": {
                    "{0, 1}": {"Type": "ExtensionInput"}}},
                "WFSerializationType": "WFTextTokenString",
            })
        self.assert_output(parameters(stop)["WFOutput"], end_if, "If Result")
        self.assertEqual(parameters(stop)["WFNoOutputSurfaceBehavior"], "Do Nothing")
        self.assertTrue(helper["WFWorkflowHasShortcutInputVariables"])
        self.assertEqual(helper["WFWorkflowInputContentItemClasses"], ["WFStringContentItem"])
        self.assertEqual(helper["WFWorkflowNoInputBehavior"], {
            "Name": "WFWorkflowNoInputBehaviorAskForInput",
            "Parameters": {"ItemClass": "WFStringContentItem"},
        })
        self.assertEqual(helper["WFWorkflowIcon"], {
            "WFWorkflowIconStartColor": 4282601983, "WFWorkflowIconGlyphNumber": 59771,
        })

    def test_commands_pass_exact_text_to_pi_and_only_help_shows_result(self):
        identifiers = set()
        for name, command in EXPECTED_COMMANDS.items():
            with self.subTest(name=name):
                workflow = self.workflows[name]
                actions = workflow["WFWorkflowActions"]
                expected = ["gettext", "runworkflow"]
                if name == "Sonos Tasks":
                    expected.append("showresult")
                self.assertEqual([kind(item) for item in actions], expected)
                command_text, run = actions[:2]
                self.assertEqual(parameters(command_text)["WFTextActionText"], command)
                reference = parameters(run)["WFWorkflow"]
                self.assertEqual(reference["workflowName"], "Pi")
                self.assertFalse(reference["isSelf"])
                identifiers.add(reference["workflowIdentifier"])
                self.assert_output(parameters(run)["WFInput"], command_text, "Text")
                self.assertFalse(parameters(run)["WFShowWorkflow"])
                self.assertEqual(workflow["WFWorkflowInputContentItemClasses"], [])
                self.assertFalse(workflow["WFWorkflowHasShortcutInputVariables"])
                self.assertEqual(workflow["WFWorkflowIcon"], {
                    "WFWorkflowIconStartColor": 2846468607,
                    "WFWorkflowIconGlyphNumber": 59860 if name == "Brown Noise" else 59771,
                })
                if name == "Sonos Tasks":
                    result = parameters(actions[2])["Text"]
                    self.assertEqual(result["WFSerializationType"], "WFTextTokenString")
                    self.assertEqual(result["Value"]["string"], "￼")
                    self.assert_output({
                        "Value": result["Value"]["attachmentsByRange"]["{0, 1}"],
                        "WFSerializationType": "WFTextTokenAttachment",
                    }, run, "Shortcut Result")
        self.assertEqual(identifiers, {"C9BA39E0-8849-4AAC-AF2A-831A0BE408E7"})

    def test_explicit_workflow_list_limits_build(self):
        directory = self.directory / "selected"
        paths = build_shortcuts(directory, [self.workflows["Pi"]], sign=False)
        self.assertEqual([path.name for path in paths], ["Pi.unsigned.shortcut"])
        self.assertEqual({path.resolve() for path in directory.iterdir()}, set(paths))
        self.assertEqual(plistlib.loads(paths[0].read_bytes()), self.workflows["Pi"])

    def test_explicit_empty_workflow_list_builds_nothing(self):
        directory = self.directory / "empty"
        self.assertEqual(build_shortcuts(directory, [], sign=False), [])
        self.assertEqual(list(directory.iterdir()), [])

    def test_signing_uses_anyone_and_returns_signed_paths(self):
        def sign(command, **kwargs):
            self.assertEqual(command[:5], ["/usr/bin/shortcuts", "sign", "--mode", "anyone", "--input"])
            self.assertEqual(command[6], "--output")
            self.assertTrue(kwargs["check"])
            source, destination = Path(command[5]), Path(command[7])
            self.assertFalse(destination.exists())
            destination.write_bytes(source.read_bytes())
            return subprocess.CompletedProcess(command, 0)

        stale = self.directory / "Pi.shortcut"
        stale.write_bytes(b"stale signed workflow")
        with patch(MODULE + ".subprocess.run", side_effect=sign) as signer:
            paths = build_shortcuts(self.directory)
        self.assertEqual(signer.call_count, 15)
        self.assertEqual(paths[0].name, "Pi.shortcut")
        self.assertEqual({path.name for path in paths},
                         {name + ".shortcut" for name in self.workflows})
        self.assertTrue(all(path.exists() for path in paths))

    def test_cli_unsigned_success_and_output_paths(self):
        result = subprocess.run(
            [sys.executable, "-B", "-m", MODULE, "--no-sign", "--output-dir", str(self.directory)],
            cwd=ROOT, capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(result.stdout.splitlines(), [str(path) for path in self.paths])

    def test_cli_help_and_invalid_argument(self):
        for arguments, expected in ((["--help"], 0), (["--unknown"], 2)):
            with self.subTest(arguments=arguments):
                result = subprocess.run(
                    [sys.executable, "-B", "-m", MODULE, *arguments],
                    cwd=ROOT, capture_output=True, text=True,
                )
                self.assertEqual(result.returncode, expected)
                self.assertIn("usage:", result.stdout + result.stderr)

    def test_cli_signing_failure_is_nonzero_without_success_paths(self):
        error = subprocess.CalledProcessError(1, ["shortcuts", "sign"], stderr="Signing failed")
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch(MODULE + ".subprocess.run", side_effect=error):
            with redirect_stdout(stdout), redirect_stderr(stderr):
                result = main(["--output-dir", str(self.directory)])
        self.assertEqual(result, 1)
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(stderr.getvalue(), "Pi Shortcut signing failed: Signing failed\n")


if __name__ == "__main__":
    unittest.main()
