const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {execFileSync} = require('node:child_process');
const {withCommon, shellArguments} = require('./btt_test_support.cjs');

const folder = path.join(__dirname, '../bettertouchtool');
const workerCode = fs.readFileSync(path.join(folder, 'media_submenu_install.js'), 'utf8');
const commonCode = withCommon('');
const payload = JSON.parse(execFileSync('/usr/bin/python3', ['-c',
    'import json; from install_lighting_menu import definition; print(json.dumps(definition()))'], {
    cwd: folder, env: {...process.env, PYTHONDONTWRITEBYTECODE: '1'}, encoding: 'utf8',
}));
const [SUBMENU, BACK, SOLDER, COLOUR, PANEL, VIEW] = payload.items.map(item => item.BTTUUID);
const item = id => payload.items.find(candidate => candidate.BTTUUID === id);

function clone(value) {
    return JSON.parse(JSON.stringify(value));
}

function setup(options = {}) {
    const input = Object.assign(clone(payload), {menu_order: 4, unlinked_actions: []}, options.payload || {});
    const media = {BTTUUID: payload.media_uuid, BTTTriggerType: 767, BTTMenuItems: [
        {BTTUUID: 'moon', BTTTriggerType: 773, BTTOrder: 16, BTTMenuName: '🌙',
         BTTMenuConfig: {BTTMenuElementIdentifier: '🌙'}},
        {BTTUUID: 'notes', BTTTriggerType: 774, BTTOrder: 28, BTTMenuName: 'Notes',
         BTTMenuItems: [{BTTUUID: 'private-note'}]},
    ]};
    const topLevel = [];
    if (options.prepare) options.prepare(media, topLevel);
    const state = {snapshot: undefined, mediaReads: 0, writes: [], refreshes: 0, actions: 0};
    // Like BTT: an item takes its identifier as its name, and a new action gets a fresh UUID.
    const save = item => {
        item.BTTMenuName = (item.BTTMenuConfig || {}).BTTMenuElementIdentifier;
        for (const action of item.BTTMenuItemActions || []) {
            action.BTTUUID = action.BTTUUID || 'saved-action-' + ++state.actions;
        }
        return item;
    };
    const find = id => (function walk(items) {
        for (const item of items || []) {
            if (item.BTTUUID === id) return item;
            const nested = walk(item.BTTMenuItems);
            if (nested) return nested;
        }
    })([media, ...topLevel]);
    const btt = {
        get_trigger(id) {
            if (id !== payload.media_uuid) return JSON.stringify(topLevel.filter(menu => menu.BTTUUID === id)[0] || {});
            state.mediaReads += 1;
            if (state.mediaReads === 2 && options.race) options.race(media);
            return JSON.stringify([media]);
        },
        add_new_trigger(json, {parent_uuid} = {}) {
            if (options.addFails) throw Error('BTT refused the trigger');
            const item = JSON.parse(json);
            if (parent_uuid === undefined) {
                assert.ok(!('BTTTriggerParentUUID' in item));
                state.writes.push(['add', item.BTTUUID, null, item.BTTOrder]);
                topLevel.push(save(item));
                return;
            }
            const parent = find(parent_uuid);
            if (item.BTTTriggerType === -1) {
                item.BTTUUID = item.BTTUUID || 'saved-action-' + ++state.actions;
                state.writes.push(['add action', item.BTTUUID, parent_uuid]);
                parent.BTTMenuItemActions = [...(parent.BTTMenuItemActions || []), item];
                return;
            }
            save(item);
            state.writes.push(['add', item.BTTUUID, parent_uuid, item.BTTOrder]);
            if (options.dropActions) delete item.BTTMenuItemActions;
            parent.BTTMenuItems = parent.BTTMenuItems || [];
            parent.BTTMenuItems.push(item);
        },
        update_trigger() { throw Error('a full update would unlink new actions from the preset'); },
        update_menu_item(id, {json, persist}) {
            assert.equal(persist, true);
            state.writes.push(['style', id]);
            save(Object.assign(find(id), {BTTMenuConfig: JSON.parse(json)}));
        },
        delete_trigger(id) {
            state.writes.push(['delete', id]);
            (function remove(node) {
                for (const key of ['BTTMenuItems', 'BTTMenuItemActions']) {
                    node[key] = node[key] && node[key].filter(item => item.BTTUUID !== id);
                    for (const item of node[key] || []) remove(item);
                }
            })({BTTMenuItems: [media, ...topLevel]});
        },
        trigger_action() { state.refreshes += 1; },
    };
    const $ = value => ({writeToFileAtomicallyEncodingError() {
        state.snapshot = JSON.parse(value);
        return true;
    }});
    $.NSString = {stringWithContentsOfFileEncodingError: requested => {
        if (requested === 'payload') return JSON.stringify(input);
        if (requested === 'common') return commonCode;
        return JSON.stringify(state.snapshot);
    }};
    $.NSUUID = {UUID: {UUIDString: 'test'}};
    $.NSFileManager = {defaultManager: {
        createDirectoryAtPathWithIntermediateDirectoriesAttributesError: () => true,
        setAttributesOfItemAtPathError: () => true,
    }};
    const context = vm.createContext({$, ObjC: {import() {}, unwrap: value => value},
        Application: () => btt});
    vm.runInContext(workerCode, context);
    return {media, topLevel, state, find,
        run: (mode = 'apply', refresh = false) => context.run(
            ['payload', 'common', '/build', mode, refresh ? 'refresh' : ''])};
}

