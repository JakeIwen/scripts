#!/usr/bin/env python3
"""Preview or explicitly install two standalone Mac Panel triggers in BTT."""
from __future__ import annotations

import argparse
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import time

from btt_common import MEDIA, backup_configuration, connect, current_database

DIRECTORY = Path(__file__).resolve().parent
URL = "http://127.0.0.1:8769/"
MENU_ID = "AF69261A-4119-4B82-A3C5-F7C337DB7A17"
KEY_ID = "317CB648-FCCA-4774-9E23-750CB2BE82C4"
# BTT saves a trigger's notes as its description (ZDESC); the menu item's marker stays readable.
MARKERS = {MENU_ID: "Mac Panel", KEY_ID: "mac-panel:keyboard"}
MENU_ICON = "slider.horizontal.3"
MODIFIERS = 1835008  # Control | Option | Command, from BTT's modifier-key table.
ADVANCED_MODIFIERS = "1835049"  # Same combination in the live BTT keyboard exports.
SHORTCUTS = (("P", 35), ("M", 46), ("K", 40))


def definitions(preset, key_code):
    common = {"BTTAppBundleIdentifier": "BT.G", "BTTTriggerBelongsToPreset": preset,
              "BTTEnabled": 1, "BTTEnabled2": 1, "BTTPredefinedActionType": 59,
              "BTTOpenURL": URL, "BTTOpenURLBrowser": "Default"}
    menu = dict(common, BTTUUID=MENU_ID, BTTTriggerType=674,
                BTTTriggerClass="BTTTriggerTypeOtherTriggers",
                BTTGestureNotes=MARKERS[MENU_ID],
                # BTT's documented menu-bar icon form; it ignores BTTTouchBarButtonName here.
                BTTTriggerConfig={"BTTTouchBarItemIconType": 1,
                                  "BTTTouchBarItemSFSymbolDefaultIcon": MENU_ICON,
                                  "BTTTouchBarItemIconWidth": 18, "BTTTouchBarItemIconHeight": 18,
                                  "BTTTouchBarOnlyShowIcon": 1})
    keyboard = dict(common, BTTUUID=KEY_ID, BTTTriggerType=0,
                    BTTTriggerClass="BTTTriggerTypeKeyboardShortcut",
                    BTTGestureNotes=MARKERS[KEY_ID], BTTTriggerOnDown=1,
                    BTTShortcutKeyCode=key_code, BTTShortcutModifierKeys=MODIFIERS,
                    BTTShortcutAdvancedModifierKeys=ADVANCED_MODIFIERS,
                    BTTAdditionalConfiguration=ADVANCED_MODIFIERS,
                    BTTShortcutScope=0, BTTAutoAdaptToKeyboardLayout=0)
    return [menu, keyboard]


def saved_state(database):
    """Read configuration and app links without writing the live Core Data store."""
    with closing(connect(database)) as connection:
        connection.execute("BEGIN")
        rows = [dict(row) for row in connection.execute("""SELECT Z_PK,ZUNIQUEIDENTIFIER,
            ZPARENT,ZBELONGSTOPRESET2,ZGESTURETYPE,ZACTION,ZACTIONCATEGORY,ZORDER,ZTRAIT,ZDESC,ZONLYICON,
            ZISENABLED,ZENABLEDNEW,ZKEYCODE,ZMODIFIERKEYS,ZTRIGGERONDOWN,ZNOTES,
            ZLAUNCHPATH,ZADDITIONALACTIONSTRING,ZACTIONDATA,ZGESTURECONFIG,ZCONDITIONS,
            ZACTIVATED,ZNAME3,ZBUNDLEIDENTIFIER FROM ZBTTBASEENTITY""")]
        # This installer deliberately supports the inspected schema only; drift fails closed.
        links = [tuple(row) for row in connection.execute(
            "SELECT Z_2GESTURES,Z_9APPS_GESTURES FROM Z_2APPS_GESTURES ORDER BY 1,2")]
    by_pk = {row["Z_PK"]: row for row in rows}
    result = {}
    for row in rows:
        uid = row["ZUNIQUEIDENTIFIER"]
        if not uid:
            continue
        if uid in result:
            raise RuntimeError("Duplicate saved UUID; no changes made.")
        row["app_scope"] = sorted({by_pk[app]["ZBUNDLEIDENTIFIER"]
                                   for app, trigger in links if trigger == row["Z_PK"]})
        result[uid] = row
    return result


