const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

// The document has block boundaries; Firefox's AXValue does not. Native Paste
// uses DOM endpoints, independently of the flattened AX offset setter.
//
// The native adapter models Firefox main as of 2026-10-17, from
// accessible/mac/GeckoTextMarker.mm, accessible/mac/MOXTextMarkerDelegate.mm,
// accessible/base/TextLeafRange.cpp and accessible/base/nsAccUtils.cpp:
// - AXIndexForTextMarker is the flat length from the document start, with no
//   separator between text nodes (GeckoTextMarkerRange::Length).
// - AXUIElementForTextMarker (GeckoTextMarker::Leaf) returns the previous
//   character's node for any offset-0 marker, and a text field in place of its
//   only text node (nsAccUtils::MustPrune).
// - AXNextTextMarkerForTextMarker from a node's trailing edge is the next node's
//   first character at the same flat index; past the document end it has no value
//   (TextLeafPoint::FindBoundary, BOUNDARY_CHAR).
// - AXPreviousTextMarkerForTextMarker from offset 0 is the previous node's last
//   character, one UTF-16 unit per step (TextLeafPoint::NeighborLeafPoint).
// - AXParagraphTextMarkerRangeForTextMarker spans a block's first node start to
//   the next block's first node start, or the document end
//   (TextLeafPoint::FindParagraphSameAcc).
// - AXSelectedTextRange and AXValue are flat and relative to the focused element.
function editorFixture({paragraphs = ['first', 'mIxEd tail'], blocks = paragraphs.map(text => [text]),
    chrome = [], field = false, node = 1, start = 0, endNode = node, end = 5, refuseMarkers = false} = {}) {
    const editor = {}, system = {}, app = {}, menu = {};
    const leaves = chrome.map((text, block) => ({text, block:'chrome' + block, editor:false}));
    blocks.forEach((runs, block) => runs.forEach(text => leaves.push({text, block, editor:true})));
    const state = {leaves, selection:{start:{node,offset:start}, end:{node:endNode,offset:end}},
        clipboard:'unrelated', presses:0};
    const equal = (a,b) => JSON.stringify(a) === JSON.stringify(b);
    const length = node => state.leaves[node].text.length;
    const flatIndex = point => state.leaves.slice(0,point.node).reduce((sum,leaf) => sum + leaf.text.length, 0) + point.offset;
    const editorStart = () => flatIndex({node:state.leaves.findIndex(leaf => leaf.editor), offset:0});
    const editorText = () => state.leaves.filter(leaf => leaf.editor).map(leaf => leaf.text).join('');
    const rangeText = range => state.leaves.map(leaf => leaf.text).join('').slice(flatIndex(range.start),flatIndex(range.end));
    const offsets = () => ({location:flatIndex(state.selection.start) - editorStart(),
        length:flatIndex(state.selection.end) - flatIndex(state.selection.start)});
    let markerIdentity = 0;
    const attribute = (element,name) => {
        if (element === system) return name === 'AXFocusedApplication' ? app : editor;
        assert.equal(element,editor);
        if (name === 'AXSelectedTextMarkerRange')
            return {...state.selection, identity:++markerIdentity};
        return {AXValue:editorText(), AXSelectedText:rangeText(state.selection),
            AXSelectedTextRange:offsets()}[name];
    };
    const native = nativeEditor({state, editor, menu, equal, rangeText, flatIndex, length, field, refuseMarkers});
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
        offsets:r=>r, range:(r,text)=>({location:r.location,length:text.length}),
        markers:context.textMarkerSelection(editor,attribute)};
    const run = replacement => context.pasteSelectionUsingMenu(system,app,
        {element:editor,range:offsets(),text:rangeText(state.selection)},replacement,helpers);
    const texts = () => state.leaves.filter(leaf => leaf.editor).map(leaf => leaf.text);
    return {state,run,rangeText,helpers,texts};
}

