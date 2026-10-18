const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const os = require('node:os');
const {execFileSync} = require('node:child_process');
const transforms = require('../lib/text_transforms.js');
const transformCases = require('./text_transform_cases.cjs');
const directory = path.resolve(__dirname, '..');
const dependencies = {
    cases: {
        snakeCase: text => text.toLowerCase().replace(/ /g, '_'),
        kebabCase: text => text.toLowerCase().replace(/ /g, '-'),
        camelCase: text => text.toLowerCase().replace(/ (.)/g, (_, c) => c.toUpperCase())
    },
    encodeBase64: text => Buffer.from(text, 'utf8').toString('base64'),
    decodeBase64: text => new TextDecoder('utf-8', {fatal: true}).decode(Buffer.from(text, 'base64'))
};

test('Firefox bypasses the faulty selected-text setter while Notes keeps native replacement', () => {
    const context = vm.createContext({ObjC:{import() {}}});
    vm.runInContext(fs.readFileSync(path.join(directory,'text_selection.js'),'utf8'),context);
    assert.equal(context.selectionNeedsMenuPaste('org.mozilla.firefox'),true);
    assert.equal(context.selectionNeedsMenuPaste('org.whispersystems.signal-desktop'),true);
    assert.equal(context.selectionNeedsMenuPaste('com.apple.Notes'),false);
});

for (const [kind, input, expected] of transformCases) {
    test(kind + ' preserves its intended text transformation', () => {
        assert.equal(transforms.transform(kind, input, dependencies), expected);
    });
}

function uiFixture(text = 'Hello', changes = {}) {
    let reads = 0;
    const writes = [];
    const snapshot = {text, writable: true, element: 1, range: [2, text.length]};
    const ui = {
        read() { return {...snapshot, ...(reads++ ? changes : {})}; },
        same(a, b) { return JSON.stringify(a) === JSON.stringify(b); },
        write(selection, text) { writes.push(text); }
    };
    return {ui, writes};
}

test('replacement writes exactly the transformed selection once', () => {
    const {ui, writes} = uiFixture();
    assert.equal(transforms.replace('lower', ui, dependencies), 'replaced');
    assert.deepEqual(writes, ['hello']);
});

for (const changes of [{element: 2}, {range: [20, 5]}, {text: 'World'}, {writable: false}]) {
    test('changed selection/focus stops before writing ' + JSON.stringify(changes), () => {
        const {ui, writes} = uiFixture('Hello', changes);
        assert.throws(() => transforms.replace('lower', ui, dependencies));
        assert.deepEqual(writes, []);
    });
}

for (const [kind, text] of [['lower', ''], ['uri-decode', '%zz'], ['base64-decode', 'junk!'],
                           ['base64-decode', '/w==']]) {
    test('missing selection or malformed data never writes: ' + kind + ' ' + text, () => {
        const {ui, writes} = uiFixture(text);
        assert.throws(() => transforms.replace(kind, ui, dependencies));
        assert.deepEqual(writes, []);
    });
}

test('read-only selection never writes', () => {
    const {ui, writes} = uiFixture();
    ui.read = () => ({text: 'Hello', writable: false});
    assert.throws(() => transforms.replace('lower', ui, dependencies));
    assert.deepEqual(writes, []);
});

test('unchanged output avoids an unnecessary edit', () => {
    const {ui, writes} = uiFixture('hello');
    assert.equal(transforms.replace('lower', ui, dependencies), 'unchanged');
    assert.deepEqual(writes, []);
});

