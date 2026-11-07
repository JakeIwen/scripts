"""Escape stays disabled until its visibility gate and passthrough are saved."""
import importlib.util
import json
from pathlib import Path
import plistlib
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import test_repair_rps

DIRECTORY=test_repair_rps.DIRECTORY
sys.path.insert(0,str(DIRECTORY))
spec=importlib.util.spec_from_file_location('install_menu_escape',DIRECTORY/'install_menu_escape.py')
escape=importlib.util.module_from_spec(spec);spec.loader.exec_module(escape)
sys.path.pop(0)


CURRENT=object()  # add_escape default: a visibility condition matching the plan


def condition_archive(names):
    """A predicate archive shaped like BTT's saved visibility condition (strings only, in plan order)."""
    objects=['$null','valueForKey:']
    for name in names:objects+=['visible_floating_menu_identifiers',name]
    return plistlib.dumps({'$objects':objects},fmt=plistlib.FMT_BINARY)


def three_menu_plan():
    """The plan as installed before the Lighting Colour dropdown was added to MENUS."""
    current=escape.MENUS
    escape.MENUS=current[:3]
    try:return escape.definition()
    finally:escape.MENUS=current


class EscapeTests(test_repair_rps.RPSDatabaseFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        with sqlite3.connect(self.path) as c:
            for name,kind in [('ZKEYCODE','INTEGER'),('ZMODIFIERKEYS','INTEGER'),('ZTRIGGERONDOWN','INTEGER'),
                              ('ZACTIONCATEGORY','INTEGER'),('ZCONDITIONS','BLOB')]:
                c.execute('ALTER TABLE ZBTTBASEENTITY ADD COLUMN '+name+' '+kind)
        for index,(uid,name) in enumerate(escape.MENUS):
            self.insert(Z_PK=20+index,ZUNIQUEIDENTIFIER=uid,ZGESTURETYPE=767,ZBELONGSTOPRESET2=100,
                        ZICONDATA3=b'\x01'+json.dumps({'BTTMenuElementIdentifier':name}).encode())

    def add_escape(self, enabled=0, condition=CURRENT, passthrough=True, plan=None, **overrides):
        plan=plan or escape.definition()
        condition=condition_archive(plan['names']) if condition is CURRENT else condition
        row=dict(Z_PK=30,ZUNIQUEIDENTIFIER=escape.SHORTCUT,ZGESTURETYPE=0,ZBELONGSTOPRESET2=100,
                 ZACTION=281,ZKEYCODE=53,ZMODIFIERKEYS=0,ZTRIGGERONDOWN=1,ZACTIONCATEGORY=0,
                 ZCONDITIONS=condition,ZISENABLED=enabled,ZENABLEDNEW=enabled,
                 ZACTIONDATA=json.dumps(plan['definition']['BTTAdditionalActionData']).encode(),
                 ZICONDATA3=b'\x01'+json.dumps({escape.PASSTHROUGH:passthrough}).encode())
        row.update(overrides);self.insert(**row)

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
        with self.assertRaises(RuntimeError):escape.preflight(self.path)

    def test_disk_verification_distinguishes_disabled_and_enabled(self):
        self.add_escape();plan=escape.definition()
        self.assertTrue(escape.disk_check(self.path,plan,100))
        self.assertFalse(escape.disk_check(self.path,plan,100,require_enabled=True))
        with sqlite3.connect(self.path) as c:c.execute('UPDATE ZBTTBASEENTITY SET ZISENABLED=1,ZENABLEDNEW=1 WHERE Z_PK=30')
        self.assertTrue(escape.disk_check(self.path,plan,100,require_enabled=True))

    def test_missing_condition_or_passthrough_fails_closed(self):
        self.add_escape(condition=None)
        with self.assertRaises(RuntimeError):escape.disk_check(self.path,escape.definition(),100)
        with sqlite3.connect(self.path) as c:
            c.execute('UPDATE ZBTTBASEENTITY SET ZCONDITIONS=?,ZICONDATA3=? WHERE Z_PK=30',(b'predicate',b'\x01{}'))
        with self.assertRaises(RuntimeError):escape.disk_check(self.path,escape.definition(),100)

    @unittest.skipUnless(sys.platform=='darwin','requires macOS Foundation, not BTT')
    def test_real_foundation_condition_truth_table(self):
        plan_path=self.root/'plan.json';plan_path.write_text(json.dumps(escape.definition()))
        result=subprocess.run(['/usr/bin/osascript','-l','JavaScript',str(escape.WORKER),
                               'condition-test',str(plan_path)],capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr)

    def btt_rewrites(self, plan, calls, lagging=None):
        """Patched worker: record the mode and make the saved row change the way BTT's would.

        With a `lagging` list, the update is queued there (BTT's API answers before its SQLite save)."""
        def save_update():
            with sqlite3.connect(self.path) as c:
                c.execute('UPDATE ZBTTBASEENTITY SET ZACTIONDATA=?,ZCONDITIONS=?,ZISENABLED=0,ZENABLEDNEW=0 WHERE Z_PK=30',
                          (json.dumps(plan['definition']['BTTAdditionalActionData']).encode(),
                           condition_archive(plan['names'])))
        def run(mode,path):
            calls.append(mode)
            if mode=='update':
                if lagging is None:save_update()
                else:lagging.append(save_update)
            elif mode=='enable':
                with sqlite3.connect(self.path) as c:
                    c.execute('UPDATE ZBTTBASEENTITY SET ZISENABLED=1,ZENABLEDNEW=1 WHERE Z_PK=30')
        return run

    def install_with(self, plan, calls, lagging=None):
        with patch.object(escape,'current_database',return_value=self.path), \
             patch.object(escape,'backup_configuration',return_value=self.root/'plan.json'), \
             patch.object(escape,'worker',side_effect=self.btt_rewrites(plan,calls,lagging)):
            escape.install()

    def test_current_shortcut_is_verified_then_enabled(self):
        plan=escape.definition(order=0);calls=[]
        self.add_escape(plan=plan)
        self.install_with(plan,calls)
        self.assertEqual(calls,['condition-test','verify','enable'])
        self.assertTrue(escape.disk_check(self.path,plan,100,require_enabled=True))

    def test_stale_three_menu_shortcut_is_updated_then_enabled(self):
        old=three_menu_plan();self.assertLess(len(old['names']),len(escape.definition()['names']))
        self.add_escape(plan=old,enabled=1)  # installed and live before the fourth menu existed
        _,plan,exists=escape.preflight(self.path);calls=[]
        self.assertTrue(exists);self.assertTrue(escape.needs_update(self.path,plan,100))
        with self.assertRaises(RuntimeError):escape.disk_check(self.path,plan,100)
        self.install_with(plan,calls)
        self.assertEqual(calls,['condition-test','update','enable'])
        self.assertFalse(escape.needs_update(self.path,plan,100))
        self.assertTrue(escape.disk_check(self.path,plan,100,require_enabled=True))

    def test_stale_condition_alone_is_updated(self):
        plan=escape.definition()
        self.add_escape(condition=condition_archive(three_menu_plan()['names']))
        self.assertTrue(escape.needs_update(self.path,plan,100))

    def test_failed_update_disables_and_reports(self):
        old=three_menu_plan();self.add_escape(plan=old);calls=[]
        _,plan,_=escape.preflight(self.path)
        def run(mode,path):
            calls.append(mode)  # BTT never saves the new definition
        with patch.object(escape,'current_database',return_value=self.path), \
             patch.object(escape,'backup_configuration',return_value=self.root/'plan.json'), \
             patch.object(escape,'worker',side_effect=run), patch.object(escape,'wait_saved',side_effect=RuntimeError('stale')):
            with self.assertRaisesRegex(RuntimeError,'disabled'):escape.install()
        self.assertEqual(calls,['condition-test','update','disable'])

    def test_update_saved_after_the_api_answers_is_waited_for(self):
        self.add_escape(plan=three_menu_plan(),enabled=1);_,plan,_=escape.preflight(self.path)
        calls=[];lagging=[]
        def sleep(seconds):  # the save lands while wait_saved sleeps after its first stale read
            while lagging:lagging.pop()()
        with patch.object(escape.time,'sleep',side_effect=sleep) as slept:
            self.install_with(plan,calls,lagging)
        self.assertEqual(calls,['condition-test','update','enable'])
        self.assertTrue(slept.called)
        self.assertTrue(escape.disk_check(self.path,plan,100,require_enabled=True))

    def test_update_that_is_never_saved_fails_after_the_deadline_and_disables(self):
        self.add_escape(plan=three_menu_plan());_,plan,_=escape.preflight(self.path)
        calls=[];lagging=[]
        clock=iter(range(0,300,3))
        with patch.object(escape.time,'sleep'), patch.object(escape.time,'monotonic',side_effect=lambda:next(clock)):
            with self.assertRaisesRegex(RuntimeError,'did not persist.*\n.*disabled'):
                self.install_with(plan,calls,lagging)
        self.assertEqual(calls,['condition-test','update','disable'])

    def test_wrong_scope_after_the_update_fails_at_once(self):
        self.add_escape(plan=three_menu_plan());_,plan,_=escape.preflight(self.path)
        with sqlite3.connect(self.path) as c:c.execute('UPDATE ZBTTBASEENTITY SET ZACTION=248 WHERE Z_PK=30')
        with patch.object(escape.time,'sleep') as slept:
            with self.assertRaisesRegex(RuntimeError,'wrong scope/action'):escape.wait_saved(self.path,plan,100)
        slept.assert_not_called()

    def test_stale_shortcut_with_wrong_scope_or_action_still_raises_without_the_worker(self):
        for override in ({'ZACTION':248},{'ZBELONGSTOPRESET2':101},{'ZGESTURETYPE':767}):
            with self.subTest(override=override):
                with sqlite3.connect(self.path) as c:c.execute('DELETE FROM ZBTTBASEENTITY WHERE Z_PK=30')
                self.add_escape(plan=three_menu_plan(),**override);calls=[]
                with patch.object(escape,'current_database',return_value=self.path), \
                     patch.object(escape,'backup_configuration') as backup, \
                     patch.object(escape,'worker',side_effect=lambda mode,p:calls.append(mode)):
                    with self.assertRaisesRegex(RuntimeError,'wrong scope/action'):escape.install()
                self.assertEqual(calls,[]);backup.assert_not_called()

    def test_stale_shortcut_with_wrong_key_still_raises_without_the_worker(self):
        self.add_escape(plan=three_menu_plan(),ZKEYCODE=36);calls=[]
        with patch.object(escape,'current_database',return_value=self.path), \
             patch.object(escape,'worker',side_effect=lambda mode,p:calls.append(mode)):
            with self.assertRaises(RuntimeError):escape.install()
        self.assertEqual(calls,[])

    def test_installer_never_enables_before_verification(self):
        calls=[];plan=escape.definition();path=self.root/'plan.json'
        with patch.object(escape,'current_database',return_value=self.path), \
             patch.object(escape,'preflight',return_value=(100,plan,False)), \
             patch.object(escape,'backup_configuration',return_value=path), \
             patch.object(escape,'worker',side_effect=lambda mode,p:calls.append(mode)), \
             patch.object(escape,'wait_saved',side_effect=RuntimeError('not persisted')):
            with self.assertRaises(RuntimeError):escape.install()
        self.assertEqual(calls,['condition-test','create','disable'])

    def test_runtime_action_hides_exactly_the_listed_menus(self):
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


# JXA harness: runs the real worker against a fake BTT whose export renames BTTAppleScriptString to
# BTTScriptString and replaces the condition format with derived keys, as BTT 6.990 does.
FAKE_BTT=r'''ObjC.import('Foundation');
function readText(path){return ObjC.unwrap($.NSString.stringWithContentsOfFileEncodingError(path,$.NSUTF8StringEncoding,null));}
function run(argv){
    const [workerPath,planPath,mode,statePath]=argv;
    const log=[],state=JSON.parse(readText(statePath));
    let stored=state.trigger;
    const exported=()=>{
        const copy=JSON.parse(JSON.stringify(stored)),data=copy.BTTAdditionalActionData;
        data.BTTScriptString=data.BTTAppleScriptString;delete data.BTTAppleScriptString;
        copy.BTTTriggerConditionsFormatReadOnly=copy.BTTTriggerConditionsFormat;delete copy.BTTTriggerConditionsFormat;
        copy.BTTTriggerConditionsData='derived archive';
        return JSON.stringify(copy);
    };
    const btt={
        get_trigger:()=>exported(),
        add_new_trigger:json=>{stored=JSON.parse(json);log.push({call:'add',payload:stored});},
        update_trigger:(uuid,options)=>{
            const payload=JSON.parse(options.json);log.push({call:'update',payload:payload});
            if(state.ignoreUpdates)return;
            stored=Object.assign({},payload);
            if(!stored.BTTTriggerConditionsFormat)stored.BTTTriggerConditionsFormat=stored.BTTTriggerConditionsFormatReadOnly;
            delete stored.BTTTriggerConditionsFormatReadOnly;delete stored.BTTTriggerConditionsData;
        },
    };
    const worker=new Function('Application',readText(workerPath)+';\nreturn run;')(()=>btt);
    let error=null;
    try{worker([mode,planPath]);}catch(e){error=String(e.message||e);}
    return JSON.stringify({error:error,log:log});
}
'''


@unittest.skipUnless(sys.platform=='darwin','requires macOS JXA, not BTT')
class EscapeWorkerTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.plan=escape.definition();(self.root/'plan.json').write_text(json.dumps(self.plan))
        (self.root/'harness.js').write_text(FAKE_BTT)

    def run_worker(self, mode, trigger, ignore_updates=False):
        (self.root/'state.json').write_text(json.dumps({'trigger':trigger,'ignoreUpdates':ignore_updates}))
        result=subprocess.run(['/usr/bin/osascript','-l','JavaScript',str(self.root/'harness.js'),str(escape.WORKER),
                               str(self.root/'plan.json'),mode,str(self.root/'state.json')],
                              capture_output=True,text=True,timeout=60)
        self.assertEqual(result.returncode,0,result.stderr)
        return json.loads(result.stdout)

    def stale(self, **changes):
        trigger=three_menu_plan()['definition'];trigger.update(BTTEnabled=1,BTTEnabled2=1,BTTOrder=7,**changes)
        return trigger

    def test_export_with_renamed_script_key_verifies(self):
        self.assertIsNone(self.run_worker('verify',self.plan['definition'])['error'])

    def test_create_sends_the_saved_script_key_and_accepts_the_renamed_export(self):
        result=self.run_worker('create',self.plan['definition'])
        self.assertIsNone(result['error'])
        self.assertEqual([entry['call'] for entry in result['log']],['add'])

    def test_stale_definition_fails_verification(self):
        self.assertRegex(self.run_worker('verify',self.stale())['error'],'Escape (visibility condition|action mismatch)')

    def test_update_replaces_the_definition_disabled_and_keeps_the_rest(self):
        result=self.run_worker('update',self.stale(BTTCustomKey='kept'))
        self.assertIsNone(result['error']);self.assertEqual([entry['call'] for entry in result['log']],['update'])
        payload=result['log'][0]['payload'];wanted=self.plan['definition']
        self.assertEqual((payload['BTTEnabled'],payload['BTTEnabled2']),(0,0))
        self.assertEqual(payload['BTTAdditionalActionData'],wanted['BTTAdditionalActionData'])
        self.assertEqual(payload['BTTTriggerConditionsFormat'],wanted['BTTTriggerConditionsFormat'])
        self.assertEqual(payload['BTTGestureNotes'],wanted['BTTGestureNotes'])
        self.assertTrue(payload[escape.PASSTHROUGH]);self.assertTrue(payload['BTTAdditionalDataJSON'][escape.PASSTHROUGH])
        self.assertEqual((payload['BTTOrder'],payload['BTTCustomKey']),(7,'kept'))
        for derived in ('BTTTriggerConditionsData','BTTTriggerConditionsFormatReadOnly','BTTScriptString'):
            self.assertNotIn(derived,json.dumps(payload))

    def test_update_refuses_extra_actions_and_other_shortcuts_without_writing(self):
        for changes,message in (({'BTTActionsToExecute':[{'BTTPredefinedActionType':1}]},'extra Escape actions'),
                                ({'BTTShortcutKeyCode':36},'readback mismatch')):
            with self.subTest(changes=changes):
                result=self.run_worker('update',self.stale(**changes))
                self.assertIn(message,result['error']);self.assertEqual(result['log'],[])

    def test_disable_passes_only_when_both_flags_read_back_zero(self):
        enabled=dict(self.plan['definition'],BTTEnabled=1,BTTEnabled2=1)
        result=self.run_worker('disable',enabled)
        self.assertIsNone(result['error']);self.assertEqual(len(result['log']),1)
        ignored=self.run_worker('disable',enabled,ignore_updates=True)
        self.assertIn('confirmed disabled',ignored['error'])

    def test_enable_writes_back_the_saved_script_key_not_the_export_alias(self):
        result=self.run_worker('enable',dict(self.plan['definition']))
        self.assertIsNone(result['error'])
        payload=result['log'][0]['payload']
        self.assertEqual((payload['BTTEnabled'],payload['BTTEnabled2']),(1,1))
        self.assertEqual(payload['BTTAdditionalActionData']['BTTAppleScriptString'],
                         self.plan['definition']['BTTAdditionalActionData']['BTTAppleScriptString'])
        self.assertNotIn('BTTScriptString',payload['BTTAdditionalActionData'])


if __name__=='__main__':unittest.main()
