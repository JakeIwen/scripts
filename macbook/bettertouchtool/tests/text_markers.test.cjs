const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

// The document has paragraph separators; Firefox's AXValue does not. Native
// Paste uses DOM endpoints, independently of the flattened AX offset setter.
function editorFixture({paragraphs = ['first', 'mIxEd tail'], start = 0, end = 5,
    node = 1, legacy = false, refuseMarkers = false} = {}) {
    const editor = {}, system = {}, app = {}, menu = {};
    const state = {paragraphs:[...paragraphs], selection:{start:{node,offset:start},
        end:{node,offset:end}}, clipboard:'unrelated', presses:0, numericWrites:0};
    const equal = (a,b) => JSON.stringify(a) === JSON.stringify(b);
    const flatIndex = point => state.paragraphs.slice(0,point.node).join('').length + point.offset;
    const domIndex = point => flatIndex(point) + point.node;
    const domPoint = index => {
        for (let node = 0; node < state.paragraphs.length; node++) {
            if (index <= state.paragraphs[node].length) return {node,offset:index};
            index -= state.paragraphs[node].length + 1;
        }
        throw Error('DOM point outside editor');
    };
    const flatPoint = index => {
        for (let node = 0; node < state.paragraphs.length; node++) {
            if (index <= state.paragraphs[node].length) return {node,offset:index};
            index -= state.paragraphs[node].length;
        }
        throw Error('AX point outside editor');
    };
    const rangeText = range => state.paragraphs.join('').slice(flatIndex(range.start),flatIndex(range.end));
    const offsets = () => ({location:flatIndex(state.selection.start),
        length:flatIndex(state.selection.end)-flatIndex(state.selection.start)});
    const attribute = (element,name) => {
        if (element === system) return name === 'AXFocusedApplication' ? app : editor;
        assert.equal(element,editor);
        return {AXValue:state.paragraphs.join(''), AXSelectedText:rangeText(state.selection),
            AXSelectedTextRange:offsets(), AXSelectedTextMarkerRange:state.selection}[name];
    };
    const native = nativeEditor({state, editor, menu, equal, rangeText,
        domIndex, domPoint, flatPoint, refuseMarkers});
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
        markers:legacy ? null : context.textMarkerSelection(editor,attribute)};
    const run = replacement => context.pasteSelectionUsingMenu(system,app,
        {element:editor,range:offsets(),text:rangeText(state.selection)},replacement,helpers);
    return {state,run,rangeText,helpers};
}

function nativeEditor({state, editor, menu, equal, rangeText,
    domIndex, domPoint, flatPoint, refuseMarkers}) {
    const native = value => value;
    Object.assign(native, {
        CFEqual:equal, NSPasteboard:{generalPasteboard:{}},
        NSMutableArray:{get array() { const values=[];
            return {addObject:v=>values.push(v), values}; }},
        AXUIElementCopyParameterizedAttributeValue(element,name,value,result) {
            assert.equal(element,editor);
            const point = value;
            switch (name) {
                case 'AXStartTextMarkerForTextMarkerRange': result[0]=value.start; break;
                case 'AXEndTextMarkerForTextMarkerRange': result[0]=value.end; break;
                case 'AXUIElementForTextMarker': result[0]={node:point.node}; break;
                case 'AXStringForTextMarkerRange': result[0]=rangeText(value); break;
                case 'AXTextMarkerRangeForUnorderedTextMarkers':
                    result[0]={start:value.values[0],end:value.values[1]}; break;
                case 'AXPreviousTextMarkerForTextMarker': {
                    let node=point.node, offset=point.offset;
                    if (!offset) { node--; offset=state.paragraphs[node]?.length; }
                    if (node < 0) return -25212;
                    const previous=Array.from(state.paragraphs[node].slice(0,offset)).at(-1);
                    result[0]={node,offset:offset-(previous?.length || 0)}; break;
                }
                default: throw Error('Unexpected parameter '+name);
            }
            return 0;
        },
        AXUIElementPerformAction(element,action) {
            assert.equal(element,menu); assert.equal(action,'AXPress'); state.presses++;
            const document=state.paragraphs.join('\n');
            const from=domIndex(state.selection.start), to=domIndex(state.selection.end);
            state.paragraphs=(document.slice(0,from)+state.clipboard+document.slice(to)).split('\n');
            const caret=domPoint(from+state.clipboard.length);
            state.selection={start:caret,end:caret};
            return 0;
        },
        AXUIElementSetAttributeValue(element,name,range) {
            assert.equal(element,editor);
            if (name === 'AXSelectedTextRange') {
                state.numericWrites++;
                state.selection={start:flatPoint(range.location),end:flatPoint(range.location+range.length)};
            } else {
                assert.equal(name,'AXSelectedTextMarkerRange');
                if (refuseMarkers) return -25204;
                state.selection=range;
            }
            return 0;
        }
    });
    return native;
}

