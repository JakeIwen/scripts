// node --test macbook/tests/test_performance_audio_menu.cjs
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {execFileSync} = require('node:child_process');
const folder = path.join(__dirname, '../bettertouchtool');
execFileSync('/usr/bin/python3', [path.join(folder, 'install_performance_audio_menu.py')]);
const payload = JSON.parse(fs.readFileSync(path.join(__dirname, '../build/Performance Audio Tools.json')));
const code = fs.readFileSync(path.join(folder, 'install_performance_audio_menu.js'), 'utf8');

function setup({legacy = true, backup = true, badDropdown = false, badLauncher = false,
                omitDisabled = false, omitEnabledFields = false, ignoreRetirement = false} = {}) {
    const parent = {BTTUUID: payload.parent, BTTTriggerType: '767', BTTMenuName: 'Media',
        BTTMenuConfig: {BTTMenuLayoutDirection: 7, BTTMenuFrameWidth: 3736}};
    // Actual exports call Tools "image-tools" and omit default enabled fields.
    const tools = {BTTUUID: payload.tools_uuid, BTTTriggerType: '774', BTTMenuName: 'image-tools'};
    const strip = {BTTUUID: 'strip-metadata', BTTTriggerType: 773, BTTMenuName: 'strip-image-metadata',
        BTTMenuItemActions: [{BTTShellTaskActionScript: 'original-strip-command'}]};
    const old = {BTTUUID: payload.legacy_uuid, BTTTriggerType: 774, BTTMenuName: 'performance-audio-tools',
        BTTOrder: 3, BTTMenuConfig: {BTTMenuLayoutDirection: 6}};
    const oldItems = [{BTTUUID: 'existing-preset', BTTTriggerType: 773, BTTMenuName: 'My custom setting'}];
    const menus = [{BTTUUID: 'unrelated', BTTTriggerType: 767}, parent];
    const children = new Map([[parent.BTTUUID, [tools]], [tools.BTTUUID, legacy ? [strip, old] : [strip]],
                              [old.BTTUUID, oldItems]]);
    const snapshots = [], messages = [], writes = [];
    const $ = value => ({writeToFileAtomicallyEncodingError() {
        snapshots.push(JSON.parse(value)); return backup;
    }});
    $.NSString = {stringWithContentsOfFileEncodingError: () => JSON.stringify(payload)};
    $.NSFileManager = {defaultManager: {setAttributesOfItemAtPathError: () => true}};
    const btt = {
        get_triggers(query) {
            let result = query.trigger_id === 767 ? menus : children.get(query.trigger_parent_uuid) || [];
            if (omitDisabled) result = result.filter(x => x.BTTEnabled !== 0 && x.BTTEnabled2 !== 0);
            if (omitEnabledFields) result = result.map(x => {
                const copy = {...x}; delete copy.BTTEnabled; delete copy.BTTEnabled2; return copy;
            });
            return JSON.stringify(result);
        },
        add_new_trigger(json, options) {
            const item = JSON.parse(json);
            writes.push(['add', item.BTTUUID, options && options.parent_uuid]);
            if (options) children.get(options.parent_uuid).push(item);
            else menus.push(item);
            children.set(item.BTTUUID, item.BTTMenuItems || []);
            if (badDropdown && item.BTTUUID === payload.dropdown.BTTUUID) item.BTTMenuConfig.BTTMenuLayoutDirection = 7;
            if (badLauncher && item.BTTUUID === payload.launcher.BTTUUID) item.BTTMenuItemActions[0].BTTAdditionalActionData.BTTMenuActionMenuID = 'wrong';
        },
        update_trigger(id, {json}) {
            writes.push(['update', id]);
            if (ignoreRetirement && id === payload.legacy_uuid) return;
            for (const items of [menus, ...children.values()]) {
                const i = items.findIndex(item => item.BTTUUID === id);
                if (i !== -1) items[i] = {...items[i], ...JSON.parse(json)};
            }
        },
    };
    const context = vm.createContext({$, ObjC: {import() {}, unwrap: x => x}, Application: () => btt,
                                     console: {log: x => messages.push(x)}});
    vm.runInContext(code, context);
    return {run: mode => context.run(['payload.json', '/build', mode || 'apply']),
            children, menus, parent, tools, strip, oldItems, writes, snapshots, messages};
}

test('migrates inherited submenu to real top-level vertical menu and preserves old contents', () => {
    const env = setup();
    const parentBefore = JSON.stringify(env.parent), stripBefore = JSON.stringify(env.strip);
    const oldBefore = JSON.stringify(env.oldItems);
    env.run();
    const root = env.menus.find(x => x.BTTUUID === payload.dropdown.BTTUUID);
    assert.equal(root.BTTTriggerType, 767);
    assert.equal(root.BTTTriggerParentUUID, undefined);
    assert.equal(root.BTTAppBundleIdentifier, 'BT.G');
    assert.equal(root.BTTMenuConfig.BTTMenuLayoutDirection, 6);
    assert.equal(root.BTTMenuConfig.BTTMenuFrameWidth, 350);
    const items = env.children.get(env.tools.BTTUUID);
    const launcher = items.find(x => x.BTTUUID === payload.launcher.BTTUUID);
    assert.equal(launcher.BTTTriggerType, 773);
    assert.equal(launcher.BTTMenuItemActions[0].BTTAdditionalActionData.BTTMenuActionMenuID, root.BTTUUID);
    assert.equal(launcher.BTTOrder, 3);
    assert.equal(items.find(x => x.BTTUUID === payload.legacy_uuid).BTTEnabled, 0);
    assert.equal(JSON.stringify(env.oldItems), oldBefore);
    assert.equal(JSON.stringify(env.strip), stripBefore);
    assert.equal(JSON.stringify(env.parent), parentBefore);
    assert.deepEqual(env.writes.map(x => x[0]), ['add', 'add', 'update']);
    assert.equal(env.writes[0][2], undefined, 'dropdown must not be a child of Media or Tools');
    assert.match(env.messages[0], /Verified configuration.*separate vertical dropdown/);
    assert.deepEqual(env.snapshots[0].legacyItems, env.oldItems);
    env.run();
    assert.equal(env.writes.length, 3, 'rerun must not duplicate or rewrite working entries');
});

