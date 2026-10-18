const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {execFileSync, spawnSync} = require('node:child_process');
const {
    withCommon,
    createMediaToolsFixture,
    generateMenuDefinition,
    shellArguments,
} = require('./btt_test_support.cjs');

const folder = path.join(__dirname, '../bettertouchtool');
const fixture = createMediaToolsFixture();
fs.writeFileSync(path.join(fixture.root,
    'build/Performance Audio.app/Contents/Resources/temp_card_printer.py'),
    '# fixture resource\n');
const workerCode = fs.readFileSync(path.join(folder, 'install_tools_menu_item.js'), 'utf8');
const commonCode = withCommon('');
const modules = {
    bpm: 'install_bpm_menu',
    convert: 'install_convert_video_menu',
    rhythm: 'install_rhythm_menu',
    interval: 'install_interval_menu',
    card: 'install_card_printer_menu',
};
const payloads = Object.fromEntries(Object.entries(modules).map(([name, moduleName]) =>
    [name, generateMenuDefinition(fixture, moduleName)]));
const specs = Object.fromEntries(Object.entries(modules).map(([name, moduleName]) => {
    const source = `import json; from ${moduleName} import SPEC; print(json.dumps(SPEC.worker_options()))`;
    return [name, JSON.parse(execFileSync('/usr/bin/python3', ['-c', source], {
        cwd: folder,
        env: {...fixture.env, PYTHONDONTWRITEBYTECODE: '1'},
        encoding: 'utf8',
    }))];
}));

function clone(value) {
    return JSON.parse(JSON.stringify(value));
}

function menuFixture(payload) {
    const sibling = {BTTUUID: 'strip', BTTTriggerType: 773, BTTOrder: 3,
        BTTMenuName: 'strip-image-metadata',
        BTTMenuItemActions: [{BTTShellTaskActionScript: 'keep existing command'}]};
    const tools = {BTTUUID: payload.tools_uuid, BTTTriggerType: 774, BTTMenuName: 'image-tools',
        BTTMenuConfig: {BTTMenuAttributedText: '{\\rtf1\\fs28 Tools}', BTTMenuItemMaxHeight: 40},
        BTTMenuItems: [sibling, {BTTUUID: 'audio', BTTTriggerType: 773, BTTOrder: 7}]};
    const privateEntry = {BTTUUID: 'entry-omitted-by-filtered-api', BTTTriggerType: 774,
        BTTMenuItems: [{BTTUUID: 'nested-private-data'}]};
    const media = {BTTUUID: payload.media_uuid, BTTTriggerType: 767,
        BTTMenuItems: [tools, privateEntry]};
    return {media, tools, sibling};
}

function foundationMock(options, payload, state) {
    const $ = value => ({
        writeToFileAtomicallyEncodingError() {
            state.snapshot = JSON.parse(value);
            return !options.backupFails;
        },
    });
    $.NSString = {stringWithContentsOfFileEncodingError: requested => {
        if (requested === 'payload') return JSON.stringify(payload);
        if (requested === 'common') return commonCode;
        return state.snapshot === undefined ? null : JSON.stringify(state.snapshot);
    }};
    $.NSUUID = {UUID: {UUIDString: 'test-backup'}};
    $.NSFileManager = {defaultManager: {
        createDirectoryAtPathWithIntermediateDirectoriesAttributesError: () => !options.createFails,
        setAttributesOfItemAtPathError: () => true,
    }};
    return $;
}

