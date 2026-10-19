// Narrow action-only repair; the original copy/delay records remain disabled.
ObjC.import('Foundation');
const TEXT_MENU = 'F08B0C3E-B629-441B-BFAE-3E1E43F390AA';
const TEXT_ITEMS = [
    ['A0F73D40-36D5-4BCD-8A99-D60FAA3C6904', 'lower', 'to_lower'],
    ['2CA984CB-4660-45B0-829A-80DB2E756E0E', 'upper', 'to_upper'],
    ['8AE049ED-3C07-4883-A564-4AAD85D866EB', 'snake', 'to_snake'],
    ['8B332E0E-87D5-491D-8C73-FDD4D3180095', 'kebab', 'to_kebab'],
    ['DC3B7FB0-6C34-4815-8147-50AE4A97DBB0', 'camel', 'to_camel'],
    ['7C30DC64-6F54-4D05-BA8D-529B7796543A', 'base64-encode', 'to_64'],
    ['7E1C433B-10DE-4C09-B451-4FB52DD4F2E1', 'base64-decode', 'from_64'],
    ['636C724B-BE53-47C7-8126-CE71EAB88BF9', 'uri-encode', 'encode_uri'],
    ['DF28B610-D8C3-465C-8D7F-EF26FD6F239D', 'uri-decode', 'decode_uri']
];

/** Wire contract consumed by repair_text_transforms.py.
 * @typedef {{parent:string, kind:string, before:Object, patch:Object, matches:boolean}} TextEdit
 * @typedef {{version:number, menu:Object, edits:TextEdit[]}} TextRepairPlan
 */

function textActionData(kind, directory, library) {
    return {
        BTTScriptType: 3, BTTAppleScriptUsePath: false, BTTScriptLocation: 0,
        BTTAppleScriptRunInBackground: true, BTTScriptFunctionToCall: 'replaceSelectedText',
        BTTActionJSRunInSeparateContext: true,
        BTTAppleScriptString: `async function replaceSelectedText() {
    const options = {
        kind: ${JSON.stringify(kind)},
        directory: ${JSON.stringify(directory)},
        caseLibrary: ${JSON.stringify(library)}
    };

    // All buttons use the same selection handling and transformation code.
    const source = await readFile(options.directory + '/text_selection.js');
    const invocation = '\\nrunTextTransform(' + JSON.stringify(options) + ');';
    const result = JSON.parse(await runJXA(source + invocation));

    if (!result.ok) {
        await callBTT('display_notification', {
            title: 'Text transform stopped',
            '': result.error
        });
    }
    return result.ok ? result.result : result.error;
}`
    };
}

function textGet(api, uuid) {
    const value = JSON.parse(api.call('get_trigger', {uuid}));
    const matches = (Array.isArray(value) ? value : [value]).filter(x => x && x.BTTUUID === uuid);
    if (matches.length !== 1) throw new Error('Cannot uniquely export text-transform record: ' + uuid);
    return matches[0];
}

function textExportedData(value) {
    const data = typeof value === 'string' ? JSON.parse(value) : Object.assign({}, value);
    // Export aliases this key; update_trigger and the editor require BTTAppleScriptString.
    if ('BTTScriptString' in data && !('BTTAppleScriptString' in data)) {
        data.BTTAppleScriptString = data.BTTScriptString;
        delete data.BTTScriptString;
    }
    return data;
}

function textPatchMatches(actual, patch, common) {
    return Object.keys(patch).every(key => {
        const normalize = value => key === 'BTTAdditionalActionData' ? textExportedData(value) : value;
        const value = actual[key] === undefined && key === 'BTTEnabled' ? 1 : actual[key];
        return common.fingerprint(normalize(value)) === common.fingerprint(normalize(patch[key]));
    });
}

