#!/usr/bin/env python3
"""Preview or install Media > Lighting: a 💡 submenu with Solder SW and a 🎨 colour panel
for the WiZ bulbs and the LED floods.

The submenu holds a blind Solder SW toggle and a 🎨 button that opens the
"Lighting Colour" dropdown, a top-level floating menu whose web view is the
colour control built from lighting_control/. Inspects by default. --apply backs up
the Media export, adds or repairs only this installer's UUIDs through BTT's
scripting API, verifies the runtime and saved records, then lets BTT Guard accept
exactly those edits.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shlex
import subprocess
import uuid

from btt_common import MEDIA, ROOT, current_database, records
from btt_guard.installer_checkpoint import after_install, before_install, parse_edit_receipt
from btt_touchbar_folder_to_floating_submenu import base_menu_config, base_menu_item_config
from lighting_control.page import build_page
from tools_menu_item import BUILD, verify_saved_item

DIRECTORY = Path(__file__).resolve().parent
NAMESPACE = uuid.UUID("5B0F3E64-7C1D-4E3A-9B8F-2D6A1C4E7F90")
TOGGLE = ROOT / "macbook/scripts/van_lighting.py"
SOLDER_SWITCH = "switch.solder_flood"  # the dashboard Lighting tile's Solder SW target
FLOATING = "BTTTriggerTypeFloatingMenu"
INSTALLER = "install_lighting_menu"


def uid(name: str) -> str:
    return str(uuid.uuid5(NAMESPACE, name)).upper()


SUBMENU, BACK, SOLDER, COLOUR, PANEL, PANEL_VIEW = (uid(name) for name in (
    "submenu", "back", "solder", "colour", "colour-menu", "colour-view"))
PANEL_IDENTIFIER = "media-lighting-colour-dropdown"
PANEL_WIDTH, PANEL_HEIGHT = 300, 430  # tall enough for every room's chip row
# The lights the colour panel controls: the WiZ bulbs by the dashboard's rooms, and the
# LED floods as one group, which the dashboard drives through the same hue, white and
# brightness endpoints. A flood reads as unavailable while its power switch is off.
PANEL_LIGHTS = (
    {"entity_id": "light.wiz_front_driver", "label": "Driver", "group_label": "Cab"},
    {"entity_id": "light.wiz_front_passenger", "label": "Passenger", "group_label": "Cab"},
    {"entity_id": "light.wiz_dresser", "label": "Dresser", "group_label": "Rear"},
    {"entity_id": "light.wiz_werkbench", "label": "Werkbench", "group_label": "Rear"},
    {"entity_id": "light.wiz_kitchen", "label": "Kitchen", "group_label": "Kitchen"},
    {"entity_id": "light.solder_led", "label": "Solder LED", "group_label": "Floods"},
    {"entity_id": "light.ext_led", "label": "Exterior LED", "group_label": "Floods"},
)


# The Media bar's Screen Cap and Notes buttons: 14pt Helvetica broken by a line separator.
TWO_LINE_RTF = ("{\\rtf1\\ansi\\ansicpg1252\\cocoartf2869\n\\cocoatextscaling0\\cocoaplatform0"
                "{\\fonttbl\\f0\\fswiss\\fcharset0 Helvetica;}\n"
                "{\\colortbl;\\red255\\green255\\blue255;\\red0\\green0\\blue0;}\n"
                "{\\*\\expandedcolortbl;;\\csgray\\c0;}\n\\deftab720\n\\pard\\pardeftab720\\qc\\partightenfactor0\n\n"
                "\\f0\\fs28 \\cf2 %s\\uc0\\u8232 %s}")


def tooltip(config: dict, text: str) -> dict:
    # BTTMenuItemShowIdentifierAsTooltip=2 shows BTTMenuElementTooltip, as on the Sonos join button.
    return dict(config, BTTMenuElementTooltip=text, BTTMenuItemShowIdentifierAsTooltip=2)


def _shell_action(parent: str, command: str) -> dict:
    return {"BTTTriggerParentUUID": parent, "BTTTriggerClass": FLOATING, "BTTEnabled": 1, "BTTOrder": 0,
            "BTTPredefinedActionType": 206, "BTTPredefinedActionName": "Run Shell Script / Task",
            "BTTShellTaskActionScript": command, "BTTShellTaskActionConfig": "/bin/zsh:::-c:::-:::"}


def _show_menu_action(parent: str, menu: str, identifier: str) -> dict:
    # The Notes launchers' form: show a hidden dropdown below the button that was clicked.
    return {"BTTTriggerParentUUID": parent, "BTTTriggerClass": FLOATING, "BTTEnabled": 1, "BTTOrder": 0,
            "BTTPredefinedActionType": 386, "BTTPredefinedActionName": "Show Floating Menu",
            "BTTAdditionalActionData": {"BTTMenuActionMenuID": menu, "BTTMenuActionMenuName": identifier,
                                        "BTTMenuActionRestorePosition": True,
                                        "BTTMenuActionActivateKeyboardFocus": 0}}


def _panel() -> list[dict]:
    """The hidden "Lighting Colour" dropdown (top level, like the Notes dropdowns) and its web view."""
    common = {"BTTTriggerClass": FLOATING, "BTTEnabled": 1, "BTTEnabled2": 1}
    size = {"BTTMenuItemMinWidth": PANEL_WIDTH, "BTTMenuItemMaxWidth": PANEL_WIDTH,
            "BTTMenuItemMinHeight": PANEL_HEIGHT, "BTTMenuItemMaxHeight": PANEL_HEIGHT}
    menu = dict(common, BTTUUID=PANEL, BTTTriggerType=767, BTTTriggerParentUUID=None,
                BTTAppBundleIdentifier="BT.G", BTTMenuName=PANEL_IDENTIFIER, BTTMenuConfig={
                    "BTTMenuElementIdentifier": PANEL_IDENTIFIER,
                    "BTTMenuLayoutDirection": 6, "BTTMenuSizingBehavior": 3,
                    "BTTMenuFrameWidth": PANEL_WIDTH, "BTTMenuFrameHeight": PANEL_HEIGHT,
                    "BTTMenuHorizontalSpacing": 0, "BTTMenuVerticalSpacing": 0,
                    "BTTMenuPositioningType": 1, "BTTMenuPositionRelativeTo": 7,
                    "BTTMenuAnchorMenu": 0, "BTTMenuAnchorRelation": 0, "BTTMenuOffsetX": 0, "BTTMenuOffsetY": -4,
                    # Hidden until the 🎨 button shows it; slider drags must not close it.
                    "BTTMenuVisibility": 1, "BTTMenuCloseOnOutsideClick": 1, "BTTMenuCloseAfterAction": 0,
                    "BTTMenuCloseOnMoveMouseAway": 0, "BTTMenuWindowResizable": 0, "BTTMenuWindowLevel": 3,
                    "BTTMenuOpacityActive": 1, "BTTMenuOpacityInactive": 1, "BTTMenuDisableDrag": 1,
                    "BTTMenuKeepCached": 0, "BTTMenuModifierKeys": 0, "BTTMenuModifierMode": -1,
                    "BTTMenuItemBackgroundType": 4, "BTTMenuItemBackgroundColor": "50, 50, 60, 245",
                    "BTTMenuItemBackgroundColorDark": "50, 50, 60, 245",
                    "BTTMenuItemVisibleWhileActive": 1, "BTTMenuItemVisibleWhileInactive": 1, **size})
    # BTTMenuItemWebViewPlain=0 keeps BTT's script bridge (runShellScript) in the page.
    view = dict(common, BTTUUID=PANEL_VIEW, BTTTriggerType=778, BTTTriggerParentUUID=PANEL, BTTOrder=0,
                BTTMenuName="lighting-colour-control", BTTMenuConfig={
                    "BTTMenuElementIdentifier": "lighting-colour-control",
                    "BTTMenuItemText": build_page(list(PANEL_LIGHTS), str(TOGGLE)),
                    "BTTMenuItemWebViewPlain": 0, "BTTMenuItemBackgroundType": 4,
                    "BTTMenuItemVisibleWhileActive": 1, "BTTMenuItemVisibleWhileInactive": 1, **size})
    return [menu, view]


def definition() -> dict:
    """Every owned item in install order, each naming its parent (None: a top-level menu).

    Items without a BTTOrder (the submenu and the dropdown) are appended when first added:
    the submenu after the Media bar's last item, the dropdown at saved_facts' menu_order.
    """
    # BTT renames each item after its element identifier, so names repeat the identifiers;
    # it also assigns new actions their UUIDs itself.
    if not os.access(TOGGLE, os.X_OK):
        raise SystemExit(f"{TOGGLE} is missing or not executable; no changes made.")
    toggle = shlex.join(["/usr/bin/python3", "-B", str(TOGGLE), "toggle", SOLDER_SWITCH])
    common = {"BTTTriggerClass": FLOATING, "BTTEnabled": 1, "BTTEnabled2": 1}
    bulb = dict(base_menu_config("Lighting", "💡"), BTTMenuItemMinWidth=40, BTTMenuItemMaxWidth=40)
    submenu = dict(common, BTTUUID=SUBMENU, BTTTriggerType=774, BTTTriggerParentUUID=MEDIA,
                   BTTMenuName="Lighting", BTTMenuConfig=tooltip(bulb, "Lighting"))
    back = dict(common, BTTUUID=BACK, BTTTriggerType=777, BTTTriggerParentUUID=SUBMENU, BTTOrder=0,
                BTTMenuName="Go Back", BTTMenuConfig=base_menu_item_config("Go Back", label_text="<<"))
    label = dict(base_menu_item_config("Solder SW"), BTTMenuAttributedText=TWO_LINE_RTF % ("Solder", "SW"),
                 BTTMenuItemText="Solder\nSW", BTTMenuItemMinWidth=54, BTTMenuItemMaxWidth=78)
    solder = dict(common, BTTUUID=SOLDER, BTTTriggerType=773, BTTTriggerParentUUID=SUBMENU, BTTOrder=1,
                  BTTMenuName="Solder SW", BTTMenuConfig=tooltip(label, "Toggle solder power"),
                  BTTMenuItemActions=[_shell_action(SOLDER, toggle)])
    palette = dict(base_menu_item_config("Colour", "🎨"), BTTMenuItemMinWidth=40, BTTMenuItemMaxWidth=40)
    colour = dict(common, BTTUUID=COLOUR, BTTTriggerType=773, BTTTriggerParentUUID=SUBMENU, BTTOrder=2,
                  BTTMenuName="Colour", BTTMenuConfig=tooltip(palette, "Light colours"),
                  BTTMenuItemActions=[_show_menu_action(COLOUR, PANEL, PANEL_IDENTIFIER)])
    return {"media_uuid": MEDIA, "items": [submenu, back, solder, colour, *_panel()]}


def saved_facts(items: list[dict]) -> dict:
    """From BTT's saved store: actions on this installer's buttons that lack the Media
    preset link, and the next free position among the preset's top-level floating menus."""
    saved = records(current_database())
    preset = saved[MEDIA]["ZBELONGSTOPRESET2"]
    owners = {item["BTTUUID"] for item in items if item.get("BTTMenuItemActions")}
    unlinked = sorted(key for key, record in saved.items() if record["parent"] in owners
                      and record["ZGESTURETYPE"] == -1 and record["ZBELONGSTOPRESET2"] != preset)
    menus = [record["ZORDER"] or 0 for record in saved.values() if record["parent"] is None
             and record["ZGESTURETYPE"] == 767 and record["ZBELONGSTOPRESET2"] == preset]
    return {"unlinked_actions": unlinked, "menu_order": 1 + max(menus, default=-1)}


