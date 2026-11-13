"""Mac Panel planning and saved-state checks use isolated configuration fixtures."""
import copy
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

import install_mac_panel_menu as panel


def fixture_rows():
    fields = ["ZPARENT", "ZBELONGSTOPRESET2", "ZGESTURETYPE", "ZACTION", "ZACTIONCATEGORY",
              "ZTRAIT", "ZDESC", "ZONLYICON", "ZBEZIERPATH",
              "ZORDER", "ZISENABLED", "ZENABLEDNEW", "ZKEYCODE", "ZMODIFIERKEYS",
              "ZTRIGGERONDOWN", "ZNOTES", "ZLAUNCHPATH", "ZADDITIONALACTIONSTRING", "ZACTIONDATA",
              "ZGESTURECONFIG", "ZCONDITIONS", "ZACTIVATED", "ZNAME3", "ZBUNDLEIDENTIFIER"]
    def row(pk, uid, **values):
        return dict.fromkeys(fields) | {"Z_PK": pk, "ZUNIQUEIDENTIFIER": uid,
                                      "app_scope": [], **values}
    return {
        "preset": row(1, "preset", ZNAME3="Master", ZACTIVATED=2),
        panel.MEDIA: row(2, panel.MEDIA, ZBELONGSTOPRESET2=1, ZGESTURETYPE=767),
        "global": row(3, "global", ZBUNDLEIDENTIFIER="BT.G"),
        "other": row(4, "other", ZGESTURETYPE=0, ZKEYCODE=35, ZMODIFIERKEYS=1179648,
                     ZBELONGSTOPRESET2=1, ZISENABLED=1, ZENABLEDNEW=1, ZORDER=7),
    }


def installed_rows():
    rows = fixture_rows()
    plan = panel.installation_plan(rows, [])
    for index, node in enumerate(plan["desired"], 10):
        rows[node["BTTUUID"]] = dict(rows["other"], Z_PK=index, ZUNIQUEIDENTIFIER=node["BTTUUID"],
                                    ZGESTURETYPE=node["BTTTriggerType"], ZORDER=node["BTTOrder"],
                                    ZDESC=node["BTTGestureNotes"], ZNOTES=None, ZACTION=59, ZLAUNCHPATH=panel.URL,
                                    ZPARENT=None, app_scope=["BT.G"])
        if node["BTTUUID"] == panel.MENU_ID:
            rows[panel.MENU_ID].update(ZTRAIT=100)
            rows["menu-trait"] = dict(rows["global"], Z_PK=100, ZUNIQUEIDENTIFIER="menu-trait", ZONLYICON=0,
                                      ZBEZIERPATH=json.dumps(node["BTTTriggerConfig"]).encode())
        if node["BTTUUID"] == panel.KEY_ID:
            rows[panel.KEY_ID].update(ZKEYCODE=35, ZMODIFIERKEYS=panel.MODIFIERS,
                                      ZTRIGGERONDOWN=1, ZGESTURECONFIG=panel.ADVANCED_MODIFIERS)
    return rows, plan


