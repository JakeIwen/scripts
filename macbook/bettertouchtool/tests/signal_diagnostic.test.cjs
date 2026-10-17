const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function diagnosticContext(value, parameterizedText, status = 0) {
    const native = value => value;
    native.AXUIElementCopyParameterizedAttributeValue = (element, name, range, output) => {
        assert.equal(name,'AXStringForRange');
        output[0] = parameterizedText;
        return status;
    };
    const context = vm.createContext({
        $:native, Ref:() => [], ObjC:{import() {},unwrap:x => x,castRefToObject:x => x},
        selectionOffsets:range => range,
        selectionAttribute:(element,name) => name === 'AXRole' ? 'AXTextArea' : value
    });
    vm.runInContext(fs.readFileSync(path.join(__dirname,'../signal_text_diagnostic.js'),'utf8'),context);
    return context;
}

test('range mismatch diagnostics expose offsets without exposing draft text', () => {
    const context = diagnosticContext('hello\nworld','orld');
    const report = context.signalSelectionReport({element:{},range:{location:7,length:5},text:'world',writable:true});
    assert.equal(report.valueLength,11);
    assert.equal(report.selectedTextLength,5);
    assert.equal(report.rangeMatchesSelectedText,false);
    assert.equal(JSON.stringify(report.matchingOffsets),'[6]');
    assert.equal(report.stringForRange.matchesSelectedText,false);
    assert.equal(JSON.stringify(report).includes('world'),false);
    assert.equal(JSON.stringify(report).includes('hello'),false);
});

test('unsupported parameterized attributes remain diagnostic data', () => {
    const context = diagnosticContext('test','',-25205);
    const report = context.signalSelectionReport({element:{},range:{location:0,length:4},text:'test',writable:true});
    assert.equal(report.rangeMatchesSelectedText,true);
    assert.equal(report.stringForRange.status,-25205);
});

function runnableDiagnostic({restoreFails = false, saveFails = false, staleWorkspace = false,
    autoActivate = true, switchAfter = null} = {}) {
    const context = diagnosticContext('test','test');
    let front;
    const previous = {processIdentifier:1, activateWithOptions() {
        if (restoreFails) throw new Error('fixture focus restoration failed');
        front = previous;
        return true;
    }};
    const signal = {processIdentifier:2, activateWithOptions() { if (autoActivate) front = signal; return autoActivate; }};
    front = previous;
    const native = value => typeof value === 'string' && value.startsWith('{')
        ? {writeToFileAtomicallyEncodingError() { return !saveFails; }} : value;
    Object.assign(native, context.$, {
        NSWorkspace:{sharedWorkspace:{get frontmostApplication() { return staleWorkspace ? previous : front; }}},
        NSRunningApplication:{runningApplicationsWithBundleIdentifier:() => ({count:1, objectAtIndex:() => signal})},
        NSFileManager:{defaultManager:{setAttributesOfItemAtPathError:() => true}},
        AXUIElementCreateSystemWide:() => ({}),
        AXUIElementGetPid:(app, result) => { result[0] = app.processIdentifier; return 0; },
        CGEventSourceButtonState:() => false, kCGEventSourceStateCombinedSessionState:'0'
    });
    context.$ = native;
    let waits = 0;
    context.delay = () => { if (++waits === switchAfter) front = signal; };
    const attribute = context.selectionAttribute;
    context.selectionAttribute = (element,name) => name === 'AXFocusedApplication' ? front : attribute(element,name);
    context.selectionUI = () => ({read:() => ({element:{},range:{location:0,length:4},text:'test',writable:true}), same:() => true});
    context.transformSelection = () => { throw new Error('fixture transform failed'); };
    return options => JSON.parse(context.signalTextDiagnostic({directory:'/fixture',caseLibrary:'/fixture/cases',
        transform:'upper',selectionTimeoutSeconds:1,performTransform:true,...options}));
}

test('focus restoration errors cannot hide the original transform error or report', () => {
    const report = runnableDiagnostic({restoreFails:true})({});
    assert.equal(report.stage,'transform selected text');
    assert.equal(report.error.message,'fixture transform failed');
    assert.equal(report.focusRestored,false);
    assert.match(report.focusRestoreError,/fixture focus restoration failed/);
    assert.equal(report.reportSaved,true);
});

test('failed persistence is explicit in the returned Script Result', () => {
    const report = runnableDiagnostic({saveFails:true})({performTransform:false});
    assert.equal(report.ok,true);
    assert.equal(report.reportSaved,false);
    assert.match(report.saveError,/Copy the Script Result/);
});

test('live keyboard focus works even when NSWorkspace still reports BTT', () => {
    const report = runnableDiagnostic({staleWorkspace:true})({performTransform:false});
    assert.equal(report.ok,true);
    assert.equal(report.focusRestored,true);
});

test('manual switching to Signal is accepted if automatic activation fails', () => {
    const report = runnableDiagnostic({autoActivate:false,switchAfter:2})({performTransform:false});
    assert.equal(report.ok,true);
    assert.equal(report.activationRequested,false);
});

test('remaining in BTT times out without attempting a transform', () => {
    const report = runnableDiagnostic({autoActivate:false})({selectionTimeoutSeconds:0.2});
    assert.equal(report.ok,false);
    assert.equal(report.stage,'wait for Signal selection');
    assert.match(report.error.message,/Timed out waiting/);
    assert.equal(report.result,undefined);
});