function firefoxMarkerModel({state, editor, length, field}) {
    const leafFor = point => {
        const node = point.offset === 0 && point.node > 0 ? point.node - 1 : point.node;
        return field && state.leaves[node].editor ? editor : {leaf:node};
    };
    const next = point => {
        if (point.offset + 1 < length(point.node)) return {node:point.node, offset:point.offset + 1};
        if (point.node + 1 < state.leaves.length) return {node:point.node + 1, offset:0};
        return point.offset < length(point.node) ? {node:point.node, offset:length(point.node)} : null;
    };
    const previous = point => {
        if (point.offset > 0) return {node:point.node, offset:point.offset - 1};
        return point.node > 0 ? {node:point.node - 1, offset:length(point.node - 1) - 1} : null;
    };
    const paragraph = point => {
        const block = state.leaves[point.node].block;
        const first = state.leaves.findIndex(leaf => leaf.block === block);
        const last = state.leaves.findLastIndex(leaf => leaf.block === block);
        const end = last + 1 < state.leaves.length ? {node:last + 1, offset:0} : {node:last, offset:length(last)};
        return {start:{node:first, offset:0}, end};
    };
    return {leafFor, next, previous, paragraph};
}

function nativeEditor({state, editor, menu, equal, rangeText, flatIndex, length, field, refuseMarkers}) {
    const native = value => value;
    const NO_VALUE = -25212;
    const before = (a,b) => a.node < b.node || (a.node === b.node && a.offset < b.offset);
    const {leafFor, next, previous, paragraph} = firefoxMarkerModel({state, editor, length, field});
    Object.assign(native, {
        kAXErrorNoValue:NO_VALUE,
        CFEqual:equal, NSPasteboard:{generalPasteboard:{}},
        NSMutableArray:{get array() { const values=[];
            return {addObject:v=>values.push(v), values}; }},
        AXUIElementCopyParameterizedAttributeValue(element,name,value,result) {
            assert.equal(element,editor);
            const point = value;
            switch (name) {
                case 'AXStartTextMarkerForTextMarkerRange': result[0]=value.start; break;
                case 'AXEndTextMarkerForTextMarkerRange': result[0]=value.end; break;
                case 'AXUIElementForTextMarker': result[0]=leafFor(point); break;
                case 'AXIndexForTextMarker': result[0]=flatIndex(point); break;
                case 'AXStringForTextMarkerRange': result[0]=rangeText(value); break;
                case 'AXTextMarkerRangeForUnorderedTextMarkers': {
                    const [a,b]=value.values;
                    result[0]=before(b,a) ? {start:b,end:a} : {start:a,end:b}; break;
                }
                case 'AXNextTextMarkerForTextMarker': result[0]=next(point); break;
                case 'AXPreviousTextMarkerForTextMarker': result[0]=previous(point); break;
                case 'AXParagraphTextMarkerRangeForTextMarker': result[0]=paragraph(point); break;
                default: throw Error('Unexpected parameter '+name);
            }
            return result[0] === null ? NO_VALUE : 0;
        },
        AXUIElementPerformAction(element,action) {
            assert.equal(element,menu); assert.equal(action,'AXPress'); state.presses++;
            // Deleting a DOM range joins its end node, and that node's following
            // runs, into the start node's block before the clipboard text goes in.
            const from=state.selection.start, to=state.selection.end;
            const merged={...state.leaves[from.node], text:state.leaves[from.node].text.slice(0,from.offset)
                + state.clipboard + state.leaves[to.node].text.slice(to.offset)};
            const tail=state.leaves.slice(to.node+1).map(leaf => leaf.block === state.leaves[to.node].block
                ? {...leaf, block:merged.block} : leaf);
            state.leaves=[...state.leaves.slice(0,from.node), merged, ...tail];
            const caret={node:from.node, offset:from.offset+state.clipboard.length};
            state.selection={start:caret,end:caret};
            return 0;
        },
        AXUIElementSetAttributeValue(element,name,range) {
            assert.equal(element,editor);
            if (name === 'AXSelectedTextRange') throw Error('Firefox numeric selection writes are never used');
            assert.equal(name,'AXSelectedTextMarkerRange');
            if (refuseMarkers) return -25204;
            state.selection={start:range.start,end:range.end};
            return 0;
        }
    });
    return native;
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
    helpers.markers.endingAt=(caret,text)=>{
        const range=resolve(caret,text);
        state.selection=moved;
        return range;
    };
    assert.throws(()=>run('MIXED'));
    assert.deepEqual(state.selection,moved);
    assert.equal(state.presses,1);
});
