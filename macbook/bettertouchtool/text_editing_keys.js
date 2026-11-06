// Runtime for the system-wide editing keys (install_text_editing_keys.py).
// BTT's JXA engine runs it after text_selection.js, whose AX helpers it uses.
ObjC.import('AppKit');

var LINE_PASTEBOARD_TYPE = 'com.jacobr.editing-keys.line';
var LINE_CUT_INLINE = 'Line cut, but Cmd-V will paste it inline.';

function wrapSelection(open, directory) {
    return selectionTransforms(directory).wrap(open, selectionUI(directory));
}

// The caret's line is its logical line (paragraph) with the break that ends
// it, so a cut removes the line rather than emptying it; an unterminated last
// line has none, and an empty one is nothing. CRLF is one break.
function lineRangeAt(text, caret) {
    const isBreak = index => /[\n\r\u2029]/.test(text.charAt(index));
    // A caret between CR and LF belongs to the line that CRLF ends.
    if (caret > 0 && text.charAt(caret - 1) === '\r' && text.charAt(caret) === '\n') caret--;
    let start = caret, end = caret;
    while (start > 0 && !isBreak(start - 1)) start--;
    while (end < text.length && !isBreak(end)) end++;
    if (end < text.length) end += text.startsWith('\r\n', end) ? 2 : 1;
    return end > start ? {location: start, length: end - start} : null;
}

// A line strategy finds the caret's line: {range, text, copy}, where `range` is
// what Cmd-X cuts (null for an empty last line) and `copy` what Cmd-C puts on
// the clipboard, the line's text with one \n in every case, the last line too.
// Cocoa and Chromium/Electron: numeric ranges over AXValue, whose breaks are characters.
function rangeLines(element) {
    const read = () => selectionOffsets(selectionAttribute(element, 'AXSelectedTextRange'));
    const same = (a, b) => a.location === b.location && a.length === b.length;
    const selectedText = () => {
        const selected = selectionAttribute(element, 'AXSelectedText', {optional: true});
        return selected === null ? '' : ObjC.unwrap(selected);
    };
    return {
        read,
        same,
        flatText: text => text,
        start: (line, caret) => ({location:line.range ? line.range.location : caret.location, length:0}),
        afterPaste: (caret, pasted) => ({location:caret.location + pasted.length, length:0}),
        collapsed: range => range.length === 0,
        // Exactly `range`, and the app agrees on its text.
        isSelected: (range, text) => same(read(), range) && selectedText() === text,
        lineAt: (caret, text) => {
            if (caret.location > text.length) throw new Error('The app’s caret lies outside its text.');
            const range = lineRangeAt(text, caret.location);
            const line = range ? text.slice(range.location, range.location + range.length) : '';
            return {range, text: line, copy: line.replace(/(?:\r\n?|[\n\u2029])$/, '') + '\n'};
        },
        cutRefusal: () => null,
        select: range => {
            const value = selectionRangeValue(range.location, range.length);
            if ($.AXUIElementSetAttributeValue(element, $('AXSelectedTextRange'), value) !== 0)
                throw new Error('The app refused the selection.');
        }
    };
}

// Firefox: flat offsets omit paragraph breaks and are ambiguous at paragraph
// boundaries, so its lines are text-marker paragraphs, whose text has no break.
function markerLines(markers) {
    return {
        read: markers.read,
        flatText: text => text.replace(/\r\n?|[\n\u2029]/g, ''),
        start: line => markers.startOf(line.range),
        afterPaste: (caret, pasted, offset) => markers.forwardCaret(markers.read(), offset),
        same: markers.same,
        collapsed: markers.collapsed,
        // Marker endpoints are exact, so they also fix the text.
        isSelected: range => markers.same(markers.read(), range),
        lineAt: caret => {
            const range = markers.paragraphAt(caret);
            const text = markers.text(range);
            return {range, text, copy: text + '\n'};
        },
        // A blank paragraph is unsafe to cut: its marker range cannot be told
        // from an empty one, and with no text in the flat AXValue the cut
        // cannot be confirmed. Fail closed until it is verified live.
        cutRefusal: line => line.text === '' ? 'Blank lines can’t be cut in Firefox yet; nothing was cut.' : null,
        select: markers.select
    };
}

function editMenuHelpers(directory) {
    return new Function(selectionReadSource(directory + '/text_menu_paste.js')
        + '\nreturn {findEditMenuItem, focusUnchanged, copyPasteboard, writeClipboardText, restorePasteboard};')();
}