def worker(mode, plan_path=None):
    command = ["/usr/bin/osascript", "-l", "JavaScript", str(DIRECTORY / "install_mac_panel_menu.js"),
               mode, str(DIRECTORY / "btt_common.js"), str(DIRECTORY / "btt_socket.js")]
    if plan_path is not None:
        command.append(str(plan_path))
    result = subprocess.run(command, capture_output=True, text=True, timeout=90)
    if result.returncode:
        # API exports contain private actions; never echo their stdout on failure.
        raise RuntimeError(result.stderr.strip() or "BTT socket request failed; no success confirmed.")
    return json.loads(result.stdout)


def choose_shortcut(saved, runtime):
    candidates = []
    for letter, code in SHORTCUTS:
        saved_conflicts = [r for uid, r in saved.items() if uid != KEY_ID
                           and r["ZGESTURETYPE"] == 0 and r["ZKEYCODE"] == code
                           and (int(r["ZMODIFIERKEYS"] or 0) & 0xFFFF0000) == MODIFIERS]
        live_conflicts = [r for r in runtime if r.get("BTTUUID") != KEY_ID
                          and r.get("BTTTriggerType") == 0 and r.get("BTTShortcutKeyCode") == code
                          and (int(r.get("BTTShortcutModifierKeys", 0)) & 0xFFFF0000) == MODIFIERS]
        candidates.append({"shortcut": "ctrl+opt+cmd+" + letter, "key_code": code,
                           "saved_conflicts": len(saved_conflicts),
                           "runtime_conflicts": len(live_conflicts)})
    free = next((item for item in candidates if not item["saved_conflicts"]
                 and not item["runtime_conflicts"]), None)
    if free is None:
        raise RuntimeError("All three Mac Panel shortcuts are occupied; no changes made.")
    return free, candidates


def validate_owned(saved, runtime, preset):
    for uid, marker in MARKERS.items():
        stored = [row for row in saved.values()
                  if row["ZUNIQUEIDENTIFIER"] == uid or marker in (row["ZNOTES"], row["ZDESC"])]
        live = [row for row in runtime
                if row.get("BTTUUID") == uid or row.get("BTTGestureNotes") == marker]
        if len(stored) > 1 or len(live) > 1:
            raise RuntimeError("Ambiguous Mac Panel items; no changes made.")
        if any(row["ZUNIQUEIDENTIFIER"] != uid for row in stored) or any(
                row.get("BTTUUID") != uid for row in live):
            raise RuntimeError("Mac Panel marker has a foreign UUID; no changes made.")
        if bool(stored) != bool(live):
            raise RuntimeError("Saved/runtime Mac Panel state differs; wait for BTT to save.")
        if not stored:
            continue
        row = stored[0]
        expected_type = 674 if uid == MENU_ID else 0
        if row["ZPARENT"] is not None or row["ZBELONGSTOPRESET2"] != preset["Z_PK"] or \
                row["app_scope"] != ["BT.G"] or row["ZGESTURETYPE"] != expected_type:
            raise RuntimeError("Existing Mac Panel UUID has a foreign type or scope; no changes made.")
        current = live[0]
        if current.get("BTTTriggerType") != expected_type or \
                current.get("BTTTriggerBelongsToPreset") != preset["ZNAME3"] or \
                current.get("BTTAppBundleIdentifier", "BT.G") != "BT.G":
            raise RuntimeError("Runtime Mac Panel type or scope differs; no changes made.")
        if row["ZCONDITIONS"] or any(r["ZPARENT"] == row["Z_PK"] for r in saved.values()):
            raise RuntimeError("Existing Mac Panel item has conditions/children; review before replacing.")
        if any(live[0].get(key) for key in ("BTTActionsToExecute", "BTTAdditionalActions",
                                           "BTTMenuItems", "BTTMenuItemActions",
                                           "BTTTriggerParentUUID", "BTTTriggerConditionsFormat",
                                           "BTTTriggerConditionsFormatReadOnly")):
            raise RuntimeError("Existing Mac Panel runtime tree needs review; no changes made.")


