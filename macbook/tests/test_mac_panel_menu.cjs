const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {execFileSync} = require('node:child_process');
const {withCommon} = require('./btt_test_support.cjs');

const directory = path.join(__dirname, '../bettertouchtool');
const code = fs.readFileSync(path.join(directory, 'install_mac_panel_menu.js'), 'utf8');
const common = withCommon('');
const desired = JSON.parse(execFileSync('/usr/bin/python3', ['-B', '-c',
    'import json; from install_mac_panel_menu import definitions; print(json.dumps(definitions("Master",35)))'],
    {cwd: directory, encoding: 'utf8'}));
const clone = value => JSON.parse(JSON.stringify(value));

function setup(options = {}) {
    const unrelated = {BTTUUID: 'protected-media', BTTTriggerType: 767,
        BTTMenuItems: [{BTTUUID: 'nested', BTTShellTaskActionScript: 'private action'}]};
    const state = {items: [unrelated], writes: [], reads: 0};
    const plan = {before: clone(state.items), desired: clone(desired),
        summary: {url: 'http://127.0.0.1:8769/'}};
    const socket = {call(command, parameters) {
        if (command === 'get_triggers') {
            state.reads++;
            if (options.beforeRead) options.beforeRead(state);
            return JSON.stringify(state.items);
        }
        assert.ok(['add_new_trigger', 'update_trigger'].includes(command));
        const item = JSON.parse(parameters.json);
        state.writes.push({command, uuid: item.BTTUUID});
        if (command === 'add_new_trigger') {
            assert.ok(!state.items.some(node => node.BTTUUID === item.BTTUUID));
            state.items.push(item);
        } else {
            assert.equal(parameters.uuid, item.BTTUUID);
            Object.assign(state.items.find(node => node.BTTUUID === item.BTTUUID), item);
        }
        if (options.afterWrite) options.afterWrite(state, item);
        return 'No mutation response text is trusted';
    }};
    const $ = value => value;
    $.NSString = {stringWithContentsOfFileEncodingError: name => {
        if (name === 'common') return common;
        if (name === 'socket') return 'var BTTSocket = mockSocket;';
        if (name === 'plan') return JSON.stringify(plan);
        throw new Error('Unexpected read');
    }};
    const context = vm.createContext({$, ObjC: {import() {}, unwrap: value => value}, mockSocket: socket});
    vm.runInContext(code, context);
    return {state, plan, unrelated, run(mode = 'apply') {
        return JSON.parse(context.run([mode, 'common', 'socket', 'plan']));
    }};
}

test('read-only runtime inspection cannot call mutations', () => {
    const env = setup();
    assert.equal(env.run('inspect').length, 1);
    assert.deepEqual(env.state.writes, []);
    assert.throws(() => env.run('invalid'), /mode required/);
    assert.deepEqual(env.state.writes, []);
});

test('adds exactly two top-level items, leaves protected trees intact, reruns as no-op', () => {
    const env = setup();
    const original = clone(env.unrelated);
    assert.equal(env.run().writes, 2);
    assert.deepEqual(env.state.writes.map(item => item.command), ['add_new_trigger', 'add_new_trigger']);
    assert.deepEqual(env.unrelated, original);
    env.plan.before = clone(env.state.items);
    assert.equal(env.run().writes, 0);
    assert.equal(env.state.items.length, 3);
});

test('existing stale item is updated in place, not duplicated', () => {
    const env = setup();
    env.state.items.push({...clone(desired[0]), BTTOpenURL: 'http://127.0.0.1:1/'});
    env.plan.before = clone(env.state.items);
    env.run();
    assert.deepEqual(env.state.writes.map(item => item.command), ['update_trigger', 'add_new_trigger']);
    assert.equal(env.state.items.filter(item => item.BTTUUID === desired[0].BTTUUID).length, 1);
});

test('duplicate runtime UUIDs and changed backup snapshots refuse before mutation', () => {
    const duplicate = setup();
    duplicate.state.items.push(clone(duplicate.unrelated));
    assert.throws(() => duplicate.run(), /Duplicate runtime UUID/);
    assert.equal(duplicate.state.writes.length, 0);
    const changed = setup();
    changed.unrelated.BTTMenuItems[0].extra = true;
    assert.throws(() => changed.run(), /changed since preview/);
    assert.equal(changed.state.writes.length, 0);
});

test('unsafe nested plan and non-global scope are refused', () => {
    for (const change of [{BTTTriggerParentUUID: 'protected-media'},
                          {BTTAppBundleIdentifier: 'some.app'},
                          {BTTMenuItems: []}, {BTTPredefinedActionType: 206}]) {
        const env = setup();
        Object.assign(env.plan.desired[0], change);
        assert.throws(() => env.run(), /Unsafe/);
        assert.equal(env.state.writes.length, 0);
    }
});

test('runtime verification rejects wrong action and unrelated changes without continuing', () => {
    const badAction = setup({afterWrite(state, item) { item.BTTOpenURL = 'wrong'; }});
    assert.throws(() => badAction.run(), /verification failed/);
    assert.equal(badAction.state.writes.length, 1);
    const outside = setup({afterWrite(state) { state.items[0].BTTMenuItems[0].extra = true; }});
    assert.throws(() => outside.run(), /outside the intended item/);
    assert.equal(outside.state.writes.length, 1);
});

test('concurrent edit before the second item stops without retrying', () => {
    const env = setup({beforeRead(state) {
        if (state.reads === 4) state.items[0].concurrent = true;
    }});
    assert.throws(() => env.run(), /before the next item/);
    assert.equal(env.state.writes.length, 1);
});

test('default enabled flags and global export omission still verify', () => {
    const env = setup({afterWrite(state, item) {
        delete item.BTTEnabled;
        delete item.BTTEnabled2;
        delete item.BTTAppBundleIdentifier;
        item.BTTLastUpdatedAt = Date.now();
    }});
    assert.equal(env.run().writes, 2);
});