// The focused field, its text, caret and the caret's line, read once: every
// later step checks the field against this record before it acts. The caret
// and focus are read again after the text and line, so a key typed or a focus
// change in between is caught.
function caretLine(directory, verb) {
    if (!$.AXIsProcessTrusted()) throw new Error('BetterTouchTool needs Accessibility access.');
    const system = $.AXUIElementCreateSystemWide();
    const application = selectionAttribute(system, 'AXFocusedApplication');
    const element = focusedSelectionElement(system, application, false);
    const helpers = editMenuHelpers(directory);
    const lines = selectionIsFirefox(selectionApplicationBundle(application))
        ? markerLines(selectionMarkers(directory, element)) : rangeLines(element);
    const caret = lines.read();
    if (!lines.collapsed(caret)) throw new Error('Text is selected now; nothing was ' + verb + '.');
    const text = ObjC.unwrap(selectionAttribute(element, 'AXValue'));
    if (typeof text !== 'string') throw new Error('The focused field does not expose its text; nothing was ' + verb + '.');
    const line = lines.lineAt(caret, text);
    if (!lines.same(lines.read(), caret) || !helpers.focusUnchanged(system, application, element, selectionAttribute))
        throw new Error('The caret or focus moved while the line was read; nothing was ' + verb + '.');
    return {system, application, element, lines, text, caret, line, helpers};
}

// True while this field's text is unchanged, `range` (holding `selected`) is
// selected and the field still has focus: anything else is the user's newer
// state. Focus is read last, as the Edit menu acts on whatever is focused then.
function editorHolds(editor, range, selected) {
    const {element} = editor;
    return ObjC.unwrap(selectionAttribute(element, 'AXValue')) === editor.text
        && editor.lines.isSelected(range, selected)
        && editor.helpers.focusUnchanged(editor.system, editor.application, element, selectionAttribute);
}

function waitFor(condition, attempts) {
    for (let attempt = 0; ; attempt++) {
        if (condition()) return true;
        if (attempt >= attempts) return false;
        delay(0.05);
    }
}

// Finding the Edit menu takes a while: a caret moved meanwhile must not be
// overwritten by selecting the line read before it.
function assertCaretUnchanged(editor) {
    if (!editorHolds(editor, editor.caret, '')) throw new Error('The caret, text or focus changed; nothing was cut.');
}

function selectLine(editor) {
    editor.lines.select(editor.line.range);
    // Apps apply a selection asynchronously and may clamp or ignore it: only
    // the exact line, read back, counts.
    if (!waitFor(() => editorHolds(editor, editor.line.range, editor.line.text), 10))
        throw new Error('The app did not select the line; nothing was cut.');
}

function pressCut(editor, command) {
    // NSMenu validates lazily: AXEnabled may still be false from before the
    // selection. The post-press text confirmation, not that stale flag, proves Cut.
    // The menu command acts on whatever is focused and selected when it runs.
    if (!editorHolds(editor, editor.line.range, editor.line.text))
        throw new Error('The selection or focus changed before Cut; nothing was cut.');
    if ($.AXUIElementPerformAction(command, $('AXPress')) !== 0) throw new Error('The app refused its Cut command.');
}

// Puts the caret back only over the line this run selected (or finds it never
// left); a caret, edit or focus change made since then is left alone.
function restoreCaret(editor) {
    const {lines, caret} = editor;
    if (editorHolds(editor, caret, '')) return true;
    if (!editorHolds(editor, editor.line.range, editor.line.text)) return false;
    lines.select(caret);
    if (!waitFor(() => editorHolds(editor, caret, ''), 10)) throw new Error('The app did not confirm the caret.');
    return true;
}

// What became of the caret, as a sentence for a report ('' once it is back).
function caretRestoreNote(editor) {
    try {
        return restoreCaret(editor) ? '' : ' The selection changed meanwhile and was left as it is.';
    } catch (error) {
        return ' The caret could not be put back: ' + error.message;
    }
}

function textWithoutLine(editor) {
    // Flat offsets place the line in AXValue in every app; Firefox's lack only its breaks.
    const flat = selectionOffsets(selectionAttribute(editor.element, 'AXSelectedTextRange'));
    if (editor.text.slice(flat.location, flat.location + flat.length) !== editor.line.text)
        throw new Error('Cannot place the selected line in the text; nothing was cut.');
    return editor.text.slice(0, flat.location) + editor.text.slice(flat.location + flat.length);
}

