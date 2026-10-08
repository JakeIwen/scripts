const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {withCommon} = require('./btt_test_support.cjs');

const context = {ObjC:{import(){}}};
vm.createContext(context);
vm.runInContext(withCommon(fs.readFileSync(
    path.join(__dirname,'../bettertouchtool/btt_guard/worker.js'),'utf8')),context);

const clone = value => value === null ? null : JSON.parse(JSON.stringify(value));

function createPlan() {
    return {schema_version:1,checkpoint_id:'checkpoint',conflicts:[],operations:[{
        uuid:'item',parent:'root',mode:'create',definition:{
            BTTUUID:'item',BTTTriggerType:773,BTTTriggerClass:'BTTTriggerTypeFloatingMenu',
            BTTTriggerParentUUID:'root',BTTOrder:0,BTTMenuConfig:{BTTMenuVisibility:1},
        },
    }]};
}

function fixture(plan=createPlan()) {
    const records={root:{BTTUUID:'root',BTTTriggerType:767,
        BTTMenuConfig:{BTTMenuVisibility:1,BTTMenuItemText:'original'}}};
    const calls=[];
    const get=uuid=>records[uuid] ? clone(records[uuid]) : null;
    const btt={
        trigger_action(){throw new Error('configured actions must never execute');},
        add_new_trigger(raw,args){
            const node=JSON.parse(raw);calls.push(['add',node,args]);records[node.BTTUUID]=clone(node);
            if(args) {
                const key=Number(node.BTTTriggerType)===-1?'BTTMenuItemActions':'BTTMenuItems';
                (records[args.parent_uuid][key] ||= []).push(clone(node));
            }
        },
        update_trigger(uuid,args){
            calls.push(['update',uuid,clone(args)]);Object.assign(records[uuid],JSON.parse(args.json));
            const key=Number(records[uuid].BTTTriggerType)===-1?'BTTMenuItemActions':'BTTMenuItems';
            (records[args.trigger_parent_uuid][key] ||= []).push(clone(records[uuid]));
        },
    };
    return {plan,records,calls,get,btt,fp:context.BTTCommon.fingerprint};
}

test('snapshot is read-only and apply creates only the flat expected record',()=>{
    const x=fixture();
    x.plan.runtime_snapshot=context.repairSnapshot(x.plan,x.get);
    assert.deepEqual(Object.keys(x.plan.runtime_snapshot).sort(),['item','root']);
    assert.equal(x.calls.length,0);
    const result=context.repairApply(x.btt,x.plan,x.get,x.fp);
    assert.match(result,/Created 1 records and reattached 0/);
    assert.equal(x.calls.length,1);
    assert.equal(x.calls[0][2].parent_uuid,'root');
    assert.equal(x.records.root.BTTMenuConfig.BTTMenuItemText,'original');
});

test('whole missing trees are created parent-first without replacing a tree',()=>{
    const plan={schema_version:1,checkpoint_id:'checkpoint',conflicts:[],operations:[
        {uuid:'menu',parent:null,mode:'create',definition:{BTTUUID:'menu',BTTTriggerType:767,
            BTTTriggerClass:'BTTTriggerTypeFloatingMenu',BTTTriggerBelongsToPreset:'Master'}},
        {uuid:'child',parent:'menu',mode:'create',definition:{BTTUUID:'child',BTTTriggerType:773,
            BTTTriggerClass:'BTTTriggerTypeFloatingMenu',BTTTriggerParentUUID:'menu'}},
    ]};
    const x=fixture(plan);delete x.records.root;
    plan.runtime_snapshot=context.repairSnapshot(plan,x.get);
    context.repairApply(x.btt,plan,x.get,x.fp);
    assert.deepEqual(x.calls.map(call=>call[1].BTTUUID),['menu','child']);
    assert.equal(x.calls[0][2],undefined);
    assert.equal(x.calls[1][2].parent_uuid,'menu');
});

test('all fingerprints are checked before the first write',()=>{
    const x=fixture();x.plan.runtime_snapshot=context.repairSnapshot(x.plan,x.get);
    x.records.root.BTTMenuConfig.BTTMenuVisibility=2;
    assert.throws(()=>context.repairApply(x.btt,x.plan,x.get,x.fp),/changed after the verified snapshot/);
    assert.equal(x.calls.length,0);
});