class PlanningTests(unittest.TestCase):
    def test_definitions_and_first_free_shortcut(self):
        plan = panel.installation_plan(fixture_rows(), [])
        menu, keyboard = plan["desired"]
        self.assertEqual((menu["BTTTriggerType"], menu["BTTTriggerClass"]),
                         (681, "BTTTriggerTypeOtherTriggers"))
        self.assertNotIn("BTTTouchBarButtonName", menu)
        config = menu["BTTTriggerConfig"]
        self.assertEqual(config["BTTTouchBarOnlyShowIcon"], 0)  # text must show beside the icon
        self.assertEqual(config["BTTTouchBarScriptUpdateInterval"], 3)
        self.assertEqual(config["BTTTouchBarAppleScriptStringRunOnInit"], 1)
        self.assertEqual(config["BTTTouchBarShellScriptString"],
                         "/usr/bin/python3 -I -B /Users/jacobr/dev/scripts/macbook/scripts/cpu_percent.py")
        self.assertIsNone(plan["summary"]["menu_bar"]["retires_legacy_type"])
        self.assertEqual(plan["legacy"], {"uuid": panel.LEGACY_MENU_ID, "type": 674, "present": False})
        self.assertNotEqual(panel.MENU_ID, panel.LEGACY_MENU_ID)
        self.assertEqual(menu["BTTTriggerConfig"]["BTTTouchBarItemSFSymbolDefaultIcon"], panel.MENU_ICON)
        self.assertEqual(keyboard["BTTShortcutKeyCode"], 35)
        self.assertEqual(keyboard["BTTShortcutModifierKeys"], 1835008)
        self.assertEqual(keyboard["BTTShortcutAdvancedModifierKeys"], "1835049")
        for node in plan["desired"]:
            self.assertEqual(node["BTTOpenURL"], "http://127.0.0.1:8769/")
            self.assertEqual(node["BTTPredefinedActionType"], 59)
            self.assertEqual(node["BTTAppBundleIdentifier"], "BT.G")
            self.assertEqual(node["BTTTriggerBelongsToPreset"], "Master")
            self.assertNotIn("BTTTriggerParentUUID", node)
            self.assertNotIn("BTTShellTaskActionScript", node)

    def test_disabled_saved_and_unsaved_runtime_shortcuts_reserve_combinations(self):
        rows = fixture_rows()
        rows["other"].update(ZMODIFIERKEYS=panel.MODIFIERS, ZISENABLED=0)
        runtime = [{"BTTUUID": "unsaved", "BTTTriggerType": 0,
                    "BTTShortcutKeyCode": 46, "BTTShortcutModifierKeys": panel.MODIFIERS}]
        plan = panel.installation_plan(rows, runtime)
        self.assertEqual(plan["summary"]["keyboard"]["shortcut"], "ctrl+opt+cmd+K")
        runtime.append(dict(runtime[0], BTTUUID="last", BTTShortcutKeyCode=40))
        with self.assertRaisesRegex(RuntimeError, "occupied"):
            panel.installation_plan(rows, runtime)

    def test_existing_items_keep_uuid_and_order(self):
        rows, plan = installed_rows()
        rows[panel.KEY_ID]["ZORDER"] = 42
        new = panel.installation_plan(rows, plan["desired"])
        self.assertEqual([n["BTTUUID"] for n in new["desired"]], [panel.MENU_ID, panel.KEY_ID])
        self.assertEqual(new["desired"][1]["BTTOrder"], 42)

    def legacy_state(self):
        """Saved rows and runtime as an install from before the 681 item left them."""
        rows, plan = installed_rows()
        menu = rows.pop(panel.MENU_ID)
        rows.pop("menu-trait")
        rows[panel.LEGACY_MENU_ID] = dict(menu, ZUNIQUEIDENTIFIER=panel.LEGACY_MENU_ID, ZGESTURETYPE=674,
                                         ZTRAIT=None, ZORDER=5)
        legacy = dict(plan["desired"][0], BTTUUID=panel.LEGACY_MENU_ID, BTTTriggerType=674)
        return rows, [legacy, plan["desired"][1]]

    def test_installed_icon_only_item_is_kept_while_the_new_item_is_planned_beside_it(self):
        rows, runtime = self.legacy_state()
        new = panel.installation_plan(rows, runtime)
        self.assertEqual(new["summary"]["menu_bar"]["retires_legacy_type"], 674)
        self.assertEqual(new["legacy"]["present"], True)
        menu = new["desired"][0]
        self.assertEqual((menu["BTTUUID"], menu["BTTTriggerType"], menu["BTTOrder"]),
                         (panel.MENU_ID, 681, 5))  # takes the retiring item's slot
        self.assertEqual([n["BTTUUID"] for n in new["desired"]], [panel.MENU_ID, panel.KEY_ID])
        rows[panel.LEGACY_MENU_ID]["ZGESTURETYPE"] = 773  # any other type under the legacy UUID stays refused
        with self.assertRaisesRegex(RuntimeError, "foreign type"):
            panel.installation_plan(rows, runtime)

    def test_saved_and_runtime_type_must_agree_per_uuid(self):
        rows, runtime = self.legacy_state()
        runtime[0] = dict(runtime[0], BTTTriggerType=681)  # saved 674, live 681
        with self.assertRaisesRegex(RuntimeError, "Runtime Mac Panel type"):
            panel.installation_plan(rows, runtime)
        rows, plan = installed_rows()
        rows[panel.MENU_ID]["ZGESTURETYPE"] = 674  # saved 674 under the new UUID: not an allowed type
        with self.assertRaisesRegex(RuntimeError, "foreign type"):
            panel.installation_plan(rows, plan["desired"])

    def test_both_items_present_plans_only_the_retirement(self):
        rows, plan = installed_rows()
        legacy_rows, runtime = self.legacy_state()
        rows[panel.LEGACY_MENU_ID] = legacy_rows[panel.LEGACY_MENU_ID]
        both = panel.installation_plan(rows, plan["desired"] + [runtime[0]])
        self.assertEqual(both["legacy"]["present"], True)
        self.assertEqual(both["desired"][0]["BTTOrder"], rows[panel.MENU_ID]["ZORDER"])

    def test_foreign_markers_and_duplicate_candidates_refused(self):
        rows, plan = installed_rows()
        cases = [dict(plan["desired"][0]), dict(plan["desired"][0], BTTUUID="foreign")]
        for item in cases:
            with self.subTest(item=item), self.assertRaisesRegex(RuntimeError, "Ambiguous"):
                panel.installation_plan(rows, plan["desired"] + [item])
        rows["foreign"] = dict(rows[panel.MENU_ID], ZUNIQUEIDENTIFIER="foreign")
        with self.assertRaisesRegex(RuntimeError, "Ambiguous"):
            panel.installation_plan(rows, plan["desired"])
        del rows[panel.MENU_ID]
        with self.assertRaisesRegex(RuntimeError, "foreign UUID"):
            panel.installation_plan(rows, [dict(plan["desired"][0], BTTUUID="foreign"), plan["desired"][1]])

    def test_foreign_scope_children_conditions_and_pending_save_refused(self):
        for changes in ({"ZPARENT": 2}, {"app_scope": ["some.app"]},
                        {"ZBELONGSTOPRESET2": 55}, {"ZGESTURETYPE": 773}, {"ZCONDITIONS": b"condition"}):
            rows, plan = installed_rows()
            rows[panel.MENU_ID].update(changes)
            with self.subTest(changes=changes), self.assertRaises(RuntimeError):
                panel.installation_plan(rows, plan["desired"])
        for changes in ({"BTTTriggerType": 773}, {"BTTAppBundleIdentifier": "some.app"},
                        {"BTTTriggerBelongsToPreset": "Other"}):
            rows, plan = installed_rows()
            plan["desired"][0].update(changes)
            with self.subTest(runtime=changes), self.assertRaisesRegex(RuntimeError, "Runtime Mac Panel"):
                panel.installation_plan(rows, plan["desired"])
        rows, plan = installed_rows()
        with self.assertRaisesRegex(RuntimeError, "differs"):
            panel.installation_plan(rows, [])
        rows["other"]["ZPARENT"] = rows[panel.MENU_ID]["Z_PK"]
        with self.assertRaisesRegex(RuntimeError, "children"):
            panel.installation_plan(rows, plan["desired"])

    def test_wrong_or_duplicate_master_refused(self):
        for change in ({"ZACTIVATED": 0}, {"ZNAME3": "Not Master"}):
            rows = fixture_rows()
            rows["preset"].update(change)
            with self.assertRaises(RuntimeError):
                panel.installation_plan(rows, [])
        rows = fixture_rows()
        rows["duplicate"] = dict(rows["preset"], Z_PK=88)
        with self.assertRaisesRegex(RuntimeError, "Ambiguous Master"):
            panel.installation_plan(rows, [])


