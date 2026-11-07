// Two-stage installer: verify the conditional shortcut while disabled, then enable.
ObjC.import('Foundation');

function escapeCondition(format, names) {
    const predicate=$.NSPredicate.predicateWithFormatArgumentArray($(format),$([]));
    for(const value of ['', 'Media', 'unrelated-menu'].concat(names)) {
        const actual=Boolean(predicate.evaluateWithObject($({visible_floating_menu_identifiers:value})));
        if(actual!==names.includes(value))throw new Error('Escape visibility condition failed its truth table.');
    }
    return ObjC.unwrap(predicate.predicateFormat);
}
const PASS_KEY='BTTKeyboardShortcutPerformDefaultOnAdvancedConditionMismatch';
function asObject(value) {
    return typeof value==='string'?JSON.parse(value):Object.assign({},value);
}
function exportedActionData(value) {
    const data=asObject(value);
    // A fresh export renames the saved BTTAppleScriptString to BTTScriptString, while the stored record and
    // update_trigger keep the saved name (same alias as repair_text_transforms.js and btt_guard/definitions.py).
    if('BTTScriptString' in data && !('BTTAppleScriptString' in data)) {
        data.BTTAppleScriptString=data.BTTScriptString;
        delete data.BTTScriptString;
    }
    return data;
}
function savableTrigger(trigger) {
    // Writing the export back unchanged would save the alias, not the script key BTT's editor reads.
    const copy=Object.assign({},trigger);
    if(trigger.BTTAdditionalActionData!==undefined) {
        const data=exportedActionData(trigger.BTTAdditionalActionData);
        copy.BTTAdditionalActionData=typeof trigger.BTTAdditionalActionData==='string'?JSON.stringify(data):data;
    }
    return copy;
}
function escapeIdentity(actual, wanted) {
    for(const key of ['BTTUUID','BTTTriggerType','BTTTriggerClass','BTTShortcutKeyCode',
        'BTTShortcutModifierKeys','BTTTriggerOnDown','BTTPredefinedActionType']) {
        if(actual[key]!==wanted[key])throw new Error('Escape readback mismatch: '+key);
    }
    if(Number(actual.BTTActionCategory||0)!==0)throw new Error('Escape must use the standard action category.');
    for(const key of ['BTTActionsToExecute','BTTAdditionalActions','BTTMenuItemActions']) {
        if((actual[key]||[]).length)throw new Error('Unexpected extra Escape actions; refusing to overwrite them.');
    }
}
function escapeCheck(actual, wanted, names) {
    escapeIdentity(actual,wanted);
    const pass=actual[PASS_KEY] || (actual.BTTAdditionalDataJSON||{})[PASS_KEY];
    if(!pass)throw new Error('Escape passthrough setting was not retained.');
    const format=actual.BTTTriggerConditionsFormat || actual.BTTTriggerConditionsFormatReadOnly;
    if(!format || escapeCondition(format,names)!==escapeCondition(wanted.BTTTriggerConditionsFormat,names)) {
        throw new Error('Escape visibility condition was not retained.');
    }
    const data=exportedActionData(actual.BTTAdditionalActionData);
    for(const key of Object.keys(wanted.BTTAdditionalActionData)) {
        const expected=wanted.BTTAdditionalActionData[key];
        const equal=typeof expected==='boolean'?
            data[key]!==undefined && Boolean(data[key])===expected:data[key]===expected;
        if(!equal)throw new Error('Escape action mismatch: '+key);
    }
}
function staleUpdate(current, wanted) {
    // The export's derived condition keys would override the new format string, so drop them and let BTT rebuild.
    const next=Object.assign({},current);
    delete next.BTTTriggerConditionsData;
    delete next.BTTTriggerConditionsFormatReadOnly;
    next.BTTEnabled=0;
    next.BTTEnabled2=0;
    next.BTTAdditionalActionData=wanted.BTTAdditionalActionData;
    next.BTTTriggerConditionsFormat=wanted.BTTTriggerConditionsFormat;
    next.BTTGestureNotes=wanted.BTTGestureNotes;
    next[PASS_KEY]=wanted[PASS_KEY];
    next.BTTAdditionalDataJSON=Object.assign(asObject(current.BTTAdditionalDataJSON),wanted.BTTAdditionalDataJSON);
    return next;
}
function run(argv) {
    const mode=argv[0];
    const raw=$.NSString.stringWithContentsOfFileEncodingError(argv[1],$.NSUTF8StringEncoding,null);
    if(!raw)throw new Error('Cannot read Escape installation plan.');
    const plan=JSON.parse(ObjC.unwrap(raw)),wanted=plan.definition;
    if(mode==='condition-test')return escapeCondition(wanted.BTTTriggerConditionsFormat,plan.names);
    const btt=Application('/Applications/BetterTouchTool.app');
    function get() {
        const value=JSON.parse(btt.get_trigger(wanted.BTTUUID));
        const hits=(Array.isArray(value)?value:[value]).filter(x=>x&&x.BTTUUID===wanted.BTTUUID);
        if(hits.length!==1)throw new Error('Cannot export Escape shortcut.');
        return hits[0];
    }
    if(mode==='create') {
        escapeCondition(wanted.BTTTriggerConditionsFormat,plan.names);
        btt.add_new_trigger(JSON.stringify(wanted)); // Both enable flags are zero.
    } else if(mode!=='verify' && mode!=='update' && mode!=='enable' && mode!=='disable')throw new Error('Unknown Escape mode.');
    let current=get();
    if(mode==='update') {
        // Upgrade an installed shortcut whose saved definition predates the plan; only its own definition changes.
        escapeIdentity(current,wanted);
        btt.update_trigger(wanted.BTTUUID,{json:JSON.stringify(staleUpdate(current,wanted))});
        current=get();
        escapeCheck(current,wanted,plan.names);
        if(Number(current.BTTEnabled)!==0 || (current.BTTEnabled2!==undefined && Number(current.BTTEnabled2)!==0)) {
            throw new Error('Escape was not left disabled by the update; activation aborted.');
        }
        return JSON.stringify(current);
    }
    if(mode==='create' && (Number(current.BTTEnabled)!==0 ||
        (current.BTTEnabled2!==undefined && Number(current.BTTEnabled2)!==0))) {
        throw new Error('Escape was not created disabled; activation aborted.');
    }
    if(mode!=='disable')escapeCheck(current,wanted,plan.names);
    if(mode==='enable' || mode==='disable') {
        // This new standalone shortcut has no children. Preserve its full export.
        current.BTTEnabled=mode==='enable'?1:0;
        current.BTTEnabled2=current.BTTEnabled;
        btt.update_trigger(wanted.BTTUUID,{json:JSON.stringify(savableTrigger(current))});
        current=get();
        if(mode==='enable')escapeCheck(current,wanted,plan.names);
    }
    return JSON.stringify(current);
}
