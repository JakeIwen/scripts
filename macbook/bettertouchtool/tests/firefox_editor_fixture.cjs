// A Firefox editable document behind its macOS accessibility interface. The
// document has block boundaries; Firefox's AXValue does not.
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
const assert = require('node:assert/strict');

const NO_VALUE = -25212;

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

// `editor` is the focused element; `native` holds its marker functions, merged
// into each test's own `$`. Elements and leaves compare by value (`equal`).
function firefoxEditor({paragraphs = ['first', 'mIxEd tail'], blocks = paragraphs.map(text => [text]),
    chrome = [], field = false, node = 1, start = 0, endNode = node, end = 5, refuseMarkers = false} = {}) {
    const editor = {element:'firefox editor'};
    const leaves = chrome.map((text, block) => ({text, block:'chrome' + block, editor:false}));
    blocks.forEach((runs, block) => runs.forEach(text => leaves.push({text, block, editor:true})));
    const state = {leaves, selection:{start:{node,offset:start}, end:{node:endNode,offset:end}}};
    const equal = (a,b) => JSON.stringify(a) === JSON.stringify(b);
    const length = node => state.leaves[node].text.length;
    const flatIndex = point => state.leaves.slice(0,point.node).reduce((sum,leaf) => sum + leaf.text.length, 0) + point.offset;
    const editorStart = () => flatIndex({node:state.leaves.findIndex(leaf => leaf.editor), offset:0});
    const editorText = () => state.leaves.filter(leaf => leaf.editor).map(leaf => leaf.text).join('');
    const rangeText = range => state.leaves.map(leaf => leaf.text).join('').slice(flatIndex(range.start),flatIndex(range.end));
    const offsets = () => ({location:flatIndex(state.selection.start) - editorStart(),
        length:flatIndex(state.selection.end) - flatIndex(state.selection.start)});
    const texts = () => state.leaves.filter(leaf => leaf.editor).map(leaf => leaf.text);
    let markerIdentity = 0;
    const attribute = name => {
        if (name === 'AXSelectedTextMarkerRange')
            return {...state.selection, identity:++markerIdentity};
        return {AXValue:editorText(), AXSelectedText:rangeText(state.selection),
            AXSelectedTextRange:offsets()}[name];
    };
    // Deleting a DOM range joins its end node, and that node's following runs,
    // into the start node's block before `text` goes in; the caret follows it.
    const replaceSelection = text => {
        const from=state.selection.start, to=state.selection.end;
        const merged={...state.leaves[from.node], text:state.leaves[from.node].text.slice(0,from.offset)
            + text + state.leaves[to.node].text.slice(to.offset)};
        const tail=state.leaves.slice(to.node+1).map(leaf => leaf.block === state.leaves[to.node].block
            ? {...leaf, block:merged.block} : leaf);
        state.leaves=[...state.leaves.slice(0,from.node), merged, ...tail];
        const caret={node:from.node, offset:from.offset+text.length};
        state.selection={start:caret,end:caret};
    };
    const native = nativeMarkers({state, editor, equal, rangeText, flatIndex, length, field, refuseMarkers});
    return {editor, state, equal, rangeText, offsets, texts, attribute, replaceSelection, native};
}

function nativeMarkers({state, editor, rangeText, flatIndex, length, field, refuseMarkers}) {
    const before = (a,b) => a.node < b.node || (a.node === b.node && a.offset < b.offset);
    const {leafFor, next, previous, paragraph} = firefoxMarkerModel({state, editor, length, field});
    return {
        kAXErrorNoValue:NO_VALUE,
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
        AXUIElementSetAttributeValue(element,name,range) {
            assert.equal(element,editor);
            if (name === 'AXSelectedTextRange') throw Error('Firefox numeric selection writes are never used');
            assert.equal(name,'AXSelectedTextMarkerRange');
            if (refuseMarkers) return -25204;
            state.selection={start:range.start,end:range.end};
            return 0;
        }
    };
}

module.exports = {firefoxEditor};
