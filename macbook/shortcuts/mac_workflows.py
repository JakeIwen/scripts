"""Build the photo share-sheet workflow without running its SSH command."""
from uuid import uuid4

from . import mac_shortcuts as spec
from .pi_shortcuts import LAN_PREFIX
from .workflows import action, attachment, output, text, workflow


def create_workflow():
    ip_address = action(
        "getipaddress", WFIPAddressSourceOption="Local", WFIPAddressTypeOption="IPv4"
    )
    conditional_group = str(uuid4()).upper()
    repeat_group = str(uuid4()).upper()
    end_if = action("conditional", GroupingIdentifier=conditional_group, WFControlFlowMode=2)
    ssh = action(
        "runsshscript",
        WFSSHHost=text(output(end_if, "If Result")),
        WFSSHUser=spec.MAC_SSH_USER,
        WFSSHAuthenticationType="SSH Key",
        WFInput=attachment({"Type": "Variable", "VariableName": "Repeat Item"}),
        WFSSHScript=spec.MAC_INBOX_COMMAND,
    )
    actions = [
        ip_address,
        action(
            "conditional",
            GroupingIdentifier=conditional_group,
            WFControlFlowMode=0,
            WFInput={"Type": "Variable", "Variable": output(ip_address, "IP Address")},
            WFCondition=8,
            WFConditionalActionString=LAN_PREFIX,
        ),
        action("gettext", WFTextActionText=spec.MAC_LAN_HOST),
        action("conditional", GroupingIdentifier=conditional_group, WFControlFlowMode=1),
        action("gettext", WFTextActionText=spec.MAC_TAILSCALE_HOST),
        end_if,
        action(
            "repeat.each",
            GroupingIdentifier=repeat_group,
            WFControlFlowMode=0,
            WFInput=attachment({"Type": "ExtensionInput"}),
        ),
        ssh,
        action(
            "notification",
            WFNotificationActionTitle=spec.MAC_NOTIFICATION_TITLE,
            WFNotificationActionBody=text(output(ssh, "Shell Script Result")),
            WFNotificationActionSound=False,
        ),
        action("repeat.each", GroupingIdentifier=repeat_group, WFControlFlowMode=2),
    ]
    return workflow(
        spec.MAC_SHORTCUT_NAME, actions, spec.MAC_COLOR, spec.MAC_GLYPH,
        accepts_input=True,
        workflow_types=["ActionExtension"],
        input_content_item_classes=["WFImageContentItem"],
        no_input_behavior={
            "Name": "WFWorkflowNoInputBehaviorAskForInput",
            "Parameters": {"ItemClass": "WFImageContentItem"},
        },
    )
