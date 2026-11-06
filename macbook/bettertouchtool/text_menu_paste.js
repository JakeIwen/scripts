// Native Paste bypasses broken AXSelectedText setters (Signal and Firefox).
// No Copy shortcut, submit, or navigation action is used.
ObjC.import('AppKit');

// A snapshot of every readable clipboard item and type, with the changeCount
// it was taken at.
function copyPasteboard(board) {
    const version = Number(board.changeCount), copies = $.NSMutableArray.array, items = board.pasteboardItems;
    for (let i = 0; items && i < Number(items.count); i++) {
        const item = items.objectAtIndex(i), copy = $.NSPasteboardItem.alloc.init;
        for (let j = 0; j < Number(item.types.count); j++) {
            const type = item.types.objectAtIndex(j), data = item.dataForType(type);
            if (!data || data.length === undefined || !copy.setDataForType(data.copy, type))
                throw new Error('Could not preserve the current clipboard.');
        }
        copies.addObject(copy);
    }
    if (Number(board.changeCount) !== version) throw new Error('The clipboard changed.');
    return {version, copies};
}

// Puts `saved` back, but only while the clipboard still holds the write that
// produced `owned`: a newer copy made since then is kept.
function restorePasteboard(board, saved, owned) {
    if (Number(board.changeCount) !== owned) return;
    board.clearContents;
    if (Number(saved.copies.count) && !board.writeObjects(saved.copies))
        throw new Error('The previous clipboard could not be restored.');
}

// Writes `text` (plain by default, preserving app items when requested), plus
// '1' under each of `flags`
// (nspasteboard.org markers), and returns the changeCount it then owns. It
// refuses unless the clipboard is still at `saved.version` just before it
// clears it, so a copy made meanwhile (even while the item was built) is
// kept, and if the write then fails it puts `saved` back.
function writeClipboardText(board, text, saved, {flags = [], options = 0, preserveItems = false} = {}) {
    // Cut may already supply the right text and rich formats. Copy its items
    // separately: mutating saved.copies would also tag the rollback snapshot.
    const rich = preserveItems ? copyPasteboard(board) : null;
    if (rich && rich.version !== saved.version) throw new Error('The clipboard changed.');
    // A board's stringForType searches all items. Do not add duplicate text to
    // a leading non-text item: that unusual Cut is normalized to plain text.
    const keepRich = rich && Number(rich.copies.count) > 0
        && ObjC.unwrap(rich.copies.objectAtIndex(0).stringForType($.NSPasteboardTypeString)) === text;
    const item = keepRich ? rich.copies.objectAtIndex(0) : $.NSPasteboardItem.alloc.init;
    const items = keepRich ? rich.copies : $.NSArray.arrayWithObject(item);
    if (!item.setStringForType(text, $.NSPasteboardTypeString)) throw new Error('Could not prepare the clipboard text.');
    for (const type of flags) {
        if (!item.setStringForType('1', type)) throw new Error('Could not mark the clipboard data.');
    }
    if (Number(board.changeCount) !== saved.version) throw new Error('The clipboard changed.');
    const owned = Number(board.prepareForNewContentsWithOptions(options));
    try {
        if (Number(board.changeCount) !== owned) throw new Error('The clipboard changed during the write.');
        if (!board.writeObjects(items)) throw new Error('Could not write the clipboard.');
        // writeObjects never bumps changeCount, so `owned` stays the generation
        // prepareForNewContents returned: adopting a newer one here would let a
        // failed readback restore our text over somebody's newer copy.
        if (Number(board.changeCount) !== owned) throw new Error('The clipboard changed during the write.');
        if (ObjC.unwrap(board.stringForType($.NSPasteboardTypeString)) !== text)
            throw new Error('The clipboard text did not read back.');
        return owned;
    } catch (error) {
        try {
            restorePasteboard(board, saved, owned);
        } catch (restore) {
            throw new Error(error.message + ' ' + restore.message);
        }
        throw error;
    }
}

function withTemporaryClipboard(board, text, action) {
    let saved, owned;
    try {
        saved = copyPasteboard(board);
        owned = writeClipboardText(board, text, saved, {options: $.NSPasteboardContentsCurrentHostOnly,
            flags: ['org.nspasteboard.TransientType', 'org.nspasteboard.ConcealedType']});
    } catch (error) {
        throw new Error(error.message + ' Nothing was pasted.');
    }
    try {
        action(() => Number(board.changeCount) === owned);
    } finally {
        // Preserve a newer user copy instead of restoring our older snapshot over it.
        restorePasteboard(board, saved, owned);
    }
}

function findPasteMenuItem(application, attribute) {
    return findEditMenuItem(application, attribute, 'v');
}

