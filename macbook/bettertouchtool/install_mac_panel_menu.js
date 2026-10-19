// Standalone triggers only: no menu imports, trigger execution, or BTT restart.
ObjC.import('Foundation');

function source(path) {
    const text = $.NSString.stringWithContentsOfFileEncodingError(path, $.NSUTF8StringEncoding, null);
    if (!text) throw new Error('Cannot load Mac Panel installer dependency.');
    return ObjC.unwrap(text);
}

function readRuntime(socket, common) {
    const value = JSON.parse(socket.call('get_triggers', {}));
    if (!Array.isArray(value) || value.some(item => !item || typeof item !== 'object' || !item.BTTUUID)) {
        throw new Error('Incomplete BTT runtime response; no changes made.');
    }
    if (new Set(value.map(item => item.BTTUUID)).size !== value.length) {
        throw new Error('Duplicate runtime UUID; no changes made.');
    }
    return common.canonical(value);
}

function matches(actual, desired, common) {
    if (!actual) return false;
    return Object.keys(desired).every(key => {
        // Global app scope is absent from BTT's export; Python verifies its saved relation.
        if (key === 'BTTAppBundleIdentifier') return true;
        if (key === 'BTTEnabled' || key === 'BTTEnabled2') {
            return Number(actual[key] === undefined ? 1 : actual[key]) === desired[key];
        }
        if (key === 'BTTShortcutScope' || key === 'BTTAutoAdaptToKeyboardLayout' || key === 'BTTOrder') {
            return Number(actual[key] || 0) === desired[key];
        }
        if (key === 'BTTTriggerConfig') {
            return Object.keys(desired[key]).every(setting => {
                const value = (actual[key] || {})[setting];
                if (typeof desired[key][setting] === 'number') return Number(value || 0) === desired[key][setting];
                return common.fingerprint(value) === common.fingerprint(desired[key][setting]);
            });
        }
        return common.fingerprint(actual[key]) === common.fingerprint(desired[key]);
    });
}

function outsideOwned(items, ids) {
    return items.filter(item => !ids.includes(item.BTTUUID));
}

function validatePlan(plan) {
    if (!plan || !Array.isArray(plan.before) || !Array.isArray(plan.desired) || plan.desired.length !== 2) {
        throw new Error('Invalid Mac Panel installation plan.');
    }
    const ids = plan.desired.map(node => node.BTTUUID);
    if (new Set(ids).size !== 2) throw new Error('Duplicate Mac Panel plan UUID.');
    for (const node of plan.desired) {
        if (node.BTTAppBundleIdentifier !== 'BT.G' || node.BTTTriggerBelongsToPreset !== 'Master' ||
            node.BTTPredefinedActionType !== 59 || ![0, 674].includes(node.BTTTriggerType) ||
            node.BTTOpenURL !== plan.summary.url || node.BTTTriggerParentUUID ||
            ['BTTActionsToExecute', 'BTTAdditionalActions', 'BTTMenuItems', 'BTTMenuItemActions']
                .some(key => node[key])) throw new Error('Unsafe Mac Panel plan scope.');
    }
    return ids;
}

function applyPlan(socket, common, plan) {
    const ids = validatePlan(plan);
    let before = readRuntime(socket, common);
    if (common.fingerprint(before) !== common.fingerprint(plan.before)) {
        throw new Error('BTT changed since preview/backup; no changes made.');
    }
    let writes = 0;
    for (const desired of plan.desired) {
        const previous = before.find(node => node.BTTUUID === desired.BTTUUID);
        if (matches(previous, desired, common)) continue;
        const fresh = readRuntime(socket, common);
        if (common.fingerprint(fresh) !== common.fingerprint(before)) {
            throw new Error('BTT changed before the next item; stopped without retrying.');
        }
        if (previous) socket.call('update_trigger', {uuid: desired.BTTUUID, json: JSON.stringify(desired)});
        else socket.call('add_new_trigger', {json: JSON.stringify(desired)});
        writes++;
        const after = readRuntime(socket, common);
        if (!matches(after.find(node => node.BTTUUID === desired.BTTUUID), desired, common)) {
            throw new Error('Mac Panel runtime verification failed; stopped without retrying.');
        }
        if (common.fingerprint(outsideOwned(after, [desired.BTTUUID])) !==
            common.fingerprint(outsideOwned(before, [desired.BTTUUID]))) {
            throw new Error('Runtime configuration outside the intended item changed; stopped.');
        }
        before = after;
    }
    if (common.fingerprint(outsideOwned(before, ids)) !==
        common.fingerprint(outsideOwned(plan.before, ids))) {
        throw new Error('Unexpected runtime changes outside Mac Panel.');
    }
    return {writes};
}

function run(argv) {
    if (!['inspect', 'apply'].includes(argv[0])) throw new Error('Explicit inspect/apply mode required.');
    eval(source(argv[1]));
    eval(source(argv[2]));
    if (argv[0] === 'inspect') return JSON.stringify(readRuntime(BTTSocket, BTTCommon));
    return JSON.stringify(applyPlan(BTTSocket, BTTCommon, BTTCommon.readJSON(argv[3])));
}
