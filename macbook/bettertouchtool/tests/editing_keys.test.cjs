const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const {firefoxEditor} = require('./firefox_editor_fixture.cjs');
const {PLAIN_TEXT, array:nsArray, Item, pasteboardFake} = require('./pasteboard_fixture.cjs');

// Cmd-X / Cmd-C with no selection, run through runEditingKey against a modelled
// focused field, its app's Edit menu (found by the real text_menu_paste.js) and
// the general pasteboard (written by its real writeClipboardText). Elements
// carry ids so they stay distinct under the Firefox model's by-value equality.
function editMenu() {
    const bar = {id:'menu bar'}, appMenu = {id:'app menu'}, edit = {id:'edit'}, menu = {id:'edit menu'};
    const cut = {id:'cut'}, copy = {id:'copy'}, paste = {id:'paste'};
    const attributes = new Map([
        [bar, {AXChildren:nsArray([appMenu, edit])}],
        [appMenu, {AXChildren:nsArray([])}],
        [edit, {AXChildren:nsArray([menu])}],
        [menu, {AXRole:'AXMenu', AXChildren:nsArray([cut, copy, paste])}],
        ...[[cut, 'X'], [copy, 'C'], [paste, 'V']].map(([item, key]) =>
            [item, {AXRole:'AXMenuItem', AXMenuItemCmdChar:key, AXMenuItemCmdModifiers:0}])
    ]);
    return {bar, cut, copy, attributes};
}

// `field` is the focused element's model: `attribute(name)`, `set(name, value)`
// for AX writes and `press(write)` for the app's Cut. A second field,
// `state.other` (all of its text selected), can take focus; the Edit menu acts
// on whichever field is focused when it is pressed. `onAttribute` (every AX
// read) and `beforePress` (Cut's AXEnabled read) let the user act mid-run.
function keyFixture(field, {bundle = 'com.apple.TextEdit', enabled = true, clipboard:items = [{[PLAIN_TEXT]:'earlier'}],
    onAttribute, beforePress} = {}) {
    const system = {id:'system'}, app = {id:'app'}, menu = editMenu();
    const other = cocoaField('IMPORTANT', 0, {id:'other field'});
    other.selection = {location:0, length:other.text.length};
    const {board, native:clipboard, contents} = pasteboardFake({items, changeCount:7});
    // An app's own Cut.
    const write = text => { board.clearContents; board.writeObjects([new Item({[PLAIN_TEXT]:text})]); };
    const state = {focused:field.element, other, presses:[], board};
    const fields = [field, other];
    const attribute = (element, name, options) => {
        if (onAttribute) onAttribute(state, element, name);
        const owner = fields.find(candidate => candidate.element === element);
        let value;
        if (element === system) value = {AXFocusedApplication:app, AXFocusedUIElement:state.focused}[name];
        else if (element === app) value = {AXMenuBar:menu.bar}[name];
        else if (owner) value = name === 'AXRole' ? 'AXTextArea' : owner.attribute(name);
        else if (element === menu.cut && name === 'AXEnabled') {
            if (beforePress) beforePress(state);
            return enabled;
        } else if (menu.attributes.has(element)) value = menu.attributes.get(element)[name];
        if (value !== undefined) return value;
        if (options && options.optional) return null;
        throw new Error('Unexpected attribute ' + name);
    };
    const native = value => value;
    Object.assign(native, field.native, clipboard, {
        kAXErrorAttributeUnsupported:-25205, kAXErrorNoValue:-25212,
        AXIsProcessTrusted:() => true,
        AXUIElementCreateSystemWide:() => system,
        CFEqual:field.equal || ((a, b) => a === b),
        NSFileManager:{defaultManager:{fileExistsAtPath:() => false}},
        AXUIElementSetAttributeValue(element, name, value) {
            assert.equal(element, field.element);
            return field.set(name, value);
        },
        AXUIElementPerformAction(element, action) {
            assert.equal(action, 'AXPress');
            assert.equal(element, menu.cut, 'only Cut is ever pressed');
            state.presses.push('cut');
            fields.find(candidate => candidate.element === state.focused).press(write);
            return 0;
        }
    });
    const context = vm.createContext({$:native, Ref:() => [],
        delay() {},
        ObjC:{import() {}, castRefToObject:x => x, unwrap:x => x}});
    for (const file of ['text_selection.js', 'text_editing_keys.js'])
        vm.runInContext(fs.readFileSync(path.join(__dirname, '..', file), 'utf8'), context);
    Object.assign(context, {
        selectionAttribute:attribute,
        selectionOffsets:range => ({location:range.location, length:range.length}),
        selectionRangeValue:(location, length) => ({location, length}),
        selectionApplicationBundle:() => bundle,
        // The real Edit-menu and marker sources, as BTT loads them.
        selectionReadSource:file => fs.readFileSync(path.join(__dirname, '..', path.basename(file)), 'utf8')
    });
    const run = kind => JSON.parse(context.runEditingKey({kind, directory:'/fixture'}));
    return {run, state, board, contents, context};
}

