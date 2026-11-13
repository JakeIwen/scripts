#!/usr/bin/env python3
"""Preview or explicitly install two standalone Mac Panel triggers in BTT: a menu-bar item
showing the CPU busy percentage and an Open URL keyboard shortcut."""
from __future__ import annotations

import argparse
from contextlib import closing, redirect_stdout
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time

from btt_common import MEDIA, backup_configuration, connect, current_database
from btt_guard.installer_checkpoint import after_install, before_install

DIRECTORY = Path(__file__).resolve().parent
URL = "http://127.0.0.1:8769/"
# The 681 item gets a fresh UUID so it can be added and verified while the old 674 item
# still exists; only then is the legacy item deleted (a retype under one UUID needs
# delete-then-add, which loses the menu item if the add fails).
MENU_ID = "77805B78-4542-4929-A947-B7CD6172AB7A"
LEGACY_MENU_ID = "AF69261A-4119-4B82-A3C5-F7C337DB7A17"
KEY_ID = "317CB648-FCCA-4774-9E23-750CB2BE82C4"
# BTT saves a trigger's notes as its description (ZDESC); the menu item's marker stays readable.
MARKERS = {MENU_ID: "Mac Panel", KEY_ID: "mac-panel:keyboard"}
OWNED_IDS = (MENU_ID, LEGACY_MENU_ID, KEY_ID)
OWNED_TYPES = {MENU_ID: (681,), LEGACY_MENU_ID: (674,), KEY_ID: (0,)}
INSTALLER = "install_mac_panel_menu"
MENU_ICON = "slider.horizontal.3"
# 681 is BTT's Menu Bar Icon whose title is a shell script's stdout; 674 is the icon-only
# form an earlier install used, under LEGACY_MENU_ID, which is retired once 681 is saved.
MENU_TYPE = 681
LEGACY_MENU_TYPE = 674
# The main checkout path on purpose: BTT runs this long after any worktree is gone.
CPU_SCRIPT = "/usr/bin/python3 -I -B /Users/jacobr/dev/scripts/macbook/scripts/cpu_percent.py"
REFRESH_SECONDS = 3
MODIFIERS = 1835008  # Control | Option | Command, from BTT's modifier-key table.
ADVANCED_MODIFIERS = "1835049"  # Same combination in the live BTT keyboard exports.
SHORTCUTS = (("P", 35), ("M", 46), ("K", 40))


def definitions(preset, key_code):
    common = {"BTTAppBundleIdentifier": "BT.G", "BTTTriggerBelongsToPreset": preset,
              "BTTEnabled": 1, "BTTEnabled2": 1, "BTTPredefinedActionType": 59,
              "BTTOpenURL": URL, "BTTOpenURLBrowser": "Default"}
    menu = dict(common, BTTUUID=MENU_ID, BTTTriggerType=MENU_TYPE,
                BTTTriggerClass="BTTTriggerTypeOtherTriggers",
                BTTGestureNotes=MARKERS[MENU_ID],
                # BTT's documented script-driven menu-bar form: the icon plus the script's
                # stdout (the CPU percentage) as text; it ignores BTTTouchBarButtonName here.
                BTTTriggerConfig={"BTTTouchBarItemIconType": 1,
                                  "BTTTouchBarItemSFSymbolDefaultIcon": MENU_ICON,
                                  "BTTTouchBarItemIconWidth": 18, "BTTTouchBarItemIconHeight": 18,
                                  "BTTTouchBarOnlyShowIcon": 0,
                                  "BTTTouchBarShellScriptString": CPU_SCRIPT,
                                  "BTTTouchBarScriptUpdateInterval": REFRESH_SECONDS,
                                  "BTTTouchBarAppleScriptStringRunOnInit": 1})
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
            ZACTIVATED,ZNAME3,ZBUNDLEIDENTIFIER,ZBEZIERPATH FROM ZBTTBASEENTITY""")]
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


def saved_trigger_config(saved, row):
    """BTT keeps a menu-bar item's script settings (BTTTriggerConfig) as JSON in its trait
    row's ZBEZIERPATH; {} when the trait or its JSON is missing."""
    trait = next((r for r in saved.values() if row["ZTRAIT"] is not None
                  and r["Z_PK"] == row["ZTRAIT"]), None)
    raw = (trait or {}).get("ZBEZIERPATH")
    try:
        config = json.loads(bytes(raw) if isinstance(raw, (bytes, bytearray)) else raw)
    except (TypeError, ValueError):
        return {}
    return config if isinstance(config, dict) else {}


