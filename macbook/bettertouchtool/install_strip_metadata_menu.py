#!/usr/bin/env python3
"""Add Tools > Strip Metadata using BTT's API; never edit its database."""
import argparse
import json
from pathlib import Path
import shlex
import subprocess
import sys
import uuid

from btt_touchbar_folder_to_floating_submenu import base_menu_config, back_button_item
from tools_menu_style import tools_button_config

ROOT = Path(__file__).resolve().parents[1]
MAIN_MENU = "D9B0ED12-C4BE-4E74-B0DA-0CC3BE092289"
NAMESPACE = uuid.UUID("84063911-82c2-4d13-bc3d-abf22731cafa")


def uid(name):
    return str(uuid.uuid5(NAMESPACE, name)).upper()


def definition(parent):
    submenu_id = uid("tools")
    item_id = uid("strip")
    config = tools_button_config("strip-image-metadata", "Strip Metadata")
    item = {
        "BTTUUID": item_id, "BTTTriggerType": 773, "BTTEnabled": 1,
        "BTTTriggerClass": "BTTTriggerTypeFloatingMenu", "BTTOrder": 1,
        "BTTMenuName": "Strip Metadata", "BTTTriggerParentUUID": submenu_id,
        "BTTMenuConfig": config,
        "BTTMenuItemActions": [{
            "BTTUUID": uid("strip-action"), "BTTTriggerParentUUID": item_id,
            "BTTTriggerClass": "BTTTriggerTypeFloatingMenu", "BTTEnabled": 1, "BTTOrder": 0,
            "BTTPredefinedActionType": 206, "BTTPredefinedActionName": "Run Shell Script / Task",
            "BTTShellTaskActionScript": shlex.join([
                "/bin/zsh", str(ROOT / "scripts" / "strip-image-metadata.zsh")]),
            "BTTShellTaskActionConfig": "/bin/zsh:::-c:::-:::"}],
    }
    config = base_menu_config("image-tools", "Tools")
    config.update(BTTMenuItemMinWidth=65, BTTMenuItemMaxWidth=80,
                  BTTMenuUseStyleForSubmenu=0)
    back = back_button_item(submenu_id)
    back["BTTUUID"] = uid("back")
    return {
        "BTTUUID": submenu_id, "BTTTriggerType": 774,
        "BTTTriggerTypeDescriptionReadOnly": "Sub Menu",
        "BTTTriggerClass": "BTTTriggerTypeFloatingMenu", "BTTEnabled": 1,
        "BTTTriggerParentUUID": parent, "BTTMenuName": "Tools",
        "BTTMenuConfig": config, "BTTMenuItems": [back, item],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="install using live BTT AppleScript API")
    mode.add_argument("--inspect", action="store_true", help="list live menu names/IDs/state without changing BTT")
    parser.add_argument("--parent-uuid", default=MAIN_MENU)
    args = parser.parse_args()
    build = ROOT / "build"
    build.mkdir(exist_ok=True)
    payload = build / "Strip Metadata Tools.json"
    payload.write_text(json.dumps(definition(args.parent_uuid), indent=2) + "\n")
    if not args.apply and not args.inspect:
        print(f"Generated {payload}; use --apply to install into the live parent menu.")
        return
    result = subprocess.run(["/usr/bin/osascript", "-l", "JavaScript",
                             str(Path(__file__).with_name("install_strip_metadata_menu.js")),
                             str(payload), str(build), "inspect" if args.inspect else "apply"])
    if result.returncode:
        print("BTT command failed; see the specific error above. Do not use sudo.", file=sys.stderr)
        raise SystemExit(result.returncode)


if __name__ == "__main__":
    main()