function installerFixture() {
    const context = vm.createContext({ObjC: {import() {}}, Map, Set});
    vm.runInContext(fs.readFileSync(path.join(directory, 'btt_common.js'), 'utf8'), context);
    vm.runInContext(fs.readFileSync(path.join(directory, 'repair_text_transforms.js'), 'utf8'), context);
    const items = vm.runInContext('TEXT_ITEMS', context);
    const menuID = vm.runInContext('TEXT_MENU', context);
    const records = new Map(), writes = [];
    const menu = {BTTUUID: menuID, BTTTriggerType: 774, BTTMenuItems: []};
    records.set(menuID, menu);
    for (const [id, kind, transformer] of items) {
        const actions = [264,129,193].map((type, index) => ({
            BTTUUID: id + '-' + index, BTTTriggerParentUUID: id, BTTOrder: index,
            BTTPredefinedActionType: type, BTTShortcutToSend: index === 0 ? '55,8' : '-1',
            ...(index === 2 ? {BTTAdditionalActionData: JSON.stringify({BTTActionCustomTextTransformer: transformer})} : {})
        }));
        actions.forEach(a => records.set(a.BTTUUID, a));
        menu.BTTMenuItems.push({BTTUUID: id, BTTTriggerType: 773, BTTMenuItemActions: actions,
            BTTMenuConfig: {BTTMenuElementIdentifier: kind, BTTMenuItemMinWidth: 42}});
    }
    const api = {call(command, args) {
        if (command === 'get_trigger') return JSON.stringify([records.get(args.uuid)]);
        assert.equal(command, 'update_trigger');
        const patch = JSON.parse(args.json);
        writes.push({uuid: args.uuid, patch});
        Object.assign(records.get(args.uuid), patch);
        return 'success';
    }};
    return {context, records, writes, api, menu};
}

test('installer updates nine flows and explicit reapplication preserves configuration', () => {
    const {context: c, api, writes, menu} = installerFixture();
    const plan = c.textPlan(api, c.BTTCommon, '/repo', '/cases.cjs', 'source');
    c.textApply(api, c.BTTCommon, plan, '/repo', '/cases.cjs');
    assert.equal(writes.length, 36);
    assert.ok(writes.slice(0,9).every(w => w.patch.BTTEnabled === 0));
    for (const item of menu.BTTMenuItems) {
        const active = item.BTTMenuItemActions.filter(a => a.BTTEnabled !== 0);
        assert.equal(active.length, 1);
        assert.equal(active[0].BTTPredefinedActionType, 281);
    }
    const next = c.textPlan(api, c.BTTCommon, '/repo', '/cases.cjs', 'source');
    c.textApply(api, c.BTTCommon, next, '/repo', '/cases.cjs');
    assert.equal(writes.length, 54);
    assert.ok(c.textPlan(api, c.BTTCommon, '/repo', '/cases.cjs').edits.every(e => e.matches));
});

test('installer rejects a concurrent edit before any write', () => {
    const {context: c, api, writes, menu} = installerFixture();
    const plan = c.textPlan(api, c.BTTCommon, '/repo', '/cases.cjs', 'source');
    menu.BTTMenuItems[0].BTTMenuItemActions[0].BTTShortcutToSend = '55,45';
    assert.throws(() => c.textApply(api, c.BTTCommon, plan, '/repo', '/cases.cjs'));
    assert.equal(writes.length, 0);
});

test('installer rejects an unfamiliar chain', () => {
    const {context: c, api, writes, menu} = installerFixture();
    menu.BTTMenuItems[0].BTTMenuItemActions[2].BTTPredefinedActionType = 118;
    assert.throws(() => c.textPlan(api, c.BTTCommon, '/repo', '/cases.cjs', 'source'));
    assert.equal(writes.length, 0);
});

test('API export aliases are accepted without writing the export-only script field', () => {
    const {context: c} = installerFixture();
    const desired = c.textActionData('lower', '/repo', '/cases.cjs');
    assert.equal(typeof desired.BTTAppleScriptString, 'string');
    assert.equal(desired.BTTScriptString, undefined);
    const exported = {...desired, BTTScriptString:desired.BTTAppleScriptString};
    delete exported.BTTAppleScriptString;
    const patch = {BTTAdditionalActionData:JSON.stringify(desired)};
    assert.ok(c.textPatchMatches({BTTAdditionalActionData:exported}, patch, c.BTTCommon));
    exported.BTTAppleScriptString = 'conflicting script';
    assert.equal(c.textPatchMatches({BTTAdditionalActionData:exported}, patch, c.BTTCommon), false);
});