test('the old flat-offset round trip passes text checks while deleting a paragraph', () => {
    const {state,run}=editorFixture({legacy:true});
    run('MIXED');
    assert.deepEqual(state.paragraphs,['first','MIXED tail']);
    run('mixed');
    assert.deepEqual(state.paragraphs,['firstmixed tail']);
});

test('native markers keep the paragraph intact across upper then lower without refocus', () => {
    const {state,run,rangeText}=editorFixture();
    run('MIXED');
    assert.deepEqual(state.selection,{start:{node:1,offset:0},end:{node:1,offset:5}});
    run('mixed');
    assert.deepEqual(state.paragraphs,['first','mixed tail']);
    assert.equal(rangeText(state.selection),'mixed');
    assert.equal(state.numericWrites,0);
    assert.equal(state.presses,2);
    assert.equal(state.clipboard,'unrelated');
});

test('length-changing Unicode transformations reselect only the inserted text', () => {
    const {state,run}=editorFixture({paragraphs:['same','ß😀 same'],end:3});
    run('SS😀');
    assert.deepEqual(state.paragraphs,['same','SS😀 same']);
    assert.deepEqual(state.selection,{start:{node:1,offset:0},end:{node:1,offset:4}});
    run('ss😀');
    assert.deepEqual(state.paragraphs,['same','ss😀 same']);
});

test('Firefox URL selections also use native markers and retain all surrounding text', () => {
    const url='https://example.test/abc/def';
    const start=url.indexOf('abc');
    const {state,run}=editorFixture({paragraphs:[url],node:0,start,end:start+3});
    run('ABC');
    run('abc');
    assert.deepEqual(state.paragraphs,[url]);
    assert.deepEqual(state.selection,{start:{node:0,offset:start},end:{node:0,offset:start+3}});
    assert.equal(state.numericWrites,0);
});

test('repeated text never causes a search to select an earlier occurrence', () => {
    const {state,run}=editorFixture({paragraphs:['MIXED','mixed MIXED'],start:6,end:11});
    run('mixed');
    assert.deepEqual(state.paragraphs,['MIXED','mixed mixed']);
    assert.deepEqual(state.selection,{start:{node:1,offset:6},end:{node:1,offset:11}});
});

test('an old selection spanning an invisible paragraph separator is refused before Paste', () => {
    const {state,run}=editorFixture();
    state.selection.start={node:0,offset:5};
    assert.throws(()=>run('MIXED'),/crosses rich-text nodes/);
    assert.equal(state.presses,0);
    assert.deepEqual(state.paragraphs,['first','mIxEd tail']);
});

test('marker refusal reports the completed edit without falling back to numeric selection', () => {
    const {state,run}=editorFixture({refuseMarkers:true});
    assert.throws(()=>run('MIXED'),/Text transformed, but selection could not be restored/);
    assert.deepEqual(state.paragraphs,['first','MIXED tail']);
    assert.equal(state.presses,1);
    assert.equal(state.numericWrites,0);
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
    assert.throws(()=>run('MIXED'),/selection or text changed/);
    assert.deepEqual(state.selection,moved);
    assert.equal(state.numericWrites,0);
    assert.equal(state.presses,1);
});
