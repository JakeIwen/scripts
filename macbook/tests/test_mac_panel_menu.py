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
              "ZTRAIT", "ZDESC", "ZONLYICON",
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
            rows["menu-trait"] = dict(rows["global"], Z_PK=100, ZUNIQUEIDENTIFIER="menu-trait", ZONLYICON=0)
        if node["BTTUUID"] == panel.KEY_ID:
            rows[panel.KEY_ID].update(ZKEYCODE=35, ZMODIFIERKEYS=panel.MODIFIERS,
                                      ZTRIGGERONDOWN=1, ZGESTURECONFIG=panel.ADVANCED_MODIFIERS)
    return rows, plan


class PlanningTests(unittest.TestCase):
    def test_definitions_and_first_free_shortcut(self):
        plan = panel.installation_plan(fixture_rows(), [])
        menu, keyboard = plan["desired"]
        self.assertEqual((menu["BTTTriggerType"], menu["BTTTriggerClass"]),
                         (674, "BTTTriggerTypeOtherTriggers"))
        self.assertNotIn("BTTTouchBarButtonName", menu)
        self.assertEqual(menu["BTTTriggerConfig"]["BTTTouchBarOnlyShowIcon"], 1)
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

    def test_apply_backs_up_before_only_mutating_worker_and_verifies_disk(self):
        events = []
        rows = fixture_rows()
        def worker(mode, path=None):
            events.append(mode)
            return [] if mode == "inspect" else {"writes": 2}
        def backup(*args, **kwargs):
            events.append("backup")
            self.assertEqual(kwargs["prefix"], "btt-mac-panel-backup-")
            return Path("/private/backup/backup.json")
        with patch.object(panel, "current_database", return_value=Path("fixture")), \
                patch.object(panel, "saved_state", return_value=rows), \
                patch.object(panel, "worker", side_effect=worker), \
                patch.object(panel, "backup_configuration", side_effect=backup), \
                patch.object(panel, "verify_saved", return_value=True) as verify:
            result = panel.install(apply=True)
        self.assertEqual(events, ["inspect", "backup", "apply"])
        self.assertTrue(result["saved_verified"])
        verify.assert_called_once()

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