// A Cocoa text view: AXValue holds the breaks and ranges are numeric. Options
// model an app that ignores or alters a range write, or whose Cut does nothing.
function cocoaField(text, caret, {id = 'field', selectAs = range => range, refuseCaret = false, cuts = true} = {}) {
    const field = {element:{id}, text, selection:{location:caret, length:0}, writes:0};
    const selected = () => field.text.slice(field.selection.location, field.selection.location + field.selection.length);
    return Object.assign(field, {
        attribute:name => ({AXValue:field.text, AXSelectedText:selected(),
            AXSelectedTextRange:{...field.selection}})[name],
        set(name, value) {
            assert.equal(name, 'AXSelectedTextRange');
            field.writes++;
            if (refuseCaret && value.length === 0) return -25200;
            field.selection = {...selectAs(value)};
            return 0;
        },
        press(write) {
            if (cuts) {
                write(selected());
                const {location, length} = field.selection;
                field.text = field.text.slice(0, location) + field.text.slice(location + length);
                field.selection = {location, length:0};
            }
        }
    });
}

const THREE = 'one\ntwo\nthree';

test('Cmd-X cuts the caret’s logical line and its break, wherever the caret sits in it', () => {
    for (const [text, caret, line, rest] of [
        [THREE, 0, 'one\n', 'two\nthree'],
        [THREE, 3, 'one\n', 'two\nthree'],
        [THREE, 4, 'two\n', 'one\nthree'],
        [THREE, 5, 'two\n', 'one\nthree'],
        [THREE, 13, 'three', 'one\ntwo\n'],
        ['a\n\nb', 2, '\n', 'a\nb'],
        ['one\r\ntwo', 1, 'one\r\n', 'two'],
        ['one\r\ntwo', 4, 'one\r\n', 'two'],
        ['one\rtwo', 5, 'two', 'one\r'],
        ['one\u2029two', 0, 'one\u2029', 'two']
    ]) {
        const field = cocoaField(text, caret);
        const {run, state, board} = keyFixture(field);
        assert.deepEqual(run('cut-line'), {ok:true, result:'cut line'}, JSON.stringify([text, caret]));
        assert.deepEqual(state.presses, ['cut']);
        assert.equal(board.text, line);
        assert.equal(field.text, rest);
    }
});

// What Cmd-C copies is what Cmd-X removes, its break written as \n.
test('Cmd-C puts the caret’s line on the clipboard as plain text and leaves the field alone', () => {
    for (const [text, caret, line] of [
        [THREE, 0, 'one\n'], [THREE, 5, 'two\n'], [THREE, 13, 'three'], ['a\n\nb', 2, '\n'],
        ['one\r\ntwo', 1, 'one\n'], ['one\r\ntwo', 4, 'one\n'], ['one\rtwo', 0, 'one\n'], ['one\rtwo', 5, 'two'],
        ['one\u2029two', 0, 'one\n']
    ]) {
        const field = cocoaField(text, caret);
        const {run, state, board, contents} = keyFixture(field);
        assert.deepEqual(run('copy-line'), {ok:true, result:'copied line'}, JSON.stringify([text, caret]));
        // One ordinary copy: plain text alone, not marked transient or kept off other devices.
        assert.deepEqual(contents(), [{[PLAIN_TEXT]:line}]);
        assert.deepEqual(board.prepared, [0]);
        assert.deepEqual(state.presses, []);
        assert.equal(field.writes, 0);
        assert.equal(field.text, text);
        assert.deepEqual(field.selection, {location:caret, length:0});
    }
});

test('a key typed between the copy’s reads is caught and nothing is copied', () => {
    const typed = field => {
        const {location} = field.selection;
        field.text = field.text.slice(0, location) + '!' + field.text.slice(location);
        field.selection = {location:location + 1, length:0};
    };
    const field = cocoaField(THREE, 5);
    let typing = true;
    const {run, board, contents} = keyFixture(field, {onAttribute:(state, element, name) => {
        if (typing && element === field.element && name === 'AXValue') { typing = false; typed(field); }
    }});
    assert.deepEqual(run('copy-line'), {ok:false, error:'The caret or focus moved while the line was read; nothing was copied.'});
    assert.deepEqual(contents(), [{[PLAIN_TEXT]:'earlier'}]);
    assert.equal(board.changeCount, 7);
});

