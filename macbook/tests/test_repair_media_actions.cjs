const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {withCommon} = require('./btt_test_support.cjs');
const ctx = {ObjC:{import(){}}}; vm.createContext(ctx);
vm.runInContext(withCommon(fs.readFileSync(path.join(__dirname,'../bettertouchtool/repair_media_actions.js'),'utf8')),ctx);

function fixture() {
    const records = {
        button:{BTTUUID:'button',BTTTriggerType:773,BTTOrder:14,BTTMenuConfig:{color:'black',width:40}},
        named:{BTTUUID:'named',BTTTriggerType:643,BTTPredefinedActionType:206,BTTShellTaskActionScript:'echo fixture'}
    }, calls=[];
    const entity={uuid:'action',parent:'button',node:{BTTUUID:'action',BTTTriggerParentUUID:'button',
        BTTTriggerType:-1,BTTTriggerClass:'BTTTriggerTypeFloatingMenu',BTTPredefinedActionType:248,
        BTTNamedTriggerToTrigger:'Partymode',BTTOrder:0,BTTActionCategory:0,BTTEnabled:1}};
    const plan={version:1,entities:[entity],buttons:[{uuid:'button'}],dependencies:[{uuid:'named',name:'Partymode'}],
        dependency_sources:[]};
    const get=id=>records[id]?JSON.parse(JSON.stringify(records[id])):null;
    const btt={add_new_trigger(raw,args) {
        const node=JSON.parse(raw); calls.push([node,args]); records[node.BTTUUID]=node;
        if(args) (records[args.parent_uuid].BTTMenuItemActions ||= []).push(node);
    }};
    plan.snapshot=ctx.actionSnapshot(plan,get,ctx.BTTCommon.fingerprint);
    return {plan,records,get,btt,calls,fp:ctx.BTTCommon.fingerprint};
}
test('restores only missing child actions with explicit parents and preserves button appearance/order',()=>{
    const x=fixture();ctx.applyRecoveredPlan(x.btt,x.plan,x.get,x.fp);
    assert.equal(x.calls.length,1);assert.equal(x.calls[0][1].parent_uuid,'button');
    assert.equal(x.records.action.BTTTriggerType,-1);assert.equal(x.records.button.BTTOrder,14);
    assert.deepEqual(x.records.button.BTTMenuConfig,{color:'black',width:40});
});
test('concurrent change prevents creation',()=>{
    const x=fixture();x.records.button.BTTOrder=15;
    assert.throws(()=>ctx.applyRecoveredPlan(x.btt,x.plan,x.get,x.fp),/changed during backup/);
    assert.equal(x.calls.length,0);
});
test('saved-late runtime actions are not duplicated on rerun',()=>{
    const x=fixture();ctx.applyRecoveredPlan(x.btt,x.plan,x.get,x.fp);
    x.plan.snapshot=ctx.actionSnapshot(x.plan,x.get,x.fp);
    ctx.applyRecoveredPlan(x.btt,x.plan,x.get,x.fp);
    assert.equal(x.calls.length,1);
});
test('existing different actions and orphaned recovery UUIDs are rejected',()=>{
    const x=fixture();x.records.button.BTTMenuItemActions=[{BTTUUID:'custom'}];
    assert.throws(()=>ctx.actionSnapshot(x.plan,x.get,x.fp),/different action/);
    delete x.records.button.BTTMenuItemActions;x.records.action=x.plan.entities[0].node;
    assert.throws(()=>ctx.actionSnapshot(x.plan,x.get,x.fp),/belongs elsewhere/);
});
test('failed association or payload persistence is not called success',()=>{
    const x=fixture();x.btt.add_new_trigger=(raw)=>{const n=JSON.parse(raw);x.records[n.BTTUUID]=n;};
    assert.throws(()=>ctx.applyRecoveredPlan(x.btt,x.plan,x.get,x.fp),/association/);
    x.records.button.BTTMenuItemActions=[x.records.action];x.records.action.BTTNamedTriggerToTrigger='wrong';
    assert.throws(()=>ctx.verifyRecoveredPlan(x.plan,x.get,x.fp),/payload differs/);
});
test('verification detects post-restart loss and unrelated parent changes',()=>{
    const x=fixture();ctx.applyRecoveredPlan(x.btt,x.plan,x.get,x.fp);
    delete x.records.action;
    assert.throws(()=>ctx.verifyRecoveredPlan(x.plan,x.get,x.fp),/Missing action/);
    x.records.action=x.plan.entities[0].node;x.records.button.BTTMenuConfig.width=99;
    assert.throws(()=>ctx.verifyRecoveredPlan(x.plan,x.get,x.fp),/Existing button changed/);
});
test('named dependency created before its child link without enabling its old preset',()=>{
    const x=fixture();delete x.records.named;x.records.inactive={BTTUUID:'inactive',BTTTriggerType:643};
    const node={BTTUUID:'named',BTTTriggerType:643,BTTPredefinedActionType:206,
        BTTShellTaskActionScript:'echo fixture',BTTEnabled:1,BTTOrder:0};
    x.plan.entities.unshift({uuid:'named',parent:null,node});x.plan.dependency_sources=['inactive'];
    x.plan.snapshot=ctx.actionSnapshot(x.plan,x.get,x.fp);
    ctx.applyRecoveredPlan(x.btt,x.plan,x.get,x.fp);
    assert.equal(x.calls[0][0].BTTUUID,'named');assert.equal(x.calls[0][1],undefined);
    assert.deepEqual(x.records.inactive,{BTTUUID:'inactive',BTTTriggerType:643});
});
