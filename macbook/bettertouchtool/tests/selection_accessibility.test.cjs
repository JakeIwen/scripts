const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function accessibilityFixture(options = {}) {
    const system = {}, application = {}, otherApplication = {}, field = {}, range = {};
    const writes = [];
    const state = {manual:options.manual, waits:0, front:application};
    const native = value => value;
    Object.assign(native, {
        kAXErrorAttributeUnsupported:'-25205', kAXErrorNoValue:'-25212',
        AXIsProcessTrusted:() => true,
        AXUIElementCreateSystemWide:() => system,
        CFEqual:(a,b) => a === b,
        AXUIElementCopyAttributeValue(element, name, result) {
            if (element === application && name === 'AXManualAccessibility') {
                if (options.attributeError) return options.attributeError;
                if (state.manual === undefined) return -25205;
                result[0] = state.manual;
            } else if (element === system && name === 'AXFocusedApplication') result[0] = state.front;
            else if (element === system && name === 'AXFocusedUIElement') {
                if (state.waits < (options.readyAfter || 0)) return -25212;
                result[0] = field;
            } else if (element === field) {
                result[0] = {AXRole:'AXTextArea', AXSelectedText:'Hello', AXSelectedTextRange:range}[name];
                if (result[0] === undefined) return -25205;
            } else return -25205;
            return 0;
        },
        AXUIElementSetAttributeValue(element, name, value) {
            writes.push({element, name, value});
            assert.equal(element, application);
            assert.equal(name, 'AXManualAccessibility');
            if (options.refuseEnable) return -25204;
            state.manual = value;
            return 0;
        },
        AXUIElementIsAttributeSettable(element, name, result) {
            assert.equal(element, field);
            assert.equal(name, 'AXSelectedText');
            result[0] = true;
            return 0;
        }
    });
    const context = vm.createContext({
        $:native, Ref:() => [], ObjC:{import() {}, castRefToObject:x => x, unwrap:x => x},
        delay(seconds) {
            assert.equal(seconds, 0.05);
            state.waits++;
            if (options.switchApp) state.front = otherApplication;
        }
    });
    vm.runInContext(fs.readFileSync(path.join(__dirname, '../text_selection.js'), 'utf8'), context);
    return {context, state, writes};
}

test('native editors without Electron attributes remain on the immediate path', () => {
    const {context, state, writes} = accessibilityFixture();
    const selected = context.selectionUI().read();
    assert.equal(selected.text, 'Hello');
    assert.equal(selected.writable, true);
    assert.equal(state.waits, 0);
    assert.equal(writes.length, 0);
});

test('Electron accessibility is enabled once and its editor tree is allowed to appear', () => {
    const {context, state, writes} = accessibilityFixture({manual:false, readyAfter:2});
    const ui = context.selectionUI();
    const before = ui.read();
    assert.equal(before.text, 'Hello');
    assert.ok(ui.same(before, ui.read()));
    assert.equal(writes.length, 1);
    assert.equal(writes[0].value, true);
    assert.equal(state.waits, 2);
});

test('already enabled accessibility is preserved without a write or delay', () => {
    const {context, state, writes} = accessibilityFixture({manual:true});
    assert.equal(context.selectionUI().read().text, 'Hello');
    assert.equal(writes.length, 0);
    assert.equal(state.waits, 0);
});

test('refused accessibility enablement stops before selection editing', () => {
    const {context, writes} = accessibilityFixture({manual:false, refuseEnable:true});
    assert.throws(() => context.selectionUI(), /could not enable text accessibility/);
    assert.equal(writes.length, 1);
});

test('waiting for an Electron tree is bounded', () => {
    const {context, state} = accessibilityFixture({manual:false, readyAfter:100});
    assert.throws(() => context.selectionUI().read(), /Focus an editable text field/);
    assert.equal(state.waits, 10);
});

test('switching apps during tree initialization never uses the new app selection', () => {
    const {context, state, writes} = accessibilityFixture({manual:false, readyAfter:2, switchApp:true});
    assert.throws(() => context.selectionUI().read(), /active app changed/);
    assert.equal(state.waits, 1);
    assert.equal(writes.length, 1);
});

test('AX communication failure is not treated as an unsupported attribute', () => {
    const {context, writes} = accessibilityFixture({attributeError:-25204});
    assert.throws(() => context.selectionUI(), /does not expose/);
    assert.equal(writes.length, 0);
});
