// Runtime for the system-wide editing keys (install_text_editing_keys.py).
// BTT's JXA engine runs it after text_selection.js, whose AX helpers it uses.

function wrapSelection(open, directory) {
    return selectionTransforms(directory).wrap(open, selectionUI(directory));
}

function cutSelectedLine(directory) {
    // BTT has just sent the paragraph-selection keys. Cut goes through the Edit
    // menu: BTT swallows a Cmd-X sent from its own Cmd-X trigger.
    if (!$.AXIsProcessTrusted()) throw new Error('BetterTouchTool needs Accessibility access.');
    const system = $.AXUIElementCreateSystemWide();
    const application = selectionAttribute(system, 'AXFocusedApplication');
    const element = focusedSelectionElement(system, application, false);
    let previous = null;
    // Wait for the keys to land: cut only a nonempty selection read twice in a row.
    for (let attempt = 0; attempt < 20; attempt++) {
        const range = selectionOffsets(selectionAttribute(element, 'AXSelectedTextRange'));
        if (range.length && previous && range.location === previous.location && range.length === previous.length) {
            const cut = new Function(selectionReadSource(directory + '/text_menu_paste.js')
                + '\nreturn findEditMenuItem;')()(application, selectionAttribute, 'x');
            if (ObjC.unwrap(selectionAttribute(cut, 'AXEnabled')) !== true) throw new Error('The app’s Cut command is unavailable.');
            if ($.AXUIElementPerformAction(cut, $('AXPress')) !== 0) throw new Error('The app refused its Cut command.');
            return 'cut line';
        }
        previous = range;
        delay(0.03);
    }
    // An empty last line has nothing to select; that is not an error.
    if (!previous.length) return 'nothing to cut';
    throw new Error('The line selection did not settle; nothing was cut.');
}

// var, not const: BTT's runJXA reuses its context, so this file runs again in
// the same scope, and a repeated top-level const is a SyntaxError.
var EDITING_KEYS = {
    'wrap': options => wrapSelection(options.open, options.directory),
    'cut-line': options => cutSelectedLine(options.directory)
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