def worker(payload: Path, mode: str, refresh: bool) -> subprocess.CompletedProcess:
    return subprocess.run(["/usr/bin/osascript", "-l", "JavaScript", str(DIRECTORY / "install_lighting_menu.js"),
                           str(payload), str(DIRECTORY / "btt_common.js"), str(BUILD), mode,
                           "refresh" if refresh else ""], capture_output=True, text=True, timeout=120)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="back up, install through BTT, verify saved records")
    mode.add_argument("--inspect", action="store_true", help="read-only preview (the default)")
    parser.add_argument("--no-checkpoint", action="store_true", help="do not advance BTT Guard after verification")
    parser.add_argument("--refresh", action="store_true", help="recreate the visible Media menu afterwards")
    args = parser.parse_args(argv)
    if args.refresh and not args.apply:
        parser.error("--refresh requires --apply")
    os.umask(0o077)
    BUILD.mkdir(exist_ok=True)
    payload = BUILD / "lighting_menu.json"
    wanted = definition()
    payload.write_text(json.dumps(dict(wanted, **saved_facts(wanted["items"])),
                                  ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if not args.apply:
        result = worker(payload, "inspect", False)
        print(result.stdout.strip() if not result.returncode else
              result.stderr.strip() or "BTT inspection failed; no changes made.")
        return result.returncode
    baseline = before_install(no_checkpoint=args.no_checkpoint)
    result = worker(payload, "apply", args.refresh)
    if result.returncode:
        # The worker's stdout can hold exported Media data; report only its error.
        print(result.stderr.strip() or "BTT Lighting installer failed; no checkpoint requested.")
        return result.returncode
    receipt = parse_edit_receipt(result.stdout)
    print(receipt["message"])
    if receipt["wrote"]:
        verify_saved_item(receipt["saved_plan"])
    after_install(baseline, receipt["touched_uuids"], INSTALLER, wrote=receipt["wrote"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
