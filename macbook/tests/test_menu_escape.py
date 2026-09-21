"""Escape stays disabled until its visibility gate and passthrough are saved."""
import importlib.util
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import unittest
from unittest.mock import patch

from test_repair_rps import RPSPersistenceTests, DIRECTORY

sys.path.insert(0,str(DIRECTORY))
spec=importlib.util.spec_from_file_location('install_menu_escape',DIRECTORY/'install_menu_escape.py')
escape=importlib.util.module_from_spec(spec);spec.loader.exec_module(escape)
sys.path.pop(0)


class EscapeTests(RPSPersistenceTests):
    def setUp(self):
        super().setUp()
        with sqlite3.connect(self.path) as c:
            for name,kind in [('ZKEYCODE','INTEGER'),('ZMODIFIERKEYS','INTEGER'),('ZTRIGGERONDOWN','INTEGER'),
                              ('ZACTIONCATEGORY','INTEGER'),('ZCONDITIONS','BLOB')]:
                c.execute('ALTER TABLE ZBTTBASEENTITY ADD COLUMN '+name+' '+kind)
        for index,(uid,name) in enumerate(escape.MENUS):
            self.insert(Z_PK=20+index,ZUNIQUEIDENTIFIER=uid,ZGESTURETYPE=767,ZBELONGSTOPRESET2=100,
                        ZICONDATA3=b'\x01'+json.dumps({'BTTMenuElementIdentifier':name}).encode())

    def add_escape(self, enabled=0, condition=b'fixture-predicate', passthrough=True):
        plan=escape.definition()
        self.insert(Z_PK=30,ZUNIQUEIDENTIFIER=escape.SHORTCUT,ZGESTURETYPE=0,ZBELONGSTOPRESET2=100,
                    ZACTION=281,ZKEYCODE=53,ZMODIFIERKEYS=0,ZTRIGGERONDOWN=1,ZACTIONCATEGORY=0,
                    ZCONDITIONS=condition,ZISENABLED=enabled,ZENABLEDNEW=enabled,
                    ZACTIONDATA=json.dumps(plan['definition']['BTTAdditionalActionData']).encode(),
                    ZICONDATA3=b'\x01'+json.dumps({escape.PASSTHROUGH:passthrough}).encode())

    def test_preflight_only_known_dropdowns_and_no_main_bar(self):
        preset,plan,exists=escape.preflight(self.path)
        self.assertEqual(preset,100);self.assertFalse(exists)
        self.assertNotIn(escape.MEDIA,plan['names'])
        self.assertEqual(plan['definition']['BTTEnabled'],0)
        self.assertTrue(plan['definition'][escape.PASSTHROUGH])
        self.assertEqual(plan['definition']['BTTShortcutModifierKeys'],0)

    def test_plain_escape_conflict_is_rejected(self):
        self.insert(Z_PK=30,ZUNIQUEIDENTIFIER='other-escape',ZGESTURETYPE=0,ZBELONGSTOPRESET2=100,
                    ZKEYCODE=53,ZMODIFIERKEYS=0)
        with self.assertRaisesRegex(RuntimeError,'existing plain-Escape'):escape.preflight(self.path)

    def test_disk_verification_distinguishes_disabled_and_enabled(self):
        self.add_escape();plan=escape.definition()
        self.assertTrue(escape.disk_check(self.path,plan,100))
        self.assertFalse(escape.disk_check(self.path,plan,100,require_enabled=True))
        with sqlite3.connect(self.path) as c:c.execute('UPDATE ZBTTBASEENTITY SET ZISENABLED=1,ZENABLEDNEW=1 WHERE Z_PK=30')
        self.assertTrue(escape.disk_check(self.path,plan,100,require_enabled=True))

    def test_missing_condition_or_passthrough_fails_closed(self):
        self.add_escape(condition=None)
        with self.assertRaisesRegex(RuntimeError,'condition did not persist'):escape.disk_check(self.path,escape.definition(),100)
        with sqlite3.connect(self.path) as c:
            c.execute('UPDATE ZBTTBASEENTITY SET ZCONDITIONS=?,ZICONDATA3=? WHERE Z_PK=30',(b'predicate',b'\x01{}'))
        with self.assertRaisesRegex(RuntimeError,'passthrough did not persist'):escape.disk_check(self.path,escape.definition(),100)

    @unittest.skipUnless(sys.platform=='darwin','requires macOS Foundation, not BTT')
    def test_real_foundation_condition_truth_table(self):
        plan_path=self.root/'plan.json';plan_path.write_text(json.dumps(escape.definition()))
        result=subprocess.run(['/usr/bin/osascript','-l','JavaScript',str(escape.WORKER),
                               'condition-test',str(plan_path)],capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('visible_floating_menu_identifiers',result.stdout)

    def test_installer_never_enables_before_verification(self):
        calls=[];plan=escape.definition();path=self.root/'plan.json'
        with patch.object(escape,'current_database',return_value=self.path), \
             patch.object(escape,'preflight',return_value=(100,plan,False)), \
             patch.object(escape,'backup_configuration',return_value=path), \
             patch.object(escape,'worker',side_effect=lambda mode,p:calls.append(mode)), \
             patch.object(escape,'wait_saved',side_effect=RuntimeError('not persisted')):
            with self.assertRaisesRegex(RuntimeError,'not persisted'):escape.install()
        self.assertEqual(calls,['condition-test','create','disable'])

    def test_runtime_action_only_hides_three_menus(self):
        script=escape.definition()['definition']['BTTAdditionalActionData']['BTTAppleScriptString']
        harness='''const vm=require('node:vm');const calls=[];
const context=vm.createContext({trigger_action:async ({json})=>{calls.push(JSON.parse(json));}});
vm.runInContext(JSON.parse(process.argv[1]),context);
context.dismissMediaDropdowns().then(()=>process.stdout.write(JSON.stringify(calls)));
'''
        result=subprocess.run(['node','-e',harness,json.dumps(script)],capture_output=True,text=True,check=True)
        actions=json.loads(result.stdout)
        self.assertEqual([a['BTTAdditionalActionData']['BTTMenuActionMenuID'] for a in actions],
                         [uid for uid,_ in escape.MENUS])
        for action in actions:
            self.assertEqual(action['BTTPredefinedActionType'],387)
            self.assertEqual(action['BTTAdditionalActionData']['BTTMenuActionTriggerHoveredOnHide'],0)


if __name__=='__main__':unittest.main()
