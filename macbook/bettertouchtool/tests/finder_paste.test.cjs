const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

// Cmd-V in Finder without a named pasteboard or destination: where an image goes,
// and when Finder's own Paste is pressed instead. Accessibility, Finder scripting and
// the pasteboard are stubbed; test_finder_paste.py covers real pasteboards and files.
function finderFixture({types = ['public.png'], editable = false, frontWindow = 'FinderWindow',
                        bundle = 'com.apple.finder', pasteEnabled = true, insertion = 'file:///Users/me/Work/'} = {}) {
    const system = {}, finder = {}, focused = {}, paste = {}, presses = [], saves = [], selected = [];
    const native = value => value;
    Object.assign(native, {
        kAXErrorAttributeUnsupported:-25205, kAXErrorNoValue:-25212,
        AXIsProcessTrusted:() => true,
        AXUIElementCreateSystemWide:() => system,
        AXUIElementIsAttributeSettable(element, name, settable) { settable[0] = editable; return 0; },
        AXUIElementPerformAction(element, action) { presses.push({element, action}); return 0; },
        NSURL:{URLWithString:url => ({path:decodeURIComponent(new URL(url).pathname).replace(/\/$/, '')})},
        NSPasteboard:{generalPasteboard:{types, pasteboardItems:{count:0},
            canReadObjectForClassesOptions:() => false}},
        NSImage:{}
    });
    const windows = frontWindow ? [{class:() => frontWindow}] : [];
    const application = {
        insertionLocation:{url() {
            if (insertion === null) throw new Error('Can’t get object.');
            return insertion;
        }},
        desktop:{url:() => 'file:///Users/me/Desktop/'},
        windows,
        select(item) { selected.push(item); }
    };
    const context = vm.createContext({$:native, Ref:() => [], delay() {}, Path:value => ({path:value}),
        Application:name => { assert.equal(name, 'Finder'); return application; },
        ObjC:{import() {}, castRefToObject:x => x, unwrap:x => x, deepUnwrap:x => x}});
    for (const file of ['text_selection.js', 'text_menu_paste.js', 'finder_paste.js'])
        vm.runInContext(fs.readFileSync(path.join(__dirname, '..', file), 'utf8'), context);
    context.selectionAttribute = (element, name) => {
        if (element === system && name === 'AXFocusedApplication') return finder;
        if (element === finder && name === 'AXFocusedUIElement') return focused;
        if (element === paste && name === 'AXEnabled') return pasteEnabled;
        throw new Error('Unexpected attribute ' + name);
    };
    context.selectionApplicationBundle = element => element === finder ? bundle : null;
    context.findEditMenuItem = (app, attribute, key) => {
        assert.equal(app, finder);
        assert.equal(key, 'v');
        return paste;
    };
    context.saveClipboardImage = (board, folder) => {
        saves.push(folder);
        return folder + '/Screenshot 2026-01-05 at 07.08.09.png';
    };
    const run = () => JSON.parse(context.runFinderPaste({}));
    return {run, presses, saves, selected, paste};
}

test('an image is saved in the insertion location and selected there', () => {
    const {run, presses, saves, selected} = finderFixture();
    assert.deepEqual(run(), {ok:true, result:'saved Screenshot 2026-01-05 at 07.08.09.png'});
    assert.deepEqual(saves, ['/Users/me/Work']);
    assert.deepEqual(selected, [{path:'/Users/me/Work/Screenshot 2026-01-05 at 07.08.09.png'}]);
    assert.deepEqual(presses, []);
});

test('without a folder from Finder, an image goes to the Desktop only when no window is open', () => {
    const closed = finderFixture({insertion:null, frontWindow:null});
    assert.equal(closed.run().ok, true);
    assert.deepEqual(closed.saves, ['/Users/me/Desktop']);
    // Recents and search windows have no folder; Finder's own New Folder is unavailable there too.
    const recents = finderFixture({insertion:null});
    assert.deepEqual(recents.run(), {ok:false, error:'This Finder window has no folder to paste into.'});
    assert.deepEqual(recents.saves, []);
});

test('text focus and the Get Info window keep Finder’s own paste for an image', () => {
    for (const options of [{editable:true}, {frontWindow:'informationWindow'}]) {
        const {run, presses, saves, paste} = finderFixture(options);
        assert.deepEqual(run(), {ok:true, result:'pasted'});
        assert.deepEqual(presses, [{element:paste, action:'AXPress'}]);
        assert.deepEqual(saves, []);
    }
});

test('copied files press Paste even though their icon is an image', () => {
    const {run, presses, saves, paste} = finderFixture({types:['public.file-url', 'public.tiff']});
    assert.deepEqual(run(), {ok:true, result:'pasted'});
    assert.deepEqual(presses, [{element:paste, action:'AXPress'}]);
    assert.deepEqual(saves, []);
});

test('a disabled Paste is left alone and is not an error', () => {
    const {run, presses} = finderFixture({types:['public.utf8-plain-text'], pasteEnabled:false});
    assert.deepEqual(run(), {ok:true, result:'nothing to paste'});
    assert.deepEqual(presses, []);
});

test('nothing is pasted into another app that came to the front meanwhile', () => {
    for (const types of [['public.png'], ['public.file-url']]) {
        const {run, presses, saves} = finderFixture({types, bundle:'com.apple.TextEdit'});
        assert.deepEqual(run(), {ok:false, error:'Finder is no longer frontmost.'});
        assert.deepEqual(presses, []);
        assert.deepEqual(saves, []);
    }
});

test('the runtime can load again into the same context, as BTT reuses one', () => {
    const context = vm.createContext({ObjC:{import() {}}});
    for (let run = 0; run < 2; run++) {
        for (const file of ['text_selection.js', 'text_menu_paste.js', 'finder_paste.js'])
            vm.runInContext(fs.readFileSync(path.join(__dirname, '..', file), 'utf8'), context);
    }
    assert.equal(typeof context.runFinderPaste, 'function');
});
