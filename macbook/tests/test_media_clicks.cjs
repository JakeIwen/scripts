const test = require('node:test'), assert = require('node:assert/strict');
const fs = require('node:fs'), vm = require('node:vm'), path = require('node:path');
const dir = path.join(__dirname, '../bettertouchtool');
const source = fs.readFileSync(path.join(dir, 'one_off_fixes/test_media_clicks.js'), 'utf8');
const helpers = fs.readFileSync(path.join(dir, 'fix_menu_sizes.js'), 'utf8');

function fixture(failure) {
 const original = {BTTUUID:'D9B0ED12-C4BE-4E74-B0DA-0CC3BE092289', BTTTriggerType:767,
  BTTMenuConfig:{BTTMenuDisableDrag:1,BTTMenuModifierKeys:1835008,BTTMenuModifierMode:0,
   BTTMenuPositionRelativeTo:1,BTTMenuSizingBehavior:3,BTTMenuFrameHeight:40},
  BTTMenuItems:[{BTTUUID:'button',BTTMenuItemActions:[{BTTUUID:'action',BTTPredefinedActionType:386}]}]};
 let current = structuredClone(original), reads = 0; const events = [];
 const ctx = vm.createContext({ObjC:{import(){}}}); vm.runInContext(helpers+'\n'+source,ctx);
 const btt = {get_trigger(){ reads++; if(failure==='race'&&reads===2)current.BTTMenuConfig.BTTMenuFrameHeight=99;
   return JSON.stringify([current]);},
  update_menu_item(id,{json,persist}) {assert.equal(id,original.BTTUUID);assert.equal(persist,true);
   const patch=JSON.parse(json); assert.deepEqual(Object.keys(patch),['BTTMenuDisableDrag']);
   events.push('write');Object.assign(current.BTTMenuConfig,patch);
   if(failure==='readback')current.BTTMenuItems=[];},
  update_trigger(){throw Error('must not import trees');}};
 const backup = snapshot => {events.push('backup');assert.deepEqual(JSON.parse(JSON.stringify(snapshot.media)),current);
  if(failure==='backup')throw Error('backup failed');return '/private/backup.json';};
 return {run(undo=false){return ctx.clicksTest(btt,backup,ctx.sizeFingerprint,undo);},
  events,original,current:()=>structuredClone(current)};
}
test('backs up, changes one input flag, and preserves modifiers, layout and actions',()=>{
 const f=fixture();assert.match(f.run(),/Set only Disable Drag = 0/);assert.deepEqual(f.events,['backup','write']);
 const expected=structuredClone(f.original);expected.BTTMenuConfig.BTTMenuDisableDrag=0;
 assert.deepEqual(f.current(),expected);
 assert.match(f.run(),/already 0/);assert.deepEqual(f.events,['backup','write']);
 assert.match(f.run(true),/Restored Disable Drag = 1/);assert.deepEqual(f.current(),f.original);
});
for(const failure of ['backup','race'])test(failure+' prevents live writes',()=>{
 const f=fixture(failure);assert.throws(()=>f.run());assert.deepEqual(f.events,['backup']);
});
test('unexpected loss of descendants fails readback',()=>{
 const f=fixture('readback');assert.throws(()=>f.run(),/Readback differs/);
});
