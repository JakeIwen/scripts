#!/usr/bin/env python3
import json, sys, time, uuid, re

NOW = time.time()

def new_uuid():
    return str(uuid.uuid4()).upper()

# ---- RTF label builder (uses your exact template) ----
RTF_TEMPLATE = (
    "{\\rtf1\\ansi\\ansicpg1252\\cocoartf2822\n"
    "\\cocoatextscaling0\\cocoaplatform0{\\fonttbl\\f0\\fnil\\fcharset0 HelveticaNeue;}\n"
    "{\\colortbl;\\red255\\green255\\blue255;\\red255\\green255\\blue255;}\n"
    "{\\*\\expandedcolortbl;;\\cssrgb\\c100000\\c100000\\c100000;}\n"
    "\\pard\\tx560\\tx1120\\tx1680\\tx2240\\tx2800\\tx3360\\tx3920\\tx4480\\tx5040\\tx5600\\tx6160\\tx6720\\pardirnatural\\qc\\partightenfactor0\n\n"
    "\\f0\\fs50 \\cf2 %s}"
)

def rtf_escape(text: str) -> str:
    # Escape backslashes and braces for RTF
    text = text.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")
    # Turn literal newlines into RTF hard line breaks used by your samples (backslash + newline)
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\\\n")
    return text

def rtf_label(text: str) -> str:
    return RTF_TEMPLATE % rtf_escape(text if text is not None else "")

# ---- Menu config builders ----
def base_menu_config(element_identifier="New", label_text=None):
    return {
        "BTTMenuItemMaxHeight": 40,
        "BTTMenuItemSelectedTab": 0,
        "BTTMenuItemMinWidth": 50,
        "BTTMenuAlwaysUseLightMode": 1,
        "BTTMenuItemBackgroundColorDark": "108.442, 96.000, 190.435, 166.991",
        "BTTMenuElementIdentifier": element_identifier,
        "BTTMenuUseStyleForSubmenu": 0,
        "BTTMenuItemVisibleWhileInactive": 1,
        "BTTMenuCategoryBackground": 1,
        "BTTMenuItemBorderColorHover": "255.000000, 255.000000, 255.000000, 255.000000",
        "BTTMenuItemBackgroundTypeDark": 4,
        "BTTLastChangeUUID": new_uuid(),
        "BTTMenuItemBackgroundType": 4,
        "BTTMenuAppearanceStyle": 0,
        "BTTMenuItemBackgroundColorHoverDark": "90, 90.000, 180, 166.991",
        "BTTMenuItemBorderColorHoverDark": "255.000000, 255.000000, 255.000000, 255.000000",
        "BTTMenuCategoryItemSizing": 1,
        "BTTMenuItemBackgroundColor": "108.442, 96.000, 190.435, 166.991",
        "BTTMenuItemBackgroundColorHover": "90, 90.000, 180, 166.991",
        "BTTMenuItemMinHeight": 40,
        "BTTMenuItemBorderColorDark": "255.000000, 255.000000, 255.000000, 255.000000",
        "BTTMenuHoverEndAnimationDuration": 0.15,
        "BTTMenuItemBorderColor": "255.000000, 255.000000, 255.000000, 255.000000",
        "BTTMenuAttributedText": rtf_label(label_text or element_identifier),
        "BTTMenuTextMinimumScaleFactor": 0.30,
        "BTTMenuHoverStartAnimationDuration": 0.15,
        "BTTMenuItemMaxWidth": 40,
        "BTTMenuItemVisibleWhileActive": 1,
        "BTTMenuItemIconColor1": "255.000000, 255.000000, 255.000000, 255.000000",
    }

def base_menu_item_config(element_identifier, label_text=None):
    cfg = base_menu_config(element_identifier, label_text=label_text or element_identifier)
    cfg["BTTMenuItemMinWidth"] = 60
    cfg["BTTMenuUseStyleForSubmenu"] = 1
    return cfg

def back_button_item(parent_uuid, order=0):
    return {
        "BTTActionCategory": 0,
        "BTTLastUpdatedAt": NOW,
        "BTTTriggerType": 777,
        "BTTTriggerTypeDescriptionReadOnly": "Back Button Item",
        "BTTTriggerTypeDescription": "Standard Item",
        "BTTTriggerParentUUID": parent_uuid,
        "BTTTriggerClass": "BTTTriggerTypeFloatingMenu",
        "BTTUUID": new_uuid(),
        "BTTEnabled": 1,
        "BTTOrder": order,
        "BTTMenuConfig": base_menu_item_config("Go Back", label_text="<<"),
        "BTTMenuAvailability": 0,
        "BTTMenuName": "Go Back",
        "BTTGestureNotes": "Standard Item"
    }

