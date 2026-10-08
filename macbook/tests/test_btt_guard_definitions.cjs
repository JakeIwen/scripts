// node --test macbook/tests/test_btt_guard_definitions.cjs
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const folder = path.join(__dirname, '../bettertouchtool');
const worker = fs.readFileSync(path.join(folder, 'btt_guard/export.js'), 'utf8');
const libraries = new Map([
    ['/repo/macbook/bettertouchtool/btt_common.js',
        fs.readFileSync(path.join(folder, 'btt_common.js'), 'utf8')],
    ['/repo/macbook/bettertouchtool/restore_media.js',
        fs.readFileSync(path.join(folder, 'restore_media.js'), 'utf8')],
]);

function setup(input, responses) {
    const calls = [];
    const $ = () => { throw new Error('unexpected Objective-C conversion'); };
    $.NSUTF8StringEncoding = 4;
    $.NSString = {stringWithContentsOfFileEncodingError(file) {
        if (file === '/private/input.json') return JSON.stringify(input);
        return libraries.get(file) || null;
    }};
    const btt = {get_trigger(id) {
        calls.push(id);
        return JSON.stringify(responses[id] === undefined ? [] : responses[id]);
    }};
    const context = vm.createContext({
        $, ObjC: {import() {}, unwrap: value => value},
        Application(application) {
            assert.equal(application, '/Applications/BetterTouchTool.app');
            return btt;
        },
    });
    vm.runInContext(worker, context);
    return {run: () => context.run(['/repo', '/private/input.json']), calls};
}

test('exports exactly the requested definitions without executing actions', () => {
    const root = {BTTUUID: 'root', BTTTriggerType: 767, BTTMenuItems: []};
    const named = {BTTUUID: 'named', BTTTriggerType: 643};
    const environment = setup({ids: ['root', 'named']}, {
        root: [root], named,
    });
    assert.deepEqual(JSON.parse(environment.run()), [root, named]);
    assert.deepEqual(environment.calls, ['root', 'named']);
});

test('strict shared getter rejects missing, mismatched, and duplicate responses', () => {
    assert.throws(() => setup({ids: ['root']}, {root: []}).run(), /unavailable/);
    assert.throws(() => setup({ids: ['root']}, {
        root: [{BTTUUID: 'other'}],
    }).run(), /Unexpected BTT readback/);
    assert.throws(() => setup({ids: ['root']}, {
        root: [{BTTUUID: 'root'}, {BTTUUID: 'root'}],
    }).run(), /Duplicate runtime UUID/);
});

test('invalid or duplicate request ids fail before BetterTouchTool access', () => {
    for (const input of [{ids: ['root', 'root']}, {ids: [3]}, {ids: []}, {ids: [], extra: true}]) {
        const environment = setup(input, {});
        assert.throws(environment.run, /Invalid BTT Guard export request/);
        assert.deepEqual(environment.calls, []);
    }
});