class InstallerTests(unittest.TestCase):
    def test_default_cli_only_inspects_and_never_creates_backup(self):
        with patch.object(panel, "current_database", return_value=Path("fixture")), \
                patch.object(panel, "saved_state", return_value=fixture_rows()), \
                patch.object(panel, "worker", return_value=[]) as worker, \
                patch.object(panel, "backup_configuration") as backup, \
                patch("sys.argv", ["install_mac_panel_menu.py"]), redirect_stdout(io.StringIO()) as output:
            panel.main()
        self.assertFalse(json.loads(output.getvalue())["changes_applied"])
        worker.assert_called_once_with("inspect")
        backup.assert_not_called()

    def run_apply(self, rows, runtime, worker, verify=None, backup=None):
        with patch.object(panel, "current_database", return_value=Path("fixture")), \
                patch.object(panel, "saved_state", return_value=rows), \
                patch.object(panel, "worker", side_effect=worker), \
                patch.object(panel, "backup_configuration", side_effect=backup or (
                    lambda *a, **k: Path("/private/backup/backup.json"))), \
                patch.object(panel, "before_install", return_value={"fixture": True}), \
                patch.object(panel, "after_install", return_value={"status": "taken"}) as after, \
                patch.object(panel, "verify_saved", side_effect=verify or (lambda *a, **k: True)) as check:
            return panel.install(apply=True), after, check

    def test_apply_backs_up_before_only_mutating_worker_and_verifies_disk(self):
        events = []
        def worker(mode, path=None):
            events.append(mode)
            return [] if mode == "inspect" else {"writes": 2, "touched": [panel.MENU_ID, panel.KEY_ID]}
        def backup(*args, **kwargs):
            events.append("backup")
            self.assertEqual(kwargs["prefix"], "btt-mac-panel-backup-")
            return Path("/private/backup/backup.json")
        result, after, check = self.run_apply(fixture_rows(), [], worker, backup=backup)
        self.assertEqual(events, ["inspect", "backup", "apply"])  # nothing to retire on a fresh install
        self.assertTrue(result["saved_verified"])
        check.assert_called_once()
        after.assert_called_once_with({"fixture": True}, [panel.MENU_ID, panel.KEY_ID],
                                      "install_mac_panel_menu", wrote=True)

    def test_no_checkpoint_flag_reaches_the_guard_baseline(self):
        with patch.object(panel, "current_database", return_value=Path("fixture")), \
                patch.object(panel, "saved_state", return_value=fixture_rows()), \
                patch.object(panel, "worker", return_value={"writes": 0, "touched": []}), \
                patch.object(panel, "backup_configuration", return_value=Path("/private/b/backup.json")), \
                patch.object(panel, "verify_saved", return_value=True), \
                patch.object(panel, "before_install", return_value={}) as before, \
                patch.object(panel, "after_install", return_value={}):
            panel.worker.side_effect = lambda mode, path=None: [] if mode == "inspect" else {"writes": 0, "touched": []}
            panel.install(apply=True, no_checkpoint=True)
        before.assert_called_once_with(no_checkpoint=True)

    def test_legacy_item_is_retired_only_after_the_new_one_is_saved_and_verified(self):
        rows, runtime = PlanningTests.legacy_state(self)
        events = []
        def worker(mode, path=None):
            events.append(mode)
            if mode == "inspect":
                return runtime
            return {"writes": 2, "touched": [panel.MENU_ID, panel.KEY_ID]} if mode == "apply" else \
                {"writes": 1, "touched": [panel.LEGACY_MENU_ID]}
        def verify(database, plan, before, retired=False):
            events.append("saved-retired" if retired else "saved-new")
            return True
        result, after, _ = self.run_apply(rows, runtime, worker, verify)
        self.assertEqual(events, ["inspect", "apply", "saved-new", "inspect", "retire", "saved-retired"])
        self.assertEqual(result["touched_uuids"], [panel.MENU_ID, panel.KEY_ID, panel.LEGACY_MENU_ID])
        after.assert_called_once_with({"fixture": True}, [panel.MENU_ID, panel.KEY_ID, panel.LEGACY_MENU_ID],
                                      "install_mac_panel_menu", wrote=True)

    def test_failed_add_never_reaches_the_retirement_or_the_checkpoint(self):
        rows, runtime = PlanningTests.legacy_state(self)
        events = []
        def worker(mode, path=None):
            events.append(mode)
            if mode == "apply":
                raise RuntimeError("add failed")
            return runtime
        with self.assertRaisesRegex(RuntimeError, "add failed"):
            self.run_apply(rows, runtime, worker)
        self.assertEqual(events, ["inspect", "apply"])

    def test_failed_delete_leaves_both_items_and_a_rerun_finishes_it(self):
        rows, runtime = PlanningTests.legacy_state(self)
        installed, plan = installed_rows()
        both_rows = dict(installed, **{panel.LEGACY_MENU_ID: rows[panel.LEGACY_MENU_ID]})
        both_runtime = plan["desired"] + [runtime[0]]
        def failing(mode, path=None):
            if mode == "retire":
                raise RuntimeError("delete failed")
            return runtime if mode == "inspect" else {"writes": 2, "touched": [panel.MENU_ID, panel.KEY_ID]}
        with self.assertRaisesRegex(RuntimeError, "delete failed"):
            self.run_apply(rows, runtime, failing)
        events = []
        def rerun(mode, path=None):
            events.append(mode)
            if mode == "inspect":
                return both_runtime
            return {"writes": 0, "touched": []} if mode == "apply" else \
                {"writes": 1, "touched": [panel.LEGACY_MENU_ID]}
        result, after, _ = self.run_apply(both_rows, both_runtime, rerun)
        self.assertEqual(events, ["inspect", "apply", "inspect", "retire"])  # no re-add, only the delete
        self.assertEqual(result["touched_uuids"], [panel.LEGACY_MENU_ID])

    def test_backup_failure_or_saved_race_never_invokes_mutation(self):
        for race in (False, True):
            rows = fixture_rows()
            changed = copy.deepcopy(rows)
            changed["other"]["ZACTION"] = 99
            with patch.object(panel, "current_database", return_value=Path("fixture")), \
                    patch.object(panel, "saved_state", side_effect=[rows, changed]), \
                    patch.object(panel, "worker", return_value=[]) as worker, \
                    patch.object(panel, "backup_configuration", return_value=Path("/private/backup.json"),
                                 side_effect=None if race else RuntimeError("backup failed")):
                with self.assertRaises(RuntimeError):
                    panel.install(apply=True)
            worker.assert_called_once_with("inspect")

    def test_saved_verification_checks_scope_action_key_title_and_unrelated_rows(self):
        saved, plan = installed_rows()
        with patch.object(panel, "saved_state", return_value=saved):
            self.assertTrue(panel.verify_saved("fixture", plan, fixture_rows()))
            for uid, field, value in [(panel.KEY_ID, "ZKEYCODE", 46),
                                      (panel.MENU_ID, "ZACTION", 206),
                                      (panel.MENU_ID, "ZDESC", "Wrong title"),
                                      (panel.KEY_ID, "app_scope", ["other.app"])]:
                old = saved[uid][field]
                saved[uid][field] = value
                self.assertFalse(panel.verify_saved("fixture", plan, fixture_rows()))
                saved[uid][field] = old
            saved["other"]["ZACTION"] = 88
            with self.assertRaisesRegex(RuntimeError, "outside Mac Panel"):
                panel.verify_saved("fixture", plan, fixture_rows())

    def test_saved_verification_requires_the_script_settings(self):
        saved, plan = installed_rows()
        wanted = plan["desired"][0]["BTTTriggerConfig"]
        with patch.object(panel, "saved_state", return_value=saved):
            before = fixture_rows()
            for key, value in [("BTTTouchBarShellScriptString", "/bin/echo 1%"),
                               ("BTTTouchBarScriptUpdateInterval", 60),
                               ("BTTTouchBarAppleScriptStringRunOnInit", 0),
                               ("BTTTouchBarOnlyShowIcon", 1)]:
                with self.subTest(key=key):
                    saved["menu-trait"]["ZBEZIERPATH"] = json.dumps(dict(wanted, **{key: value})).encode()
                    self.assertFalse(panel.verify_saved("fixture", plan, before))
                    missing = {k: v for k, v in wanted.items() if k != key}
                    saved["menu-trait"]["ZBEZIERPATH"] = json.dumps(missing).encode()
                    self.assertFalse(panel.verify_saved("fixture", plan, before))
            for broken in (None, b"not json", b"[1]"):
                saved["menu-trait"]["ZBEZIERPATH"] = broken
                self.assertFalse(panel.verify_saved("fixture", plan, before))
            saved["menu-trait"]["ZBEZIERPATH"] = json.dumps(dict(wanted, BTTTouchBarOnlyShowIcon=False)).encode()
            self.assertTrue(panel.verify_saved("fixture", plan, before))  # False == 0, as BTT stores flags

    def test_saved_verification_tracks_the_legacy_item(self):
        saved, plan = installed_rows()
        legacy_rows, _ = PlanningTests.legacy_state(self)
        before = fixture_rows()
        before[panel.LEGACY_MENU_ID] = legacy_rows[panel.LEGACY_MENU_ID]
        saved[panel.LEGACY_MENU_ID] = dict(before[panel.LEGACY_MENU_ID])
        with patch.object(panel, "saved_state", return_value=saved):
            self.assertTrue(panel.verify_saved("fixture", plan, before))
            self.assertFalse(panel.verify_saved("fixture", plan, before, retired=True))  # not deleted yet
            saved[panel.LEGACY_MENU_ID]["ZORDER"] = 99
            with self.assertRaisesRegex(RuntimeError, "legacy"):
                panel.verify_saved("fixture", plan, before)
            del saved[panel.LEGACY_MENU_ID]
            self.assertTrue(panel.verify_saved("fixture", plan, before, retired=True))

    def test_shared_display_trait_is_refused_before_writes(self):
        saved, plan = installed_rows()
        saved["other"]["ZTRAIT"] = saved[panel.MENU_ID]["ZTRAIT"]
        with self.assertRaisesRegex(RuntimeError, "shares a display trait"):
            panel.installation_plan(saved, plan["desired"])

    def test_saved_reader_is_read_only_and_rejects_duplicate_uuid(self):
        rows = fixture_rows()
        with tempfile.TemporaryDirectory() as folder:
            database = Path(folder) / "btt.sqlite"
            with sqlite3.connect(database) as connection:
                columns = [key for key in rows["preset"] if key != "app_scope"]
                connection.execute("CREATE TABLE ZBTTBASEENTITY (" + ",".join(columns) + ")")
                connection.execute("CREATE TABLE Z_2APPS_GESTURES (Z_2GESTURES,Z_9APPS_GESTURES)")
                for row in rows.values():
                    connection.execute("INSERT INTO ZBTTBASEENTITY VALUES (" +
                                       ",".join("?" for _ in columns) + ")", [row[key] for key in columns])
                connection.execute("INSERT INTO Z_2APPS_GESTURES VALUES (3,4)")
            before = database.read_bytes()
            result = panel.saved_state(database)
            self.assertEqual(result["other"]["app_scope"], ["BT.G"])
            self.assertEqual(database.read_bytes(), before)
            with sqlite3.connect(database) as connection:
                connection.execute("INSERT INTO ZBTTBASEENTITY SELECT * FROM ZBTTBASEENTITY WHERE Z_PK=4")
            with self.assertRaisesRegex(RuntimeError, "Duplicate saved UUID"):
                panel.saved_state(database)


if __name__ == "__main__":
    unittest.main()