// A failed write has already cleared the clipboard: what was there goes back.
test('a clipboard write that fails puts the previous clipboard back, every item and type', () => {
    const previous = [{[PLAIN_TEXT]:'previous text', 'public.rtf':'previous rich text'}, {'custom/type':'second item'}];
    for (const [fail, error] of [
        [function () { return false; }, 'Could not write the clipboard. Nothing was copied.'],
        [function () { return true; }, 'The clipboard text did not read back. Nothing was copied.']
    ]) {
        const field = cocoaField(THREE, 5);
        const {run, board, contents} = keyFixture(field, {clipboard:previous});
        const write = board.writeObjects;
        let first = true;
        board.writeObjects = function (items) {
            if (first) { first = false; return fail.call(this, items); }
            return write.call(this, items);
        };
        assert.deepEqual(run('copy-line'), {ok:false, error});
        assert.deepEqual(contents(), previous);
    }
});

// Copies made while the line is read, here or in another app, are newer than
// the line and are kept.
test('a copy made while the line is read is kept, with or without a focus change', () => {
    for (const [moveFocus, error] of [
        [true, 'The caret or focus moved while the line was read; nothing was copied.'],
        [false, 'The clipboard changed while the line was read. Nothing was copied.']
    ]) {
        const field = cocoaField(THREE, 5);
        let reads = 0;
        const {run, contents} = keyFixture(field, {onAttribute:(state, element, name) => {
            if (element === field.element && name === 'AXSelectedTextRange' && ++reads === 2) {
                if (moveFocus) state.focused = state.other.element;
                state.board.clearContents;
                state.board.writeObjects([new Item({[PLAIN_TEXT]:'newer copy'})]);
            }
        }});
        assert.deepEqual(run('copy-line'), {ok:false, error});
        assert.deepEqual(contents(), [{[PLAIN_TEXT]:'newer copy'}]);
    }
});

test('focus that moves while the line is read stops the copy', () => {
    const field = cocoaField(THREE, 5);
    const {run, contents} = keyFixture(field, {onAttribute:(state, element, name) => {
        if (element === field.element && name === 'AXValue') state.focused = state.other.element;
    }});
    assert.deepEqual(run('copy-line'), {ok:false, error:'The caret or focus moved while the line was read; nothing was copied.'});
    assert.deepEqual(contents(), [{[PLAIN_TEXT]:'earlier'}]);
});

test('an empty last line or field has nothing to cut or copy and is left alone', () => {
    for (const [text, caret] of [['one\n', 4], ['', 0]]) {
        for (const [kind, result] of [['cut-line', 'nothing to cut'], ['copy-line', 'nothing to copy']]) {
            const field = cocoaField(text, caret);
            const {run, state, board} = keyFixture(field);
            assert.deepEqual(run(kind), {ok:true, result});
            assert.deepEqual(state.presses, []);
            assert.equal(field.writes, 0);
            assert.equal(board.changeCount, 7);
        }
    }
});

test('a line the app does not select exactly is refused before anything is pressed', () => {
    for (const selectAs of [range => ({location:range.location, length:0}),
        range => ({location:range.location, length:range.length - 1})]) {
        const field = cocoaField(THREE, 5, {selectAs});
        const {run, state, board} = keyFixture(field);
        const outcome = run('cut-line');
        assert.equal(outcome.ok, false);
        assert.match(outcome.error, /did not select the line; nothing was cut/);
        assert.deepEqual(state.presses, []);
        assert.equal(field.text, THREE);
        assert.equal(board.changeCount, 7);
    }
});

test('focus or text that changes before the press stops the command untouched', () => {
    const changes = [state => { state.focused = state.other.element; },
        (state, field) => { field.text = 'one\ntwo!\nthree'; }];
    for (const change of changes) {
        const field = cocoaField(THREE, 5);
        const {run, state, board} = keyFixture(field, {beforePress:state => change(state, field)});
        const outcome = run('cut-line');
        assert.equal(outcome.ok, false);
        assert.match(outcome.error, /changed before Cut.*left as it is/);
        assert.deepEqual(state.presses, []);
        assert.equal(board.changeCount, 7);
        // The line stays selected: once the user has moved on, nothing is written back.
        assert.deepEqual(field.selection, {location:4, length:4});
    }
});