test('each transform gets an isolated BTT context and older shared contexts are upgraded', () => {
    const {context: c, api, menu} = installerFixture();
    let plan = c.textPlan(api, c.BTTCommon, '/repo', '/cases.cjs');
    c.textApply(api, c.BTTCommon, plan, '/repo', '/cases.cjs');
    for (const item of menu.BTTMenuItems) {
        const action = item.BTTMenuItemActions[2];
        const data = JSON.parse(action.BTTAdditionalActionData);
        assert.equal(data.BTTActionJSRunInSeparateContext, true);
        delete data.BTTActionJSRunInSeparateContext;
        action.BTTAdditionalActionData = JSON.stringify(data);
    }
    plan = c.textPlan(api, c.BTTCommon, '/repo', '/cases.cjs');
    assert.equal(plan.edits.filter(e => !e.matches).length, 9);
    c.textApply(api, c.BTTCommon, plan, '/repo', '/cases.cjs');
    for (const item of menu.BTTMenuItems)
        assert.equal(JSON.parse(item.BTTMenuItemActions[2].BTTAdditionalActionData).BTTActionJSRunInSeparateContext, true);
});

test('installer rejects a saved plan with an out-of-scope edit', () => {
    const {context: c, api, writes} = installerFixture();
    const plan = c.textPlan(api, c.BTTCommon, '/repo', '/cases.cjs');
    plan.edits[0].before.BTTUUID = 'unrelated-action';
    assert.throws(() => c.textApply(api, c.BTTCommon, plan, '/repo', '/cases.cjs'));
    assert.equal(writes.length, 0);
});

test('silently refused copy-disable stops before any replacement is activated', () => {
    const {context: c, api, writes} = installerFixture();
    const plan = c.textPlan(api, c.BTTCommon, '/repo', '/cases.cjs');
    const refusing = {call(command, args) {
        if (command === 'update_trigger' && args.uuid === plan.edits[0].before.BTTUUID)
            return JSON.stringify({error: 'refused'});
        return api.call(command, args);
    }};
    assert.throws(() => c.textApply(refusing, c.BTTCommon, plan, '/repo', '/cases.cjs'));
    assert.equal(writes.length, 9);
    assert.ok(writes.every(w => w.patch.BTTEnabled === 0));
});

test('a fresh plan recovers after interruption in each mutation phase', () => {
    for (const failAt of [1, 10, 28, 36]) {
        const {context: c, api, menu} = installerFixture();
        const plan = c.textPlan(api, c.BTTCommon, '/repo', '/cases.cjs');
        let calls = 0;
        const interrupted = {call(command, args) {
            if (command === 'update_trigger' && ++calls === failAt) throw new Error('interrupted');
            return api.call(command, args);
        }};
        assert.throws(() => c.textApply(interrupted, c.BTTCommon, plan, '/repo', '/cases.cjs'));
        const resume = c.textPlan(api, c.BTTCommon, '/repo', '/cases.cjs');
        c.textApply(api, c.BTTCommon, resume, '/repo', '/cases.cjs');
        for (const item of menu.BTTMenuItems) {
            const active = item.BTTMenuItemActions.filter(a => a.BTTEnabled !== 0);
            assert.equal(active.length, 1, 'boundary ' + failAt);
            assert.equal(active[0].BTTPredefinedActionType, 281);
        }
    }
});

test('an exact managed script left disabled can be enabled on retry', () => {
    const {context: c, api, menu} = installerFixture();
    const plan = c.textPlan(api, c.BTTCommon, '/repo', '/cases.cjs');
    c.textApply(api, c.BTTCommon, plan, '/repo', '/cases.cjs');
    menu.BTTMenuItems[0].BTTMenuItemActions[2].BTTEnabled = 0;
    const resume = c.textPlan(api, c.BTTCommon, '/repo', '/cases.cjs');
    c.textApply(api, c.BTTCommon, resume, '/repo', '/cases.cjs');
    assert.equal(menu.BTTMenuItems[0].BTTMenuItemActions[2].BTTEnabled, 1);
});

