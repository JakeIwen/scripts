#!/usr/bin/env python3
"""Preview or install Media > Lighting: a 💡 submenu holding the Solder SW toggle.

Inspects by default. --apply backs up the Media export, adds or repairs only this
installer's UUIDs through BTT's scripting API, verifies the runtime and saved
records, then lets BTT Guard accept exactly those edits.
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
from tools_menu_item import BUILD, verify_saved_item

DIRECTORY = Path(__file__).resolve().parent
NAMESPACE = uuid.UUID("5B0F3E64-7C1D-4E3A-9B8F-2D6A1C4E7F90")
TOGGLE = ROOT / "macbook/scripts/van_lighting.py"
SOLDER_SWITCH = "switch.solder_flood"  # the dashboard Lighting tile's Solder SW target
FLOATING = "BTTTriggerTypeFloatingMenu"
INSTALLER = "install_lighting_menu"


def uid(name: str) -> str:
    return str(uuid.uuid5(NAMESPACE, name)).upper()


SUBMENU, BACK, SOLDER = (uid(name) for name in ("submenu", "back", "solder"))


# The Media bar's Screen Cap and Notes buttons: 14pt Helvetica broken by a line separator.
TWO_LINE_RTF = ("{\\rtf1\\ansi\\ansicpg1252\\cocoartf2869\n\\cocoatextscaling0\\cocoaplatform0"
                "{\\fonttbl\\f0\\fswiss\\fcharset0 Helvetica;}\n"
                "{\\colortbl;\\red255\\green255\\blue255;\\red0\\green0\\blue0;}\n"
                "{\\*\\expandedcolortbl;;\\csgray\\c0;}\n\\deftab720\n\\pard\\pardeftab720\\qc\\partightenfactor0\n\n"
                "\\f0\\fs28 \\cf2 %s\\uc0\\u8232 %s}")


def tooltip(config: dict, text: str) -> dict:
    # BTTMenuItemShowIdentifierAsTooltip=2 shows BTTMenuElementTooltip, as on the Sonos join button.
    return dict(config, BTTMenuElementTooltip=text, BTTMenuItemShowIdentifierAsTooltip=2)


def definition() -> dict:
    # BTT renames each item after its element identifier, so names repeat the identifiers;
    # it also assigns the action's UUID itself.
    if not os.access(TOGGLE, os.X_OK):
        raise SystemExit(f"{TOGGLE} is missing or not executable; no changes made.")
    command = shlex.join(["/usr/bin/python3", "-B", str(TOGGLE), "toggle", SOLDER_SWITCH])
    common = {"BTTTriggerClass": FLOATING, "BTTEnabled": 1, "BTTEnabled2": 1}
    bulb = dict(base_menu_config("Lighting", "💡"), BTTMenuItemMinWidth=40, BTTMenuItemMaxWidth=40)
    submenu = dict(common, BTTUUID=SUBMENU, BTTTriggerType=774, BTTMenuName="Lighting",
                   BTTMenuConfig=tooltip(bulb, "Lighting"))
    back = dict(common, BTTUUID=BACK, BTTTriggerType=777, BTTOrder=0, BTTMenuName="Go Back",
                BTTMenuConfig=base_menu_item_config("Go Back", label_text="<<"))
    label = dict(base_menu_item_config("Solder SW"), BTTMenuAttributedText=TWO_LINE_RTF % ("Solder", "SW"),
                 BTTMenuItemText="Solder\nSW", BTTMenuItemMinWidth=54, BTTMenuItemMaxWidth=78)
    solder = dict(common, BTTUUID=SOLDER, BTTTriggerType=773, BTTOrder=1, BTTMenuName="Solder SW",
                  BTTMenuConfig=tooltip(label, "Toggle solder power"),
                  BTTMenuItemActions=[{
                      "BTTTriggerParentUUID": SOLDER, "BTTTriggerClass": FLOATING,
                      "BTTEnabled": 1, "BTTOrder": 0, "BTTPredefinedActionType": 206,
                      "BTTPredefinedActionName": "Run Shell Script / Task",
                      "BTTShellTaskActionScript": command,
                      "BTTShellTaskActionConfig": "/bin/zsh:::-c:::-:::"}])
    return {"media_uuid": MEDIA, "submenu": submenu, "children": [back, solder]}


def unlinked_actions(payload: dict) -> list[str]:
    """Saved actions on this installer's buttons that lack the Media preset link."""
    owners = {child["BTTUUID"] for child in payload["children"] if child.get("BTTMenuItemActions")}
    saved = records(current_database())
    preset = saved[MEDIA]["ZBELONGSTOPRESET2"]
    return sorted(key for key, record in saved.items() if record["parent"] in owners
                  and record["ZGESTURETYPE"] == -1 and record["ZBELONGSTOPRESET2"] != preset)


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
    payload.write_text(json.dumps(dict(wanted, unlinked_actions=unlinked_actions(wanted)),
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
