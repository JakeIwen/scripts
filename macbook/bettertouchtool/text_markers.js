// Rich editors can give the end of one paragraph and the start of the next
// the same flat offset. Keep native node positions when restoring a selection.
function textMarkerParameter(element, name, value) {
    const result = Ref();
    const status = $.AXUIElementCopyParameterizedAttributeValue(element, $(name), value, result);
    if (status !== 0) throw new Error('Cannot resolve text markers: ' + name + ' (' + status + ').');
    const resolved = ObjC.castRefToObject(result[0]);
    if (resolved === null || resolved === undefined)
        throw new Error('The editor returned an invalid text marker.');
    return resolved;
}

function textMarkerSelection(element, attribute) {
    const parameter = (name, value) => textMarkerParameter(element, name, value);
    function samePoint(first, second) {
        const firstIndex = Number(ObjC.unwrap(parameter('AXIndexForTextMarker', first)));
        const secondIndex = Number(ObjC.unwrap(parameter('AXIndexForTextMarker', second)));
        if (![firstIndex, secondIndex].every(index => Number.isSafeInteger(index) && index >= 0))
            throw new Error('The editor returned an invalid text-marker position.');
        return firstIndex === secondIndex && $.CFEqual(
            parameter('AXUIElementForTextMarker', first), parameter('AXUIElementForTextMarker', second));
    }
    function same(first, second) {
        // AXTextMarkerRange objects do not compare by endpoints with CFEqual.
        // An index alone is ambiguous at paragraph boundaries; require the node too.
        return ['AXStartTextMarkerForTextMarkerRange', 'AXEndTextMarkerForTextMarkerRange']
            .every(name => samePoint(parameter(name, first), parameter(name, second)));
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
        const start = parameter('AXStartTextMarkerForTextMarkerRange', range);
        const end = parameter('AXEndTextMarkerForTextMarkerRange', range);
        const first = parameter('AXUIElementForTextMarker', start);
        const last = parameter('AXUIElementForTextMarker', end);
        // Firefox's flattened AX text also omits structural separators between
        // nodes. Until a lossless representation is available, do not replace a
        // selection spanning them with text that could silently omit line breaks.
        if (!$.CFEqual(first, last))
            throw new Error('Selection crosses rich-text nodes; Firefox cannot verify their separators. Nothing pasted.');
    }
    function endingAt(caret, replacement) {
        const end = parameter('AXEndTextMarkerForTextMarkerRange', caret);
        let start = end;
        const deadline = Date.now() + 2000;
        // Walk native character boundaries, not UTF-16 offsets or a search for
        // matching text elsewhere. Stop at the nearest exact suffix so a zero-
        // width paragraph boundary before the inserted text stays unselected.
        for (let steps = 0; steps <= replacement.length; steps++) {
            if (Date.now() > deadline) break;
            const range = between(start, end);
            const value = text(range);
            if (value === replacement) {
                validate(range);
                return range;
            }
            if (typeof value !== 'string' || !replacement.endsWith(value)) break;
            const previous = parameter('AXPreviousTextMarkerForTextMarker', start);
            if (samePoint(previous, start)) break;
            start = previous;
        }
        throw new Error('The editor could not identify the inserted text’s native boundaries.');
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