function findEditMenuItem(application, attribute, key) {
    const bar = attribute(application, 'AXMenuBar');
    const groups = attribute(bar, 'AXChildren');
    for (let group = 0; group < Number(groups.count); group++) {
        let items = attribute(groups.objectAtIndex(group), 'AXChildren', {optional:true});
        if (items && Number(items.count) === 1 &&
            ObjC.unwrap(attribute(items.objectAtIndex(0), 'AXRole', {optional:true})) === 'AXMenu')
            items = attribute(items.objectAtIndex(0), 'AXChildren', {optional:true});
        // Standard Edit commands are immediate siblings. Never traverse history,
        // bookmarks or Services submenus, and do not depend on a localized title.
        if (!items || Number(items.count) > 128) continue;
        const commands = {x:[], c:[], v:[]};
        for (let index = 0; index < Number(items.count); index++) {
            const item = items.objectAtIndex(index);
            if (ObjC.unwrap(attribute(item, 'AXRole', {optional:true})) !== 'AXMenuItem') continue;
            const key = ObjC.unwrap(attribute(item, 'AXMenuItemCmdChar', {optional:true}));
            const modifiers = attribute(item, 'AXMenuItemCmdModifiers', {optional:true});
            if (typeof key === 'string' && Object.prototype.hasOwnProperty.call(commands, key.toLowerCase())
                && modifiers !== null && Number(ObjC.unwrap(modifiers)) === 0)
                commands[key.toLowerCase()].push(item);
        }
        if (commands.x.length === 1 && commands.c.length === 1) {
            const name = {x:'Cut', c:'Copy', v:'Paste'}[key];
            if (commands[key].length !== 1) throw new Error('The Edit menu does not have one unambiguous ' + name + ' command.');
            return commands[key][0];
        }
    }
    throw new Error('Cannot identify the app’s Cut/Copy/Paste menu commands.');
}

function focusUnchanged(system, application, element, attribute) {
    return $.CFEqual(application, attribute(system, 'AXFocusedApplication')) &&
        $.CFEqual(element, attribute(system, 'AXFocusedUIElement'));
}

function assertPasteFocus(system, application, element, attribute) {
    if (!focusUnchanged(system, application, element, attribute))
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

function restorePastedSelection(system, application, selection, replacement, expected, helpers, selected) {
    const {attribute, markers} = helpers;
    const chosen = replacement.slice(selected.offset, selected.offset + selected.length);
    assertPasteFocus(system, application, selection.element, attribute);
    let range;
    if (markers) {
        const caretRange = attribute(selection.element, 'AXSelectedTextRange');
        const caret = helpers.offsets(caretRange);
        const caretMarker = markers.read();
        const original = helpers.offsets(selection.range);
        if (caret.length !== 0 || caret.location !== original.location + replacement.length)
            throw new Error('The editor did not confirm the insertion caret.');
        range = markers.endingAt(caretMarker, replacement, selected);
        assertPasteFocus(system, application, selection.element, attribute);
        if (!markers.same(caretMarker, markers.read()) ||
            !$.CFEqual(caretRange, attribute(selection.element, 'AXSelectedTextRange')) ||
            ObjC.unwrap(attribute(selection.element, 'AXValue')) !== expected)
            throw new Error('The selection or text changed before reselection.');
        markers.select(range);
    } else {
        range = helpers.range(selection.range, selected);
        if ($.AXUIElementSetAttributeValue(selection.element, $('AXSelectedTextRange'), range) !== 0)
            throw new Error('The app refused reselection.');
    }
    for (let attempt = 0; attempt < 10; attempt++) {
        assertPasteFocus(system, application, selection.element, attribute);
        const current = markers ? markers.read() : attribute(selection.element, 'AXSelectedTextRange');
        if ((markers ? markers.same(range, current) : $.CFEqual(range, current)) &&
            ObjC.unwrap(attribute(selection.element, 'AXSelectedText')) === chosen) return;
        delay(0.05);
    }
    const actual = helpers.offsets(attribute(selection.element, 'AXSelectedTextRange'));
    throw new Error('The app did not confirm reselection (selected ' + actual.length
        + ' of ' + chosen.length + ' characters).');
}

function pasteSelectionUsingMenu(system, application, selection, replacement, helpers, selected) {
    const attribute = helpers.attribute, offsets = helpers.offsets(selection.range);
    const original = ObjC.unwrap(attribute(selection.element, 'AXValue'));
    if (typeof original !== 'string' || original.slice(offsets.location, offsets.location + offsets.length) !== selection.text)
        throw new Error('Cannot verify the selected text range; nothing pasted.');
    const expected = original.slice(0, offsets.location) + replacement + original.slice(offsets.location + offsets.length);
    const markerRange = helpers.markers && helpers.markers.read();
    if (markerRange) helpers.markers.validate(markerRange);
    if (markerRange && helpers.markers.text(markerRange) !== selection.text)
        throw new Error('The editor’s text-marker selection disagrees; nothing pasted.');
    const menu = findPasteMenuItem(application, attribute);
    withTemporaryClipboard($.NSPasteboard.generalPasteboard, replacement, ownsClipboard => {
        assertPasteFocus(system, application, selection.element, attribute);
        if (!$.CFEqual(selection.range, attribute(selection.element, 'AXSelectedTextRange')) ||
            (markerRange && !helpers.markers.same(markerRange, helpers.markers.read())) ||
            ObjC.unwrap(attribute(selection.element, 'AXSelectedText')) !== selection.text ||
            ObjC.unwrap(attribute(selection.element, 'AXValue')) !== original || !ownsClipboard())
            throw new Error('Selection or clipboard changed; nothing pasted.');
        if (ObjC.unwrap(attribute(menu, 'AXEnabled')) !== true) throw new Error('The app’s Paste command is unavailable.');
        if ($.AXUIElementPerformAction(menu, $('AXPress')) !== 0) throw new Error('The app refused its Paste command.');
        waitForPastedText(system, application, selection.element, expected, attribute);
    });
    try {
        restorePastedSelection(system, application, selection, replacement, expected, helpers, selected);
    } catch (error) {
        throw new Error('Text transformed, but selection could not be restored: ' + error.message);
    }
}
