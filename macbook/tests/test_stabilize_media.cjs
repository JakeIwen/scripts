const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const dir=path.join(__dirname,'../bettertouchtool');
const source=require('./btt_test_support.cjs').withCommon(fs.readFileSync(path.join(dir,'stabilize_media.js'),'utf8'));

test('flattening creates explicit persistent children and action records in parent-first order',()=>{
 const context=vm.createContext({ObjC:{import(){}}});vm.runInContext(source,context);
 context.tree={BTTUUID:'group',BTTMenuItems:[
  {BTTUUID:'button',BTTTriggerType:773,BTTOrder:9,BTTMenuItemActions:[{BTTUUID:'action',BTTOrder:119,BTTPredefinedActionType:248,BTTNamedTriggerToTrigger:'unchanged'}]},
  {BTTUUID:'back',BTTTriggerType:777,BTTOrder:0}]};
 const flat=JSON.parse(vm.runInContext('stableCompactOrder(tree); const rows=[]; tree.BTTMenuItems.forEach(x=>stableFlatten(x,"group","item",rows)); JSON.stringify(rows)',context));
 assert.deepEqual(flat.map(x=>x.uuid),['back','button','action']);
 assert.deepEqual(flat.map(x=>x.parent),['group','group','button']);
 assert.deepEqual(flat.map(x=>x.node.BTTOrder),[0,1,0]);
 assert.equal(flat[2].node.BTTTriggerType,-1);
 assert.equal(flat[2].node.BTTNamedTriggerToTrigger,'unchanged');
 assert(flat.every(x=>!x.node.BTTMenuItems&&!x.node.BTTMenuItemActions));
});
test('compact Rear Movie action export becomes a complete standalone action record',()=>{
 const context=vm.createContext({ObjC:{import(){}}});vm.runInContext(source,context);
 const action={BTTUUID:'434F1558-4DD7-411F-B903-82000CB20448',BTTIsPureAction:true,
  BTTTriggerParentUUID:'BDB2E3C5-4474-42B3-AE6A-C3CD5472934B',BTTOrder:0,
  BTTPredefinedActionType:248,BTTNamedTriggerToTrigger:'Rear Movie',
  BTTPredefinedActionName:'Trigger Named Trigger (Configured in Other Tab)'};
 context.action=action;
 const result=JSON.parse(vm.runInContext('const rows=[]; stableFlatten(action,action.BTTTriggerParentUUID,"action",rows); JSON.stringify(rows[0])',context));
 const {BTTIsPureAction,...unchanged}=action;
 assert.deepEqual(result.node,{...unchanged,BTTTriggerType:-1,BTTTriggerClass:'BTTTriggerTypeFloatingMenu'});
 assert.equal(result.parent,action.BTTTriggerParentUUID);
 assert.equal(action.BTTIsPureAction,true,'backup input must not be mutated');
});
test('flattening preserves an explicit action category and disabled state',()=>{
 const context=vm.createContext({ObjC:{import(){}}});vm.runInContext(source,context);
 const result=JSON.parse(vm.runInContext('const rows=[]; stableFlatten({BTTUUID:"a",BTTEnabled:0,BTTActionCategory:2,BTTTriggerClass:"BTTTriggerTypeFloatingMenu"},"p","action",rows); JSON.stringify(rows[0].node)',context));
 assert.equal(result.BTTEnabled,0);assert.equal(result.BTTActionCategory,2);
});
test('layout uses screen top-left and content height, with explicit full-screen dropdown fallback',()=>{
 const context=vm.createContext({ObjC:{import(){}}});vm.runInContext(source,context);
 const result=JSON.parse(vm.runInContext('JSON.stringify([stableLayout({}, {width:1728,height:1080},true,false),stableLayout({BTTMenuFrameWidth:400},{width:1728,height:1080},false,false),stableLayout({BTTMenuFrameWidth:400},{width:1728,height:1080},false,true)])',context));
 assert.equal(result[0].BTTMenuPositionRelativeTo,1);assert.equal(result[0].BTTMenuAnchorMenu,0);assert.equal(result[0].BTTMenuAnchorRelation,0);
 assert.equal(result[0].BTTMenuSizingBehavior,3);assert.equal(result[0].BTTMenuFrameMaxHeight,1080);
 assert.equal(result[1].BTTMenuSizingBehavior,3);assert.equal(result[1].BTTMenuFrameMaxWidth,400);
 assert.equal(result[2].BTTMenuSizingBehavior,1);assert.equal(result[2].BTTMenuFrameHeight,1080);
});
test('layout repair preserves working drag and held-modifier click handling',()=>{
 const context=vm.createContext({ObjC:{import(){}}});vm.runInContext(source,context);
 for(const existing of [{},{BTTMenuDisableDrag:0},{BTTMenuDisableDrag:1}]) {
  context.existing=existing;
  const patch=JSON.parse(vm.runInContext('JSON.stringify(stableLayout(existing,{width:2560,height:1440},true,false))',context));
  assert.equal(Object.hasOwn(patch,'BTTMenuDisableDrag'),false);
  assert.equal(Object.hasOwn(patch,'BTTMenuModifierMode'),false);
  assert.equal(Object.hasOwn(patch,'BTTMenuModifierKeys'),false);
  assert.equal({...existing,...patch}.BTTMenuDisableDrag,existing.BTTMenuDisableDrag);
 }
});
for(const alreadySaved of [false,true])test('apply uses individual adds; skips saved layout = '+alreadySaved,()=>{
 const plan={create:[{uuid:'button',parent:'group',node:{BTTUUID:'button',BTTTriggerType:773}},
  {uuid:'action',parent:'button',node:{BTTUUID:'action',BTTTriggerType:-1,BTTPredefinedActionType:386}}],
  configChanges:[{uuid:'main',patch:{BTTMenuSizingBehavior:3}}]};
 if(alreadySaved)plan.applyConfigChanges=[];
 const store=new Map(),configs=new Map(),events=[];
 function $(x){return x;}$.NSString={stringWithContentsOfFileEncodingError(p){
  if(p==='/plan.json')return JSON.stringify(plan);
  return fs.readFileSync(path.join(dir,p.split('/macbook/bettertouchtool/')[1]),'utf8');
 }};
 const context=vm.createContext({$,ObjC:{import(){},unwrap(x){return x;}},Application(){return{
  add_new_trigger(json,{parent_uuid}){const item=JSON.parse(json);store.set(item.BTTUUID,{...item,parent:parent_uuid});events.push('add');},
  update_menu_item(id,{json,persist}){assert.equal(persist,true);configs.set(id,JSON.parse(json));events.push('persist');},
  update_trigger(){throw Error('nested cache update must not be used');}
 };}});
 vm.runInContext(source,context);const out=vm.runInContext('run(["apply","/repo","/plan.json"])',context);
 assert.match(out,/Created 2/);assert.deepEqual(events,alreadySaved?['add','add']:['add','add','persist']);
 const restartedStore=new Map(JSON.parse(JSON.stringify([...store])));
 assert.equal(restartedStore.get('action').parent,'button');
 if(!alreadySaved)assert.equal(configs.get('main').BTTMenuSizingBehavior,3);
});
