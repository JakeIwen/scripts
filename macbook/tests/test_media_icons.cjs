const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const dir=path.join(__dirname,'../bettertouchtool');
const source=fs.readFileSync(path.join(dir,'port_media_icons.js'),'utf8');
const helpers=require('./btt_test_support.cjs').withCommon(fs.readFileSync(path.join(dir,'fix_menu_sizes.js'),'utf8'));
function fixture(failure) {
 const original={BTTUUID:'D9B0ED12-C4BE-4E74-B0DA-0CC3BE092289',BTTTriggerType:767,
  BTTMenuConfig:{BTTMenuDisableDrag:0,BTTMenuVerticalSpacing:2.5},BTTMenuItems:[
   {BTTUUID:'transport',BTTTriggerType:773,BTTOrder:7,
    BTTMenuConfig:{BTTMenuAttributedText:'old emoji',BTTMenuItemMinWidth:40,BTTMenuItemMaxHeight:40},
    BTTMenuItemActions:[{BTTUUID:'existing-action',BTTNamedTriggerToTrigger:'VLCNext'}]},
   {BTTUUID:'status',BTTTriggerType:773,BTTMenuConfig:{BTTMenuScriptSettings:{script:'unchanged'}}}]};
 const patch={BTTMenuAttributedText:'',BTTMenuItemIconType:1,BTTMenuItemImage:'original-base64',BTTMenuItemImageChangeColor:1};
 const plan={media:structuredClone(original),changes:[{uuid:'transport',label:'Next',patch}]};
 const current=structuredClone(original),calls=[];
 if(failure==='race')current.BTTMenuItems[0].BTTOrder=12;
 const context=vm.createContext({ObjC:{import(){}}});vm.runInContext(helpers+'\n'+source,context);
 const api={get_trigger(){return JSON.stringify([current]);},update_menu_item(id,{json,persist}){
  calls.push('update');assert.equal(persist,true);assert.equal(id,'transport');
  Object.assign(current.BTTMenuItems[0].BTTMenuConfig,JSON.parse(json));
  if(failure==='lost-action')current.BTTMenuItems[0].BTTMenuItemActions=[];
 }};
 const btt=new Proxy(api,{get(target,key){if(!(key in target))throw Error('Forbidden BTT API '+key);return target[key];}});
 return {plan,original,current,calls,run:()=>context.iconApply(btt,plan,context.sizeFingerprint,()=>{
  calls.push('validate');if(failure==='image')throw Error('invalid image');
 })};
}
test('appearance update keeps actions, dimensions, order, status script and menu config',()=>{
 const f=fixture();f.run();
 const expected=structuredClone(f.original);Object.assign(expected.BTTMenuItems[0].BTTMenuConfig,f.plan.changes[0].patch);
 assert.deepEqual(f.current,expected);assert.deepEqual(f.calls,['validate','update']);
});
test('changed configuration aborts before any write',()=>{
 const f=fixture('race');assert.throws(f.run);assert.deepEqual(f.calls,[]);
});
test('bad artwork aborts before any write',()=>{
 const f=fixture('image');assert.throws(f.run);assert.deepEqual(f.calls,['validate']);
});
test('missing action after appearance update is an error',()=>{
 const f=fixture('lost-action');assert.throws(f.run);
});
test('a speaker target outside the main item list is rejected',()=>{
 const context=vm.createContext({ObjC:{import(){}}});vm.runInContext(source,context);
 assert.throws(()=>context.mainStyleChanges({items:[],icons:[{uuid:'elsewhere',patch:{}}]},s=>s));
});
test('verified embedding-to-preset conversion normalizes only image representation fields',()=>{
 const context=vm.createContext({ObjC:{import(){}}});vm.runInContext(source,context);
 const expected={BTTMenuItemIconType:1,BTTMenuItemImage:'original-tiff',BTTMenuItemImageChangeColor:1,BTTMenuItemPaddingLeft:0};
 const actual={BTTMenuItemIconType:7,BTTMenuItemIconPresetPath:'/bundle/saved.png',BTTMenuItemImageChangeColor:1,BTTMenuItemPaddingLeft:8};
 const normalized=JSON.parse(JSON.stringify(context.normalizeIconConfig(actual,expected,(data,path,mask)=>{
  assert.equal(data,'original-tiff');assert.equal(path,'/bundle/saved.png');assert.equal(mask,true);return true;
 })));
 assert.deepEqual(normalized,{...expected,BTTMenuItemPaddingLeft:8});
 assert.equal(actual.BTTMenuItemIconType,7);
 const wrong=JSON.parse(JSON.stringify(context.normalizeIconConfig(actual,expected,()=>false)));
 assert.deepEqual(wrong,actual);
});
test('image normalization does not hide a lost action or unrelated setting change',()=>{
 const context=vm.createContext({ObjC:{import(){}}});vm.runInContext(helpers+'\n'+source,context);
 const expected={BTTUUID:'button',BTTMenuConfig:{BTTMenuItemIconType:1,BTTMenuItemImage:'original'},
  BTTMenuItemActions:[{BTTUUID:'action',BTTPredefinedActionType:248}]};
 const actual={BTTUUID:'button',BTTMenuConfig:{BTTMenuItemIconType:7,BTTMenuItemIconPresetPath:'/bundle/icon.png'},BTTMenuItemActions:[]};
 const normalized=context.normalizeIconTree(actual,expected,()=>true);
 assert.notEqual(context.sizeFingerprint(normalized),context.sizeFingerprint(expected));
});