test('the Solder SW button runs the lighting toggle for the solder power switch', () => {
    const command = item(SOLDER).BTTMenuItemActions[0].BTTShellTaskActionScript;
    assert.deepEqual(shellArguments(command), ['/usr/bin/python3', '-B',
        path.resolve(__dirname, '../scripts/van_lighting.py'), 'toggle', 'switch.solder_flood']);
    // Same two-line form as the Media bar's Screen Cap button, not a larger button.
    const label = item(SOLDER).BTTMenuConfig;
    assert.match(label.BTTMenuAttributedText, /Helvetica;[\s\S]*\\fs28 \\cf2 Solder\\uc0\\u8232 SW}$/);
    assert.deepEqual([label.BTTMenuItemText, label.BTTMenuItemMinWidth, label.BTTMenuItemMaxWidth,
        label.BTTMenuItemMaxHeight], ['Solder\nSW', 54, 78, 40]);
    assert.match(item(SUBMENU).BTTMenuConfig.BTTMenuAttributedText, /\\u-10179\?\\u-9055\?}$/);
});

test('the colour button shows the dropdown whose web view keeps BTT\'s script bridge', () => {
    const action = item(COLOUR).BTTMenuItemActions[0];
    assert.equal(action.BTTPredefinedActionType, 386);
    assert.equal(action.BTTAdditionalActionData.BTTMenuActionMenuID, PANEL);
    assert.equal(item(PANEL).BTTTriggerParentUUID, null);
    assert.equal(item(PANEL).BTTMenuConfig.BTTMenuVisibility, 1);
    assert.equal(item(VIEW).BTTTriggerType, 778);
    assert.equal(item(VIEW).BTTMenuConfig.BTTMenuItemWebViewPlain, 0);
    assert.match(item(VIEW).BTTMenuConfig.BTTMenuItemText, /^<!doctype html>/i);
});

test('apply appends the submenu and the dropdown, then adds their items inside them', () => {
    const environment = setup();
    const result = JSON.parse(environment.run());
    assert.deepEqual(environment.state.writes, [
        ['add', SUBMENU, payload.media_uuid, 29],
        ['add', BACK, SUBMENU, 0],
        ['add', SOLDER, SUBMENU, 1],
        ['add', COLOUR, SUBMENU, 2],
        ['add', PANEL, null, 4],
        ['add', VIEW, PANEL, 0],
    ]);
    assert.deepEqual(result.touched_uuids.sort(),
        [SUBMENU, BACK, SOLDER, COLOUR, PANEL, VIEW, 'saved-action-1', 'saved-action-2'].sort());
    assert.equal(result.wrote, true);
    assert.deepEqual(result.saved_plan.entities.map(entity => [entity.uuid, entity.parent]), [
        [SUBMENU, payload.media_uuid], [BACK, SUBMENU], [SOLDER, SUBMENU], ['saved-action-1', SOLDER],
        [COLOUR, SUBMENU], ['saved-action-2', COLOUR], [PANEL, null], [VIEW, PANEL]]);
    assert.equal(environment.state.snapshot.trees[0].BTTMenuItems[1].BTTMenuItems[0].BTTUUID, 'private-note');
    assert.equal(environment.state.refreshes, 0);

    const again = JSON.parse(environment.run('apply', true));
    assert.equal(again.wrote, false);
    assert.equal(environment.state.writes.length, 6);
    assert.equal(environment.state.refreshes, 2);
});

