const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const {firefoxEditor} = require('./firefox_editor_fixture.cjs');

// Native Paste uses DOM endpoints, independently of the flattened AX offset
// setter; the document model is firefox_editor_fixture.cjs.
function editorFixture(options) {
    const document = firefoxEditor(options);
    const {state, editor, equal, rangeText, offsets, texts} = document;
    const system = {}, app = {}, menu = {};
    Object.assign(state, {clipboard:'unrelated', presses:0});
    const attribute = (element,name) => {
        if (element === system) return name === 'AXFocusedApplication' ? app : editor;
        assert.equal(element,editor);
        return document.attribute(name);
    };
    const native = value => value;
    Object.assign(native, document.native, {
        CFEqual:equal, NSPasteboard:{generalPasteboard:{}},
        AXUIElementPerformAction(element,action) {
            assert.equal(element,menu); assert.equal(action,'AXPress'); state.presses++;
            document.replaceSelection(state.clipboard);
            return 0;
        }
    });
    const context=vm.createContext({$:native,Ref:()=>[],delay:()=>{},
        ObjC:{import() {},unwrap:v=>v,castRefToObject:v=>v}});
    for (const file of ['text_markers.js','text_menu_paste.js'])
        vm.runInContext(fs.readFileSync(path.join(__dirname,'..',file),'utf8'),context);
    context.findPasteMenuItem=()=>menu;
    context.withTemporaryClipboard=(board,text,action)=>{
        const original=state.clipboard;
        try { state.clipboard=text; action(()=>true); } finally { state.clipboard=original; }
    };
    const helpers={attribute:(element,name)=>element===menu ? true : attribute(element,name),
        offsets:r=>r, range:(r,selected)=>({location:r.location+selected.offset,length:selected.length}),
        markers:context.textMarkerSelection(editor,attribute)};
    const run = (replacement, selected = {offset:0,length:replacement.length}) => context.pasteSelectionUsingMenu(system,app,
        {element:editor,range:offsets(),text:rangeText(state.selection)},replacement,helpers,selected);
    return {state,run,rangeText,helpers,texts};
}

test('independently allocated marker ranges compare by endpoint nodes and positions', () => {
    const {helpers}=editorFixture();
    const first=helpers.markers.read(), second=helpers.markers.read();
    assert.notDeepEqual(first,second);
    assert.equal(helpers.markers.same(first,second),true);
});

test('equal flat offsets on opposite sides of a paragraph are different marker positions', () => {
    // Both markers report the first paragraph's node as their AXUIElement.
    const {helpers}=editorFixture();
    const range=helpers.markers.read();
    const acrossBreak={...range,start:{node:0,offset:5}};
    assert.equal(helpers.markers.same(range,acrossBreak),false);
});

test('a selection starting at a paragraph start is transformed although Firefox reports the previous node', () => {
    // Live failure 2026-10-17T22:28: the start marker at offset 0 resolved to the
    // first paragraph's node, so a same-node guard refused a one-paragraph selection.
    const {state,run,rangeText,texts}=editorFixture();
    run('MIXED');
    assert.deepEqual(texts(),['first','MIXED tail']);
    assert.deepEqual(state.selection,{start:{node:1,offset:0},end:{node:1,offset:5}});
    run('mixed');
    assert.deepEqual(texts(),['first','mixed tail']);
    assert.equal(rangeText(state.selection),'mixed');
    assert.equal(state.presses,2);
    assert.equal(state.clipboard,'unrelated');
});

test('length-changing Unicode transformations reselect only the inserted text', () => {
    const {state,run,texts}=editorFixture({paragraphs:['same','ß😀 same'],end:3});
    run('SS😀');
    assert.deepEqual(texts(),['same','SS😀 same']);
    assert.deepEqual(state.selection,{start:{node:1,offset:0},end:{node:1,offset:4}});
    run('ss😀');
    assert.deepEqual(texts(),['same','ss😀 same']);
});

test('wrapping at a paragraph start selects only the original text inside the pair', () => {
    const {state,run,rangeText,texts}=editorFixture();
    run('(mIxEd)',{offset:1,length:5});
    assert.deepEqual(texts(),['first','(mIxEd) tail']);
    assert.deepEqual(state.selection,{start:{node:1,offset:1},end:{node:1,offset:6}});
    assert.equal(rangeText(state.selection),'mIxEd');
    // A second opening character wraps the still-selected text again.
    run('"mIxEd"',{offset:1,length:5});
    assert.deepEqual(texts(),['first','("mIxEd") tail']);
    assert.equal(rangeText(state.selection),'mIxEd');
    assert.equal(state.clipboard,'unrelated');
});

