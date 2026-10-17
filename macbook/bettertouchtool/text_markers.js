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
        const paragraph = marker => parameter('AXParagraphTextMarkerRangeForTextMarker', marker);
        if (!same(paragraph(start), paragraph(end)))
            throw new Error('The selection spans a paragraph break; select text within one paragraph. Nothing pasted.');
    }
    function endingAt(caret, replacement) {
        // Each backward character step consumes exactly one UTF-16 unit, even
        // across node boundaries, so stepping back the replacement's length
        // reaches its first character without selecting a zero-width break.
        const end = parameter('AXEndTextMarkerForTextMarkerRange', caret);
        const unresolved = () => new Error('The editor could not identify the inserted text’s native boundaries.');
        let start = end;
        for (let step = 0; step < replacement.length; step++) {
            start = parameter('AXPreviousTextMarkerForTextMarker', start, {optional: true});
            if (start === null) throw unresolved();
        }
        const range = between(start, end);
        if (text(range) !== replacement) throw unresolved();
        validate(range);
        return range;
    }
    return {
        read: () => attribute(element, 'AXSelectedTextMarkerRange'),
        text,
        same,
        validate,
        endingAt,
        select: function (range) {
            if ($.AXUIElementSetAttributeValue(element, $('AXSelectedTextMarkerRange'), range) !== 0)
                throw new Error('The editor refused text-marker reselection.');
        }
    };
}