def installation_plan(saved, runtime):
    media = saved.get(MEDIA)
    presets = [row for row in saved.values() if media and
               row["Z_PK"] == media["ZBELONGSTOPRESET2"]]
    if len(presets) != 1 or presets[0]["ZNAME3"] != "Master" or not presets[0]["ZACTIVATED"]:
        raise RuntimeError("Cannot identify Media's active Master preset; no changes made.")
    preset = presets[0]
    if sum(row["ZNAME3"] == preset["ZNAME3"] for row in saved.values()) != 1:
        raise RuntimeError("Ambiguous Master preset name; no changes made.")
    validate_owned(saved, runtime, preset)
    outside_saved(saved)
    shortcut, candidates = choose_shortcut(saved, runtime)
    desired = definitions(preset["ZNAME3"], shortcut["key_code"])
    for node in desired:
        previous = saved.get(node["BTTUUID"])
        node["BTTOrder"] = previous["ZORDER"] or 0 if previous else 1 + max(
            [-1] + [row["ZORDER"] or 0 for row in saved.values()
                    if row["ZBELONGSTOPRESET2"] == preset["Z_PK"]
                    and row["ZGESTURETYPE"] == node["BTTTriggerType"]])
    return {"before": runtime, "desired": desired, "preset_pk": preset["Z_PK"],
            "summary": {"mode": "preview", "url": URL, "preset": preset["ZNAME3"],
                        "preset_uuid": preset["ZUNIQUEIDENTIFIER"], "app_scope": "BT.G (All Apps)",
                        "menu_bar": {"uuid": MENU_ID, "trigger_type": 674,
                                     "trigger_class": desired[0]["BTTTriggerClass"],
                                     "icon": MENU_ICON, "existing": MENU_ID in saved},
                        "keyboard": {"uuid": KEY_ID, **shortcut, "modifier_flags": MODIFIERS,
                                     "existing": KEY_ID in saved}, "candidates": candidates,
                        "protected_media_roots_touched": False, "restarts_btt": False,
                        "transport": "BTT local socket (no Apple Events)", "changes_applied": False}}


def outside_saved(saved):
    # BTT stores trigger display properties in a separate, exclusively owned trait.
    trait_pks = {row["ZTRAIT"] for uid, row in saved.items()
                 if uid in MARKERS and row["ZTRAIT"] is not None}
    if any(row["ZTRAIT"] in trait_pks for uid, row in saved.items() if uid not in MARKERS):
        raise RuntimeError("Mac Panel shares a display trait with another item; no changes made.")
    return {uid: row for uid, row in saved.items() if uid not in MARKERS and row["Z_PK"] not in trait_pks}


def verify_saved(database, plan, before):
    saved = saved_state(database)
    if outside_saved(saved) != outside_saved(before):
        raise RuntimeError("Saved configuration outside Mac Panel changed; inspect the private backup.")
    for node in plan["desired"]:
        uid = node["BTTUUID"]
        row = saved.get(uid)
        if not row:
            return False
        expected = {"ZPARENT": None, "ZBELONGSTOPRESET2": plan["preset_pk"],
                    "ZGESTURETYPE": node["BTTTriggerType"], "ZACTION": 59,
                    "ZLAUNCHPATH": URL, "ZDESC": node["BTTGestureNotes"],
                    "ZORDER": node["BTTOrder"], "ZISENABLED": 1, "ZENABLEDNEW": 1,
                    "app_scope": ["BT.G"]}
        if uid == KEY_ID:
            expected.update(ZKEYCODE=node["BTTShortcutKeyCode"], ZMODIFIERKEYS=MODIFIERS,
                            ZTRIGGERONDOWN=1, ZGESTURECONFIG=ADVANCED_MODIFIERS)
        if any(row[key] != value for key, value in expected.items()):
            return False
        if any(r["ZPARENT"] == row["Z_PK"] for r in saved.values()) or row["ZCONDITIONS"]:
            raise RuntimeError("Unexpected saved Mac Panel children or conditions.")
    return True


def install(apply=False):
    os.umask(0o077)
    database = current_database()
    before = saved_state(database)
    plan = installation_plan(before, worker("inspect"))
    if not apply:
        return plan["summary"]
    path = backup_configuration(database, plan, prefix="btt-mac-panel-backup-")
    if saved_state(database) != before:
        raise RuntimeError("BTT changed during backup; no changes made.")
    try:
        result = worker("apply", path)
        deadline = time.monotonic() + 60
        while not verify_saved(database, plan, before):
            if time.monotonic() >= deadline:
                raise RuntimeError("BTT has not saved the expected configuration; no restart or retry attempted.")
            time.sleep(.25)
    except (RuntimeError, ValueError, OSError, sqlite3.Error, subprocess.SubprocessError) as error:
        raise RuntimeError(str(error) + " Backup: " + str(path.parent)) from error
    return {**plan["summary"], "mode": "apply", "changes_applied": bool(result["writes"]),
            "writes": result["writes"], "saved_verified": True, "backup": str(path.parent)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="back up, apply through BTT's socket, verify disk")
    mode.add_argument("--inspect", action="store_true", help="read-only preview (the default)")
    args = parser.parse_args()
    try:
        print(json.dumps(install(apply=args.apply), indent=2))
    except (RuntimeError, ValueError, OSError, sqlite3.Error, subprocess.SubprocessError) as error:
        parser.exit(1, str(error) + "\n")


if __name__ == "__main__":
    main()
