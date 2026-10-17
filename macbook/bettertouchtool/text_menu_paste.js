// Native Paste bypasses broken AXSelectedText setters (Signal and Firefox).
// No Copy shortcut, submit, or navigation action is used.
ObjC.import('AppKit');

function copyPasteboard(board) {
    const version = Number(board.changeCount), copies = $.NSMutableArray.array, items = board.pasteboardItems;
    for (let i = 0; items && i < Number(items.count); i++) {
        const item = items.objectAtIndex(i), copy = $.NSPasteboardItem.alloc.init;
        for (let j = 0; j < Number(item.types.count); j++) {
            const type = item.types.objectAtIndex(j), data = item.dataForType(type);
            if (!data || data.length === undefined || !copy.setDataForType(data.copy, type))
                throw new Error('Could not preserve the current clipboard; nothing pasted.');
        }
        copies.addObject(copy);
    }
    if (Number(board.changeCount) !== version) throw new Error('Clipboard changed; nothing pasted.');
    return {version, copies};
}

function withTemporaryClipboard(board, text, action) {
    const saved = copyPasteboard(board);
    const item = $.NSPasteboardItem.alloc.init;
    if (!item.setStringForType(text, $.NSPasteboardTypeString)) throw new Error('Could not prepare transformed text.');
    for (const type of ['org.nspasteboard.TransientType', 'org.nspasteboard.ConcealedType']) {
        if (!item.setStringForType('1', type)) throw new Error('Could not mark temporary clipboard data.');
    }
    let owned = null;
    try {
        if (Number(board.changeCount) !== saved.version) throw new Error('Clipboard changed; nothing pasted.');
        owned = Number(board.prepareForNewContentsWithOptions($.NSPasteboardContentsCurrentHostOnly));
        if (Number(board.changeCount) !== owned) throw new Error('Clipboard changed; nothing pasted.');
        if (!board.writeObjects($.NSArray.arrayWithObject(item))) throw new Error('Could not set temporary clipboard data.');
        owned = Number(board.changeCount);
        if (ObjC.unwrap(board.stringForType($.NSPasteboardTypeString)) !== text)
            throw new Error('Temporary clipboard text was not verified; nothing pasted.');
        action(() => Number(board.changeCount) === owned);
    } finally {
        // Preserve a newer user copy instead of restoring our older snapshot over it.
        if (owned !== null && Number(board.changeCount) === owned) {
            board.clearContents;
            if (Number(saved.copies.count) && !board.writeObjects(saved.copies))
                throw new Error('Clipboard restoration failed after the paste attempt.');
        }
    }
}

function findPasteMenuItem(application, attribute) {
    const queue = [attribute(application, 'AXMenuBar')], matches = [];
    let visited = 0;
    while (queue.length && visited++ < 500) {
        const element = queue.shift();
        const role = ObjC.unwrap(attribute(element, 'AXRole', {optional:true}));
        if (role === 'AXMenuItem') {
            const key = ObjC.unwrap(attribute(element, 'AXMenuItemCmdChar', {optional:true}));
            const modifiers = attribute(element, 'AXMenuItemCmdModifiers', {optional:true});
            if (typeof key === 'string' && key.toLowerCase() === 'v' && modifiers !== null
                && Number(ObjC.unwrap(modifiers)) === 0) matches.push(element);
        }
        const children = attribute(element, 'AXChildren', {optional:true});
        for (let i = 0; children && i < Number(children.count); i++) queue.push(children.objectAtIndex(i));
    }
    if (queue.length || matches.length !== 1) throw new Error('Cannot uniquely identify the app’s Paste menu command.');
    return matches[0];
}

function assertPasteFocus(system, application, element, attribute) {
    if (!$.CFEqual(application, attribute(system, 'AXFocusedApplication')) ||
        !$.CFEqual(element, attribute(system, 'AXFocusedUIElement')))
        throw new Error('Focus changed during the paste operation; no retry was performed.');
}

function waitForPastedText(system, application, element, expected, attribute) {
    let matched = false;
    for (let attempt = 0; attempt < 20; attempt++) {
        assertPasteFocus(system, application, element, attribute);
        const correct = ObjC.unwrap(attribute(element, 'AXValue')) === expected;
        if (correct && matched) return;
        matched = correct;
        delay(0.05);
    }
    throw new Error('The app did not confirm the transformed text; no retry was performed.');
}

function pasteSelectionUsingMenu(system, application, selection, replacement, helpers) {
    const attribute = helpers.attribute, offsets = helpers.offsets(selection.range);
    const original = ObjC.unwrap(attribute(selection.element, 'AXValue'));
    if (typeof original !== 'string' || original.slice(offsets.location, offsets.location + offsets.length) !== selection.text)
        throw new Error('Cannot verify the selected text range; nothing pasted.');
    const expected = original.slice(0, offsets.location) + replacement + original.slice(offsets.location + offsets.length);
    const range = helpers.range(selection.range, replacement);
    const menu = findPasteMenuItem(application, attribute);
    withTemporaryClipboard($.NSPasteboard.generalPasteboard, replacement, ownsClipboard => {
        assertPasteFocus(system, application, selection.element, attribute);
        if (!$.CFEqual(selection.range, attribute(selection.element, 'AXSelectedTextRange')) ||
            ObjC.unwrap(attribute(selection.element, 'AXSelectedText')) !== selection.text ||
            ObjC.unwrap(attribute(selection.element, 'AXValue')) !== original || !ownsClipboard())
            throw new Error('Selection or clipboard changed; nothing pasted.');
        if (ObjC.unwrap(attribute(menu, 'AXEnabled')) !== true) throw new Error('The app’s Paste command is unavailable.');
        if ($.AXUIElementPerformAction(menu, $('AXPress')) !== 0) throw new Error('The app refused its Paste command.');
        waitForPastedText(system, application, selection.element, expected, attribute);
    });
    try {
        assertPasteFocus(system, application, selection.element, attribute);
        if ($.AXUIElementSetAttributeValue(selection.element, $('AXSelectedTextRange'), range) !== 0)
            throw new Error('The app refused reselection.');
        for (let attempt = 0; attempt < 10; attempt++) {
            assertPasteFocus(system, application, selection.element, attribute);
            if ($.CFEqual(range, attribute(selection.element, 'AXSelectedTextRange')) &&
                ObjC.unwrap(attribute(selection.element, 'AXSelectedText')) === replacement) return;
            delay(0.05);
        }
        throw new Error('The app did not confirm reselection.');
    } catch (error) {
        throw new Error('Text transformed, but selection could not be restored: ' + error.message);
    }
}
