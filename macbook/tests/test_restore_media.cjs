const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const context = {ObjC: {import() {}}};
vm.createContext(context);
vm.runInContext(fs.readFileSync(path.join(__dirname, '../bettertouchtool/restore_media.js'), 'utf8'), context);
const plan = {entities: [
    {uuid: 'root', parent: null, node: {BTTUUID: 'root', BTTTriggerType: 767}},
    {uuid: 'button', parent: 'root', node: {BTTUUID: 'button', BTTTriggerType: 773}},
]};
function fake() {
    const records = {}, calls = [];
    return {records, calls,
        get_trigger(id) {return JSON.stringify(records[id] || []);},
        add_new_trigger(raw, args) {const node = JSON.parse(raw); records[node.BTTUUID] = node; calls.push([node, args]);},
        update_trigger(id, args) {Object.assign(records[id], JSON.parse(args.json));},
    };
}
test('preflight is read-only and root stays disabled until enable step', () => {
    const btt = fake();
    context.restoreStep(btt, plan, 'preflight');
    assert.equal(btt.calls.length, 0);
    context.restoreStep(btt, plan, 'root');
    assert.equal(btt.records.root.BTTEnabled, 0);
    context.restoreStep(btt, plan, 'children');
    assert.equal(btt.calls[1][1].parent_uuid, 'root');
    assert.equal(btt.records.root.BTTEnabled, 0);
    context.restoreStep(btt, plan, 'enable');
    assert.equal(btt.records.root.BTTEnabled, 1);
});
test('existing runtime menu aborts without adding anything', () => {
    const btt = fake(); btt.records.root = {BTTUUID: 'root'};
    assert.throws(() => context.restoreStep(btt, plan, 'root'), /still exists/);
    assert.equal(btt.calls.length, 0);
});
test('live enabled roots and child collisions fail closed', () => {
    const btt = fake(); btt.records.root = {BTTUUID: 'root', BTTEnabled: 1};
    assert.throws(() => context.restoreStep(btt, plan, 'children'), /disabled/);
    btt.records.root.BTTEnabled = 0; btt.records.button = {BTTUUID: 'button'};
    assert.throws(() => context.restoreStep(btt, plan, 'children'), /appeared/);
    assert.equal(btt.calls.length, 0);
});
test('error-shaped API responses are not mistaken for missing menus', () => {
    const btt = fake(); btt.get_trigger = () => JSON.stringify({error: 'unavailable'});
    assert.throws(() => context.restoreStep(btt, plan, 'root'), /Unexpected BTT readback/);
    assert.equal(btt.calls.length, 0);
});
test('resume reuses a disabled root and creates only unsaved children', () => {
    const btt = fake();
    btt.records.root = {BTTUUID: 'root', BTTTriggerType: 767, BTTEnabled: 0};
    const resume = {...plan, resume_origin: '/verified/backup', already_saved: ['root']};
    context.restoreStep(btt, resume, 'preflight');
    context.restoreStep(btt, resume, 'root');
    assert.equal(btt.calls.length, 0);
    context.restoreStep(btt, resume, 'children');
    assert.equal(btt.calls.length, 1);
    assert.equal(btt.calls[0][0].BTTUUID, 'button');
    context.restoreStep(btt, {...resume, already_saved: ['root', 'button']}, 'children');
    assert.equal(btt.calls.length, 1);
});
test('resume refuses a changed runtime root', () => {
    const btt = fake();
    btt.records.root = {BTTUUID: 'root', BTTTriggerType: 767, BTTEnabled: 0, BTTMenuConfig: {width: 400}};
    const resume = {...plan, resume_origin: '/verified/backup', already_saved: ['root'], entities: [
        {...plan.entities[0], node: {...plan.entities[0].node, BTTMenuConfig: {width: 600}}}
    ]};
    assert.throws(() => context.restoreStep(btt, resume, 'preflight'), /configuration changed/);
    assert.equal(btt.calls.length, 0);
});
test('resume accepts omitted database-only editor fields using the backup export projection', () => {
    const btt = fake();
    const exported = {BTTMenuElementIdentifier: 'Media', BTTMenuFrameWidth: 1920, BTTMenuModifierKeys: 1835008};
    btt.records.root = {BTTUUID: 'root', BTTTriggerType: 767, BTTEnabled: 0, BTTMenuConfig: {...exported}};
    const resume = {...plan, resume_origin: '/verified/backup', already_saved: ['root'],
        runtime_root_config: exported, entities: [{...plan.entities[0], node: {...plan.entities[0].node,
            BTTMenuConfig: {...exported, BTTMenuCategorySize: 1, BTTMenuItemSelectedTab: 0, BTTLastChangeUUID: 'old'}}}]};
    context.restoreStep(btt, resume, 'preflight');
    assert.equal(btt.calls.length, 0);
    btt.records.root.BTTMenuConfig.BTTMenuModifierKeys = 0;
    assert.throws(() => context.restoreStep(btt, resume, 'preflight'), /BTTMenuModifierKeys/);
    btt.records.root.BTTMenuConfig = {...exported};
    delete btt.records.root.BTTMenuConfig.BTTMenuFrameWidth;
    assert.throws(() => context.restoreStep(btt, resume, 'preflight'), /BTTMenuFrameWidth/);
    assert.equal(btt.calls.length, 0);
});