function textPlan(api, common, directory, library) {
    const menu = textGet(api, TEXT_MENU), edits = [];
    if (Number(menu.BTTTriggerType) !== 774 || !Array.isArray(menu.BTTMenuItems))
        throw new Error('Text-transform submenu is missing or has changed type.');
    for (const [parent, kind, transformer] of TEXT_ITEMS) {
        const items = menu.BTTMenuItems.filter(x => x.BTTUUID === parent);
        if (items.length !== 1 || Number(items[0].BTTTriggerType) !== 773)
            throw new Error('Expected text-transform item is missing: ' + kind + ' (' + parent + ').');
        const actions = (items[0].BTTMenuItemActions || []).slice().sort((a,b) => a.BTTOrder - b.BTTOrder);
        if (actions.length !== 3) throw new Error('Text-transform action chain has changed: ' + kind);
        const data = textActionData(kind, directory, library);
        const before = actions.map(a => textGet(api, a.BTTUUID));
        const pasteData = textExportedData(before[2].BTTAdditionalActionData);
        // Upgrade the prior shared-context version; all other script fields must match.
        const managedData = Object.assign({BTTActionJSRunInSeparateContext: true}, pasteData);
        const managed = Number(before[2].BTTPredefinedActionType) === 281
            && common.fingerprint(managedData) === common.fingerprint(data);
        const prefix = [264, -1].includes(Number(before[0].BTTPredefinedActionType))
            && before[0].BTTShortcutToSend === '55,8'
            && Number(before[1].BTTPredefinedActionType) === 129;
        const legacy = Number(before[2].BTTPredefinedActionType) === 193
            && pasteData && pasteData.BTTActionCustomTextTransformer === transformer;
        if (!prefix || (!legacy && !managed)) throw new Error('Unrecognized action configuration: ' + kind);
        for (let i = 0; i < 3; i++) {
            if (before[i].BTTTriggerParentUUID !== parent)
                throw new Error('Text-transform action has moved.');
            const patch = i < 2 ? {BTTEnabled: 0} : {
                BTTEnabled: 1, BTTPredefinedActionType: 281,
                BTTAdditionalActionData: JSON.stringify(data)
            };
            edits.push({parent, kind, before: before[i], patch,
                matches: textPatchMatches(before[i], patch, common)});
        }
    }
    return {version: 1, menu, edits};
}

function textVerify(api, common, plan) {
    for (const edit of plan.edits) {
        const actual = textGet(api, edit.before.BTTUUID);
        if (actual.BTTTriggerParentUUID !== edit.parent || actual.BTTOrder !== edit.before.BTTOrder)
            throw new Error('Text-transform action parent/order changed.');
        if (!textPatchMatches(actual, edit.patch, common))
            throw new Error('Text-transform runtime readback failed: ' + edit.kind);
    }
    const stripActions = menu => {
        const copy = common.clone(menu);
        copy.BTTMenuItems.forEach(item => { delete item.BTTMenuItemActions; });
        return copy;
    };
    if (common.fingerprint(stripActions(textGet(api, TEXT_MENU))) !== common.fingerprint(stripActions(plan.menu)))
        throw new Error('Text-transform menu appearance or structure changed.');
}

function textApply(api, common, snapshot, directory, library) {
    const plan = textPlan(api, common, directory, library);
    if (common.fingerprint(plan) !== common.fingerprint(snapshot))
        throw new Error('Text-transform plan or records changed during backup; no changes made.');
    function update(edit, patch) {
        api.call('update_trigger', {uuid: edit.before.BTTUUID, trigger_parent_uuid: edit.parent,
            json: JSON.stringify(patch)});
        if (!textPatchMatches(textGet(api, edit.before.BTTUUID), patch, common))
            throw new Error('BTT did not save the requested action update; stopped before further changes.');
    }
    // The coordinator skips a verified installation. Once applying, rewrite scripts:
    // API export equality alone cannot prove the executable key is stored correctly.
    const pending = plan.edits.filter(e => !e.matches || e.patch.BTTPredefinedActionType === 281);
    const replacements = pending.filter(e => e.patch.BTTPredefinedActionType === 281);
    // Prove each disable succeeded before activating any replacement.
    for (const edit of replacements) update(edit, {BTTEnabled: 0});
    for (const edit of pending.filter(e => e.patch.BTTEnabled === 0)) update(edit, edit.patch);
    for (const edit of plan.edits.filter(e => e.patch.BTTEnabled === 0)) {
        if (!textPatchMatches(textGet(api, edit.before.BTTUUID), edit.patch, common))
            throw new Error('A legacy copy/delay action is still enabled; replacements remain disabled.');
    }
    for (const edit of replacements) update(edit, edit.patch);
    textVerify(api, common, plan);
    // InstallerEditReceipt is defined in btt_guard/model.py.
    return JSON.stringify({message: 'Verified nine selection-only transforms; menu appearance preserved.',
        wrote: pending.length > 0, touched_uuids: pending.map(edit => edit.before.BTTUUID)});
}

function run(argv) {
    const directory = argv[1];
    function read(path) {
        const value = $.NSString.stringWithContentsOfFileEncodingError(path, $.NSUTF8StringEncoding, null);
        if (!value) throw new Error('Cannot read repair input.');
        return ObjC.unwrap(value);
    }
    const common = new Function(read(directory + '/btt_common.js') + '\nreturn BTTCommon;')();
    const api = new Function(read(directory + '/btt_socket.js') + '\nreturn BTTSocket;')();
    if (argv[0] === 'plan') return JSON.stringify(textPlan(api, common, directory, argv[2]));
    const plan = common.readJSON(argv[3]);
    if (argv[0] === 'apply') return textApply(api, common, plan, directory, argv[2]);
    if (argv[0] === 'verify') { textVerify(api, common, plan); return 'Runtime verified.'; }
    throw new Error('Unknown text-transform repair mode.');
}