def config_saved(saved, row, wanted):
    """Every setting we install is stored with the same value (True == 1 for BTT's flags)."""
    stored = saved_trigger_config(saved, row)
    return all(key in stored and stored[key] == value for key, value in wanted.items())


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
    for uid in OWNED_IDS:
        marker = MARKERS.get(uid)

        def mine(found_uid, *texts):
            # The legacy item shares the menu item's marker; only its UUID identifies it.
            return found_uid == uid or bool(marker and found_uid != LEGACY_MENU_ID and marker in texts)
        stored = [row for row in saved.values()
                  if mine(row["ZUNIQUEIDENTIFIER"], row["ZNOTES"], row["ZDESC"])]
        live = [row for row in runtime if mine(row.get("BTTUUID"), row.get("BTTGestureNotes"))]
        if len(stored) > 1 or len(live) > 1:
            raise RuntimeError("Ambiguous Mac Panel items; no changes made.")
        if any(row["ZUNIQUEIDENTIFIER"] != uid for row in stored) or any(
                row.get("BTTUUID") != uid for row in live):
            raise RuntimeError("Mac Panel marker has a foreign UUID; no changes made.")
        if bool(stored) != bool(live):
            raise RuntimeError("Saved/runtime Mac Panel state differs; wait for BTT to save.")
        if not stored:
            continue
        row, current = stored[0], live[0]
        allowed_types = OWNED_TYPES[uid]
        if row["ZPARENT"] is not None or row["ZBELONGSTOPRESET2"] != preset["Z_PK"] or \
                row["app_scope"] != ["BT.G"] or row["ZGESTURETYPE"] not in allowed_types:
            raise RuntimeError("Existing Mac Panel UUID has a foreign type or scope; no changes made.")
        # Saved and runtime must agree per UUID: a 674 on disk with a 681 live (or the reverse)
        # means BTT has not finished saving the last change.
        if current.get("BTTTriggerType") != row["ZGESTURETYPE"] or \
                current.get("BTTTriggerBelongsToPreset") != preset["ZNAME3"] or \
                current.get("BTTAppBundleIdentifier", "BT.G") != "BT.G":
            raise RuntimeError("Runtime Mac Panel type or scope differs; no changes made.")
        if row["ZCONDITIONS"] or any(r["ZPARENT"] == row["Z_PK"] for r in saved.values()):
            raise RuntimeError("Existing Mac Panel item has conditions/children; review before replacing.")
        if any(current.get(key) for key in ("BTTActionsToExecute", "BTTAdditionalActions",
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
    legacy = saved.get(LEGACY_MENU_ID)
    for node in desired:
        previous = saved.get(node["BTTUUID"])
        if previous is None and node["BTTUUID"] == MENU_ID and legacy:
            previous = legacy  # take the retiring item's slot so the icon keeps its position
        node["BTTOrder"] = previous["ZORDER"] or 0 if previous else 1 + max(
            [-1] + [row["ZORDER"] or 0 for row in saved.values()
                    if row["ZBELONGSTOPRESET2"] == preset["Z_PK"]
                    and row["ZGESTURETYPE"] == node["BTTTriggerType"]])
    return {"before": runtime, "desired": desired, "preset_pk": preset["Z_PK"],
            "legacy": {"uuid": LEGACY_MENU_ID, "type": LEGACY_MENU_TYPE, "present": bool(legacy)},
            "summary": {"mode": "preview", "url": URL, "preset": preset["ZNAME3"],
                        "preset_uuid": preset["ZUNIQUEIDENTIFIER"], "app_scope": "BT.G (All Apps)",
                        "menu_bar": {"uuid": MENU_ID, "trigger_type": MENU_TYPE,
                                     "trigger_class": desired[0]["BTTTriggerClass"],
                                     "icon": MENU_ICON, "existing": MENU_ID in saved,
                                     "script": CPU_SCRIPT, "refresh_seconds": REFRESH_SECONDS,
                                     "legacy_uuid": LEGACY_MENU_ID,
                                     "retires_legacy_type": LEGACY_MENU_TYPE if legacy else None},
                        "keyboard": {"uuid": KEY_ID, **shortcut, "modifier_flags": MODIFIERS,
                                     "existing": KEY_ID in saved}, "candidates": candidates,
                        "protected_media_roots_touched": False, "restarts_btt": False,
                        "transport": "BTT local socket (no Apple Events)", "changes_applied": False}}


def outside_saved(saved):
    # BTT stores trigger display properties in a separate, exclusively owned trait.
    trait_pks = {row["ZTRAIT"] for uid, row in saved.items()
                 if uid in OWNED_IDS and row["ZTRAIT"] is not None}
    if any(row["ZTRAIT"] in trait_pks for uid, row in saved.items() if uid not in OWNED_IDS):
        raise RuntimeError("Mac Panel shares a display trait with another item; no changes made.")
    return {uid: row for uid, row in saved.items() if uid not in OWNED_IDS and row["Z_PK"] not in trait_pks}


def verify_saved(database, plan, before, retired=False):
    """True once BTT has saved every desired item (including the menu item's script settings)
    and the legacy item is as expected: untouched until `retired`, then gone."""
    saved = saved_state(database)
    if outside_saved(saved) != outside_saved(before):
        raise RuntimeError("Saved configuration outside Mac Panel changed; inspect the private backup.")
    legacy_before = before.get(LEGACY_MENU_ID)
    if legacy_before and not retired and saved.get(LEGACY_MENU_ID) != legacy_before:
        raise RuntimeError("The legacy Mac Panel item changed before it was retired; inspect the private backup.")
    if retired and LEGACY_MENU_ID in saved:
        return False
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
        if "BTTTriggerConfig" in node and not config_saved(saved, row, node["BTTTriggerConfig"]):
            return False
        if any(r["ZPARENT"] == row["Z_PK"] for r in saved.values()) or row["ZCONDITIONS"]:
            raise RuntimeError("Unexpected saved Mac Panel children or conditions.")
    return True


def wait_saved(database, plan, before, retired=False):
    deadline = time.monotonic() + 60
    while not verify_saved(database, plan, before, retired):
        if time.monotonic() >= deadline:
            raise RuntimeError("BTT has not saved the expected configuration; no restart or retry attempted.")
        time.sleep(.25)


def retire_legacy(database, plan, before, verified_runtime):
    """Delete the legacy 674 item, only after the new 681 item is verified in runtime and on disk.

    The baseline is the runtime apply verified, never a fresh export: edits made since
    (a child on the legacy item, conditions on its replacement) must be refused, not adopted."""
    # Saved state is rechecked right before deletion, for both owned UUIDs (children,
    # conditions, scope); a still-pending save is not good enough here.
    if not verify_saved(database, plan, before):
        raise RuntimeError("Saved Mac Panel state changed before retirement; the legacy item stays.")
    checked = dict(plan, before=verified_runtime)
    path = backup_configuration(database, checked, prefix="btt-mac-panel-retire-")
    result = worker("retire", path)
    wait_saved(database, plan, before, retired=True)
    return result["touched"], path


def install(apply=False, no_checkpoint=False):
    os.umask(0o077)
    database = current_database()
    before = saved_state(database)
    plan = installation_plan(before, worker("inspect"))
    if not apply:
        return plan["summary"]
    path = backup_configuration(database, plan, prefix="btt-mac-panel-backup-")
    if saved_state(database) != before:
        raise RuntimeError("BTT changed during backup; no changes made.")
    baseline = before_install(no_checkpoint=no_checkpoint)
    try:
        result = worker("apply", path)
        wait_saved(database, plan, before)
        touched = list(result["touched"])
        if plan["legacy"]["present"]:
            retired, path = retire_legacy(database, plan, before, result["runtime"])
            touched += retired
    except (RuntimeError, ValueError, OSError, sqlite3.Error, subprocess.SubprocessError) as error:
        raise RuntimeError(str(error) + " Backup: " + str(path.parent)) from error
    with redirect_stdout(sys.stderr):  # the checkpoint note must not break this JSON stdout
        checkpoint = after_install(baseline, touched, INSTALLER, wrote=bool(touched))
    return {**plan["summary"], "mode": "apply", "changes_applied": bool(touched),
            "writes": len(touched), "touched_uuids": touched, "saved_verified": True,
            "backup": str(path.parent), "checkpoint": checkpoint}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="back up, apply through BTT's socket, verify disk")
    mode.add_argument("--inspect", action="store_true", help="read-only preview (the default)")
    parser.add_argument("--no-checkpoint", action="store_true", help="do not advance BTT Guard's checkpoint")
    args = parser.parse_args()
    try:
        print(json.dumps(install(apply=args.apply, no_checkpoint=args.no_checkpoint), indent=2))
    except (RuntimeError, ValueError, OSError, sqlite3.Error, subprocess.SubprocessError) as error:
        parser.exit(1, str(error) + "\n")


if __name__ == "__main__":
    main()