test('emitted Core JS invokes its native JXA loader and reports runtime refusals', async () => {
    const {context: c} = installerFixture();
    const fixture = fs.mkdtempSync(path.join(os.tmpdir(), "btt-text-'quoted-"));
    const notices = [];
    try {
        fs.writeFileSync(path.join(fixture, 'text_selection.js'),
            fs.readFileSync(path.join(directory, 'text_selection.js'), 'utf8')
            + '\ntransformSelection = function(kind) { if (kind === "lower") return "replaced"; throw Error("fixture refusal"); };');
        for (const [kind, expected] of [['lower', 'replaced'], ['upper', 'fixture refusal']]) {
            const data = c.textActionData(kind, fixture, '/unused-case-library');
            const runtime = vm.createContext({
                readFile: file => fs.readFileSync(file, 'utf8'),
                runJXA: script => execFileSync('/usr/bin/osascript', ['-l', 'JavaScript', '-e', script], {encoding:'utf8'}).trim(),
                callBTT: (command, args) => { notices.push({command, text: args['']}); }
            });
            vm.runInContext(data.BTTAppleScriptString, runtime);
            assert.equal(await runtime.replaceSelectedText(), expected);
        }
        assert.deepEqual(notices, [{command:'display_notification', text:'fixture refusal'}]);
    } finally {
        fs.rmSync(fixture, {recursive:true});
    }
});

test('native replacement ranges preserve the start and use the new UTF-16 length', () => {
    const source = fs.readFileSync(path.join(directory, 'text_selection.js'), 'utf8');
    const script = source + `
        const buffer = selectionRangeBuffer();
        const original = $.NSValue.valueWithRange($.NSMakeRange(7, 3));
        original.getValueSize(buffer.data.mutableBytes, buffer.size);
        const range = $.AXValueCreate($.kAXValueCFRangeType, buffer.data.bytes);
        const output = ['SS', 'a😀SS', '', 'a'].map(function (text) {
            const replacement = replacementSelectionRange(range, text);
            if (!$.AXValueGetValue(replacement, $.kAXValueCFRangeType, buffer.data.mutableBytes))
                throw new Error('Cannot read replacement range.');
            const value = $.NSValue.valueWithBytesObjCType(buffer.data.bytes, buffer.encoding).rangeValue;
            return {location: Number(value.location), length: Number(value.length)};
        });
        JSON.stringify(output);
    `;
    const result = execFileSync('/usr/bin/osascript', ['-l', 'JavaScript', '-e', script], {encoding:'utf8'});
    assert.deepEqual(JSON.parse(result), [2,5,0,1].map(length => ({location:7, length})));
});

for (const scenario of ['selected', 'focus-changed', 'range-refused', 'focus-read-failed', 'range-read-failed']) {
    test('selection adapter handles replacement followed by reselection: ' + scenario, () => {
        const writes = [], target = {}, range = {};
        const native = value => value;
        native.CFEqual = (a, b) => a === b;
        native.AXUIElementSetAttributeValue = (element, attribute, value) => {
            writes.push({element, attribute, value});
            return attribute === 'AXSelectedTextRange' && scenario === 'range-refused' ? -1 : 0;
        };
        const context = vm.createContext({ObjC:{import() {}}, $:native});
        vm.runInContext(fs.readFileSync(path.join(directory, 'text_selection.js'), 'utf8'), context);
        context.replacementSelectionRange = () => range;
        context.selectionAttribute = (element, attribute) => {
            if ((scenario === 'focus-read-failed' && attribute === 'AXFocusedUIElement') ||
                (scenario === 'range-read-failed' && attribute === 'AXSelectedTextRange'))
                throw new Error('AX read failed.');
            return attribute === 'AXFocusedUIElement' ? (scenario === 'focus-changed' ? {} : target) : range;
        };
        const replace = () => context.writeSelection({}, {element:target, range:{}}, 'HELLO');
        if (scenario === 'selected') replace();
        else assert.throws(replace);
        assert.equal(writes[0].attribute, 'AXSelectedText');
        assert.equal(writes[0].value, 'HELLO');
        assert.equal(writes.length, ['focus-changed', 'focus-read-failed'].includes(scenario) ? 1 : 2);
        if (writes.length === 2) {
            assert.equal(writes[1].attribute, 'AXSelectedTextRange');
            assert.equal(writes[1].value, range);
        }
    });
}