test('an unavailable Cut presses nothing and puts the caret back', () => {
    const field = cocoaField(THREE, 5);
    const {run, state} = keyFixture(field, {enabled:false});
    assert.deepEqual(run('cut-line'), {ok:false, error:'The app’s Cut command is unavailable; nothing was cut.'});
    assert.deepEqual(state.presses, []);
    assert.deepEqual(field.selection, {location:5, length:0});
});

test('a cut the app does not carry out is reported rather than claimed', () => {
    const field = cocoaField(THREE, 5, {cuts:false});
    const {run, state} = keyFixture(field);
    assert.deepEqual(run('cut-line'), {ok:false, error:'The app did not confirm the cut; check the text.'});
    assert.deepEqual(state.presses, ['cut']);
});

test('a caret the app will not put back is reported with the stopped cut', () => {
    const field = cocoaField(THREE, 5, {refuseCaret:true});
    assert.deepEqual(keyFixture(field, {enabled:false}).run('cut-line'), {ok:false, error:'The app’s Cut command is '
        + 'unavailable; nothing was cut. The caret could not be put back: The app refused the selection.'});
    assert.deepEqual(field.selection, {location:4, length:4});
});

test('a selection made before the script runs is not replaced', () => {
    const field = cocoaField(THREE, 5);
    field.selection = {location:4, length:2};
    const {run, state} = keyFixture(field);
    assert.deepEqual(run('cut-line'), {ok:false, error:'Text is selected now; nothing was cut.'});
    assert.deepEqual(state.presses, []);
    assert.equal(field.writes, 0);
});

// Firefox: the field's model from firefox_editor_fixture.cjs. Its numeric
// selection writes throw, so passing proves lines go through text markers.
// Its Cut serializes a selection ending at the next block's start with a break.
function firefoxField(paragraphs, node, offset) {
    const document = firefoxEditor({paragraphs, node, start:offset, end:offset});
    return {element:document.editor, document, native:document.native, equal:document.equal,
        attribute:name => document.attribute(name),
        set:(name, value) => document.native.AXUIElementSetAttributeValue(document.editor, name, value),
        press(write) {
            const {start, end} = document.state.selection;
            write(document.rangeText(document.state.selection) + (end.node > start.node && end.offset === 0 ? '\n' : ''));
            document.replaceSelection('');
        }};
}

test('Firefox cuts the caret’s paragraph and its break through text markers', () => {
    for (const [node, offset, line, rest] of [[1, 3, 'mIxEd tail\n', ['first', 'third']],
        [1, 0, 'mIxEd tail\n', ['first', 'third']], [2, 5, 'third', ['first', 'mIxEd tail', '']]]) {
        const field = firefoxField(['first', 'mIxEd tail', 'third'], node, offset);
        const {run, state, board} = keyFixture(field, {bundle:'org.mozilla.firefox'});
        assert.deepEqual(run('cut-line'), {ok:true, result:'cut line'});
        assert.deepEqual(state.presses, ['cut']);
        assert.equal(board.text, line);
        assert.deepEqual(field.document.texts(), rest);
    }
});

// Firefox's paragraph text has no break; the copy adds \n when another paragraph follows.
test('Firefox copies the caret’s paragraph text, with a break unless it is the last', () => {
    for (const [paragraphs, node, offset, line] of [
        [['first', 'mIxEd tail', 'third'], 1, 3, 'mIxEd tail\n'], [['first', 'mIxEd tail', 'third'], 0, 0, 'first\n'],
        [['first', 'mIxEd tail', 'third'], 2, 2, 'third'], [['first', '', 'third'], 1, 0, '\n'],
        [['', '', 'third'], 0, 0, '\n'], [['', '', 'third'], 1, 0, '\n'], [['first', ''], 0, 2, 'first\n']
    ]) {
        const field = firefoxField(paragraphs, node, offset);
        const caret = JSON.stringify(field.document.state.selection);
        const {run, state, board, contents} = keyFixture(field, {bundle:'org.mozilla.firefox'});
        assert.deepEqual(run('copy-line'), {ok:true, result:'copied line'}, JSON.stringify([paragraphs, node]));
        assert.deepEqual(contents(), [{[PLAIN_TEXT]:line}]);
        assert.deepEqual(state.presses, []);
        assert.equal(JSON.stringify(field.document.state.selection), caret);
        assert.deepEqual(field.document.texts(), paragraphs);
    }
});

