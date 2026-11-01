const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

// Cmd-X with no selection: BTT sends the line-selection keys, then the runtime
// waits for the app's selection to settle and presses Edit > Cut.
function cutFixture(ranges) {
    const system = {}, application = {}, field = {}, cut = {}, presses = [];
    let reads = 0;
    const native = value => value;
    Object.assign(native, {
        kAXErrorAttributeUnsupported:-25205, kAXErrorNoValue:-25212,
        AXIsProcessTrusted:() => true,
        AXUIElementCreateSystemWide:() => system,
        CFEqual:(a, b) => a === b,
        AXUIElementPerformAction(element, action) { presses.push({element, action}); return 0; }
    });
    const context = vm.createContext({$:native, Ref:() => [], delay() {},
        ObjC:{import() {}, castRefToObject:x => x, unwrap:x => x}});
    vm.runInContext(fs.readFileSync(path.join(__dirname, '../text_selection.js'), 'utf8'), context);
    context.selectionAttribute = (element, name) => {
        if (element === system) return name === 'AXFocusedApplication' ? application : field;
        if (element === field && name === 'AXRole') return 'AXTextArea';
        if (element === field && name === 'AXSelectedTextRange') return ranges[Math.min(reads++, ranges.length - 1)];
        if (element === cut && name === 'AXEnabled') return true;
        throw new Error('Unexpected attribute ' + name);
    };
    context.selectionOffsets = range => range;
    context.selectionReadSource = file => {
        assert.match(file, /text_menu_paste\.js$/);
        return 'function findEditMenuItem(app, attribute, key) { if (key !== "x") throw Error("wrong command"); return CUT; }';
    };
    context.CUT = cut;
    return {context, presses, cut, reads: () => reads};
}

test('the selected line is cut through the Edit menu once the keys have landed', () => {
    const {context, presses, cut} = cutFixture([{location:4, length:11}, {location:4, length:12}, {location:4, length:12}]);
    assert.equal(context.cutSelectedLine('/fixture'), 'cut line');
    assert.deepEqual(presses, [{element:cut, action:'AXPress'}]);
});

test('an empty last line has nothing to cut and presses nothing', () => {
    const {context, presses} = cutFixture([{location:9, length:0}]);
    assert.equal(context.cutSelectedLine('/fixture'), 'nothing to cut');
    assert.deepEqual(presses, []);
});

test('a selection that never settles is refused without cutting', () => {
    const ranges = Array.from({length:30}, (_, index) => ({location:0, length:index + 1}));
    const {context, presses, reads} = cutFixture(ranges);
    assert.throws(() => context.cutSelectedLine('/fixture'), /did not settle/);
    assert.deepEqual(presses, []);
    assert.equal(reads(), 20);
});