test('generated JXA parses and native Foundation round-trips UTF-8 without UI access', () => {
    const source = fs.readFileSync(path.join(directory, 'text_selection.js'), 'utf8');
    const script = source + '\nselectionUI = function() { return {read:function(){return {text:"Héllo 😀",writable:true};},'
        + 'same:function(){return true;},write:function(s,t){ if(t!=="SMOpbGxvIPCfmIA=") throw Error("bad encoding"); }}; };\n'
        + 'transformSelection("base64-encode",' + JSON.stringify(directory) + ', "unused");';
    assert.equal(execFileSync('/usr/bin/osascript', ['-l', 'JavaScript', '-e', script], {encoding:'utf8'}).trim(), 'replaced');
});

test('native Foundation decoding rejects non-UTF-8 bytes before writing', () => {
    const source = fs.readFileSync(path.join(directory, 'text_selection.js'), 'utf8');
    const script = source + '\nselectionUI = function() { return {read:function(){return {text:"/w==",writable:true};},'
        + 'same:function(){return true;},write:function(){ throw Error("UNSAFE WRITE"); }}; };\n'
        + 'try { transformSelection("base64-decode",' + JSON.stringify(directory) + ', "unused"); }'
        + 'catch (error) { error.message; }';
    assert.equal(execFileSync('/usr/bin/osascript', ['-l', 'JavaScript', '-e', script], {encoding:'utf8'}).trim(),
        'Base64 does not contain UTF-8 text.');
});

test('temporary diagnostics are opt-in and exclude additional text fields', () => {
    const fixture = fs.mkdtempSync(path.join(os.tmpdir(), 'btt-text-diagnostic-'));
    const local = path.join(fixture, '.local');
    fs.mkdirSync(local, {mode:0o700});
    const output = path.join(local, 'text-transform-last-result.json');
    const source = fs.readFileSync(path.join(directory, 'text_selection.js'), 'utf8');
    const options = {directory:fixture, kind:'upper'};
    const outcome = {ok:true, result:'replaced', text:'PRIVATE FIXTURE TEXT'};
    const script = source + '\nrecordTransformOutcome(' + JSON.stringify(options) + ',' + JSON.stringify(outcome) + ');';
    try {
        execFileSync('/usr/bin/osascript', ['-l', 'JavaScript', '-e', script]);
        assert.equal(fs.existsSync(output), false);
        fs.writeFileSync(path.join(local, 'record-text-transform'), '', {mode:0o600});
        execFileSync('/usr/bin/osascript', ['-l', 'JavaScript', '-e', script]);
        const contents = fs.readFileSync(output, 'utf8');
        const report = JSON.parse(contents);
        assert.equal(report.result, 'replaced');
        assert.equal(report.kind, 'upper');
        assert.equal(contents.includes(outcome.text), false);
        assert.deepEqual(Object.keys(report).sort(), ['app','kind','ok','result','time']);
        assert.equal(fs.statSync(output).mode & 0o777, 0o600);
        const failure = source + '\nrecordTransformOutcome(' + JSON.stringify(options)
            + ', {ok:false,error:"fixture error"});';
        execFileSync('/usr/bin/osascript', ['-l', 'JavaScript', '-e', failure]);
        execFileSync('/usr/bin/osascript', ['-l', 'JavaScript', '-e', script]);
        const lastError = path.join(local, 'text-transform-last-error.json');
        assert.equal(JSON.parse(fs.readFileSync(lastError,'utf8')).error,'fixture error');
        assert.equal(JSON.parse(fs.readFileSync(output,'utf8')).result,'replaced');
        assert.equal(fs.statSync(lastError).mode & 0o777, 0o600);
    } finally {
        fs.rmSync(fixture, {recursive:true});
    }
});