test('fresh install needs no previous submenu or standalone preset import', () => {
    const env = setup({legacy: false});
    env.run();
    assert.equal(env.writes.length, 2);
    assert.equal(env.children.get(payload.dropdown.BTTUUID).length, payload.dropdown.BTTMenuItems.length);
});

test('updates stale owned actions and layout while preserving custom items', () => {
    const env = setup(); env.run();
    const root = env.menus.find(x => x.BTTUUID === payload.dropdown.BTTUUID);
    root.BTTMenuConfig.BTTMenuLayoutDirection = 7;
    root.BTTMenuConfig.customAppearance = 'preserve';
    const items = env.children.get(root.BTTUUID);
    items[0].BTTMenuItemActions[0].BTTShellTaskActionScript = 'old';
    items[0].BTTOrder = 42;
    const missing = items.pop();
    items.push({BTTUUID:'user-custom', BTTTriggerType:773});
    env.run();
    const actual = env.children.get(root.BTTUUID);
    assert.equal(actual[0].BTTOrder, 42);
    assert.notEqual(actual[0].BTTMenuItemActions[0].BTTShellTaskActionScript, 'old');
    assert.ok(actual.some(x => x.BTTUUID === missing.BTTUUID));
    assert.ok(actual.some(x => x.BTTUUID === 'user-custom'));
    assert.equal(env.menus.find(x => x.BTTUUID === root.BTTUUID).BTTMenuConfig.customAppearance, 'preserve');
});

test('failed verification leaves old submenu enabled', () => {
    for (const options of [{badDropdown:true}, {badLauncher:true}]) {
        const env = setup(options);
        assert.throws(() => env.run(), /verification failed; old submenu remains available/);
        assert.notEqual(env.children.get(env.tools.BTTUUID).find(x => x.BTTUUID === payload.legacy_uuid).BTTEnabled, 0);
        assert.ok(!env.writes.some(x => x[0] === 'update' && x[1] === payload.legacy_uuid));
    }
});

test('backup failure or ambiguous Tools refuses mutations', () => {
    const env = setup({backup:false});
    assert.throws(() => env.run(), /private backup; no changes/);
    assert.equal(env.writes.length, 0);
    const ambiguous = setup();
    ambiguous.children.get(payload.parent).push({BTTUUID:'other-tools',BTTMenuName:'Tools',BTTTriggerType:774});
    assert.throws(() => ambiguous.run(), /exactly one enabled Tools/);
    assert.equal(ambiguous.writes.length, 0);
});

test('unknown Performance Audio item refuses replacement', () => {
    const env = setup();
    env.children.get(env.tools.BTTUUID).push({BTTUUID:'foreign',BTTMenuName:'Performance Audio',BTTTriggerType:773});
    assert.throws(() => env.run(), /Conflicting/);
    assert.equal(env.writes.length, 0);
});

test('inspect works without matching Tools and reveals no command payloads', () => {
    const env = setup();
    env.children.set(payload.parent, []);
    env.run('inspect');
    const info = JSON.parse(env.messages[0]);
    assert.equal(info.tools, null);
    assert.doesNotMatch(env.messages[0], /original-strip-command/);
    assert.equal(env.snapshots.length, 0);
    assert.equal(env.writes.length, 0);
});

test('exported display names are not required for idempotent verification', () => {
    const env = setup(); env.run();
    const launcher = env.children.get(env.tools.BTTUUID).find(x => x.BTTUUID === payload.launcher.BTTUUID);
    launcher.BTTMenuName = 'performance-audio-tools-launcher';
    for (const item of env.children.get(payload.dropdown.BTTUUID)) item.BTTMenuName = item.BTTMenuConfig.BTTMenuElementIdentifier;
    env.run();
    assert.equal(env.writes.length, 3);
});

test('retirement succeeds when BTT omits disabled items from parent queries', () => {
    const env = setup({omitDisabled: true});
    env.run();
    assert.equal(env.children.get(env.tools.BTTUUID).find(x => x.BTTUUID === payload.legacy_uuid).BTTEnabled, 0);
    assert.ok(env.children.has(payload.legacy_uuid), 'old children remain saved');
    env.run();
    assert.equal(env.writes.length, 3);
});

test('retirement is verified through visibility when BTT omits enabled flags', () => {
    const env = setup({omitEnabledFields: true});
    env.run();
    const old = env.children.get(env.tools.BTTUUID).find(x => x.BTTUUID === payload.legacy_uuid);
    assert.equal(old.BTTMenuConfig.BTTMenuItemVisibleWhileActive, 0);
    assert.equal(old.BTTMenuConfig.BTTMenuItemVisibleWhileInactive, 0);
    env.run();
    assert.equal(env.writes.length, 3);
});

test('still rejects a retirement update that leaves old submenu visible', () => {
    const env = setup({ignoreRetirement: true});
    assert.throws(() => env.run(), /Could not hide the old submenu/);
    assert.equal(env.children.get(payload.dropdown.BTTUUID).length, payload.dropdown.BTTMenuItems.length);
});