function bttMock(options, payload, menu, state) {
    function afterWrite(kind) {
        if (options.afterWrite) options.afterWrite(menu, kind);
    }
    return {
        get_trigger(id) {
            assert.equal(id, payload.media_uuid);
            state.reads += 1;
            if (state.reads === 2 && options.raceMutation) options.raceMutation(menu);
            return JSON.stringify([menu.media]);
        },
        get_triggers() { throw Error('Filtered child queries must not be used'); },
        add_new_trigger(json, {parent_uuid}) {
            assert.equal(parent_uuid, menu.tools.BTTUUID);
            state.writes += 1;
            const submitted = JSON.parse(json);
            state.submittedAdds.push(clone(submitted));
            const item = clone(submitted);
            if (options.badReadback) item.BTTMenuItemActions = [];
            menu.tools.BTTMenuItems.push(item);
            afterWrite('add');
        },
        update_trigger(id, {json, trigger_parent_uuid}) {
            assert.equal(trigger_parent_uuid, menu.tools.BTTUUID);
            state.writes += 1;
            const index = menu.tools.BTTMenuItems.findIndex(item => item.BTTUUID === id);
            menu.tools.BTTMenuItems[index] = {...menu.tools.BTTMenuItems[index], ...JSON.parse(json)};
            afterWrite('update');
        },
        update_menu_item(id, {json, persist}) {
            assert.equal(persist, true);
            state.writes += 1;
            state.appearanceWrites += 1;
            menu.tools.BTTMenuItems.find(item => item.BTTUUID === id).BTTMenuConfig = JSON.parse(json);
            afterWrite('appearance');
        },
        trigger_action(json) { state.refreshes.push(JSON.parse(json)); },
    };
}

function setupFeature(name = 'bpm', options = {}) {
    const payload = clone(payloads[name]);
    const spec = clone(specs[name]);
    const menu = menuFixture(payload);
    const state = {snapshot: undefined, reads: 0, writes: 0, appearanceWrites: 0,
        submittedAdds: [], refreshes: []};
    const $ = foundationMock(options, payload, state);
    const btt = bttMock(options, payload, menu, state);
    const context = vm.createContext({$, ObjC: {import() {}, unwrap: value => value}, Application: () => btt});
    vm.runInContext(workerCode, context);
    return {
        run: (mode = 'apply', refresh = false) => context.run([
            'payload', 'common', '/build', mode, JSON.stringify(spec), refresh ? 'refresh' : '',
        ]),
        ...menu, payload, spec,
        submittedAdds: state.submittedAdds,
        refreshes: state.refreshes,
        writes: () => state.writes,
        appearanceWrites: () => state.appearanceWrites,
        snapshot: () => state.snapshot,
    };
}

function installedItem(environment) {
    return environment.tools.BTTMenuItems.find(item => item.BTTUUID === environment.payload.item.BTTUUID);
}

test('feature commands preserve executable paths and argument boundaries', () => {
    const cases = [
        ['bpm', ['/usr/bin/python3', path.join(fixture.root,
            'build/Performance Audio.app/Contents/Resources/bpm_over_time.py'),
            '--points', '60', '--finder-selection', '--background', '--open', '--notify']],
        ['convert', ['/usr/bin/python3', path.join(fixture.root,
            'build/Performance Audio.app/Contents/Resources/convert_video.py'),
            '--interactive', '--finder-selection', '--background', '--notify', '--reveal']],
        ['rhythm', ['/usr/bin/python3', path.join(fixture.root,
            'build/Performance Audio.app/Contents/Resources/rhythm_practice.py'),
            '--finder-selection', '--background', '--open', '--notify']],
        ['interval', ['/usr/bin/python3', path.join(fixture.root,
            'build/Performance Audio.app/Contents/Resources/interval_analysis.py'),
            '--background']],
        ['card', ['/usr/bin/python3', path.join(fixture.root,
            'build/Performance Audio.app/Contents/Resources/temp_card_printer.py'),
            '--finder']],
    ];
    for (const [name, expected] of cases) {
        const command = payloads[name].item.BTTMenuItemActions[0].BTTShellTaskActionScript;
        assert.deepEqual(shellArguments(command), expected, name);
    }
});

test('Convert Video requires its runtime file to name an executable', () => {
    const invalid = createMediaToolsFixture();
    const runtime = path.join(invalid.root,
        'build/Performance Audio.app/Contents/Resources/python-path.txt');
    fs.writeFileSync(runtime, '/missing/performance-audio-python\n');
    const result = spawnSync('/usr/bin/python3', ['-c',
        'from install_convert_video_menu import definition; definition()'], {
        cwd: folder,
        env: {...invalid.env, PYTHONDONTWRITEBYTECODE: '1'},
        encoding: 'utf8',
    });
    assert.notEqual(result.status, 0);
});

