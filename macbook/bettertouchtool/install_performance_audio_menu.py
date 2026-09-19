#!/usr/bin/env python3
"""Add a Tools button that opens an independent vertical Performance Audio menu."""

import argparse
import copy
import json
import os
from pathlib import Path
import subprocess
import sys

from btt_touchbar_folder_to_floating_submenu import rtf_label
from build_performance_audio_menu import build, uid
from tools_menu_style import tools_button_config


ROOT = Path(__file__).resolve().parents[1]
MAIN_MENU = "D9B0ED12-C4BE-4E74-B0DA-0CC3BE092289"
# uuid5(install_strip_metadata_menu.NAMESPACE, "tools"); shared installer identity.
TOOLS_MENU = "FC3F8235-F102-55C4-8432-B6ADFB0D9992"


def definition(parent):
    dropdown = copy.deepcopy(build()["BTTPresetContent"][0]["BTTTriggers"][0])
    dropdown_id = uid("tools-dropdown")
    launcher_id = uid("tools-dropdown-launcher")
    dropdown.update(BTTUUID=dropdown_id, BTTMenuName="Performance Audio Tools Presets",
                    BTTTriggerName="Floating Menu: Performance Audio Tools Presets",
                    BTTAppBundleIdentifier="BT.G")
    # New IDs leave both the standalone pack and the old nested submenu intact.
    for item in dropdown["BTTMenuItems"]:
        item["BTTUUID"] = uid("tools-dropdown-" + item["BTTUUID"])
        item["BTTTriggerParentUUID"] = dropdown_id
        item["BTTMenuConfig"]["BTTMenuAttributedText"] = rtf_label(
            item["BTTMenuName"]).replace("\\fs50", "\\fs30")
        for action in item["BTTMenuItemActions"]:
            action["BTTUUID"] = uid("tools-dropdown-" + action["BTTUUID"])
            action["BTTTriggerParentUUID"] = item["BTTUUID"]
    config = tools_button_config("performance-audio-tools-launcher", "Performance Audio")
    layout = {
        "BTTMenuLayoutDirection": 6,  # verticalOneColumn
        "BTTMenuSizingBehavior": 1,
        "BTTMenuFrameWidth": 350,
        "BTTMenuFrameHeight": 44 * len(dropdown["BTTMenuItems"]) + 16,
        "BTTMenuHorizontalAlignment": 0,
        "BTTMenuVerticalAlignment": 0,
        "BTTMenuHorizontalSpacing": 0,
        "BTTMenuVerticalSpacing": 4,
        "BTTMenuPositioningType": 1,
        "BTTMenuPositionRelativeTo": 7,
        "BTTMenuVisibility": 1,
        "BTTMenuCloseAfterAction": 1,
        "BTTMenuKeepCached": 0,
    }
    dropdown["BTTMenuConfig"].update(layout)
    dropdown["BTTMenuConfig"]["BTTMenuElementIdentifier"] = "performance-audio-tools-dropdown"
    return {"parent": parent, "tools_uuid": TOOLS_MENU,
            "legacy_uuid": uid("tools-submenu"), "dropdown_layout": layout,
            "dropdown": dropdown, "launcher": {
        "BTTUUID": launcher_id, "BTTTriggerType": 773,
        "BTTTriggerTypeDescriptionReadOnly": "Standard Item",
        "BTTTriggerClass": "BTTTriggerTypeFloatingMenu", "BTTEnabled": 1,
        "BTTMenuName": "Performance Audio", "BTTMenuConfig": config,
        "BTTMenuItemActions": [{
            "BTTUUID": uid("tools-dropdown-show-action"), "BTTTriggerParentUUID": launcher_id,
            "BTTTriggerClass": "BTTTriggerTypeFloatingMenu", "BTTEnabled": 1, "BTTOrder": 0,
            "BTTPredefinedActionType": 386, "BTTPredefinedActionName": "Show Floating Menu",
            "BTTAdditionalActionData": {"BTTMenuActionMenuID": dropdown_id,
                                        "BTTMenuActionMenuName": dropdown["BTTMenuName"],
                                        "BTTMenuActionRestorePosition": False},
        }],
    }}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="back up, install, and verify using BTT's API")
    mode.add_argument("--inspect", action="store_true", help="inspect Media > Tools without changing BTT")
    parser.add_argument("--parent-uuid", default=MAIN_MENU)
    args = parser.parse_args()
    os.umask(0o077)  # Live menu backups can contain private action payloads.
    build_dir = ROOT / "build"
    build_dir.mkdir(exist_ok=True)
    (build_dir / "performance-audio-jobs").mkdir(exist_ok=True)
    payload = build_dir / "Performance Audio Tools.json"
    payload.write_text(json.dumps(definition(args.parent_uuid), indent=2) + "\n")
    if not args.apply and not args.inspect:
        print(f"Generated {payload}; use --apply to install into Media > Tools.")
        return
    result = subprocess.run([
        "/usr/bin/osascript", "-l", "JavaScript",
        str(Path(__file__).with_suffix(".js")), str(payload), str(build_dir),
        "inspect" if args.inspect else "apply"])
    if result.returncode:
        print("BTT command failed; see the specific error above. Do not use sudo.", file=sys.stderr)
        raise SystemExit(result.returncode)


if __name__ == "__main__":
    main()
