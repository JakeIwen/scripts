const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const dir=path.join(__dirname,'../bettertouchtool');
const source=fs.readFileSync(path.join(dir,'port_media_icons.js'),'utf8');
const helpers=fs.readFileSync(path.join(dir,'fix_menu_sizes.js'),'utf8');
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
 const f=fixture();assert.match(f.run(),/Updated 1/);
 const expected=structuredClone(f.original);Object.assign(expected.BTTMenuItems[0].BTTMenuConfig,f.plan.changes[0].patch);
 assert.deepEqual(f.current,expected);assert.deepEqual(f.calls,['validate','update']);
});
test('changed configuration aborts before any write',()=>{
 const f=fixture('race');assert.throws(f.run,/changed during backup/);assert.deepEqual(f.calls,[]);
});
test('bad artwork aborts before any write',()=>{
 const f=fixture('image');assert.throws(f.run,/invalid image/);assert.deepEqual(f.calls,['validate']);
});
test('missing action after appearance update is an error',()=>{
 const f=fixture('lost-action');assert.throws(f.run,/Readback differs/);
});
test('main style includes submenu labels and speaker icons without changing layout or scripts',()=>{
 const context=vm.createContext({ObjC:{import(){}}});vm.runInContext(source,context);
 const input={items:[
  {uuid:'notes',type:774,config:{BTTMenuAttributedText:'white:Recent\\line Notes',BTTMenuItemMinWidth:54}},
  {uuid:'status',type:773,config:{BTTMenuAttributedText:'white:Unk',BTTMenuScriptSettings:{script:'keep'}}},
  {uuid:'join',type:773,config:{BTTMenuAttributedText:'white:old icon',BTTMenuItemMinHeight:40}}],
  icons:[{uuid:'join',label:'Join Sonos speakers',patch:{BTTMenuAttributedText:'',BTTMenuItemImage:'saved-four-arrow-art',BTTMenuItemIconType:1}}]};
 const original=structuredClone(input);
 const changes=JSON.parse(JSON.stringify(context.mainStyleChanges(input,s=>s.replace('white:','black:'))));
 assert.deepEqual(input,original);assert.equal(changes.length,3);
 assert.equal(changes[0].patch.BTTMenuAttributedText,'black:Recent\\line Notes');
 assert.equal(changes[1].patch.BTTMenuItemFontColorHoverDark,'0, 0, 0, 255');
 assert.equal(changes[2].patch.BTTMenuItemImage,'saved-four-arrow-art');
 for(const change of changes) {
  assert(!Object.keys(change.patch).some(k=>/Script|Action|MinWidth|MinHeight|Order|DisableDrag|Spacing/.test(k)));
  Object.assign(input.items.find(x=>x.uuid===change.uuid).config,change.patch);
 }
 assert.equal(context.mainStyleChanges(input,s=>s.replace('white:','black:')).length,0);
});
test('a speaker target outside the main item list is rejected',()=>{
 const context=vm.createContext({ObjC:{import(){}}});vm.runInContext(source,context);
 assert.throws(()=>context.mainStyleChanges({items:[],icons:[{uuid:'elsewhere',patch:{}}]},s=>s),/not in Media/);
});
test('center play symbol and live status text stay black without replacing script or glyph',()=>{
 const context=vm.createContext({ObjC:{import(){}}});vm.runInContext(source,context);
 const id='BDFEAEF1-6961-4B05-9A62-E70C54332C4C';
 const config={BTTMenuItemIconType:2,BTTMenuItemSFSymbolName:'play.fill',
  BTTMenuItemIconColor1:'255, 255, 255, 255',BTTMenuAttributedText:'black:Unk',
  BTTMenuItemImageHeight:30,BTTMenuScriptSettings:{script:'keep status query'},BTTMenuScriptUpdateInterval:2};
 const input={items:[{uuid:id,type:773,config}],icons:[]};
 const changes=JSON.parse(JSON.stringify(context.mainStyleChanges(input,s=>s)));
 assert.equal(changes.length,1);const patch=changes[0].patch;
 for(const suffix of ['','Dark'])for(const field of ['IconColor1','IconColor1Hover','FontColor','FontColorHover']) {
  assert.equal(patch['BTTMenuItem'+field+suffix],'0, 0, 0, 255');
 }
 assert(!Object.keys(patch).some(k=>/Script|ImageHeight|SFSymbol|IconType|Action/.test(k)));
 const updated={...config,...patch,BTTMenuItemText:'A Movie\n01:02 / 1:23:45'};
 assert.equal(updated.BTTMenuItemFontColor,'0, 0, 0, 255');
 assert.equal(updated.BTTMenuItemIconColor1,'0, 0, 0, 255');
 assert.equal(updated.BTTMenuItemSFSymbolName,'play.fill');
 input.items[0].config=updated;
 assert.equal(context.mainStyleChanges(input,s=>s).length,0);
});
test('partymode centers the image by removing unequal legacy padding, not resizing the button',()=>{
 const context=vm.createContext({ObjC:{import(){}}});vm.runInContext(source,context);
 const id='1CEF0D9C-AB16-48F2-AF26-AE3740217CEA';
 const config={BTTMenuItemIconPosition:4,BTTMenuItemIconPositionDark:4,
  BTTMenuItemPaddingLeft:8,BTTMenuItemPaddingRight:-9,BTTMenuItemImageOffsetX:0,
  BTTMenuItemMinWidth:40,BTTMenuItemMaxWidth:40,BTTMenuItemMinHeight:40,BTTMenuItemMaxHeight:40};
 const result=JSON.parse(JSON.stringify(context.mainStyleChanges({items:[{uuid:id,type:773,config}],icons:[]},s=>s)));
 assert.equal(result[0].patch.BTTMenuItemPaddingLeft,0);assert.equal(result[0].patch.BTTMenuItemPaddingRight,0);
 assert(!Object.keys(result[0].patch).some(k=>/MinWidth|MaxWidth|MinHeight|MaxHeight|ImageWidth|ImageHeight/.test(k)));
 assert.equal(config.BTTMenuItemPaddingLeft,8);
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