test('a changed page restyles only the web view and a retargeted button gets a new action', () => {
    const environment = setup();
    environment.run();
    environment.find(VIEW).BTTMenuConfig.BTTMenuItemText = '<!doctype html><p>old page</p>';
    environment.find(COLOUR).BTTMenuItemActions[0].BTTAdditionalActionData = JSON.stringify(
        {BTTMenuActionMenuID: 'some-other-menu'});
    environment.topLevel[0].BTTMenuItems.push({BTTUUID: 'hand-added-to-panel', BTTTriggerType: 773});
    environment.run();
    assert.deepEqual(environment.state.writes.slice(6), [
        ['add action', 'saved-action-3', COLOUR], ['delete', 'saved-action-2'], ['style', VIEW]]);
    assert.ok(environment.find('hand-added-to-panel'));
});

test('rerun keeps a hand reorder and foreign submenu items while repairing a stale action', () => {
    const environment = setup();
    environment.run();
    const submenu = environment.find(SUBMENU);
    submenu.BTTOrder = 5;
    submenu.BTTMenuItems.push({BTTUUID: 'hand-added', BTTTriggerType: 773, BTTOrder: 2});
    environment.find(SOLDER).BTTOrder = 3;
    environment.find(SOLDER).BTTMenuItemActions[0].BTTShellTaskActionScript = 'old command';
    const inspected = JSON.parse(environment.run('inspect'));
    assert.deepEqual(inspected.pending.map(step => [step.uuid, step.change]), [[SOLDER, 'actions']]);
    const repaired = JSON.parse(environment.run());
    assert.deepEqual(environment.state.writes.slice(6), [
        ['add action', 'saved-action-3', SOLDER], ['delete', 'saved-action-1']]);
    assert.deepEqual(repaired.touched_uuids.sort(), [SOLDER, 'saved-action-1', 'saved-action-3'].sort());
    assert.deepEqual(environment.find(SOLDER).BTTMenuItemActions.map(action => action.BTTUUID), ['saved-action-3']);
    assert.equal(environment.find(SUBMENU).BTTOrder, 5);
    assert.equal(environment.find(SOLDER).BTTOrder, 3);
    assert.ok(environment.find('hand-added'));
});

test('an action saved without its preset link is replaced even when it looks right', () => {
    const first = setup();
    first.run();
    const environment = setup({payload: {unlinked_actions: ['saved-action-1']},
        prepare: (media, topLevel) => {
            media.BTTMenuItems.push(clone(first.find(SUBMENU)));
            topLevel.push(clone(first.topLevel[0]));
        }});
    environment.state.actions = 2;
    const repaired = JSON.parse(environment.run());
    assert.deepEqual(environment.state.writes, [['add action', 'saved-action-3', SOLDER],
        ['delete', 'saved-action-1']]);
    assert.deepEqual(repaired.touched_uuids.sort(), [SOLDER, 'saved-action-1', 'saved-action-3'].sort());
});

test('a restyle of the submenu changes only its config', () => {
    const environment = setup();
    environment.run();
    environment.find(SUBMENU).BTTMenuConfig.BTTMenuItemMaxWidth = 90;
    environment.run();
    assert.deepEqual(environment.state.writes.at(-1), ['style', SUBMENU]);
    assert.equal(environment.find(SUBMENU).BTTMenuItems.length, 3);
});

test('refuses without writing when Lighting is taken, moved, disabled or changes during backup', () => {
    const installed = setup();
    installed.run();
    const disabled = clone(installed.find(SUBMENU));
    disabled.BTTMenuItems[1].BTTEnabled = 0;
    const cases = [
        [{prepare: media => media.BTTMenuItems.push(disabled)}, /is disabled or has another type/],
        [{prepare: media => media.BTTMenuItems.push({BTTUUID: 'other', BTTTriggerType: 774,
            BTTMenuConfig: {BTTMenuElementIdentifier: 'Lighting'}})}, /already named Lighting/],
        [{prepare: media => media.BTTMenuItems[1].BTTMenuItems.push({BTTUUID: SOLDER})}, /moved elsewhere/],
        [{race: media => media.BTTMenuItems.push({BTTUUID: 'new'})}, /changed during backup/],
    ];
    for (const [options, message] of cases) {
        const environment = setup(options);
        assert.throws(() => environment.run(), message);
        assert.deepEqual(environment.state.writes, []);
    }
});

test('a readback that lost the action fails verification', () => {
    const environment = setup({dropActions: true});
    assert.throws(() => environment.run(), /verification failed. Backup: \/build\/btt-lighting-menu-backup-test/);
});

test('a BTT error during the writes names the backup', () => {
    const environment = setup({addFails: true});
    assert.throws(() => environment.run(), /^Error: BTT refused the trigger Backup: \/build\/btt-lighting-menu-backup-test/);
});