test('shared add backs up full Media, submits max-plus-one order, and reruns as no-op', () => {
    const environment = setupFeature();
    const before = JSON.stringify(environment.sibling);
    environment.run();
    assert.equal(environment.submittedAdds[0].BTTOrder, 8);
    assert.equal(environment.writes(), 1);
    assert.equal(JSON.stringify(environment.sibling), before);
    assert.equal(environment.snapshot().media.BTTMenuItems[1].BTTMenuItems[0].BTTUUID,
        'nested-private-data');
    environment.run();
    assert.equal(environment.writes(), 1);
});

test('shared stale-action repair uses a full update and preserves position', () => {
    const environment = setupFeature();
    environment.run();
    const item = installedItem(environment);
    item.BTTOrder = 27;
    item.BTTMenuItemActions[0].BTTShellTaskActionScript = 'old';
    environment.run();
    assert.equal(installedItem(environment).BTTOrder, 27);
    assert.equal(environment.writes(), 2);
    assert.equal(environment.appearanceWrites(), 0);
});

test('shared preflight rejects backup failure, full-Media races, and collisions', () => {
    const backupFailure = setupFeature('bpm', {backupFails: true});
    assert.throws(() => backupFailure.run());
    assert.equal(backupFailure.writes(), 0);
    const race = setupFeature('bpm', {raceMutation: ({media}) => {
        media.BTTMenuItems[1].BTTMenuItems[0].state = 'changed';
    }});
    assert.throws(() => race.run());
    assert.equal(race.writes(), 0);
    for (const collision of [
        {BTTUUID: 'foreign-name', BTTMenuName: specs.bpm.menuName},
        {BTTUUID: 'foreign-id', BTTMenuConfig: {BTTMenuElementIdentifier: specs.bpm.identifier}},
    ]) {
        const environment = setupFeature();
        environment.tools.BTTMenuItems.push(collision);
        assert.throws(() => environment.run());
        assert.equal(environment.writes(), 0);
    }
});

test('shared readback rejects bad owned data, sibling order, and outside changes', () => {
    const badOwned = setupFeature('bpm', {badReadback: true});
    assert.throws(() => badOwned.run());
    const reordered = setupFeature('bpm', {
        afterWrite: ({sibling}) => { sibling.BTTOrder = 99; },
    });
    assert.throws(() => reordered.run());
    const outside = setupFeature('bpm', {
        afterWrite: ({media}) => { media.BTTMenuItems[1].unexpected = true; },
    });
    assert.throws(() => outside.run());
});

test('shared inspect includes desired appearance without exposing commands or mutating', () => {
    const environment = setupFeature();
    const output = environment.run('inspect');
    const result = JSON.parse(output);
    assert.equal(result.installed, false);
    assert.doesNotMatch(output, /keep existing command/);
    assert.equal(environment.writes(), 0);
});

test('shared appearance repair preserves actions and position', () => {
    const fallback = setupFeature();
    fallback.run();
    const item = installedItem(fallback);
    const actions = JSON.stringify(item.BTTMenuItemActions);
    item.BTTMenuConfig.BTTMenuAttributedText = '{\\rtf1\\fs50 BPM Over Time}';
    item.BTTMenuConfig.BTTMenuItemVisibleWhileInactive = 0;
    fallback.run();
    assert.equal(fallback.appearanceWrites(), 1);
    assert.equal(item.BTTMenuConfig.BTTMenuItemVisibleWhileInactive, 1);
    assert.equal(JSON.stringify(item.BTTMenuItemActions), actions);
    assert.equal(item.BTTOrder, 8);
});

test('shared refresh releases and reopens only Media on an otherwise no-op apply', () => {
    const environment = setupFeature();
    environment.run();
    environment.run('apply', true);
    assert.equal(environment.writes(), 1);
    assert.deepEqual(environment.refreshes.map(action => action.BTTPredefinedActionType), [387, 386]);
    assert.ok(environment.refreshes.every(action =>
        action.BTTAdditionalActionData.BTTMenuActionMenuID === environment.payload.media_uuid));
    assert.equal(environment.refreshes[0].BTTAdditionalActionData.BTTMenuActionReleaseFromMemory, 1);
    assert.equal(environment.refreshes[0].BTTAdditionalActionData.BTTMenuActionTriggerHoveredOnHide, 0);
});
