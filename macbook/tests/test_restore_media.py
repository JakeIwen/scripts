"""Deletion recovery must not replace existing menus or execute their actions."""
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

DIRECTORY = Path(__file__).resolve().parents[1]/'bettertouchtool'
sys.path.insert(0, str(DIRECTORY))
spec = importlib.util.spec_from_file_location('restore_media', DIRECTORY/'restore_media.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
sys.path.pop(0)


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name)/'configuration.sqlite'
        with sqlite3.connect(self.path) as c:
            c.executescript('''CREATE TABLE ZBTTBASEENTITY (
                Z_PK INTEGER,ZUNIQUEIDENTIFIER TEXT,ZPARENT INTEGER,ZBELONGSTOPRESET2 INTEGER,
                ZGESTURETYPE INTEGER,ZACTION INTEGER,ZORDER INTEGER,ZICONDATA3 BLOB,
                ZACTIONDATA TEXT,ZLAUNCHPATH TEXT,ZADDITIONALACTIONSTRING TEXT,
                ZSHORTCUT TEXT,ZISENABLED INTEGER,ZENABLEDNEW INTEGER,ZNAME3 TEXT,ZACTIVATED INTEGER);
                INSERT INTO ZBTTBASEENTITY (Z_PK,ZUNIQUEIDENTIFIER,ZNAME3,ZACTIVATED)
                VALUES (42,'preset','Master',2);''')
            c.execute('''INSERT INTO ZBTTBASEENTITY (Z_PK,ZUNIQUEIDENTIFIER,ZBELONGSTOPRESET2,ZGESTURETYPE,
                ZACTION,ZORDER,ZICONDATA3,ZISENABLED,ZENABLEDNEW) VALUES (1,?,42,767,366,0,?,1,1)''',
                (module.MEDIA, b'\x01{"BTTMenuElementIdentifier":"Media"}'))
            c.execute('''INSERT INTO ZBTTBASEENTITY (Z_PK,ZUNIQUEIDENTIFIER,ZPARENT,ZBELONGSTOPRESET2,
                ZGESTURETYPE,ZACTION,ZORDER,ZISENABLED,ZENABLEDNEW) VALUES (2,'button',1,42,773,366,0,1,1)''')
            c.execute('''INSERT INTO ZBTTBASEENTITY (Z_PK,ZUNIQUEIDENTIFIER,ZPARENT,ZBELONGSTOPRESET2,
                ZGESTURETYPE,ZACTION,ZORDER,ZSHORTCUT,ZISENABLED,ZENABLEDNEW)
                VALUES (3,'action',2,42,-1,-1,0,'55,8',1,1)''')
        self.menu = {'BTTUUID': module.MEDIA, 'BTTTriggerType': 767, 'BTTMenuItems': [
            {'BTTUUID': 'button', 'BTTTriggerType': 773, 'BTTMenuItemActions': [
                {'BTTUUID': 'action', 'BTTIsPureAction': True, 'BTTPredefinedActionType': 264,
                 'BTTShortcutToSend': '55,8'}]}]}
        self.source = self.path.with_name('backup.json')
        self.write()

    def write(self, **extra):
        self.source.write_text(json.dumps({'media': self.menu, **extra}), encoding='utf-8')

    def test_parents_and_pure_actions_are_normalized(self):
        plan = module.build_plan(self.source)
        self.assertEqual(len(plan['entities']), 3)
        action = plan['entities'][-1]
        self.assertEqual(action['parent'], 'button')
        self.assertEqual(action['node']['BTTTriggerType'], -1)
        self.assertNotIn('BTTIsPureAction', action['node'])
        self.assertTrue(all(not any(k in e['node'] for k in module.COLLECTIONS) for e in plan['entities']))
        self.assertEqual(plan['entities'][0]['node']['BTTAppBundleIdentifier'], 'BT.G')

    def test_mismatched_shortcut_is_rejected(self):
        self.menu['BTTMenuItems'][0]['BTTMenuItemActions'][0]['BTTShortcutToSend'] = '55,9'
        self.write()
        with self.assertRaisesRegex(RuntimeError, 'shortcut'):
            module.build_plan(self.source)

    def test_missing_export_descendants_are_rejected(self):
        self.menu['BTTMenuItems'][0]['BTTMenuItemActions'] = []
        self.write()
        with self.assertRaisesRegex(RuntimeError, 'membership'):
            module.build_plan(self.source)

    def test_duplicate_ids_are_rejected(self):
        self.menu['BTTMenuItems'].append(self.menu['BTTMenuItems'][0])
        self.write()
        with self.assertRaisesRegex(RuntimeError, 'membership'):
            module.build_plan(self.source)

    def test_latest_appearance_patch_is_included_without_mutating_archive(self):
        self.write(configChanges=[{'uuid': 'button', 'patch': {'BTTMenuItemIconColor1': '0, 0, 0, 255'}}])
        before = self.source.read_bytes()
        plan = module.build_plan(self.source)
        self.assertEqual(plan['entities'][1]['node']['BTTMenuConfig']['BTTMenuItemIconColor1'], '0, 0, 0, 255')
        self.assertEqual(self.source.read_bytes(), before)

    def test_runtime_comparison_uses_export_projection_not_database_editor_state(self):
        self.menu['BTTMenuConfig'] = {'BTTMenuElementIdentifier': 'Media'}
        self.write()
        with sqlite3.connect(self.path) as c:
            c.execute('UPDATE ZBTTBASEENTITY SET ZICONDATA3=? WHERE Z_PK=1', (b'\x01'+json.dumps({
                'BTTMenuElementIdentifier': 'Media', 'BTTMenuCategorySize': 1,
                'BTTMenuItemSelectedTab': 0, 'BTTLastChangeUUID': 'old-change'}).encode(),))
        plan = module.build_plan(self.source)
        self.assertEqual(plan['runtime_root_config'], {'BTTMenuElementIdentifier': 'Media'})
        self.assertEqual(plan['entities'][0]['node']['BTTMenuConfig']['BTTMenuCategorySize'], 1)

    def test_saved_verification_ignores_change_stamp_but_not_layout(self):
        plan = module.build_plan(self.source)
        plan['entities'][0]['node']['BTTMenuConfig']['BTTLastChangeUUID'] = 'old-change'
        self.assertEqual(module.saved_errors(self.path, plan, 42, enabled=True), [])
        plan['entities'][0]['node']['BTTMenuConfig']['BTTMenuFrameWidth'] = 1920
        errors = module.saved_errors(self.path, plan, 42, enabled=True)
        self.assertTrue(any('BTTMenuFrameWidth' in error for error in errors))

    def test_nonappearance_patch_is_rejected(self):
        self.write(configChanges=[{'uuid': module.MEDIA, 'patch': {'BTTMenuModifierKeys': 0}}])
        with self.assertRaisesRegex(RuntimeError, 'non-appearance'):
            module.build_plan(self.source)

    def test_existing_root_is_never_overwritten(self):
        with self.assertRaisesRegex(RuntimeError, 'already exists'):
            module.preflight(self.path, module.build_plan(self.source))

    def test_resume_accepts_exact_disabled_partial_tree(self):
        plan = module.build_plan(self.source)
        with sqlite3.connect(self.path) as c:
            c.execute('UPDATE ZBTTBASEENTITY SET ZISENABLED=0,ZENABLEDNEW=0 WHERE Z_PK=1')
            c.execute("DELETE FROM ZBTTBASEENTITY WHERE ZUNIQUEIDENTIFIER='action'")
        before, preset = module.preflight(self.path, plan, resume=True)
        self.assertEqual(preset, 42)
        self.assertIn(module.MEDIA, before)
        self.assertNotIn('action', before)

    def test_resume_rejects_enabled_or_modified_root(self):
        plan = module.build_plan(self.source)
        with self.assertRaisesRegex(RuntimeError, 'disabled root'):
            module.preflight(self.path, plan, resume=True)
        with sqlite3.connect(self.path) as c:
            c.execute('UPDATE ZBTTBASEENTITY SET ZISENABLED=0,ZORDER=9 WHERE Z_PK=1')
        with self.assertRaisesRegex(RuntimeError, 'differ'):
            module.preflight(self.path, plan, resume=True)

    def test_resume_rejects_unexpected_children(self):
        plan = module.build_plan(self.source)
        with sqlite3.connect(self.path) as c:
            c.execute('UPDATE ZBTTBASEENTITY SET ZISENABLED=0 WHERE Z_PK=1')
            c.execute("UPDATE ZBTTBASEENTITY SET ZUNIQUEIDENTIFIER='other' WHERE Z_PK=3")
        with self.assertRaisesRegex(RuntimeError, 'unexpected children'):
            module.preflight(self.path, plan, resume=True)

    def test_resume_requires_our_matching_precreation_backup(self):
        plan = module.build_plan(self.source)
        with patch.object(module, 'ROOT', self.source.parent):
            with self.assertRaisesRegex(RuntimeError, 'pre-creation'):
                module.resume_origin(plan)

    def test_save_poll_can_pass_after_old_eight_second_deadline(self):
        with patch.object(module, 'saved_errors', side_effect=[['not saved'], []]), \
             patch.object(module.time, 'monotonic', side_effect=[0, 9, 9, 9]), \
             patch.object(module.time, 'sleep') as sleep, patch('builtins.print'):
            module.wait_saved(self.path, {}, 42)
        sleep.assert_called_once_with(.25)

    def test_save_poll_does_not_wait_when_already_saved(self):
        with patch.object(module, 'saved_errors', return_value=[]), patch.object(module.time, 'sleep') as sleep:
            module.wait_saved(self.path, {}, 42)
        sleep.assert_not_called()

    def test_unrelated_check_counts_only_new_records_on_resume(self):
        plan = module.build_plan(self.source)
        before = module.restore_records(self.path)
        before.pop('action')
        module.verify_unrelated(self.path, before, plan)
        before['button']['ZORDER'] = 99
        with self.assertRaisesRegex(RuntimeError, 'Existing saved records changed'):
            module.verify_unrelated(self.path, before, plan)

    def test_missing_records_and_enabled_state_are_checked(self):
        plan = module.build_plan(self.source)
        self.assertIn(module.MEDIA+': enabled state differs', module.saved_errors(self.path, plan, 42))
        self.assertEqual(module.saved_errors(self.path, plan, 42, enabled=True), [])
        with sqlite3.connect(self.path) as c:
            c.execute("DELETE FROM ZBTTBASEENTITY WHERE ZUNIQUEIDENTIFIER='action'")
        self.assertIn('action: not saved', module.saved_errors(self.path, plan, 42, enabled=True))

    def test_duplicate_named_menu_is_rejected(self):
        plan = module.build_plan(self.source)
        with sqlite3.connect(self.path) as c:
            c.execute("DELETE FROM ZBTTBASEENTITY WHERE Z_PK IN (2,3)")
            c.execute("UPDATE ZBTTBASEENTITY SET ZUNIQUEIDENTIFIER='collision' WHERE Z_PK=1")
        with self.assertRaisesRegex(RuntimeError, 'collision'):
            module.preflight(self.path, plan)


if __name__ == '__main__':
    unittest.main()
