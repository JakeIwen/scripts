"""Pure workflow construction; token encoding ported from castit's workflow-format.mjs."""
from uuid import uuid4

from . import pi_shortcuts as spec


def action(identifier, **parameters):
    return {
        "WFWorkflowActionIdentifier": "is.workflow.actions." + identifier,
        "WFWorkflowActionParameters": {"UUID": str(uuid4()).upper(), **parameters},
    }


def attachment(value):
    return {"Value": value, "WFSerializationType": "WFTextTokenAttachment"}


def output(source, name):
    return attachment({
        "Type": "ActionOutput",
        "OutputUUID": source["WFWorkflowActionParameters"]["UUID"],
        "OutputName": name,
    })


def text(variable):
    # Shortcuts text fields index embedded variables by UTF-16 code units.
    return {
        "Value": {"string": "￼", "attachmentsByRange": {"{0, 1}": variable["Value"]}},
        "WFSerializationType": "WFTextTokenString",
    }


def workflow(name, actions, color, glyph, *, accepts_input=False):
    result = {
        "WFWorkflowName": name,
        "WFWorkflowClientVersion": spec.CLIENT_VERSION,
        "WFWorkflowMinimumClientVersion": spec.MINIMUM_CLIENT_VERSION,
        "WFWorkflowMinimumClientVersionString": str(spec.MINIMUM_CLIENT_VERSION),
        "WFWorkflowIcon": {
            "WFWorkflowIconStartColor": color,
            "WFWorkflowIconGlyphNumber": glyph,
        },
        "WFWorkflowImportQuestions": [],
        "WFWorkflowTypes": [],
        "WFWorkflowInputContentItemClasses": ["WFStringContentItem"] if accepts_input else [],
        "WFWorkflowHasShortcutInputVariables": accepts_input,
        "WFWorkflowHasOutputFallback": False,
        "WFWorkflowOutputContentItemClasses": [],
        "WFWorkflowActions": actions,
    }
    if accepts_input:
        result["WFWorkflowNoInputBehavior"] = {
            "Name": "WFWorkflowNoInputBehaviorAskForInput",
            "Parameters": {"ItemClass": "WFStringContentItem"},
        }
    return result


def ssh_action(host):
    return action(
        "runsshscript",
        WFSSHHost=host,
        WFSSHUser=spec.SSH_USER,
        WFSSHAuthenticationType="SSH Key",
        WFSSHScript=text(attachment({"Type": "ExtensionInput"})),
    )


def helper_workflow():
    ip_address = action(
        "getipaddress", WFIPAddressSourceOption="Local", WFIPAddressTypeOption="IPv4"
    )
    group = str(uuid4()).upper()
    end_if = action("conditional", GroupingIdentifier=group, WFControlFlowMode=2)
    actions = [
        ip_address,
        action(
            "conditional",
            GroupingIdentifier=group,
            WFControlFlowMode=0,
            WFInput={"Type": "Variable", "Variable": output(ip_address, "IP Address")},
            WFCondition=8,
            WFConditionalActionString=spec.LAN_PREFIX,
        ),
        ssh_action(spec.LAN_HOST),
        action("conditional", GroupingIdentifier=group, WFControlFlowMode=1),
        ssh_action(spec.TAILSCALE_HOST),
        end_if,
        action(
            "output",
            WFOutput=output(end_if, "If Result"),
            WFNoOutputSurfaceBehavior="Do Nothing",
        ),
    ]
    return workflow(
        spec.HELPER_NAME, actions, spec.HELPER_COLOR, spec.COMMAND_GLYPH, accepts_input=True
    )


def command_workflow(command):
    command_text = action("gettext", WFTextActionText=command.command)
    run = action(
        "runworkflow",
        WFWorkflow={
            "isSelf": False,
            "workflowIdentifier": spec.HELPER_IDENTIFIER,
            "workflowName": spec.HELPER_NAME,
        },
        WFInput=output(command_text, "Text"),
        WFShowWorkflow=False,
    )
    actions = [command_text, run]
    if command.show_result:
        actions.append(action("showresult", Text=text(output(run, "Shortcut Result"))))
    return workflow(command.name, actions, spec.COMMAND_COLOR, command.glyph)


def create_workflows():
    """Return the helper first so it can be imported before its callers."""
    return [helper_workflow()] + [command_workflow(command) for command in spec.COMMAND_SHORTCUTS]