test('a whole URL selected in the pruned URL bar transforms and stays selected', () => {
    // Clicking the URL bar selects everything from offset 0; the start marker's
    // AXUIElement is then a toolbar node, while the end marker's is the field.
    const url='https://example.test/abc/def';
    const {state,run,texts,helpers}=editorFixture({paragraphs:[url],chrome:['Reload','Search'],field:true,
        node:2,start:0,end:url.length});
    assert.equal(helpers.markers.text(helpers.markers.read()),url);
    run(url.toUpperCase());
    assert.deepEqual(texts(),[url.toUpperCase()]);
    assert.deepEqual(state.selection,{start:{node:2,offset:0},end:{node:2,offset:url.length}});
    run(url);
    assert.deepEqual(texts(),[url]);
    assert.deepEqual(state.selection,{start:{node:2,offset:0},end:{node:2,offset:url.length}});
    assert.equal(state.presses,2);
});

test('Firefox URL selections in the middle of the field retain all surrounding text', () => {
    const url='https://example.test/abc/def';
    const start=url.indexOf('abc');
    const {state,run,texts}=editorFixture({paragraphs:[url],chrome:['Reload'],field:true,node:1,start,end:start+3});
    run('ABC');
    run('abc');
    assert.deepEqual(texts(),[url]);
    assert.deepEqual(state.selection,{start:{node:1,offset:start},end:{node:1,offset:start+3}});
});

test('a selection ending at the document end has no next marker and still reselects', () => {
    const {state,run,texts}=editorFixture({paragraphs:['first','mIxEd'],start:0,end:5});
    run('MIXED');
    assert.deepEqual(texts(),['first','MIXED']);
    assert.deepEqual(state.selection,{start:{node:1,offset:0},end:{node:1,offset:5}});
    run('mixed');
    assert.deepEqual(texts(),['first','mixed']);
});

test('repeated text never causes a search to select an earlier occurrence', () => {
    const {state,run,texts}=editorFixture({paragraphs:['MIXED','mixed MIXED'],start:6,end:11});
    run('mixed');
    assert.deepEqual(texts(),['MIXED','mixed mixed']);
    assert.deepEqual(state.selection,{start:{node:1,offset:6},end:{node:1,offset:11}});
});

test('a selection across formatting runs inside one paragraph is transformed', () => {
    const {state,run,texts}=editorFixture({blocks:[['first'],['mI','xEd',' tail']],node:1,start:0,endNode:2,end:3});
    run('MIXED');
    assert.deepEqual(texts(),['first','MIXED',' tail']);
    assert.deepEqual(state.selection,{start:{node:1,offset:0},end:{node:1,offset:5}});
    run('mixed');
    assert.deepEqual(texts(),['first','mixed',' tail']);
});

test('an old selection spanning an invisible paragraph separator is refused before Paste', () => {
    const {state,run,texts}=editorFixture({node:0,start:5,endNode:1,end:5});
    assert.throws(()=>run('MIXED'));
    assert.equal(state.presses,0);
    assert.deepEqual(texts(),['first','mIxEd tail']);
});

test('a selection ending at the start of the next paragraph is refused before Paste', () => {
    // Shift+Down from a line start selects up to the next paragraph's offset 0;
    // the flat text and numeric range look like the first paragraph alone.
    const {state,run,texts,helpers}=editorFixture({paragraphs:['first','mIxEd tail','third'],
        node:1,start:0,endNode:2,end:0});
    assert.equal(helpers.markers.text(helpers.markers.read()),'mIxEd tail');
    assert.throws(()=>run('MIXED TAIL'));
    assert.equal(state.presses,0);
    assert.deepEqual(texts(),['first','mIxEd tail','third']);
});

test('a selection with characters in two paragraphs is refused before Paste', () => {
    const {state,run,texts}=editorFixture({node:0,start:2,endNode:1,end:3});
    assert.throws(()=>run('RSTMIX'));
    assert.equal(state.presses,0);
    assert.deepEqual(texts(),['first','mIxEd tail']);
});

test('marker refusal reports the completed edit without falling back to numeric selection', () => {
    const {state,run,texts}=editorFixture({refuseMarkers:true});
    assert.throws(()=>run('MIXED'));
    assert.deepEqual(texts(),['first','MIXED tail']);
    assert.equal(state.presses,1);
});

test('moving the selection while markers are resolved never overwrites the new selection', () => {
    const {state,run,helpers}=editorFixture();
    const resolve=helpers.markers.endingAt;
    const moved={start:{node:0,offset:1},end:{node:0,offset:2}};
    helpers.markers.endingAt=(caret,text,selected)=>{
        const range=resolve(caret,text,selected);
        state.selection=moved;
        return range;
    };
    assert.throws(()=>run('MIXED'));
    assert.deepEqual(state.selection,moved);
    assert.equal(state.presses,1);
});
