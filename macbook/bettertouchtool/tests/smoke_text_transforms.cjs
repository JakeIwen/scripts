// Explicit opt-in: creates temporary BTT triggers, never edits user selections.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const vm = require('node:vm');
const {randomUUID} = require('node:crypto');
const {execFileSync} = require('node:child_process');
const cases = require('./text_transform_cases.cjs');
const directory = path.resolve(__dirname, '..');
const cli = '/Applications/BetterTouchTool.app/Contents/SharedSupport/bin/bttcli';

function btt(command, parameters = {}) {
    return execFileSync(cli, [command, ...Object.entries(parameters).map(([key, value]) => key + '=' + value)],
        {encoding:'utf8', timeout:15000}).trim();
}

function getTrigger(id) {
    const value = JSON.parse(btt('get_trigger', {uuid:id}));
    return (Array.isArray(value) ? value : [value]).find(item => item && item.BTTUUID === id);
}

function writeFixture(fixture) {
    fs.mkdirSync(path.join(fixture, 'lib'));
    fs.copyFileSync(path.join(directory, 'lib/text_transforms.js'), path.join(fixture, 'lib/text_transforms.js'));
    const samples = Object.fromEntries(cases.map(([kind, input, expected]) => [kind, {input, expected}]));
    const wrapper = `
(function () {
    const actualRun = runTextTransform;
    const samples = ${JSON.stringify(samples)};
    runTextTransform = function (options) {
        const sample = samples[options.kind];
        let writes = 0;
        selectionUI = function () {
            return {
                read: function () { return {text:sample.input, writable:true}; },
                same: function () { return true; },
                write: function (selection, text) {
                    if (text !== sample.expected) throw new Error('Incorrect fixture output.');
                    writes++;
                }
            };
        };
        const result = JSON.parse(actualRun(options));
        if (!result.ok || writes !== 1) throw new Error('Fixture transform failed.');
        return JSON.stringify({ok:true, result:options.kind});
    };
})();
`;
    fs.writeFileSync(path.join(fixture, 'text_selection.js'),
        fs.readFileSync(path.join(directory, 'text_selection.js'), 'utf8') + wrapper);
}

function createPair(entry, data) {
    btt('add_new_trigger', {json:JSON.stringify({BTTUUID:entry.root, BTTTriggerType:643,
        BTTTriggerClass:'BTTTriggerTypeOther', BTTTriggerName:'Temporary text smoke ' + entry.root,
        BTTPredefinedActionType:366})});
    btt('add_new_trigger', {parent_uuid:entry.root, json:JSON.stringify({BTTUUID:entry.child,
        BTTTriggerType:-1, BTTIsPureAction:true, BTTTriggerParentUUID:entry.root, BTTOrder:0,
        BTTPredefinedActionType:281, BTTAdditionalActionData:{BTTScriptType:3,
            BTTScriptFunctionToCall:'probe', BTTAppleScriptString:'async function probe(){return "initial";}'}})});
    assert.ok(getTrigger(entry.root));
    assert.equal(getTrigger(entry.child).BTTTriggerParentUUID, entry.root);
    // Creation accepts keys that updates do not; exercise the production update path.
    btt('update_trigger', {uuid:entry.child, trigger_parent_uuid:entry.root,
        json:JSON.stringify({BTTAdditionalActionData:JSON.stringify(data)})});
}

function smoke() {
    const builder = vm.createContext({ObjC:{import() {}}});
    vm.runInContext(fs.readFileSync(path.join(directory, 'repair_text_transforms.js'), 'utf8'), builder);
    const fixture = fs.mkdtempSync(path.join(os.tmpdir(), 'btt-text-smoke-'));
    const entries = [], cleanupErrors = [];
    try {
        writeFixture(fixture);
        const library = path.join(os.homedir(), 'dev/case-anything/dist/cjs/index.cjs');
        for (const [kind] of cases) {
            const entry = {kind, root:randomUUID().toUpperCase(), child:randomUUID().toUpperCase()};
            entries.push(entry);
            createPair(entry, builder.textActionData(kind, fixture, library));
        }
        for (const entry of [...entries, ...entries.slice().reverse()])
            assert.equal(btt('execute_assigned_actions_for_trigger', {uuid:entry.root}), entry.kind);
    } finally {
        for (const entry of entries.reverse()) {
            for (const id of [entry.child, entry.root]) {
                try {
                    btt('delete_trigger', {uuid:id});
                    assert.equal(getTrigger(id), undefined);
                } catch (error) { cleanupErrors.push(id + ': ' + error.message); }
            }
        }
        fs.rmSync(fixture, {recursive:true});
        if (cleanupErrors.length) throw new Error('Temporary trigger cleanup failed:\n' + cleanupErrors.join('\n'));
    }
    console.log('Verified nine stored transforms and 18 mixed-order runs; temporary triggers removed.');
}

if (require.main === module) {
    if (process.argv.length !== 3 || process.argv[2] !== '--run') {
        console.error('Usage: node tests/smoke_text_transforms.cjs --run');
        process.exitCode = 2;
    } else {
        try { smoke(); }
        catch (error) { console.error(error.message); process.exitCode = 1; }
    }
}
