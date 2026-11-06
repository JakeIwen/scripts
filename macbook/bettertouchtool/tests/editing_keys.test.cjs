const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const {firefoxEditor} = require('./firefox_editor_fixture.cjs');
const LINE_TYPE = 'com.jacobr.editing-keys.line';
const {PLAIN_TEXT, array:nsArray, Item, pasteboardFake} = require('./pasteboard_fixture.cjs');

// Cmd-X / Cmd-C / Cmd-V with no selection, run through runEditingKey against a modelled
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
    return {bar, cut, copy, paste, attributes};
}

// `field` is the focused element's model: `attribute(name)`, `set(name, value)`
// for AX writes and `press(write)` for the app's Cut. A second field,
// `state.other` (all of its text selected), can take focus; the Edit menu acts
// on whichever field is focused when it is pressed. `onAttribute` (every AX
// read) and `beforePress` (the final ownership check) let the user act mid-run.
function keyFixture(field, {bundle = 'com.apple.TextEdit', enabled = true, clipboard:items = [{[PLAIN_TEXT]:'earlier'}],
    onAttribute, beforePress, onDelay} = {}) {
    const system = {id:'system'}, app = {id:'app'}, menu = editMenu();
    const other = cocoaField('IMPORTANT', 0, {id:'other field'});
    other.selection = {location:0, length:other.text.length};
    const {board, native:clipboard, contents} = pasteboardFake({items, changeCount:7});
    // An app's own Cut.
    const write = text => { board.clearContents; board.writeObjects([new Item({[PLAIN_TEXT]:text})]); };
    const state = {focused:field.element, other, presses:[], board};
    const fields = [field, other];
    let selectionWrites = 0, selectionReads = 0;
    const attribute = (element, name, options) => {
        // Run interleaving hooks at the final ownership check, not a stale menu flag.
        if (selectionWrites > 0 && element === field.element && name === 'AXValue' && ++selectionReads === 2) {
            if (beforePress) beforePress(state);
        }
        if (onAttribute) onAttribute(state, element, name);
        const owner = fields.find(candidate => candidate.element === element);
        let value;
        if (element === system) value = {AXFocusedApplication:app, AXFocusedUIElement:state.focused}[name];
        else if (element === app) value = {AXMenuBar:menu.bar}[name];
        else if (owner) value = name === 'AXRole' ? (owner.role || 'AXTextArea') : owner.attribute(name);
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
            selectionWrites++;
            return field.set(name, value);
        },
        AXUIElementPerformAction(element, action) {
            assert.equal(action, 'AXPress');
            assert.ok(element === menu.cut || element === menu.paste);
            state.presses.push(element === menu.cut ? 'cut' : 'paste');
            const target = fields.find(candidate => candidate.element === state.focused);
            if (element === menu.cut) target.press(write);
            else target.paste(board.text);
            return 0;
        }
    });
    const context = vm.createContext({$:native, Ref:() => [],
        delay(seconds) { if (onDelay) onDelay(state, seconds); },
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
    const run = (kind, options = {}) => JSON.parse(context.runEditingKey({kind, directory:'/fixture', ...options}));
    return {run, state, board, contents, context};
}

// A Cocoa text view: AXValue holds the breaks and ranges are numeric. Options
// model an app that ignores or alters a range write, or whose Cut does nothing.
function cocoaField(text, caret, {id = 'field', selectAs = range => range, refuseCaret = false, cuts = true, pastes = true} = {}) {
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
        paste(text) {
            if (!pastes) return;
            const {location, length} = field.selection;
            field.text = field.text.slice(0, location) + text + field.text.slice(location + length);
            field.selection = {location:location + text.length, length:0};
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
        assert.equal(board.text, line.replace(/(?:\r\n?|[\n\u2029])$/, '') + '\n');
        assert.equal(field.text, rest);
    }
});

