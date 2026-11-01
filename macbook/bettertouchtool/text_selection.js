// Executed by BTT's JXA engine, using BTT's Accessibility authorization.
// Reads selections directly; editors with broken AX replacement use native Paste.
ObjC.import('Foundation');
ObjC.import('ApplicationServices');

function selectionAttribute(element, name, options) {
    const value = Ref();
    const status = $.AXUIElementCopyAttributeValue(element, $(name), value);
    if (options && options.optional &&
        [Number($.kAXErrorAttributeUnsupported), Number($.kAXErrorNoValue)].includes(status)) return null;
    if (status !== 0)
        throw new Error('The focused control does not expose ' + name + ' (' + status + ').');
    // AX returns an opaque CFTypeRef; JXA needs an object before another AX call.
    return ObjC.castRefToObject(value[0]);
}

function enableManualAccessibility(application) {
    // Electron exposes its editor tree on demand through this documented AX flag.
    const manual = selectionAttribute(application, 'AXManualAccessibility', {optional: true});
    if (manual === null || ObjC.unwrap(manual) !== false) return false;
    const status = $.AXUIElementSetAttributeValue(application, $('AXManualAccessibility'), $(true));
    if (status !== 0) throw new Error('This app could not enable text accessibility.');
    return true;
}

function focusedSelectionElement(system, application, waitForTree) {
    const attempts = waitForTree ? 10 : 0;
    for (let attempt = 0; attempt <= attempts; attempt++) {
        if (!$.CFEqual(application, selectionAttribute(system, 'AXFocusedApplication')))
            throw new Error('The active app changed; please try the transform again.');
        const element = selectionAttribute(system, 'AXFocusedUIElement', {optional: true});
        const role = element && ObjC.unwrap(selectionAttribute(element, 'AXRole'));
        if (['AXTextArea', 'AXTextField', 'AXComboBox'].includes(role)) return element;
        if (attempt < attempts) delay(0.05);
    }
    throw new Error('Focus an editable text field and select text first.');
}

function selectionRangeBuffer() {
    // JXA cannot infer a struct type through AXValueGetValue's void* parameter.
    // NSRange and nonnegative CFRange share the same two-integer memory layout.
    const template = $.NSValue.valueWithRange($.NSMakeRange(0, 0));
    const size = Ref();
    $.NSGetSizeAndAlignment(template.objCType, size, null);
    return {data: $.NSMutableData.dataWithLength(size[0]), size: size[0], encoding: template.objCType};
}

function selectionOffsets(original) {
    const buffer = selectionRangeBuffer();
    if (!$.AXValueGetValue(original, $.kAXValueCFRangeType, buffer.data.mutableBytes))
        throw new Error('The app did not provide a valid selection range.');
    const range = $.NSValue.valueWithBytesObjCType(buffer.data.bytes, buffer.encoding).rangeValue;
    const location = Number(range.location);
    const length = Number(range.length);
    if (!Number.isSafeInteger(location) || location < 0 || !Number.isSafeInteger(length) || length < 0)
        throw new Error('The app did not provide a valid selection position.');
    return {location, length};
}

function replacementSelectionRange(original, replacement, selected) {
    const {location} = selectionOffsets(original);
    const buffer = selectionRangeBuffer();
    const target = selected || {offset: 0, length: replacement.length};
    // JavaScript length and macOS text ranges both count UTF-16 code units.
    const replacementRange = $.NSValue.valueWithRange($.NSMakeRange(location + target.offset, target.length));
    replacementRange.getValueSize(buffer.data.mutableBytes, buffer.size);
    return ObjC.castRefToObject($.AXValueCreate($.kAXValueCFRangeType, buffer.data.bytes));
}

function writeSelection(system, selection, replacement, selected) {
    const range = replacementSelectionRange(selection.range, replacement, selected);
    const status = $.AXUIElementSetAttributeValue(selection.element, $('AXSelectedText'), $(replacement));
    if (status !== 0) throw new Error('The app refused selection replacement (' + status + ').');
    try {
        if (!$.CFEqual(selection.element, selectionAttribute(system, 'AXFocusedUIElement')))
            throw new Error('Focus changed before the result could be reselected.');
        const selected = $.AXUIElementSetAttributeValue(selection.element, $('AXSelectedTextRange'), range);
        if (selected !== 0 || !$.CFEqual(range, selectionAttribute(selection.element, 'AXSelectedTextRange')))
            throw new Error('The app could not keep the result selected.');
    } catch (error) {
        throw new Error('Text transformed, but selection could not be restored: ' + error.message);
    }
}

function selectionApplicationBundle(application) {
    ObjC.import('AppKit');
    const pid = Ref();
    if ($.AXUIElementGetPid(application, pid) !== 0) throw new Error('Cannot identify the editor application.');
    return ObjC.unwrap($.NSRunningApplication.runningApplicationWithProcessIdentifier(pid[0]).bundleIdentifier);
}

function selectionIsFirefox(bundle) {
    // Release, Developer Edition and Nightly share Gecko's accessibility code.
    return /^org\.mozilla\./.test(bundle || '');
}

function selectionNeedsMenuPaste(bundle) {
    // Firefox's AX setter passes a length to DeleteText's end-offset parameter.
    // Signal accepts the AX write without updating its rich editor.
    return selectionIsFirefox(bundle) || bundle === 'org.whispersystems.signal-desktop';
}

