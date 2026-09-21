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
function escapeCheck(actual, wanted, names) {
    for(const key of ['BTTUUID','BTTTriggerType','BTTTriggerClass','BTTShortcutKeyCode',
        'BTTShortcutModifierKeys','BTTTriggerOnDown','BTTPredefinedActionType']) {
        if(actual[key]!==wanted[key])throw new Error('Escape readback mismatch: '+key);
    }
    if(Number(actual.BTTActionCategory||0)!==0)throw new Error('Escape must use the standard action category.');
    const pass=actual.BTTKeyboardShortcutPerformDefaultOnAdvancedConditionMismatch ||
        (actual.BTTAdditionalDataJSON||{}).BTTKeyboardShortcutPerformDefaultOnAdvancedConditionMismatch;
    if(!pass)throw new Error('Escape passthrough setting was not retained.');
    const format=actual.BTTTriggerConditionsFormat || actual.BTTTriggerConditionsFormatReadOnly;
    if(!format || escapeCondition(format,names)!==escapeCondition(wanted.BTTTriggerConditionsFormat,names)) {
        throw new Error('Escape visibility condition was not retained.');
    }
    const data=typeof actual.BTTAdditionalActionData==='string'?
        JSON.parse(actual.BTTAdditionalActionData):actual.BTTAdditionalActionData;
    for(const key of Object.keys(wanted.BTTAdditionalActionData)) {
        const expected=wanted.BTTAdditionalActionData[key];
        const equal=data && (typeof expected==='boolean'?
            data[key]!==undefined && Boolean(data[key])===expected:data[key]===expected);
        if(!equal)throw new Error('Escape action mismatch: '+key);
    }
    for(const key of ['BTTActionsToExecute','BTTAdditionalActions','BTTMenuItemActions']) {
        if((actual[key]||[]).length)throw new Error('Unexpected extra Escape actions; refusing to overwrite them.');
    }
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
    } else if(mode!=='verify' && mode!=='enable' && mode!=='disable')throw new Error('Unknown Escape mode.');
    let current=get();
    if(mode==='create' && (Number(current.BTTEnabled)!==0 ||
        (current.BTTEnabled2!==undefined && Number(current.BTTEnabled2)!==0))) {
        throw new Error('Escape was not created disabled; activation aborted.');
    }
    if(mode!=='disable')escapeCheck(current,wanted,plan.names);
    if(mode==='enable' || mode==='disable') {
        // This new standalone shortcut has no children. Preserve its full export.
        current.BTTEnabled=mode==='enable'?1:0;
        current.BTTEnabled2=current.BTTEnabled;
        btt.update_trigger(wanted.BTTUUID,{json:JSON.stringify(current)});
        current=get();
        if(mode==='enable')escapeCheck(current,wanted,plan.names);
    }
    return JSON.stringify(current);
}
