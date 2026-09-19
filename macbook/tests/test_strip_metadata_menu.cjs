// Run: node --test macbook/tests/test_strip_metadata_menu.cjs
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const code = fs.readFileSync(path.join(__dirname, '../bettertouchtool/install_strip_metadata_menu.js'), 'utf8');
const payload = JSON.parse(fs.readFileSync(path.join(__dirname, '../build/Strip Metadata Tools.json'), 'utf8'));

function setup(overrides = {}, additional = []) {
    const parent = {BTTUUID: payload.BTTTriggerParentUUID, BTTTriggerType: 767,
                    BTTMenuName: 'Media', BTTEnabled: 1, ...overrides};
    const menus = [parent, ...additional];
    const children = new Map([[parent.BTTUUID, []]]);
    let additions = 0;
    const messages = [];
    const $ = value => ({writeToFileAtomicallyEncodingError: () => true});
    $.NSString = {stringWithContentsOfFileEncodingError: () => JSON.stringify(payload)};
    $.NSFileManager = {defaultManager: {setAttributesOfItemAtPathError: () => true}};
    const btt = {
        get_triggers(query) {
            if (query.trigger_id === 767) return JSON.stringify(menus);
            return JSON.stringify(children.get(query.trigger_parent_uuid) || []);
        },
        add_new_trigger(json, options) {
            additions++;
            const item = JSON.parse(json);
            children.get(options.parent_uuid).push(item);
            children.set(item.BTTUUID, item.BTTMenuItems || []);
            return item.BTTUUID;
        },
    };
    const context = vm.createContext({$, ObjC: {import() {}, unwrap: x => x},
                                     Application: () => btt, console: {log: x => messages.push(x)}});
    vm.runInContext(code, context);
    return {run: mode => context.run(['payload.json', '/build', mode || 'apply']),
            additions: () => additions, messages};
}

test('selects exact parent among multiple menus and adds only once', () => {
    const env = setup({}, [{BTTUUID: 'unrelated', BTTTriggerType: 767, BTTEnabled: 1}]);
    env.run();
    env.run();
    assert.equal(env.additions(), 1);
    assert.match(env.messages[0], /Verified: Media > Tools/);
});

test('accepts numeric strings and omitted enabled default', () => {
    for (const enabled of [undefined, '1', true]) {
        const env = setup({BTTTriggerType: '767', BTTEnabled: enabled});
        env.run();
        assert.equal(env.additions(), 1);
    }
});

test('disabled or mismatched parents fail before mutation with diagnostic summary', () => {
    for (const change of [{BTTEnabled: 0}, {BTTEnabled: '0'}, {BTTEnabled: false},
                           {BTTUUID: 'different'}, {BTTTriggerType: 774}]) {
        const env = setup(change);
        assert.throws(() => env.run(), /no changes made.*BTT returned:/);
        assert.equal(env.additions(), 0);
    }
});

test('inspect reports menu metadata without mutations', () => {
    const env = setup();
    env.run('inspect');
    assert.equal(env.additions(), 0);
    assert.equal(JSON.parse(env.messages[0])[0].name, 'Media');
    assert.doesNotMatch(env.messages[0], /BTTShellTaskActionScript/);
});
