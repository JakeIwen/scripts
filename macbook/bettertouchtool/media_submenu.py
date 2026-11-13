"""Shared launcher for guarded Media submenu installers (Lighting, Mac).

Each installer supplies definition(): a flat list of the items it owns, each naming its
parent (None: a top-level floating menu), plus the labels its messages use. This module
adds the saved-store facts, runs media_submenu_install.js through osascript, verifies the
saved records and lets BTT Guard accept exactly the edits it made.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
from typing import Callable

from btt_common import current_database, records
from btt_touchbar_folder_to_floating_submenu import base_menu_item_config
from btt_guard.installer_checkpoint import after_install, before_install, parse_edit_receipt
from tools_menu_item import BUILD, verify_saved_item

DIRECTORY = Path(__file__).resolve().parent
FLOATING = "BTTTriggerTypeFloatingMenu"
# The fields every enabled floating-menu item these installers own carries.
ITEM = {"BTTTriggerClass": FLOATING, "BTTEnabled": 1, "BTTEnabled2": 1}
# The Media bar's Screen Cap and Notes buttons: 14pt Helvetica broken by a line separator.
TWO_LINE_RTF = ("{\\rtf1\\ansi\\ansicpg1252\\cocoartf2869\n\\cocoatextscaling0\\cocoaplatform0"
                "{\\fonttbl\\f0\\fswiss\\fcharset0 Helvetica;}\n"
                "{\\colortbl;\\red255\\green255\\blue255;\\red0\\green0\\blue0;}\n"
                "{\\*\\expandedcolortbl;;\\csgray\\c0;}\n\\deftab720\n\\pard\\pardeftab720\\qc\\partightenfactor0\n\n"
                "\\f0\\fs28 \\cf2 %s\\uc0\\u8232 %s}")


def tooltip(config: dict, text: str) -> dict:
    # BTTMenuItemShowIdentifierAsTooltip=2 shows BTTMenuElementTooltip, as on the Sonos join button.
    return dict(config, BTTMenuElementTooltip=text, BTTMenuItemShowIdentifierAsTooltip=2)


def back_item(item_uuid: str, parent: str) -> dict:
    """The submenu's first item: BTT's "<<" Go Back button."""
    return dict(ITEM, BTTUUID=item_uuid, BTTTriggerType=777, BTTTriggerParentUUID=parent, BTTOrder=0,
                BTTMenuName="Go Back", BTTMenuConfig=base_menu_item_config("Go Back", label_text="<<"))


def shell_action(parent: str, command: str) -> dict:
    return {"BTTTriggerParentUUID": parent, "BTTTriggerClass": FLOATING, "BTTEnabled": 1, "BTTOrder": 0,
            "BTTPredefinedActionType": 206, "BTTPredefinedActionName": "Run Shell Script / Task",
            "BTTShellTaskActionScript": command, "BTTShellTaskActionConfig": "/bin/zsh:::-c:::-:::"}


def saved_facts(media_uuid: str, items: list[dict]) -> dict:
    """From BTT's saved store: actions on this installer's buttons that lack the Media
    preset link, and the next free position among the preset's top-level floating menus."""
    saved = records(current_database())
    preset = saved[media_uuid]["ZBELONGSTOPRESET2"]
    owners = {item["BTTUUID"] for item in items if item.get("BTTMenuItemActions")}
    unlinked = sorted(key for key, record in saved.items() if record["parent"] in owners
                      and record["ZGESTURETYPE"] == -1 and record["ZBELONGSTOPRESET2"] != preset)
    menus = [record["ZORDER"] or 0 for record in saved.values() if record["parent"] is None
             and record["ZGESTURETYPE"] == 767 and record["ZBELONGSTOPRESET2"] == preset]
    return {"unlinked_actions": unlinked, "menu_order": 1 + max(menus, default=-1)}


def worker(payload: Path, mode: str, refresh: bool) -> subprocess.CompletedProcess:
    return subprocess.run(["/usr/bin/osascript", "-l", "JavaScript", str(DIRECTORY / "media_submenu_install.js"),
                           str(payload), str(DIRECTORY / "btt_common.js"), str(BUILD), mode,
                           "refresh" if refresh else ""], capture_output=True, text=True, timeout=120)


def main(argv: list[str] | None, *, description: str, definition: Callable[[], dict],
         payload_name: str, installer: str) -> int:
    parser = argparse.ArgumentParser(description=description)
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
    payload = BUILD / payload_name
    wanted = definition()
    payload.write_text(json.dumps(dict(wanted, **saved_facts(wanted["media_uuid"], wanted["items"])),
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
        print(result.stderr.strip() or f"BTT {wanted['noun']} installer failed; no checkpoint requested.")
        return result.returncode
    receipt = parse_edit_receipt(result.stdout)
    print(receipt["message"])
    if receipt["wrote"]:
        verify_saved_item(receipt["saved_plan"])
    after_install(baseline, receipt["touched_uuids"], installer, wrote=receipt["wrote"])
    return 0