test('a create target appearing after snapshot fails instead of being skipped or duplicated',()=>{
    const x=fixture();x.plan.runtime_snapshot=context.repairSnapshot(x.plan,x.get);
    x.records.item=clone(x.plan.operations[0].definition);
    assert.throws(()=>context.repairApply(x.btt,x.plan,x.get,x.fp),/changed after the verified snapshot/);
    assert.equal(x.calls.length,0);
});

test('reattach uses a parent-only update and protects all other record fields',()=>{
    const definition={BTTUUID:'item',BTTTriggerType:773,
        BTTTriggerClass:'BTTTriggerTypeFloatingMenu',BTTTriggerParentUUID:'root',BTTOrder:0};
    const plan={schema_version:1,checkpoint_id:'checkpoint',conflicts:[],operations:[
        {uuid:'item',parent:'root',mode:'reattach',definition},
    ]};
    const x=fixture(plan);x.records.item={BTTUUID:'item',BTTTriggerType:773,
        BTTTriggerClass:'BTTTriggerTypeFloatingMenu',BTTOrder:0,
        BTTMenuConfig:{BTTMenuItemText:'keep me'}};
    plan.runtime_snapshot=context.repairSnapshot(plan,x.get);
    context.repairApply(x.btt,plan,x.get,x.fp);
    assert.deepEqual(x.calls[0],['update','item',{
        trigger_parent_uuid:'root',json:JSON.stringify({BTTTriggerParentUUID:'root'}),
    }]);
    assert.equal(x.records.item.BTTMenuConfig.BTTMenuItemText,'keep me');
});

test('reattaching an orphan action restores parent and canonical action order only',()=>{
    const definition={BTTUUID:'action-b',BTTTriggerType:-1,
        BTTTriggerClass:'BTTTriggerTypeFloatingMenu',BTTTriggerParentUUID:'root',
        BTTOrder:1,BTTActionCategory:0,BTTPredefinedActionType:206};
    const plan={schema_version:1,checkpoint_id:'checkpoint',conflicts:[],operations:[
        {uuid:'action-b',parent:'root',mode:'reattach',definition},
    ]};
    const x=fixture(plan);
    x.records.root.BTTMenuItemActions=[{BTTUUID:'action-a',BTTTriggerType:-1,
        BTTOrder:0,BTTPredefinedActionType:206}];
    x.records['action-b']={BTTUUID:'action-b',BTTTriggerType:-1,
        BTTTriggerClass:'BTTTriggerTypeFloatingMenu',BTTOrder:0,
        BTTActionCategory:0,BTTPredefinedActionType:206,
        BTTMenuConfig:{BTTMenuItemText:'keep me'}};
    plan.runtime_snapshot=context.repairSnapshot(plan,x.get);
    context.repairApply(x.btt,plan,x.get,x.fp);
    assert.deepEqual(JSON.parse(x.calls[0][2].json),{
        BTTTriggerParentUUID:'root',BTTOrder:1,
    });
    assert.equal(x.records['action-b'].BTTOrder,1);
    assert.equal(x.records['action-b'].BTTMenuConfig.BTTMenuItemText,'keep me');
    assert.equal(x.records.root.BTTMenuItemActions[0].BTTUUID,'action-a');
});

test('planner conflicts and invalid operation order prevent every write',()=>{
    const x=fixture();x.plan.conflicts=[{kind:'disabled',uuid:'item',detail:'field: enabled'}];
    assert.throws(()=>context.repairSnapshot(x.plan,x.get),/unresolved conflicts/);
    assert.equal(x.calls.length,0);
    const plan={schema_version:1,checkpoint_id:'checkpoint',conflicts:[],operations:[
        {uuid:'child',parent:'menu',mode:'create',definition:{BTTUUID:'child',BTTTriggerType:773,
            BTTTriggerClass:'BTTTriggerTypeFloatingMenu',BTTTriggerParentUUID:'menu'}},
        {uuid:'menu',parent:null,mode:'create',definition:{BTTUUID:'menu',BTTTriggerType:767,
            BTTTriggerClass:'BTTTriggerTypeFloatingMenu',BTTTriggerBelongsToPreset:'Master'}},
    ]};
    assert.throws(()=>context.repairSnapshot(plan,x.get),/not parent-first/);
    assert.equal(x.calls.length,0);
});