test('Firefox has nothing to copy in an empty last paragraph', () => {
    const field = firefoxField(['first', ''], 1, 0);
    const {run, board} = keyFixture(field, {bundle:'org.mozilla.firefox'});
    assert.deepEqual(run('copy-line'), {ok:true, result:'nothing to copy'});
    assert.equal(board.changeCount, 7);
});

test('a Firefox caret that moves while the paragraph is read copies nothing', () => {
    const field = firefoxField(['first', 'mIxEd tail', 'third'], 1, 3);
    let typing = true;
    const {run, board} = keyFixture(field, {bundle:'org.mozilla.firefox', onAttribute:(state, element, name) => {
        if (typing && element === field.element && name === 'AXValue') {
            typing = false;
            field.document.state.selection = {start:{node:1, offset:4}, end:{node:1, offset:4}};
        }
    }});
    assert.deepEqual(run('copy-line'), {ok:false, error:'The caret or focus moved while the line was read; nothing was copied.'});
    assert.equal(board.changeCount, 7);
});

test('Firefox refuses to cut a blank paragraph rather than guess at it', () => {
    for (const [paragraphs, node] of [[['first', '', 'third'], 1], [['', '', 'third'], 0], [['', '', 'third'], 1],
        [['first', ''], 1]]) {
        const field = firefoxField(paragraphs, node, 0);
        const before = JSON.stringify(field.document.state.selection);
        const {run, state, board} = keyFixture(field, {bundle:'org.mozilla.firefox'});
        assert.deepEqual(run('cut-line'), {ok:false, error:'Blank lines can’t be cut in Firefox yet; nothing was cut.'},
            JSON.stringify([paragraphs, node]));
        assert.deepEqual(state.presses, []);
        assert.deepEqual(field.document.texts(), paragraphs);
        assert.equal(JSON.stringify(field.document.state.selection), before);
        assert.equal(board.changeCount, 7);
    }
});

// Focus can move while the last checks read the field; Cut must still not
// reach the newly focused field (which has all of its text selected).
test('focus that moves while the checks read the field never lets Cut reach the new field', () => {
    for (const [makeField, bundle, name] of [
        [() => cocoaField(THREE, 5), 'com.apple.TextEdit', 'AXValue'],
        [() => cocoaField(THREE, 5), 'com.apple.TextEdit', 'AXSelectedTextRange'],
        [() => cocoaField(THREE, 5), 'com.apple.TextEdit', 'AXSelectedText'],
        [() => firefoxField(['first', 'mIxEd tail', 'third'], 1, 3), 'org.mozilla.firefox', 'AXSelectedTextMarkerRange']
    ]) {
        const field = makeField();
        let checking = false;
        const {run, state, board} = keyFixture(field, {bundle, beforePress:() => { checking = true; },
            onAttribute:(state, element, read) => {
                if (checking && element === field.element && read === name) state.focused = state.other.element;
            }});
        const outcome = run('cut-line');
        assert.equal(outcome.ok, false, name);
        assert.match(outcome.error, /changed before Cut; nothing was cut/);
        assert.deepEqual(state.presses, []);
        assert.equal(state.other.text, 'IMPORTANT');
        assert.equal(board.changeCount, 7);
    }
});

test('a caret moved while the Edit menu is found is not overwritten by the old line', () => {
    const field = cocoaField(THREE, 5);
    const {run, state} = keyFixture(field, {onAttribute:(state, element, name) => {
        if (name === 'AXMenuBar') field.selection = {location:10, length:0};
    }});
    assert.deepEqual(run('cut-line'), {ok:false, error:'The caret, text or focus changed; nothing was cut.'});
    assert.equal(field.writes, 0);
    assert.deepEqual(state.presses, []);
    assert.equal(field.text, THREE);
    assert.deepEqual(field.selection, {location:10, length:0});
});

test('editing keys dispatch by kind and report an unknown kind as a refusal', () => {
    const {run, state} = keyFixture(cocoaField(THREE, 5));
    assert.deepEqual(run('toString'), {ok:false, error:'Unknown editing key.'});
    assert.deepEqual(run('cut-line'), {ok:true, result:'cut line'});
    assert.deepEqual(state.presses, ['cut']);
});

test('the runtime can load again into the same context, as BTT reuses one', () => {
    const context = vm.createContext({ObjC:{import() {}}});
    for (let run = 0; run < 2; run++) {
        for (const file of ['text_selection.js', 'text_editing_keys.js'])
            vm.runInContext(fs.readFileSync(path.join(__dirname, '..', file), 'utf8'), context);
    }
    assert.equal(typeof context.runEditingKey, 'function');
});