function cutSelectedLine(directory) {
    const editor = caretLine(directory, 'cut');
    if (!editor.line.range) return 'nothing to cut';
    const refusal = editor.lines.cutRefusal(editor.line);
    if (refusal) throw new Error(refusal);
    // Through the Edit menu: BTT swallows a Cmd-X sent from its own Cmd-X trigger.
    const command = editor.helpers.findEditMenuItem(editor.application, selectionAttribute, 'x');
    assertCaretUnchanged(editor);
    const board = $.NSPasteboard.generalPasteboard;
    let expected, cutVersion;
    try {
        selectLine(editor);
        expected = textWithoutLine(editor);
        const before = Number(board.changeCount);
        pressCut(editor, command);
        cutVersion = Number(board.changeCount);
        if (cutVersion === before) cutVersion = null;
    } catch (error) {
        throw new Error(error.message + caretRestoreNote(editor));
    }
    // The cut is confirmed by the text: exactly the line is gone.
    if (!waitFor(() => ObjC.unwrap(selectionAttribute(editor.element, 'AXValue')) === expected, 20))
        throw new Error('The app did not confirm the cut; check the text.' + caretRestoreNote(editor));
    try {
        const saved = editor.helpers.copyPasteboard(board);
        if (saved.version !== cutVersion || ![editor.line.text, editor.line.copy].includes(
            ObjC.unwrap(board.stringForType($.NSPasteboardTypeString)))) throw new Error('Clipboard changed after Cut.');
        writeLineClipboard(board, editor.line.copy, saved, editor.helpers);
    } catch (error) {
        // The text is already cut: never undo it or overwrite a newer clipboard.
        return LINE_CUT_INLINE;
    }
    return 'cut line';
}

function writeLineClipboard(board, text, saved, helpers) {
    const owned = helpers.writeClipboardText(board, text, saved, {flags:[LINE_PASTEBOARD_TYPE]});
    try {
        if (ObjC.unwrap(board.stringForType(LINE_PASTEBOARD_TYPE)) !== '1'
            || Number(board.changeCount) !== owned) throw new Error('The line clipboard tag did not read back.');
    } catch (error) {
        helpers.restorePasteboard(board, saved, owned);
        throw error;
    }
}

// Cmd-C writes the line to the clipboard itself, as plain text: the field is
// never selected, pressed or changed, so nothing needs putting back. The
// clipboard's version is read before the field, so a copy made anywhere while
// the line is read is kept; a failed write puts the previous clipboard back.
function copyCaretLine(directory) {
    const board = $.NSPasteboard.generalPasteboard;
    const version = Number(board.changeCount);
    const field = caretLine(directory, 'copied');
    try {
        const saved = field.helpers.copyPasteboard(board);
        if (saved.version !== version) throw new Error('The clipboard changed while the line was read.');
        if (!editorHolds(field, field.caret, '')) throw new Error('The caret, text or focus changed.');
        writeLineClipboard(board, field.line.copy, saved, field.helpers);
    } catch (error) {
        throw new Error(error.message + ' Nothing was copied.');
    }
    return 'copied line';
}

// Untagged clipboard data can race the BTT variable: use the app's normal
// Paste once, never a synthetic Cmd-V that BTT would swallow or intercept again.
function normalMenuPaste(directory) {
    const system = $.AXUIElementCreateSystemWide();
    const application = selectionAttribute(system, 'AXFocusedApplication');
    const element = selectionAttribute(system, 'AXFocusedUIElement');
    const helpers = editMenuHelpers(directory);
    const command = helpers.findEditMenuItem(application, selectionAttribute, 'v');
    if (!helpers.focusUnchanged(system, application, element, selectionAttribute))
        throw new Error('Focus changed before Paste; nothing was pasted.');
    if ($.AXUIElementPerformAction(command, $('AXPress')) !== 0) throw new Error('The app refused its Paste command.');
    return 'pasted normally';
}

function restoreLinePasteCaret(editor, expected, insertionOffset, pasted, offset) {
    try {
        // Only the insertion caret belongs to this operation. A newer edit,
        // selection or focus is the user's and must not be written over.
        const insertion = editor.lines.read();
        const flat = selectionOffsets(selectionAttribute(editor.element, 'AXSelectedTextRange'));
        if (!editor.lines.collapsed(insertion) || flat.length !== 0 || flat.location !== insertionOffset) return;
        const current = {...editor, text:expected};
        if (!editorHolds(current, insertion, '')) return;
        const target = editor.lines.afterPaste(editor.caret, pasted, offset);
        if (!editorHolds(current, insertion, '')) return;
        editor.lines.select(target);
        waitFor(() => editorHolds(current, target, ''), 10);
    } catch (error) {
        // Text was confirmed; reselection is best effort, not a failed paste.
    }
}

