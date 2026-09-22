const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const base=path.join(__dirname,'../bettertouchtool');
const code=require('./btt_test_support.cjs').withCommon(fs.readFileSync(path.join(base,'one_off_fixes/2026-09-18-NONPERSISTENT-nested-media-repair.js'),'utf8'));
const mediaID='D9B0ED12-C4BE-4E74-B0DA-0CC3BE092289';
const rearID='D6D639BB-B1D1-4C90-85E3-B8F5C8310E56',sonosID='F3CC5F44-0D08-49DD-8B5C-3D6C1C022759';
const notesIDs=['0F8AEB59-60C1-4B1F-A356-26AD45E3A0A3','0C21404E-6EBD-41F5-97D0-2D18DD60A386'];
const menuIDs=['DAFF4296-EE87-5CCC-A682-50B2865D6C5A','1DF07B14-A4BB-5533-8679-BE46692E70AD'];
const clone=x=>JSON.parse(JSON.stringify(x));

function setup(failure) {
 const root={BTTUUID:mediaID,BTTTriggerType:767,BTTMenuItems:[rearID,sonosID,...notesIDs].map((id,i)=>({
  BTTUUID:id,BTTTriggerParentUUID:mediaID,BTTTriggerType:i<2?774:773,BTTOrder:i+1,
  BTTMenuConfig:{BTTMenuItemMinWidth:i<2?50:90,BTTMenuItemMaxWidth:i<2?50:130,
   BTTMenuItemMinHeight:40,BTTMenuItemMaxHeight:40,BTTMenuItemText:i<2?'keep label':'Notes'},
  BTTMenuItems:[],BTTMenuItemActions:[]}))};
 root.BTTMenuItems.push({BTTUUID:'unrelated',BTTTriggerType:773,BTTTriggerParentUUID:mediaID,BTTOrder:10,
  BTTMenuItemActions:[{BTTUUID:'unrelated-action',BTTPredefinedActionType:248,BTTNamedTriggerToTrigger:'keep'}]});
 const initial=clone(root),complete=clone(root);
 for(const [index,count] of [[0,4],[1,9]]) {
  complete.BTTMenuItems[index].BTTMenuItems=Array.from({length:count},(_,i)=>({
   BTTUUID:'child-'+index+'-'+i,BTTTriggerParentUUID:complete.BTTMenuItems[index].BTTUUID,
   BTTTriggerType:i===0?777:773,BTTOrder:i,BTTMenuConfig:{BTTMenuItemMinWidth:60,BTTMenuItemMaxWidth:40},
   BTTMenuItemActions:i===0?[]:[{BTTUUID:'action-'+index+'-'+i,BTTPredefinedActionType:248,
     BTTNamedTriggerToTrigger:'original-action-'+i}]}));
 }
 const roots=new Map([[mediaID,root],...menuIDs.map(id=>[id,{BTTUUID:id,BTTTriggerType:767,BTTMenuConfig:{BTTMenuLayoutDirection:6}}])]);
 const files=new Map([
  ['/repo/macbook/bettertouchtool/install_notes.js',fs.readFileSync(path.join(base,'install_notes.js'),'utf8')],
  ['/repo/macbook/bettertouchtool/fix_menu_sizes.js',fs.readFileSync(path.join(base,'fix_menu_sizes.js'),'utf8')],
  ['/repo/tmp/btt-labels-backup-original/backup.json',JSON.stringify({media:complete})],
  ['/repo/tmp/btt-sizing-backup-damaged/backup.json',JSON.stringify({mediaExport:initial})]
 ]);
 const events=[],variables=new Map();let visible='';
 function find(id) {if(roots.has(id))return roots.get(id);return root.BTTMenuItems.find(x=>x.BTTUUID===id);}
 const btt={get_trigger(id){return JSON.stringify(find(id)||{});},
  update_trigger(id,{json,trigger_parent_uuid}){
   events.push('update');const value=JSON.parse(json);
   assert.equal(trigger_parent_uuid,mediaID);assert.equal(value.BTTUUID,id);
   // Replace, rather than merge: omitted descendants really would disappear.
   if(failure==='dropped-actions')delete value.BTTMenuItemActions;
   root.BTTMenuItems[root.BTTMenuItems.findIndex(x=>x.BTTUUID===id)]=value;
  },add_new_trigger(json,{parent_uuid}){assert.equal(parent_uuid,mediaID);events.push('add');root.BTTMenuItems.push(JSON.parse(json));},
  set_string_variable(k,{to}){variables.set(k,to);},get_string_variable(k){return k==='visible_floating_menu_identifiers'?visible:variables.get(k)||'';},
  execute_assigned_actions_for_trigger(id){
   const n=notesIDs.indexOf(id);assert(n>=0);const item=find(id);assert.equal(item.BTTMenuItemActions[0].BTTPredefinedActionType,386);
   visible=menuIDs[n];variables.set('notes_menu_'+(n?'pinned':'recent')+'_status',JSON.stringify({state:'ok',count:n?23:10}));
  },trigger_action(){visible='';}};
 function $(value){return {value,stringByExpandingTildeInPath:typeof value==='string'?value.replace('~/dev/scripts','/repo'):value,
  writeToFileAtomicallyEncodingError(p){events.push('backup-write');if(failure==='backup')return false;files.set(p,value);return true;}};}
 $.NSString={stringWithContentsOfFileEncodingError(p){if(files.has(p)){if(p.includes('btt-controls-backup'))events.push('backup-read');return files.get(p);}return null;}};
 $.NSUUID={UUID:{UUIDString:'test'}};
 $.NSFileManager={defaultManager:{contentsOfDirectoryAtPathError(){return failure==='archive'?[]:['btt-sizing-backup-damaged','btt-labels-backup-original'];},
  attributesOfItemAtPathError(p){return {objectForKey(){return {timeIntervalSince1970:p.includes('damaged')?2:1};}};},
  createDirectoryAtPathWithIntermediateDirectoriesAttributesError(p,r,a){assert.equal(a.value.NSFilePosixPermissions,448);return true;},
  setAttributesOfItemAtPathError(a){assert.equal(a.value.NSFilePosixPermissions,384);return true;}}};
 const context=vm.createContext({$,ObjC:{import(){},unwrap(x){return x;},deepUnwrap(x){return x;}},Application(){return btt;},console:{log(){}},delay(){throw Error('Should be ready without waiting');}});
 vm.runInContext(code,context);
 return {run(args=[]){context.args=['--run-historical',...args];return vm.runInContext('run(args)',context);},root,initial,events,files};
}
test('recovers real hierarchy shapes with replacement-style API; narrows Notes and adds keep-open screenshot',()=>{
 const s=setup();assert.match(s.run(),/Recent Notes: visible/);
 assert(s.events.indexOf('backup-read')<s.events.indexOf('update'));
 assert.equal(s.root.BTTMenuItems[0].BTTMenuItems.length,4);assert.equal(s.root.BTTMenuItems[1].BTTMenuItems.length,9);
 for(const row of s.root.BTTMenuItems[1].BTTMenuItems)assert.equal(row.BTTMenuConfig.BTTMenuItemMaxWidth,60);
 for(const item of s.root.BTTMenuItems.slice(2,4)){assert.equal(item.BTTMenuConfig.BTTMenuItemMinWidth,54);assert.equal(item.BTTMenuConfig.BTTMenuItemMaxWidth,78);assert.equal(item.BTTMenuItemActions[0].BTTPredefinedActionType,386);}
 assert.deepEqual(s.root.BTTMenuItems[4],s.initial.BTTMenuItems[4]);
 const capture=s.root.BTTMenuItems.at(-1);assert.equal(capture.BTTMenuName,'Screen Cap');assert.equal(capture.BTTMenuConfig.BTTMenuItemCloseOnClick,0);assert.equal(capture.BTTOrder,11);
 assert.match(capture.BTTMenuItemActions[0].BTTShellTaskActionScript,/capture_btt_menu.zsh/);
 const mutations=s.events.filter(x=>['update','add'].includes(x)).length;s.run();assert.equal(s.events.filter(x=>['update','add'].includes(x)).length,mutations);
});
for(const kind of ['backup','archive'])test(kind+' failure prevents BTT edits',()=>{
 const s=setup(kind);assert.throws(s.run);assert(!s.events.includes('update'));assert(!s.events.includes('add'));
});
test('missing restored action fails verification instead of reporting success',()=>{
 const s=setup('dropped-actions');assert.throws(s.run,/Missing restored item/);
});
test('inspect lists plan without BTT edits or backup',()=>{
 const s=setup();const plan=JSON.parse(s.run(['--inspect']));assert.equal(plan.updates.length,4);assert.equal(s.events.length,0);
});