function selectionUI(directory) {
    if (!$.AXIsProcessTrusted()) throw new Error('BetterTouchTool needs Accessibility access.');
    const system = $.AXUIElementCreateSystemWide();
    const application = selectionAttribute(system, 'AXFocusedApplication');
    const waitForTree = enableManualAccessibility(application);
    return {
        read: function () {
            const element = focusedSelectionElement(system, application, waitForTree);
            const writable = Ref();
            const status = $.AXUIElementIsAttributeSettable(element, $('AXSelectedText'), writable);
            return {
                element: element,
                range: selectionAttribute(element, 'AXSelectedTextRange'),
                text: ObjC.unwrap(selectionAttribute(element, 'AXSelectedText')),
                writable: status === 0 && Boolean(writable[0])
            };
        },
        same: function (before, after) {
            return $.CFEqual(before.element, after.element) && $.CFEqual(before.range, after.range)
                && before.text === after.text && after.writable;
        },
        // `selected` is the part of the replacement to leave selected; default all of it.
        write: function (selection, replacement, selected) {
            const bundle = selectionApplicationBundle(application);
            if (selectionNeedsMenuPaste(bundle)) {
                const paste = new Function(selectionReadSource(directory + '/text_menu_paste.js')
                    + '\nreturn pasteSelectionUsingMenu;')();
                const markers = selectionIsFirefox(bundle)
                    ? new Function(selectionReadSource(directory + '/text_markers.js')
                        + '\nreturn textMarkerSelection;')()(selection.element, selectionAttribute)
                    : null;
                paste(system, application, selection, replacement, {
                    attribute:selectionAttribute, offsets:selectionOffsets,
                    range:replacementSelectionRange, markers
                }, selected);
            } else {
                writeSelection(system, selection, replacement, selected);
            }
        }
    };
}

function selectionReadSource(path) {
    const source = $.NSString.stringWithContentsOfFileEncodingError(path, $.NSUTF8StringEncoding, null);
    if (!source) throw new Error('A text-transform source file is missing or unreadable.');
    return ObjC.unwrap(source);
}

function selectionTransforms(directory) {
    return new Function(selectionReadSource(directory + '/lib/text_transforms.js') + '\nreturn TextTransforms;')();
}

function wrapSelection(open, directory) {
    return selectionTransforms(directory).wrap(open, selectionUI(directory));
}

function transformSelection(kind, directory, caseLibrary) {
    const transforms = selectionTransforms(directory);
    const dependencies = {
        encodeBase64: function (text) {
            return ObjC.unwrap($(text).dataUsingEncoding($.NSUTF8StringEncoding).base64EncodedStringWithOptions(0));
        },
        decodeBase64: function (text) {
            const data = $.NSData.alloc.initWithBase64EncodedStringOptions($(text), 0);
            if (!data) return null;
            const decoded = $.NSString.alloc.initWithDataEncoding(data, $.NSUTF8StringEncoding);
            return decoded ? ObjC.unwrap(decoded) : null;
        }
    };
    if (['snake', 'kebab', 'camel'].includes(kind)) {
        dependencies.cases = {};
        new Function('exports', selectionReadSource(caseLibrary))(dependencies.cases);
    }
    return transforms.replace(kind, selectionUI(directory), dependencies);
}

function recordTransformOutcome(options, outcome) {
    const directory = options.directory + '/.local/';
    if (!$.NSFileManager.defaultManager.fileExistsAtPath(directory + 'record-text-transform')) return;
    // This opt-in diagnostic contains only status metadata, never selection/clipboard text.
    try {
        let app = null;
        try {
            const system = $.AXUIElementCreateSystemWide();
            app = selectionApplicationBundle(selectionAttribute(system, 'AXFocusedApplication')) || null;
        } catch (error) {
            // Keep the failure report even when focus metadata is unavailable.
        }
        const report = {time: new Date().toISOString(), kind: options.kind,
            app,
            ok: outcome.ok, result: outcome.ok ? outcome.result : undefined,
            error: outcome.ok ? undefined : outcome.error};
        const files = ['text-transform-last-result.json'];
        if (app && /^[A-Za-z0-9_.-]+$/.test(app)) files.push('text-transform-' + app + '.json');
        if (!outcome.ok) files.push('text-transform-last-error.json');
        for (const file of files) {
            const path = directory + file;
            $(JSON.stringify(report)).writeToFileAtomicallyEncodingError(path, true, $.NSUTF8StringEncoding, null);
            $.NSFileManager.defaultManager.setAttributesOfItemAtPathError($({NSFilePosixPermissions:384}), path, null);
        }
    } catch (error) {
        // Diagnostic availability must not alter selection editing or its reported result.
    }
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

function runTextTransform(options) {
    let outcome;
    try {
        const result = options.kind === 'wrap' ? wrapSelection(options.open, options.directory)
            : options.kind === 'cut-line' ? cutSelectedLine(options.directory)
            : transformSelection(options.kind, options.directory, options.caseLibrary);
        outcome = {ok: true, result};
    } catch (error) {
        outcome = {ok: false, error: String(error.message || error)};
    }
    recordTransformOutcome(options, outcome);
    return JSON.stringify(outcome);
}

function prepareFocusedAppText(options) {
    // The editing keys' trigger condition reads the focused selection before any
    // script runs, so Electron apps must already expose their text tree by then.
    try {
        const system = $.AXUIElementCreateSystemWide();
        const application = selectionAttribute(system, 'AXFocusedApplication', {optional: true});
        if (!application) return 'no focused app';
        if (options.excluded.includes(selectionApplicationBundle(application))) return 'excluded';
        return enableManualAccessibility(application) ? 'enabled' : 'unchanged';
    } catch (error) {
        // App switches must never surface errors; the keys then simply pass through.
        return 'skipped: ' + String(error.message || error);
    }
}