# ---- Action & item conversion ----
def action_to_menuItemAction(src_action, parent_uuid):
    act = {
        "BTTActionCategory": 0,
        "BTTLastUpdatedAt": NOW,
        "BTTTriggerParentUUID": parent_uuid,
        "BTTTriggerClass": "BTTTriggerTypeFloatingMenu",
        "BTTUUID": new_uuid(),
        "BTTEnabled": 1,
        "BTTOrder": 119,
        "BTTMenuAvailability": 0,
        "BTTMenuName": new_uuid()
    }
    for k in (
        "BTTPredefinedActionType", "BTTPredefinedActionName",
        "BTTNamedTriggerToTrigger", "BTTTerminalCommand",
        "BTTShellTaskActionScript", "BTTShellTaskActionConfig",
        "BTTAppleScriptString", "BTTAppleScriptRunInBackground"
    ):
        if k in src_action:
            act[k] = src_action[k]
    if "BTTNamedTriggerToTrigger" in src_action and "BTTPredefinedActionType" not in act:
        act["BTTPredefinedActionType"] = 248
        act["BTTPredefinedActionName"] = "Trigger Named Trigger (Configured in Other Tab)"
    return act

def touchbar_button_to_menu_item(tb_btn, submenu_uuid, order_index):
    item_uuid = new_uuid()
    name = tb_btn.get("BTTTouchBarButtonName") or tb_btn.get("BTTTriggerTypeDescription") or "Menu Item"
    trigger_name = f"Menu Item: {name}"

    menu_item = {
        "BTTActionCategory": 0,
        "BTTLastUpdatedAt": NOW,
        "BTTTriggerType": 773,
        "BTTTriggerTypeDescription": "Standard Item",
        "BTTTriggerParentUUID": submenu_uuid,
        "BTTTriggerClass": "BTTTriggerTypeFloatingMenu",
        "BTTUUID": item_uuid,
        "BTTPredefinedActionType": 366,
        "BTTPredefinedActionName": "Empty Placeholder",
        "BTTAdditionalConfiguration": f"Menu Item: {name}",
        "BTTEnabled": 1,
        "BTTOrder": order_index,
        "BTTTriggerName": trigger_name,
        "BTTMenuItemActions": [],
        "BTTMenuConfig": base_menu_item_config(name, label_text=name),  # <-- RTF label here
        "BTTMenuAvailability": 0,
        "BTTMenuName": name,
        "BTTGestureNotes": "Standard Item"
    }

    # Attach the action corresponding to this TB button
    menu_item["BTTMenuItemActions"].append(action_to_menuItemAction(tb_btn, item_uuid))
    return menu_item

def convert_folder(folder, parent_uuid_override=None):
    submenu_uuid = new_uuid()
    tb_name = folder.get("BTTTouchBarButtonName") or folder.get("BTTTriggerTypeDescription") or "New"
    submenu = {
        "BTTActionCategory": 0,
        "BTTLastUpdatedAt": NOW,
        "BTTTriggerType": 774,
        "BTTTriggerTypeDescriptionReadOnly": "Sub Menu",
        "BTTTriggerTypeDescription": "Standard Item",
        "BTTTriggerParentUUID": parent_uuid_override or new_uuid(),
        "BTTTriggerClass": "BTTTriggerTypeFloatingMenu",
        "BTTUUID": submenu_uuid,
        "BTTEnabled": int(folder.get("BTTEnabled", 1)),
        "BTTOrder": int(folder.get("BTTOrder", 0)),
        "BTTMenuItems": [],
        "BTTMenuConfig": base_menu_config(tb_name, label_text=tb_name),  # <-- RTF label here
        "BTTMenuAvailability": 0,
        "BTTMenuName": tb_name,
        "BTTGestureNotes": "Standard Item"
    }

    # Back button
    submenu["BTTMenuItems"].append(back_button_item(submenu_uuid, order=0))

    # Items from folder's additional actions
    actions = folder.get("BTTAdditionalActions", [])
    order = 1
    for tb_btn in actions:
        # Skip "Close currently open Touch Bar group" (handled by back button)
        if tb_btn.get("BTTPredefinedActionType") == 191:
            continue
        submenu["BTTMenuItems"].append(touchbar_button_to_menu_item(tb_btn, submenu_uuid, order_index=order))
        order += 1

    return submenu

def main():
    if len(sys.argv) < 2:
        print("Usage: python3 btt_touchbar_folder_to_floating_submenu.py input.json [parent_uuid]", file=sys.stderr)
        sys.exit(1)
    parent_uuid_override = sys.argv[2] if len(sys.argv) >= 3 else None

    with open(sys.argv[1], "r", encoding="utf-8") as f:
        data = json.load(f)

    folders = data if isinstance(data, list) else [data]
    out = []

    # Convert top-level group items
    for folder in folders:
        if folder.get("BTTTriggerClass") == "BTTTriggerTypeTouchBar" and folder.get("BTTTriggerType") == 630:
            out.append(convert_folder(folder, parent_uuid_override))

    # If none matched, scan list for embedded groups
    if not out and isinstance(data, list):
        for obj in data:
            if isinstance(obj, dict) and obj.get("BTTTriggerType") == 630 and obj.get("BTTTriggerClass") == "BTTTriggerTypeTouchBar":
                out.append(convert_folder(obj, parent_uuid_override))

    print(json.dumps(out, indent=2, ensure_ascii=False))

if __name__ == "__main__":
    main()
