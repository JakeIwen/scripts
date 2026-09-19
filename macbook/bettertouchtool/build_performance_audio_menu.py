#!/usr/bin/env python3
"""Build a separate BTT preset; does not alter any existing menu or shortcut."""

import json
from pathlib import Path
import shlex
import sys
import uuid

from btt_touchbar_folder_to_floating_submenu import base_menu_config, rtf_label
from create_floating_dropdown_from_submenu import dropdown_menu_config


MACBOOK = Path(__file__).resolve().parents[1]
BUILD = MACBOOK / "build"
NAME = "Performance Audio"
NAMESPACE = uuid.UUID("dd3ba0bb-81d4-40c9-8626-a52b20c28a3f")


def uid(name):
    return str(uuid.uuid5(NAMESPACE, name)).upper()


def shell_action(command, parent):
    return {"BTTUUID": uid(parent + "-action"), "BTTTriggerParentUUID": parent,
            "BTTTriggerClass": "BTTTriggerTypeFloatingMenu", "BTTEnabled": 1,
            "BTTOrder": 0, "BTTPredefinedActionType": 206,
            "BTTPredefinedActionName": "Run Shell Script / Task",
            "BTTShellTaskActionScript": command,
            "BTTShellTaskActionConfig": "/bin/zsh:::-c:::-:::"}


def build():
    resources = BUILD / "Performance Audio.app" / "Contents" / "Resources"
    engine = resources / "performance_audio.py"
    if not engine.is_file():
        raise SystemExit("Build Performance Audio.app first.")
    python = (resources / "python-path.txt").read_text().strip()
    config = MACBOOK / "scripts" / "performance_audio_presets.json"
    settings = json.loads(config.read_text())
    menu_id = uid("menu")
    menu = {
        "BTTUUID": menu_id, "BTTTriggerType": 767,
        "BTTTriggerTypeDescriptionReadOnly": "Floating Menu",
        "BTTTriggerClass": "BTTTriggerTypeFloatingMenu", "BTTEnabled": 1,
        "BTTActionCategory": 0, "BTTMenuAvailability": 0,
        "BTTTriggerName": "Floating Menu: " + NAME, "BTTMenuName": NAME,
        "BTTMenuItems": [],
        "BTTMenuConfig": dropdown_menu_config(
            "performance-audio", len(settings["presets"]) + 3, 340, base_menu_config(NAME)),
    }
    # Position near the pointer rather than an unverified existing parent menu.
    menu["BTTMenuConfig"].update(BTTMenuPositioningType=1, BTTMenuPositionRelativeTo=7,
                                BTTMenuAnchorMenu=0, BTTMenuAnchorRelation=0,
                                BTTMenuFrameWidth=340,
                                BTTMenuFrameHeight=44 * (len(settings["presets"]) + 3))
    commands = []
    for name, preset in settings["presets"].items():
        command = shlex.join([python, str(engine), "process", "--preset", name,
                              "--config", str(config), "--finder-selection",
                              "--background", "--notify", "--reveal"])
        commands.append((name, preset["label"], command))
    commands += [
        ("choose", "Choose files (default preset)", shlex.join([
            python, str(engine), "process", "--config", str(config), "--choose",
            "--background", "--notify", "--reveal"])),
        ("settings", "Edit effects presets", shlex.join(["/usr/bin/open", "-e", str(config)])),
        ("logs", "Show processing logs", shlex.join(["/usr/bin/open", str(BUILD / "performance-audio-jobs")])),
    ]
    for order, (name, label, command) in enumerate(commands):
        item_id = uid("item-" + name)
        menu["BTTMenuItems"].append({
            "BTTUUID": item_id, "BTTTriggerParentUUID": menu_id,
            "BTTTriggerType": 773, "BTTTriggerTypeDescriptionReadOnly": "Standard Item",
            "BTTTriggerClass": "BTTTriggerTypeFloatingMenu", "BTTEnabled": 1,
            "BTTOrder": order, "BTTMenuName": label,
            "BTTMenuConfig": {"BTTMenuElementIdentifier": "performance-" + name,
                              "BTTMenuAttributedText": rtf_label(label),
                              "BTTMenuItemMinWidth": 330, "BTTMenuItemMaxWidth": 330,
                              "BTTMenuItemMinHeight": 40, "BTTMenuItemMaxHeight": 40,
                              "BTTMenuItemVisibleWhileActive": 1,
                              "BTTMenuItemVisibleWhileInactive": 1},
            "BTTMenuItemActions": [shell_action(command, item_id)],
        })
    trigger = {
        "BTTUUID": uid("show-trigger"), "BTTTriggerType": 643,
        "BTTTriggerClass": "BTTTriggerTypeOtherTriggers", "BTTEnabled": 1,
        "BTTEnabled2": 1, "BTTTriggerName": NAME,
        "BTTActionsToExecute": [{
            "BTTPredefinedActionType": 386, "BTTPredefinedActionName": "Show Floating Menu",
            "BTTAdditionalActionData": {"BTTMenuActionMenuID": menu_id,
                                        "BTTMenuActionMenuName": NAME,
                                        "BTTMenuActionRestorePosition": False},
        }],
    }
    pack = {
        "BTTPresetName": NAME, "BTTPresetUUID": uid("preset"),
        "BTTPresetCreatorNotes": "Apply offline effects to selected Finder videos. "
            "Uses the local Performance Audio app; no GarageBand, uploads, hotkeys, "
            "or changes to other menus. Show with named trigger Performance Audio.",
        "BTTPresetContent": [{"BTTAppBundleIdentifier": "BT.G", "BTTAppName": "Global",
                              "BTTTriggers": [menu, trigger]}],
    }
    return pack


def main():
    BUILD.mkdir(exist_ok=True)
    (BUILD / "performance-audio-jobs").mkdir(exist_ok=True)
    path = BUILD / "Performance Audio.bttpreset"
    path.write_text(json.dumps(build(), indent=2) + "\n")
    launcher = BUILD / "Performance Audio Menu.command"
    launcher.write_text('#!/bin/zsh\n/usr/bin/open "btt://trigger_named/?trigger_name=Performance%20Audio"\n')
    launcher.chmod(0o755)
    print(f"Built: {path}\nImport: open {shlex.quote(str(path))}")


if __name__ == "__main__":
    main()
