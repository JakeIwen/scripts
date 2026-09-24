const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const context = {ObjC: {import() {}}};
vm.createContext(context);
vm.runInContext(fs.readFileSync(path.join(__dirname,'../bettertouchtool/repair_script_paths.js'),'utf8'),context);
vm.runInContext(fs.readFileSync(path.join(__dirname,'../bettertouchtool/btt_common.js'),'utf8'),context);
function fixture() {
    const records = {
        a: {BTTUUID: 'a', BTTPredefinedActionType: 206, BTTShellTaskActionScript: '/old/sns.sh noise',
            BTTTriggerParentUUID: 'button', BTTOrder: 0, BTTShellTaskActionConfig: 'untouched'},
        b: {BTTUUID: 'b', BTTPredefinedActionType: 246, BTTTerminalCommand: '/old/wake.py', BTTOrder: 2},
    }, calls = [];
    const btt = {
        get_trigger(uuid) {return JSON.stringify(records[uuid]);},
        update_trigger(uuid,args) {calls.push([uuid,JSON.parse(args.json)]); Object.assign(records[uuid],JSON.parse(args.json));},
    };
    const plan = {targets: [
        {uuid:'a',action:206,field:'BTTShellTaskActionScript',before:'/old/sns.sh noise',after:'/new/sns.sh noise'},
        {uuid:'b',action:246,field:'BTTTerminalCommand',before:'/old/wake.py',after:'/new/wake.py'},
    ]};
    plan.snapshot = context.pathSnapshot(btt,plan);
    return {btt,plan,calls,records};
}
test('patches individual commands only and preserves arguments/structure', () => {
    const {btt,plan,calls,records} = fixture();
    context.pathApply(btt,plan,context.BTTCommon.fingerprint);
    assert.deepEqual(calls,[['a',{BTTShellTaskActionScript:'/new/sns.sh noise'}],['b',{BTTTerminalCommand:'/new/wake.py'}]]);
    assert.equal(records.a.BTTTriggerParentUUID,'button');
    assert.equal(records.a.BTTShellTaskActionConfig,'untouched');
    assert.equal(records.b.BTTOrder,2);
});
test('concurrent change to the last target prevents ALL writes', () => {
    const {btt,plan,calls,records} = fixture(); records.b.BTTOrder=4;
    assert.throws(()=>context.pathApply(btt,plan,context.BTTCommon.fingerprint),/changed during backup/);
    assert.equal(calls.length,0);
});
test('already-updated runtime commands are verified but not rewritten', () => {
    const {btt,plan,calls,records} = fixture();
    records.a.BTTShellTaskActionScript=plan.targets[0].after;
    records.b.BTTTerminalCommand=plan.targets[1].after;
    plan.snapshot=context.pathSnapshot(btt,plan);
    context.pathApply(btt,plan,context.BTTCommon.fingerprint);
    assert.equal(calls.length,0);
});
test('wrong runtime action or command fails before snapshot acceptance', () => {
    const {btt,plan,records}=fixture();records.a.BTTShellTaskActionScript='/custom/script';
    assert.throws(()=>context.pathSnapshot(btt,plan),/Action changed/);
    records.a.BTTShellTaskActionScript=plan.targets[0].before;records.a.BTTPredefinedActionType=172;
    assert.throws(()=>context.pathSnapshot(btt,plan),/Action changed/);
});
test('unexpected update side effects are reported rather than ignored', () => {
    const {btt,plan,records}=fixture();const update=btt.update_trigger;
    btt.update_trigger=(uuid,args)=>{update(uuid,args);records[uuid].BTTOrder=99;};
    assert.throws(()=>context.pathApply(btt,plan,context.BTTCommon.fingerprint),/readback differs/);
});