function pasteCaretLine(directory) {
    const board = $.NSPasteboard.generalPasteboard, version = Number(board.changeCount);
    if (ObjC.unwrap(board.stringForType(LINE_PASTEBOARD_TYPE)) !== '1') return normalMenuPaste(directory);
    const pasted = ObjC.unwrap(board.stringForType($.NSPasteboardTypeString));
    if (typeof pasted !== 'string' || !pasted.endsWith('\n')) throw new Error('Invalid line clipboard; nothing was pasted.');
    const editor = caretLine(directory, 'pasted');
    const start = editor.lines.start(editor.line, editor.caret);
    const flatPaste = editor.lines.flatText(pasted);
    // Firefox AXValue omits paragraph breaks: a blank insertion cannot be
    // distinguished from a menu press that did nothing, so do not guess.
    if (!flatPaste) throw new Error('A blank line paste can’t be confirmed in Firefox; nothing was pasted.');
    const command = editor.helpers.findEditMenuItem(editor.application, selectionAttribute, 'v');
    if (!editorHolds(editor, editor.caret, '')) throw new Error('The caret, text or focus changed; nothing was pasted.');
    let expected, offset, flatStart;
    const selected = {...editor, line:{range:start, text:''}};
    try {
        const caretOffset = selectionOffsets(selectionAttribute(editor.element, 'AXSelectedTextRange')).location;
        if (!editorHolds(editor, editor.caret, '')) throw new Error('The caret, text or focus changed; nothing was pasted.');
        editor.lines.select(start);
        if (!waitFor(() => editorHolds(editor, start, ''), 10)) throw new Error('The app did not confirm the line start.');
        flatStart = selectionOffsets(selectionAttribute(editor.element, 'AXSelectedTextRange')).location;
        offset = caretOffset - flatStart;
        if (offset < 0 || editor.text.slice(flatStart, flatStart + editor.line.text.length) !== editor.line.text)
            throw new Error('Cannot place the line in the text; nothing was pasted.');
        expected = editor.text.slice(0, flatStart) + flatPaste + editor.text.slice(flatStart);
        // Clipboard first, then text/selection, focus last before the menu press.
        if (Number(board.changeCount) !== version || !editorHolds(editor, start, '')
            || Number(board.changeCount) !== version
            || !editor.helpers.focusUnchanged(editor.system, editor.application, editor.element, selectionAttribute))
            throw new Error('The clipboard, selection or focus changed; nothing was pasted.');
        if ($.AXUIElementPerformAction(command, $('AXPress')) !== 0) throw new Error('The app refused its Paste command.');
        if (!waitFor(() => ObjC.unwrap(selectionAttribute(editor.element, 'AXValue')) === expected, 20))
            throw new Error('The app did not confirm the paste; check the text.');
    } catch (error) {
        throw new Error(error.message + caretRestoreNote(selected));
    }
    restoreLinePasteCaret(editor, expected, flatStart + flatPaste.length, pasted, offset);
    return 'pasted line';
}

// var, not const: BTT's runJXA reuses its context, so this file runs again in
// the same scope, and a repeated top-level const is a SyntaxError.
var EDITING_KEYS = {
    'wrap': options => wrapSelection(options.open, options.directory),
    'cut-line': options => cutSelectedLine(options.directory),
    'copy-line': options => copyCaretLine(options.directory),
    'paste-line': options => pasteCaretLine(options.directory)
};

function runEditingKey(options) {
    const outcome = JSON.parse(runSelectionAction(options, () => {
        if (!Object.prototype.hasOwnProperty.call(EDITING_KEYS, options.kind)) throw new Error('Unknown editing key.');
        return EDITING_KEYS[options.kind](options);
    }));
    if (outcome.result === LINE_CUT_INLINE) outcome.warning = LINE_CUT_INLINE;
    if (options.lineClipboardVariable)
        outcome.lineClipboard = ObjC.unwrap($.NSPasteboard.generalPasteboard.stringForType(LINE_PASTEBOARD_TYPE)) === '1';
    return JSON.stringify(outcome);
}

function runAppSwitch(options) {
    // The keys' trigger condition reads the focused selection before any script
    // runs, so Electron apps must already expose their text tree by then. App
    // switches never surface errors (the keys then simply pass through) and are
    // not recorded, so they cannot crowd out the keys' diagnostics.
    let result;
    try {
        const system = $.AXUIElementCreateSystemWide();
        const application = selectionAttribute(system, 'AXFocusedApplication', {optional: true});
        result = !application ? 'no focused app'
            : options.excluded.includes(selectionApplicationBundle(application)) ? 'excluded'
            : enableManualAccessibility(application) ? 'enabled' : 'unchanged';
    } catch (error) {
        result = 'skipped: ' + String(error.message || error);
    }
    return JSON.stringify({ok: true, result});
}
