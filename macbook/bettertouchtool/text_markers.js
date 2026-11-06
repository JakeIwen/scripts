// Firefox exposes editable text as native markers over separate text nodes.
// Its flat AX offsets cannot tell the end of one paragraph from the start of
// the next, so paragraph checks and reselection use the marker semantics of
// accessible/mac/GeckoTextMarker.mm and accessible/base/TextLeafRange.cpp.
function textMarkerParameter(element, name, value, options) {
    const result = Ref();
    const status = $.AXUIElementCopyParameterizedAttributeValue(element, $(name), value, result);
    // Firefox answers a neighbor request at a document edge with no value.
    if (options && options.optional && status === Number($.kAXErrorNoValue)) return null;
    if (status !== 0) throw new Error('Cannot resolve text markers: ' + name + ' (' + status + ').');
    const resolved = ObjC.castRefToObject(result[0]);
    if (resolved === null || resolved === undefined)
        throw new Error('The editor returned an invalid text marker.');
    return resolved;
}

// Whole-line paste restores carets through native marker steps, never ambiguous
// numeric AX ranges. Kept outside the selection factory so its existing marker
// validation logic stays separate from this new operation.
function textMarkerLineCarets({endpoints, between, parameter, index, paragraph, same}) {
    function startOf(range) {
        const start = endpoints(range).start;
        return between(start, start);
    }
    function forwardCaret(caret, steps) {
        let marker = endpoints(caret).end;
        const originalParagraph = paragraph(marker);
        const target = index(marker) + steps;
        // Paragraph boundaries can have duplicate flat indices; a finite budget
        // refuses malformed/non-progressing native marker graphs.
        for (let budget = steps * 2 + 2; index(marker) < target && budget > 0; budget--) {
            marker = parameter('AXNextTextMarkerForTextMarker', marker, {optional:true});
            if (marker === null) throw new Error('The editor could not resolve the restored caret.');
        }
        if (index(marker) !== target || !same(originalParagraph, paragraph(marker)))
            throw new Error('The editor could not resolve the restored caret.');
        return between(marker, marker);
    }
    return {startOf, forwardCaret};
}

function textMarkerSelection(element, attribute) {
    const parameter = (name, value, options) => textMarkerParameter(element, name, value, options);
    function index(marker) {
        const value = Number(ObjC.unwrap(parameter('AXIndexForTextMarker', marker)));
        if (!Number.isSafeInteger(value) || value < 0)
            throw new Error('The editor returned an invalid text-marker position.');
        return value;
    }
    function point(marker) {
        // GeckoTextMarker::Leaf() reports the previous node for any offset-0
        // marker, and the next character boundary of a node's trailing edge is
        // the following node's first character at the same flat index. Together
        // they tell apart the two markers that share an index at a node boundary.
        const position = index(marker);
        const next = parameter('AXNextTextMarkerForTextMarker', marker, {optional: true});
        return {index: position, leaf: parameter('AXUIElementForTextMarker', marker),
            trailing: next === null || index(next) === position};
    }
    function samePoint(first, second) {
        const a = point(first), b = point(second);
        return a.index === b.index && a.trailing === b.trailing && $.CFEqual(a.leaf, b.leaf);
    }
    function endpoints(range) {
        return {start: parameter('AXStartTextMarkerForTextMarkerRange', range),
            end: parameter('AXEndTextMarkerForTextMarkerRange', range)};
    }
    function same(first, second) {
        // AXTextMarkerRange objects do not compare by endpoints with CFEqual.
        const a = endpoints(first), b = endpoints(second);
        return samePoint(a.start, b.start) && samePoint(a.end, b.end);
    }
    function collapsed(range) {
        const {start, end} = endpoints(range);
        return samePoint(start, end);
    }
    function paragraph(marker) {
        // Firefox resolves the paragraph from the marker's native node, and it
        // runs to the next paragraph's start, so it carries the break.
        return parameter('AXParagraphTextMarkerRangeForTextMarker', marker);
    }
    function paragraphAt(range) {
        return paragraph(endpoints(range).start);
    }
    function text(range) {
        return ObjC.unwrap(parameter('AXStringForTextMarkerRange', range));
    }
    function between(start, end) {
        const pair = $.NSMutableArray.array;
        pair.addObject(start);
        pair.addObject(end);
        return parameter('AXTextMarkerRangeForUnorderedTextMarkers', pair);
    }
    function validate(range) {
        // Firefox's flat text omits the break between paragraphs, so a selection
        // whose endpoints lie in different paragraphs would be pasted back merged.
        // Firefox resolves each endpoint's paragraph from its native node, which
        // also covers an endpoint sitting on the far side of a paragraph break.
        const {start, end} = endpoints(range);
        if (!same(paragraph(start), paragraph(end)))
            throw new Error('The selection spans a paragraph break; select text within one paragraph. Nothing pasted.');
    }
    function endingAt(caret, replacement, selected) {
        // Each backward character step consumes exactly one UTF-16 unit, even
        // across node boundaries, so stepping back the replacement's length
        // reaches its first character without selecting a zero-width break.
        // `selected` ({offset, length}) is the part of the replacement to select.
        const unresolved = () => new Error('The editor could not identify the inserted text’s native boundaries.');
        const back = (marker, steps) => {
            for (let step = 0; step < steps; step++) {
                marker = parameter('AXPreviousTextMarkerForTextMarker', marker, {optional: true});
                if (marker === null) throw unresolved();
            }
            return marker;
        };
        const end = back(parameter('AXEndTextMarkerForTextMarkerRange', caret),
            replacement.length - selected.offset - selected.length);
        const range = between(back(end, selected.length), end);
        if (text(range) !== replacement.slice(selected.offset, selected.offset + selected.length)) throw unresolved();
        validate(range);
        return range;
    }
    return {
        read: () => attribute(element, 'AXSelectedTextMarkerRange'),
        text,
        same,
        collapsed,
        paragraphAt,
        ...textMarkerLineCarets({endpoints, between, parameter, index, paragraph, same}),
        validate,
        endingAt,
        select: function (range) {
            if ($.AXUIElementSetAttributeValue(element, $('AXSelectedTextMarkerRange'), range) !== 0)
                throw new Error('The editor refused text-marker reselection.');
        }
    };
}
