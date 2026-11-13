#!/usr/bin/env python3
"""Preview or install Media > Mac: a laptop-icon submenu for Mac window and system items.

It holds iTerm↑, which moves every iTerm window to the connected monitor, and iTerm↓,
which moves them to the MacBook's display; both tile the windows side by side at full
height across the whole width, re-tiling windows already there (macbook/scripts/
iterm_tile.js). Inspects by default. --apply backs up the Media export, adds or repairs
only this installer's UUIDs through BTT's scripting API, verifies the runtime and saved
records, then lets BTT Guard accept exactly those edits.
"""
from __future__ import annotations

import os
import shlex
import uuid

from btt_common import MEDIA, ROOT
from btt_touchbar_folder_to_floating_submenu import base_menu_config, base_menu_item_config, rtf_label
import media_submenu
from media_submenu import ITEM, TWO_LINE_RTF, back_item, shell_action, tooltip

NAMESPACE = uuid.UUID("E7D9CF4D-42C0-41FF-B62B-83BDB84A39C1")
TILE = ROOT / "macbook/scripts/iterm_tile.js"
INSTALLER = "install_mac_menu"
LAPTOP_SYMBOL = "laptopcomputer"


def uid(name: str) -> str:
    return str(uuid.uuid5(NAMESPACE, name)).upper()


SUBMENU, BACK, ITERM_UP, ITERM_DOWN = (uid(name) for name in ("submenu", "back", "iterm-up", "iterm-down"))

# The Sonos join button's SF Symbol settings: a 22pt black symbol and no text. BTT saves a
# symbol with no rendered image as icon type 2 (as on the play/pause button), so ask for that.
SYMBOL_ICON = {"BTTMenuItemShowIcon": 1, "BTTMenuItemIconType": 2, "BTTMenuItemIconTypeDark": 2,
               "BTTMenuItemIconPosition": 4, "BTTMenuItemIconPositionDark": 4,
               "BTTMenuItemImageWidth": 22, "BTTMenuItemImageHeight": 22,
               "BTTMenuItemImageWidthDark": 22, "BTTMenuItemImageHeightDark": 22,
               "BTTMenuItemResizeImage": 0, "BTTMenuItemResizeImageDark": 0,
               "BTTMenuItemImageChangeColor": 1, "BTTMenuItemImageChangeColorDark": 1,
               **{key: "0, 0, 0, 255" for key in ("BTTMenuItemIconColor1", "BTTMenuItemIconColor1Dark",
                                                 "BTTMenuItemIconColor1Hover", "BTTMenuItemIconColor1HoverDark")}}


def _tile_button(item_uuid: str, order: int, arrow: str, rtf_arrow: str, target: str, tip: str) -> dict:
    name = "iTerm" + arrow
    # TWO_LINE_RTF's \uc0 is in effect for the second line, so its \u escape needs no fallback character.
    label = dict(base_menu_item_config(name), BTTMenuAttributedText=TWO_LINE_RTF % ("iTerm", rtf_arrow),
                 BTTMenuItemText="iTerm\n" + arrow, BTTMenuItemMinWidth=54, BTTMenuItemMaxWidth=78)
    command = shlex.join(["/usr/bin/osascript", "-l", "JavaScript", str(TILE), target])
    return {**ITEM, "BTTUUID": item_uuid,
            "BTTTriggerType": 773, "BTTTriggerParentUUID": SUBMENU, "BTTOrder": order, "BTTMenuName": name,
            "BTTMenuConfig": tooltip(label, tip), "BTTMenuItemActions": [shell_action(item_uuid, command)]}


def definition() -> dict:
    """Every owned item in install order, each naming its parent. The submenu has no
    BTTOrder, so a first install appends it after the Media bar's last item."""
    if not os.access(TILE, os.R_OK):
        raise SystemExit(f"{TILE} is missing or unreadable; no changes made.")
    laptop = dict(base_menu_config("Mac"), BTTMenuAttributedText=rtf_label(""), BTTMenuItemMinWidth=40,
                  BTTMenuItemMaxWidth=40, BTTMenuItemSFSymbolName=LAPTOP_SYMBOL, **SYMBOL_ICON)
    submenu = dict(ITEM, BTTUUID=SUBMENU, BTTTriggerType=774, BTTTriggerParentUUID=MEDIA,
                   BTTMenuName="Mac", BTTMenuConfig=tooltip(laptop, "Mac"))
    back = back_item(BACK, SUBMENU)
    up = _tile_button(ITERM_UP, 1, "↑", "\\u8593 ", "external", "Tile iTerm windows on the monitor")
    down = _tile_button(ITERM_DOWN, 2, "↓", "\\u8595 ", "builtin", "Tile iTerm windows on the MacBook display")
    return {"media_uuid": MEDIA, "noun": "Mac", "title": "Media > Mac",
            "backup_prefix": "btt-mac-menu-backup-", "items": [submenu, back, up, down]}


def main(argv: list[str] | None = None) -> int:
    return media_submenu.main(argv, description=__doc__.split("\n\n")[0], definition=definition,
                              payload_name="mac_menu.json", installer=INSTALLER)


if __name__ == "__main__":
    raise SystemExit(main())