test('snapshot includes current ancestors and verify detects protected side effects',()=>{
    const x=fixture();x.records.root.BTTTriggerParentUUID='grand';
    x.records.grand={BTTUUID:'grand',BTTTriggerType:767,BTTMenuConfig:{marker:'keep'}};
    x.plan.runtime_snapshot=context.repairSnapshot(x.plan,x.get);
    assert.ok('grand' in x.plan.runtime_snapshot);
    const add=x.btt.add_new_trigger;
    x.btt.add_new_trigger=(raw,args)=>{add(raw,args);x.records.grand.BTTMenuConfig.marker='changed';};
    assert.throws(()=>context.repairApply(x.btt,x.plan,x.get,x.fp),/Protected runtime record changed/);
    assert.equal(x.calls.length,1);
});

test('compact pure-action readback accepts API defaults, JSON payload form and image externalization',()=>{
    const definition={BTTUUID:'action',BTTTriggerType:-1,
        BTTTriggerClass:'BTTTriggerTypeFloatingMenu',BTTTriggerParentUUID:'root',
        BTTEnabled:1,BTTActionCategory:0,BTTOrder:0,BTTPredefinedActionType:248,
        BTTAdditionalActionData:JSON.stringify({target:'named'}),
        BTTAppBundleIdentifier:'BT.G',BTTTriggerBelongsToPreset:'Master',
        BTTMenuConfig:{BTTMenuItemImage:'embedded-image-data'}};
    const plan={schema_version:1,checkpoint_id:'checkpoint',conflicts:[],operations:[
        {uuid:'action',parent:'root',mode:'create',definition},
    ]};
    const x=fixture(plan);plan.runtime_snapshot=context.repairSnapshot(plan,x.get);
    x.btt.add_new_trigger=(raw,args)=>{
        const sent=JSON.parse(raw);x.calls.push(['add',sent,args]);
        (x.records.root.BTTMenuItemActions ||= []).push(clone(sent));
        x.records.action={BTTUUID:'action',BTTOrder:0,BTTActionCategory:0,
            BTTPredefinedActionType:248,BTTAdditionalActionData:{target:'named'},
            BTTAPIAddedDefault:true,
            BTTMenuConfig:{BTTMenuItemIconPresetPath:'/private/externalized-image'}};
    };
    assert.doesNotThrow(()=>context.repairApply(x.btt,plan,x.get,x.fp));
    assert.equal(x.calls.length,1);
});

test('created semantic verification rejects payload and parent mismatches',()=>{
    const operation={uuid:'action',parent:'root',mode:'create',definition:{
        BTTUUID:'action',BTTTriggerType:-1,BTTTriggerClass:'BTTTriggerTypeFloatingMenu',
        BTTTriggerParentUUID:'root',BTTOrder:0,BTTActionCategory:0,
        BTTPredefinedActionType:248,BTTAdditionalActionData:{target:'expected'},
    }};
    const root={BTTUUID:'root',BTTMenuItemActions:[{BTTUUID:'action'}]};
    const get=id=>id==='root'?root:null;
    const actual={BTTUUID:'action',BTTOrder:0,BTTActionCategory:0,
        BTTPredefinedActionType:248,BTTAdditionalActionData:{target:'wrong'}};
    assert.throws(()=>context.repairCheckCreated(actual,operation,get,context.BTTCommon.fingerprint),
        /BTTAdditionalActionData/);
    actual.BTTAdditionalActionData={target:'expected'};
    actual.BTTTriggerParentUUID='different-parent';
    assert.throws(()=>context.repairCheckCreated(actual,operation,get,context.BTTCommon.fingerprint),
        /wrong parent/);
});

test('omitted trigger type is accepted only for compact pure actions',()=>{
    const operation={uuid:'item',parent:'root',mode:'create',definition:{
        BTTUUID:'item',BTTTriggerType:773,BTTTriggerClass:'BTTTriggerTypeFloatingMenu',
        BTTTriggerParentUUID:'root',BTTOrder:0,
    }};
    const parent={BTTUUID:'root',BTTMenuItems:[{BTTUUID:'item'}]};
    const actual={BTTUUID:'item',BTTOrder:0};
    assert.throws(()=>context.repairCheckCreated(actual,operation,()=>parent,
        context.BTTCommon.fingerprint),/BTTTriggerType/);
});