// Cmd-C copies the line's text and one \n, whatever break the app uses, on
// the last line too (as VS Code does), and \n alone for an empty line.
test('Cmd-C puts the caret’s line on the clipboard as plain text and leaves the field alone', () => {
    for (const [text, caret, line] of [
        [THREE, 0, 'one\n'], [THREE, 5, 'two\n'], [THREE, 13, 'three\n'], ['a\n\nb', 2, '\n'],
        ['one\r\ntwo', 1, 'one\n'], ['one\r\ntwo', 4, 'one\n'], ['one\rtwo', 0, 'one\n'], ['one\rtwo', 5, 'two\n'],
        ['one\u2029two', 0, 'one\n'], ['one\n', 4, '\n'], ['', 0, '\n']
    ]) {
        const field = cocoaField(text, caret);
        const {run, state, board, contents} = keyFixture(field);
        assert.deepEqual(run('copy-line'), {ok:true, result:'copied line'}, JSON.stringify([text, caret]));
        // Ordinary plain text plus our line tag, not transient or kept off other devices.
        assert.deepEqual(contents(), [{[PLAIN_TEXT]:line, [LINE_TYPE]:'1'}]);
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

test('an empty last line or field has nothing to cut and is left alone', () => {
    for (const [text, caret] of [['one\n', 4], ['', 0]]) {
        const field = cocoaField(text, caret);
        const {run, state, board} = keyFixture(field);
        assert.deepEqual(run('cut-line'), {ok:true, result:'nothing to cut'});
        assert.deepEqual(state.presses, []);
        assert.equal(field.writes, 0);
        assert.equal(board.changeCount, 7);
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

test('a lazily disabled Cut item still presses and confirms the cut', () => {
    const field = cocoaField(THREE, 5);
    const {run, state} = keyFixture(field, {enabled:false});
    assert.deepEqual(run('cut-line'), {ok:true, result:'cut line'});
    assert.deepEqual(state.presses, ['cut']);
    assert.equal(field.text, 'one\nthree');
});

test('a cut the app does not carry out is reported rather than claimed', () => {
    const field = cocoaField(THREE, 5, {cuts:false});
    const {run, state} = keyFixture(field);
    assert.deepEqual(run('cut-line'), {ok:false, error:'The app did not confirm the cut; check the text.'});
    assert.deepEqual(state.presses, ['cut']);
    assert.deepEqual(field.selection, {location:5, length:0});
});

test('a caret the app will not put back is reported with the stopped cut', () => {
    const field = cocoaField(THREE, 5, {refuseCaret:true, cuts:false});
    assert.deepEqual(keyFixture(field, {enabled:false}).run('cut-line'), {ok:false, error:'The app did not confirm the cut; '
        + 'check the text. The caret could not be put back: The app refused the selection.'});
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
function firefoxField(paragraphs, node, offset, options = {}) {
    const document = firefoxEditor({paragraphs, node, start:offset, end:offset, ...options});
    return {element:document.editor, document, native:document.native, equal:document.equal,
        attribute:name => document.attribute(name),
        set:(name, value) => document.native.AXUIElementSetAttributeValue(document.editor, name, value),
        paste:text => document.pasteText(text),
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
        assert.equal(board.text, line.endsWith('\n') ? line : line + '\n');
        assert.deepEqual(field.document.texts(), rest);
    }
});

// Firefox's paragraph text has no break; the copy adds one \n, to the last paragraph too.
test('Firefox copies the caret’s paragraph text and a break, blank and last paragraphs included', () => {
    for (const [paragraphs, node, offset, line] of [
        [['first', 'mIxEd tail', 'third'], 1, 3, 'mIxEd tail\n'], [['first', 'mIxEd tail', 'third'], 0, 0, 'first\n'],
        [['first', 'mIxEd tail', 'third'], 2, 2, 'third\n'], [['first', '', 'third'], 1, 0, '\n'],
        [['', '', 'third'], 0, 0, '\n'], [['', '', 'third'], 1, 0, '\n'], [['first', ''], 0, 2, 'first\n'],
        [['first', ''], 1, 0, '\n'], [['', ''], 0, 0, '\n'], [['', ''], 1, 0, '\n']
    ]) {
        const field = firefoxField(paragraphs, node, offset);
        const caret = JSON.stringify(field.document.state.selection);
        const {run, state, board, contents} = keyFixture(field, {bundle:'org.mozilla.firefox'});
        assert.deepEqual(run('copy-line'), {ok:true, result:'copied line'}, JSON.stringify([paragraphs, node]));
        assert.deepEqual(contents(), [{[PLAIN_TEXT]:line, [LINE_TYPE]:'1'}]);
        assert.deepEqual(state.presses, []);
        assert.equal(JSON.stringify(field.document.state.selection), caret);
        assert.deepEqual(field.document.texts(), paragraphs);
    }
});

// Every document of one to four paragraphs, each blank or not, with the
// caret in each paragraph: blank runs must not merge or vanish.
test('Firefox copies each paragraph of every short mix of blank and filled paragraphs', () => {
    for (let count = 1; count <= 4; count++) {
        for (let mask = 0; mask < 2 ** count; mask++) {
            const paragraphs = Array.from({length:count}, (_, index) => mask & (1 << index) ? 'x' : '');
            for (let node = 0; node < count; node++) {
                const {run, contents} = keyFixture(firefoxField(paragraphs, node, 0), {bundle:'org.mozilla.firefox'});
                assert.deepEqual(run('copy-line'), {ok:true, result:'copied line'}, JSON.stringify([paragraphs, node]));
                assert.deepEqual(contents(), [{[PLAIN_TEXT]:paragraphs[node] + '\n', [LINE_TYPE]:'1'}], JSON.stringify([paragraphs, node]));
            }
        }
    }
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


test('tagged Cmd-V inserts above the line and keeps the original character under the caret', () => {
    for (const [text, caret, expected, position] of [
        [THREE, 5, 'one\ncopy\ntwo\nthree', 10],
        [THREE, 4, 'one\ncopy\ntwo\nthree', 9],
        ['one\n', 4, 'one\ncopy\n', 9],
        ['one\ntwo', 6, 'one\ncopy\ntwo', 11],
        ['one\r\ntwo\r\n', 6, 'one\r\ncopy\ntwo\r\n', 11],
        ['', 0, 'copy\n', 5]
    ]) {
        const field = cocoaField(text, caret);
        const {run, state, contents} = keyFixture(field, {clipboard:[{[PLAIN_TEXT]:'copy\n', [LINE_TYPE]:'1'}]});
        assert.deepEqual(run('paste-line'), {ok:true, result:'pasted line'}, JSON.stringify([text, caret]));
        assert.equal(field.text, expected);
        assert.deepEqual(field.selection, {location:position, length:0});
        assert.deepEqual(state.presses, ['paste']);
        assert.deepEqual(contents(), [{[PLAIN_TEXT]:'copy\n', [LINE_TYPE]:'1'}]);
    }
});

test('untagged Cmd-V uses native Paste once at the current selection', () => {
    const field = cocoaField(THREE, 5);
    const {run, state} = keyFixture(field);
    assert.deepEqual(run('paste-line'), {ok:true, result:'pasted normally'});
    assert.equal(field.text, 'one\n' + 't' + 'earlier' + 'wo\nthree');
    assert.deepEqual(state.presses, ['paste']);
});

test('a tagged paste whose selection changed uses normal Paste once', () => {
    const field = cocoaField(THREE, 5);
    field.selection.length = 2;
    const {run, state} = keyFixture(field, {clipboard:[{[PLAIN_TEXT]:'copy\n', [LINE_TYPE]:'1'}]});
    assert.deepEqual(run('paste-line'), {ok:true, result:'pasted normally'});
    assert.equal(field.text, 'one\ntcopy\n\nthree');
    assert.deepEqual(state.presses, ['paste']);
});

test('P2: a user caret moved to line start before select is not mistaken for ours', () => {
    for (const change of [(state, field) => { field.selection.location = 9; },
        (state, field) => { field.selection.location = 4; },
        state => { state.focused = state.other.element; }]) {
        const field = cocoaField(THREE, 5);
        let menuRead = false, moved = false;
        const {run, state} = keyFixture(field, {clipboard:[{[PLAIN_TEXT]:'copy\n', [LINE_TYPE]:'1'}],
            onAttribute:(state, element, name) => {
                if (name === 'AXMenuBar') menuRead = true;
                if (menuRead && !moved && element === field.element && name === 'AXSelectedTextRange') {
                    moved = true; change(state, field);
                }
            }});
        assert.equal(run('paste-line').ok, false);
        assert.deepEqual(state.presses, []);
        assert.equal(field.writes, 0);
    }
});

test('a newer clipboard before Paste restores the caret and pastes that copy normally', () => {
    const field = cocoaField(THREE, 5);
    const {run, contents, state} = keyFixture(field, {clipboard:[{[PLAIN_TEXT]:'copy\n', [LINE_TYPE]:'1'}],
        beforePress:state => { state.board.clearContents; state.board.writeObjects([new Item({[PLAIN_TEXT]:'new'})]); }});
    assert.deepEqual(run('paste-line'), {ok:true, result:'pasted normally'});
    assert.equal(field.text, 'one\ntnewwo\nthree');
    assert.deepEqual(contents(), [{[PLAIN_TEXT]:'new'}]);
    assert.deepEqual(state.presses, ['paste']);
});

test('P3: a late paste lands at the line start after confirmation times out', () => {
    const field = cocoaField(THREE, 5), paste = field.paste;
    let pending;
    field.paste = text => { pending = text; };
    const {run, state} = keyFixture(field, {clipboard:[{[PLAIN_TEXT]:'copy\n', [LINE_TYPE]:'1'}]});
    assert.deepEqual(run('paste-line'), {ok:false, error:'The app did not confirm the paste; check the text.'});
    assert.equal(field.text, THREE);
    assert.deepEqual(field.selection, {location:4, length:0});
    assert.deepEqual(state.presses, ['paste']);
    paste(pending);
    assert.equal(field.text, 'one\ncopy\ntwo\nthree');
});

test('Firefox pastes at the native paragraph start and restores a marker caret', () => {
    for (const breakLeaves of [false, true]) for (const [paragraphs, node, offset] of [
        [['first', 'middle', 'last'], 1, 3], [['first', 'middle', 'last'], 1, 0], [['first', ''], 1, 0]]) {
        const field = firefoxField(paragraphs, node, offset, {breakLeaves});
        const {run} = keyFixture(field, {bundle:'org.mozilla.firefox',
            clipboard:[{[PLAIN_TEXT]:'copy\n', [LINE_TYPE]:'1'}]});
        assert.deepEqual(run('paste-line'), {ok:true, result:'pasted line'});
        assert.deepEqual(field.document.texts(), [...paragraphs.slice(0, node), 'copy',
            ...(breakLeaves ? ['\n'] : []), ...paragraphs.slice(node)]);
        const target = node + (breakLeaves ? 2 : 1);
        assert.deepEqual(field.document.state.selection, {start:{node:target, offset}, end:{node:target, offset}});
    }
});

test('Firefox blank tagged paste falls back to native Paste instead of swallowing Cmd-V', () => {
    const field = firefoxField(['first', 'middle'], 1, 2);
    const {run, state} = keyFixture(field, {bundle:'org.mozilla.firefox',
        clipboard:[{[PLAIN_TEXT]:'\n', [LINE_TYPE]:'1'}]});
    assert.equal(run('paste-line', {lineClipboardVariable:'v'}).ok, true);
    assert.deepEqual(state.presses, ['paste']);
    assert.deepEqual(field.document.texts(), ['first', 'mi', 'ddle']);
});

test('a failed cut tag write preserves the app clipboard and reports a successful cut with warning', () => {
    const field = cocoaField(THREE, 5);
    const {run, board, contents} = keyFixture(field);
    const write = board.writeObjects;
    let writes = 0;
    board.writeObjects = function (items) { return ++writes === 2 ? false : write.call(this, items); };
    const message = 'Line cut, but Cmd-V will paste it inline.';
    assert.deepEqual(run('cut-line'), {ok:true, result:message, warning:message});
    assert.equal(field.text, 'one\nthree');
    assert.deepEqual(contents(), [{[PLAIN_TEXT]:'two\n'}]);
});

test('the line clipboard state is returned for BTT to update its custom condition variable', () => {
    const {context, board} = keyFixture(cocoaField(THREE, 5));
    const run = kind => JSON.parse(context.runEditingKey({kind, directory:'/fixture', lineClipboardVariable:'fixture'}));
    assert.equal(run('copy-line').lineClipboard, true);
    board.clearContents;
    board.writeObjects([new Item({[PLAIN_TEXT]:'native'})]);
    assert.equal(run('paste-line').lineClipboard, false);
});


test('a native copy after Cut is preserved instead of overwritten by the line tag', () => {
    const field = cocoaField(THREE, 5);
    let copied = false;
    const {run, contents} = keyFixture(field, {onAttribute:(state, element, name) => {
        if (!copied && state.presses.includes('cut') && name === 'AXValue') {
            copied = true;
            state.board.clearContents;
            state.board.writeObjects([new Item({[PLAIN_TEXT]:'newer copy'})]);
        }
    }});
    const outcome = run('cut-line');
    assert.equal(outcome.ok, true);
    assert.equal(outcome.warning, 'Line cut, but Cmd-V will paste it inline.');
    assert.equal(field.text, 'one\nthree');
    assert.deepEqual(contents(), [{[PLAIN_TEXT]:'newer copy'}]);
});

test('a newer caret or focus before Paste is left alone without pressing Paste', () => {
    for (const change of [(state, field) => { field.selection.location = 10; },
        state => { state.focused = state.other.element; }]) {
        const field = cocoaField(THREE, 5);
        const {run, state} = keyFixture(field, {clipboard:[{[PLAIN_TEXT]:'copy\n', [LINE_TYPE]:'1'}],
            beforePress:state => change(state, field)});
        const outcome = run('paste-line');
        assert.equal(outcome.ok, false);
        assert.match(outcome.error, /left as it is/);
        assert.equal(field.text, THREE);
        assert.deepEqual(state.presses, []);
    }
});

test('Firefox leaves the line-start caret after an unconfirmed Paste', () => {
    const field = firefoxField(['first', 'middle', 'last'], 1, 3);
    field.paste = () => {};
    const {run} = keyFixture(field, {bundle:'org.mozilla.firefox',
        clipboard:[{[PLAIN_TEXT]:'copy\n', [LINE_TYPE]:'1'}]});
    assert.deepEqual(run('paste-line'), {ok:false, error:'The app did not confirm the paste; check the text.'});
    assert.deepEqual(field.document.state.selection, {start:{node:1, offset:0}, end:{node:1, offset:0}});
});

test('a confirmed paste remains successful if the app refuses the restored caret', () => {
    const field = cocoaField(THREE, 5);
    const set = field.set;
    field.set = (name, value) => field.text === THREE ? set(name, value) : -25200;
    const {run} = keyFixture(field, {clipboard:[{[PLAIN_TEXT]:'copy\n', [LINE_TYPE]:'1'}]});
    assert.deepEqual(run('paste-line'), {ok:true, result:'pasted line'});
    assert.equal(field.text, 'one\ncopy\ntwo\nthree');
    assert.deepEqual(field.selection, {location:9, length:0});
});

test('Firefox leaves the insertion caret if forward steps would cross a paragraph boundary', () => {
    const field = firefoxField(['first', 'middle', 'last'], 1, 6);
    const {run} = keyFixture(field, {bundle:'org.mozilla.firefox',
        clipboard:[{[PLAIN_TEXT]:'copy\n', [LINE_TYPE]:'1'}]});
    assert.deepEqual(run('paste-line'), {ok:true, result:'pasted line'});
    assert.deepEqual(field.document.state.selection, {start:{node:2, offset:0}, end:{node:2, offset:0}});
});


test('a clipboard that loses the private tag restores the previous data', () => {
    const {run, board, contents} = keyFixture(cocoaField(THREE, 5));
    const write = board.writeObjects;
    let first = true;
    board.writeObjects = function (items) {
        const result = write.call(this, items);
        if (first) { first = false; this.items[0].values.delete(LINE_TYPE); }
        return result;
    };
    assert.deepEqual(run('copy-line'), {ok:false, error:'The line clipboard tag did not read back. Nothing was copied.'});
    assert.deepEqual(contents(), [{[PLAIN_TEXT]:'earlier'}]);
});


const TRACK_CLIPBOARD = {lineClipboardVariable:'fixture'};
const TAGGED_LINE = [{[PLAIN_TEXT]:'copy\n', [LINE_TYPE]:'1'}];

test('P4: a menu-less field releases interception if the runtime is reached', () => {
    const field = cocoaField(THREE, 5);
    field.role = 'AXTextField';
    const {run, state} = keyFixture(field, {clipboard:TAGGED_LINE, onAttribute:(_s, _e, name) => {
        if (name === 'AXMenuBar') throw new Error('Cannot read AXMenuBar (-25212).');
    }});
    const result = run('paste-line', TRACK_CLIPBOARD);
    assert.equal(result.ok, false);
    assert.equal(result.lineClipboard, false);
    assert.equal(field.text, THREE);
    assert.deepEqual(state.presses, []);
});

test('P1: unsupported fields fall back once and release interception even with the tag', () => {
    for (const role of ['AXTextField', 'AXComboBox', 'AXTextArea']) {
        const field = cocoaField('search terms', 7);
        field.role = role;
        const attribute = field.attribute;
        if (role === 'AXTextArea') field.attribute = name => name === 'AXValue' ? 123 : attribute(name);
        const {run, state} = keyFixture(field, {clipboard:TAGGED_LINE});
        assert.deepEqual(run('paste-line', TRACK_CLIPBOARD), {ok:true, result:'pasted normally', lineClipboard:false});
        assert.equal(field.text, 'search copy\nterms');
        assert.equal(field.writes, 0);
        assert.deepEqual(state.presses, ['paste']);
    }
});

test('Cut waits for a later clipboard tick and tags the app’s rich items without losing formats', () => {
    for (const lag of [0, 1, 8]) {
        const field = cocoaField(THREE, 5), press = field.press;
        let pending, ticks = 0;
        field.press = () => press(text => { pending = text; });
        const publish = state => {
            if (pending && ticks++ >= lag) {
                state.board.clearContents;
                state.board.writeObjects([new Item({[PLAIN_TEXT]:pending, 'public.rtf':'rich', 'public.html':'<b>two</b>'}),
                    new Item({'custom/type':'second'})]);
                pending = null;
            }
        };
        const {run, contents} = keyFixture(field, {onAttribute:lag === 0 ? publish : undefined, onDelay:publish});
        assert.deepEqual(run('cut-line', TRACK_CLIPBOARD), {ok:true, result:'cut line', lineClipboard:true});
        assert.equal(field.text, 'one\nthree');
        assert.deepEqual(contents(), [{[PLAIN_TEXT]:'two\n', 'public.rtf':'rich', 'public.html':'<b>two</b>', [LINE_TYPE]:'1'},
            {'custom/type':'second'}]);
    }
});

test('only an in-flight copy waits for a tag; stale native copies paste without delay', () => {
    for (const waitForTag of [true, false]) for (const arrives of [true, false]) {
        const field = cocoaField(THREE, 5);
        let ticks = 0;
        const {run, state} = keyFixture(field, {onDelay:state => {
            if (++ticks === 3 && arrives) {
                state.board.clearContents;
                state.board.writeObjects(TAGGED_LINE.map(values => new Item(values)));
            }
        }});
        const result = run('paste-line', {...TRACK_CLIPBOARD, waitForTag});
        const tagged = waitForTag && arrives;
        assert.equal(result.result, tagged ? 'pasted line' : 'pasted normally');
        assert.equal(result.lineClipboard, tagged);
        assert.equal(field.text, tagged ? 'one\ncopy\ntwo\nthree' : 'one\ntearlierwo\nthree');
        assert.equal(ticks, waitForTag ? (arrives ? 3 : 20) : 0);
        assert.deepEqual(state.presses, ['paste']);
    }
});

test('a failed copy does not keep interception armed by an old tag', () => {
    const field = cocoaField(THREE, 5);
    field.selection.length = 1;
    const {run} = keyFixture(field, {clipboard:TAGGED_LINE});
    assert.equal(run('copy-line', TRACK_CLIPBOARD).lineClipboard, false);
});

test('a failed rich cut tag restores every app item without the tag', () => {
    const field = cocoaField(THREE, 5), press = field.press;
    const {run, board, contents} = keyFixture(field);
    const rich = [{[PLAIN_TEXT]:'two\n', 'public.rtf':'rich'}, {'custom/type':'second'}];
    field.press = () => press(() => { board.clearContents; board.writeObjects(rich.map(value => new Item(value))); });
    const write = board.writeObjects;
    let writes = 0;
    board.writeObjects = function (items) { return ++writes === 2 ? false : write.call(this, items); };
    const outcome = run('cut-line', TRACK_CLIPBOARD);
    assert.equal(outcome.ok, true);
    assert.equal(outcome.lineClipboard, false);
    assert.match(outcome.warning, /Line cut/);
    assert.deepEqual(contents(), rich);
});

test('a Cut with no clipboard publication does not retag an older matching copy', () => {
    const field = cocoaField(THREE, 5), press = field.press;
    field.press = () => press(() => {});
    let ticks = 0;
    const previous = [{[PLAIN_TEXT]:'two\n', [LINE_TYPE]:'1'}];
    const {run, contents} = keyFixture(field, {clipboard:previous, onDelay:() => ticks++});
    const result = run('cut-line', TRACK_CLIPBOARD);
    assert.equal(result.ok, true);
    assert.equal(result.lineClipboard, false);
    assert.match(result.warning, /Line cut/);
    assert.equal(ticks, 20);
    assert.deepEqual(contents(), previous);
});


test('a native copy between the tag wait and version capture pastes inline', () => {
    const field = cocoaField(THREE, 5);
    const {run, board, state} = keyFixture(field, {clipboard:TAGGED_LINE});
    const read = board.stringForType;
    let changed = false;
    board.stringForType = function (type) {
        const value = read.call(this, type);
        if (type === LINE_TYPE && !changed) {
            changed = true; this.clearContents; this.writeObjects([new Item({[PLAIN_TEXT]:'native\n'})]);
        }
        return value;
    };
    assert.equal(run('paste-line', TRACK_CLIPBOARD).result, 'pasted normally');
    assert.equal(field.text, 'one\ntnative\nwo\nthree');
    assert.deepEqual(state.presses, ['paste']);
});

test('Cut with a leading non-text item is normalized instead of duplicating the text', () => {
    const field = cocoaField(THREE, 5), press = field.press;
    const {run, board, contents} = keyFixture(field);
    field.press = () => press(text => { board.clearContents; board.writeObjects([
        new Item({'custom/type':'metadata'}), new Item({[PLAIN_TEXT]:text, 'public.rtf':'rich'})]); });
    assert.equal(run('cut-line', TRACK_CLIPBOARD).result, 'cut line');
    assert.deepEqual(contents(), [{[PLAIN_TEXT]:'two\n', [LINE_TYPE]:'1'}]);
});
