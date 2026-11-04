// Runtime for the system-wide editing keys (install_text_editing_keys.py).
// BTT's JXA engine runs it after text_selection.js, whose AX helpers it uses.
ObjC.import('AppKit');

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

// A line strategy finds the caret's line ({range, text, copy}, where `copy` is
// what Cmd-C puts on the clipboard: what a cut would remove, its break as \n).
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
        collapsed: range => range.length === 0,
        // Exactly `range`, and the app agrees on its text.
        isSelected: (range, text) => same(read(), range) && selectedText() === text,
        lineAt: (caret, text) => {
            if (caret.location > text.length) throw new Error('The app’s caret lies outside its text.');
            const range = lineRangeAt(text, caret.location);
            if (!range) return null;
            const line = text.slice(range.location, range.location + range.length);
            return {range, text: line, copy: line.replace(/(?:\r\n?|\u2029)$/, '\n')};
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
        same: markers.same,
        collapsed: markers.collapsed,
        // Marker endpoints are exact, so they also fix the text.
        isSelected: range => markers.same(markers.read(), range),
        lineAt: caret => {
            const range = markers.paragraphAt(caret);
            const text = markers.text(range);
            return {range, text, copy: text + (markers.paragraphFollows(range) ? '\n' : '')};
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
        + '\nreturn {findEditMenuItem, focusUnchanged, writeClipboardText};')();
}

// The focused field, its text, caret and the caret's line, read once: every
// later step checks the field against this record before it acts. The caret
// is read again after the text and line, so a key typed in between is caught.
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
    if (!lines.same(lines.read(), caret)) throw new Error('The caret moved while the line was read; nothing was ' + verb + '.');
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
    if (ObjC.unwrap(selectionAttribute(command, 'AXEnabled')) !== true)
        throw new Error('The app’s Cut command is unavailable; nothing was cut.');
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
    if (!editor.line) return 'nothing to cut';
    const refusal = editor.lines.cutRefusal(editor.line);
    if (refusal) throw new Error(refusal);
    // Through the Edit menu: BTT swallows a Cmd-X sent from its own Cmd-X trigger.
    const command = editor.helpers.findEditMenuItem(editor.application, selectionAttribute, 'x');
    assertCaretUnchanged(editor);
    let expected;
    try {
        selectLine(editor);
        expected = textWithoutLine(editor);
        pressCut(editor, command);
    } catch (error) {
        throw new Error(error.message + caretRestoreNote(editor));
    }
    // The cut is confirmed by the text: exactly the line is gone.
    if (!waitFor(() => ObjC.unwrap(selectionAttribute(editor.element, 'AXValue')) === expected, 20))
        throw new Error('The app did not confirm the cut; check the text.');
    return 'cut line';
}

// Cmd-C writes the line to the clipboard itself, as plain text: the field is
// never selected, pressed or changed, so nothing needs putting back.
function copyCaretLine(directory) {
    const field = caretLine(directory, 'copied');
    if (!field.line || !field.line.copy) return 'nothing to copy';
    field.helpers.writeClipboardText($.NSPasteboard.generalPasteboard, field.line.copy);
    return 'copied line';
}

// var, not const: BTT's runJXA reuses its context, so this file runs again in
// the same scope, and a repeated top-level const is a SyntaxError.
var EDITING_KEYS = {
    'wrap': options => wrapSelection(options.open, options.directory),
    'cut-line': options => cutSelectedLine(options.directory),
    'copy-line': options => copyCaretLine(options.directory)
};

function runEditingKey(options) {
    return runSelectionAction(options, () => {
        if (!Object.prototype.hasOwnProperty.call(EDITING_KEYS, options.kind)) throw new Error('Unknown editing key.');
        return EDITING_KEYS[options.kind](options);
    });
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
