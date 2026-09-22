const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const dir=path.join(__dirname,'../bettertouchtool');
const source=fs.readFileSync(path.join(dir,'repair_rps.js'),'utf8');
const helpers=require('./btt_test_support.cjs').withCommon(fs.readFileSync(path.join(dir,'fix_menu_sizes.js'),'utf8'));
const MAIN='D9B0ED12-C4BE-4E74-B0DA-0CC3BE092289',BUTTON='103D7824-47C1-4B1B-9106-71E4997BCB58';
const ACTION='525212E9-F6F3-4CF0-A9F7-86C1A52EEB30',SYNC='DA200AE7-BB01-4AB5-89A7-CF1587728D7D';
function fixture(failure) {
 const media={BTTUUID:MAIN,BTTTriggerType:767,BTTMenuConfig:{BTTMenuDisableDrag:0,BTTMenuVerticalSpacing:2.5},
  BTTMenuItems:[{BTTUUID:BUTTON,BTTTriggerType:773,BTTOrder:16,BTTMenuConfig:{BTTMenuAttributedText:'RPS'},BTTMenuItemActions:[]},
   {BTTUUID:'other',BTTMenuItemActions:[{BTTUUID:'other-action'}]}]};
 const sync={BTTUUID:SYNC,BTTTriggerType:643,BTTShellTaskActionScript:'/Users/jacobr/dev/scripts/pi/sync_scripts.sh'};
 if(failure==='existing')media.BTTMenuItems[0].BTTMenuItemActions=[{BTTUUID:'unexpected'}];
 const snapshot=structuredClone({media,sync}),calls=[];
 if(failure==='race')media.BTTMenuItems[0].BTTOrder=20;
 const api={get_trigger(id){return JSON.stringify([id===MAIN?media:sync]);},
  add_new_trigger(json,{parent_uuid}){
   calls.push('add');const action=JSON.parse(json);
   assert.equal(parent_uuid,BUTTON);assert.equal(action.BTTTriggerParentUUID,BUTTON);
   assert.equal(action.BTTTriggerClass,'BTTTriggerTypeFloatingMenu');assert.equal(action.BTTTriggerType,-1);
   assert.equal(action.BTTOrder,0);assert.equal(action.BTTActionCategory,0);assert.equal(action.BTTEnabled,1);
   assert.equal(action.BTTNamedTriggerToTrigger,'Sync RPi Scripts');assert.equal(action.BTTIsPureAction,undefined);
   media.BTTMenuItems[0].BTTMenuItemActions.push(action);
   if(failure==='wrong-action')action.BTTPredefinedActionType=5;
   if(failure==='unrelated-change')media.BTTMenuConfig.BTTMenuDisableDrag=1;
  }};
 const btt=new Proxy(api,{get(target,key){if(!(key in target))throw Error('Forbidden BTT method: '+key);return target[key];}});
 const context=vm.createContext({ObjC:{import(){}}});vm.runInContext(helpers+'\n'+source,context);
 return {run:()=>context.rpsApply(btt,snapshot,context.sizeFingerprint),calls,media,snapshot};
}
test('adds only normalized RPS action, preserving all other menu data; no execution APIs',()=>{
 const f=fixture();assert.match(f.run(),/No sync executed/);assert.deepEqual(f.calls,['add']);
 const action=f.media.BTTMenuItems[0].BTTMenuItemActions[0];assert.equal(action.BTTUUID,ACTION);
 const expected=structuredClone(f.snapshot.media);expected.BTTMenuItems[0].BTTMenuItemActions=[action];
 assert.deepEqual(f.media,expected);
});
for(const failure of ['race','existing'])test(failure+' prevents creation',()=>{
 const f=fixture(failure);assert.throws(f.run);assert.deepEqual(f.calls,[]);
});
for(const failure of ['wrong-action','unrelated-change'])test(failure+' fails verification',()=>{
 const f=fixture(failure);assert.throws(f.run);assert.deepEqual(f.calls,['add']);
});
