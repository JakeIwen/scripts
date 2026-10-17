// Run after text_selection.js in JXA. Reports contain metadata, never draft text.
ObjC.import('AppKit');

function signalSelectionReport(selection) {
    const offsets = selectionOffsets(selection.range);
    const value = ObjC.unwrap(selectionAttribute(selection.element, 'AXValue'));
    const report = {
        role:ObjC.unwrap(selectionAttribute(selection.element, 'AXRole')),
        writable:selection.writable,
        selectedTextLength:selection.text.length,
        range:offsets,
        valueType:typeof value,
        valueLength:typeof value === 'string' ? value.length : null,
        rangeMatchesSelectedText:typeof value === 'string'
            && value.slice(offsets.location, offsets.location + offsets.length) === selection.text,
        matchingOffsets:[]
    };
    if (typeof value === 'string' && selection.text.length) {
        let start = 0;
        while (report.matchingOffsets.length < 20) {
            const index = value.indexOf(selection.text, start);
            if (index < 0) break;
            report.matchingOffsets.push(index);
            start = index + 1;
        }
    }
    try {
        const result = Ref();
        const status = $.AXUIElementCopyParameterizedAttributeValue(
            selection.element, $('AXStringForRange'), selection.range, result);
        report.stringForRange = {status};
        if (status === 0) {
            const text = ObjC.unwrap(ObjC.castRefToObject(result[0]));
            report.stringForRange.length = typeof text === 'string' ? text.length : null;
            report.stringForRange.matchesSelectedText = text === selection.text;
        }
    } catch (error) {
        report.stringForRange = {error:String(error.message || error)};
    }
    return report;
}

function signalHasKeyboardFocus(system, signal) {
    const application = selectionAttribute(system, 'AXFocusedApplication', {optional:true});
    const pid = Ref();
    return application !== null && $.AXUIElementGetPid(application, pid) === 0
        && Number(pid[0]) === Number(signal.processIdentifier);
}

function waitForSignalSelection(system, signal, options, report) {
    const seconds = Number(options.selectionTimeoutSeconds);
    if (!Number.isFinite(seconds) || seconds < 0 || seconds > 60) throw new Error('Selection timeout must be 0–60 seconds.');
    let previous = null, stable = 0;
    for (let attempt = 0; attempt <= Math.ceil(seconds * 10); attempt++) {
        if (!signalHasKeyboardFocus(system, signal)) {
            report.lastWaitState = 'Switch to Signal and select test draft text.';
            previous = null;
            stable = 0;
        } else {
            try {
                const ui = selectionUI(options.directory), selection = ui.read();
                if (!options.performTransform) return {ui, selection};
                const mouseDown = $.CGEventSourceButtonState(Number($.kCGEventSourceStateCombinedSessionState), 0);
                if (selection.text && !mouseDown) {
                    stable = previous && ui.same(previous, selection) ? stable + 1 : 0;
                    if (stable >= 2) return {ui, selection};
                    report.lastWaitState = 'Waiting for the selection to settle.';
                    previous = selection;
                } else {
                    report.lastWaitState = 'Select test draft text and release the mouse.';
                    previous = null;
                    stable = 0;
                }
            } catch (error) {
                report.lastWaitState = String(error.message || error);
                previous = null;
                stable = 0;
            }
        }
        if (attempt < Math.ceil(seconds * 10)) delay(0.1);
    }
    throw new Error('Timed out waiting for a Signal selection. ' + report.lastWaitState);
}

function signalTextDiagnostic(options) {
    const report = {time:new Date().toISOString(), transform:options.transform,
        performTransform:options.performTransform, stage:'find Signal', ok:false};
    const previous = $.NSWorkspace.sharedWorkspace.frontmostApplication;
    const system = $.AXUIElementCreateSystemWide();
    let signal = null;
    try {
        const apps = $.NSRunningApplication.runningApplicationsWithBundleIdentifier('org.whispersystems.signal-desktop');
        if (Number(apps.count) !== 1) throw new Error('Open Signal before running this diagnostic.');
        signal = apps.objectAtIndex(0);
        report.stage = 'activate Signal';
        report.activationRequested = Boolean(signal.activateWithOptions(2));
        report.stage = 'wait for Signal selection';
        const {ui, selection} = waitForSignalSelection(system, signal, options, report);
        report.stage = 'read selection';
        report.before = signalSelectionReport(selection);
        report.stableSelection = Boolean(ui.same(selection, ui.read()));
        if (!report.stableSelection) throw new Error('The selection changed during inspection.');
        if (options.performTransform) {
            report.stage = 'transform selected text';
            report.result = transformSelection(options.transform, options.directory, options.caseLibrary);
            report.stage = 'read result';
            report.after = signalSelectionReport(ui.read());
        }
        report.stage = 'complete';
        report.ok = true;
    } catch (error) {
        report.error = {name:String(error.name || 'Error'), message:String(error.message || error),
            stack:typeof error.stack === 'string' ? error.stack : null};
    } finally {
        try {
            if (signal && signalHasKeyboardFocus(system, signal)) {
                report.focusRestored = Boolean(previous.activateWithOptions(2));
                if (!report.focusRestored) report.focusRestoreError = 'The previous app could not be activated.';
            }
        } catch (error) {
            report.focusRestored = false;
            report.focusRestoreError = String(error.message || error);
        }
    }
    const file = options.directory + '/.local/signal-diagnostic-last.json';
    report.reportSaved = true;
    try {
        if (!$(JSON.stringify(report, null, 2)).writeToFileAtomicallyEncodingError(file, true, $.NSUTF8StringEncoding, null))
            throw new Error('Could not save the report. Copy the Script Result instead.');
        if (!$.NSFileManager.defaultManager.setAttributesOfItemAtPathError($({NSFilePosixPermissions:384}), file, null))
            report.saveWarning = 'Report saved, but private file permissions could not be verified.';
    } catch (error) {
        report.reportSaved = false;
        report.saveError = String(error.message || error);
    }
    return JSON.stringify(report, null, 2);
}
