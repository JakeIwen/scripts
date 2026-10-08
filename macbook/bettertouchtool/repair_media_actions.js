// Wire contract: ActionRepairPlan in repair_media_actions.py. No action execution.
ObjC.import('Foundation');

function validateActionPlan(plan) {
    if (plan.version !== 1 || !Array.isArray(plan.entities) || !Array.isArray(plan.buttons) ||
        !Array.isArray(plan.dependencies) || !Array.isArray(plan.dependency_sources)) {
        throw new Error('Invalid action recovery plan.');
    }
    const ids = new Set(), parents = new Set(plan.buttons.map(b => b.uuid));
    for (const entity of plan.entities) {
        if (!entity.uuid || ids.has(entity.uuid) || entity.uuid !== entity.node.BTTUUID ||
            ![206,248,480].includes(Number(entity.node.BTTPredefinedActionType))) {
            throw new Error('Invalid or duplicate recovery action.');
        }
        if (entity.parent ? (!parents.has(entity.parent) || Number(entity.node.BTTTriggerType) !== -1 ||
                            entity.node.BTTTriggerParentUUID !== entity.parent) :
                            Number(entity.node.BTTTriggerType) !== 643) {
            throw new Error('Invalid recovery parent/type.');
        }
        ids.add(entity.uuid);
    }
}

function checkRecoveredAction(actual, entity, fingerprint) {
    if (!actual || actual.BTTUUID !== entity.uuid) throw new Error('Missing action '+entity.uuid);
    const expected = entity.node;
    for (const key of ['BTTPredefinedActionType','BTTOrder','BTTActionCategory','BTTEnabled']) {
        const fallback = key === 'BTTEnabled' ? 1 : 0;
        if (Number(actual[key] === undefined ? fallback : actual[key]) !==
            Number(expected[key] === undefined ? fallback : expected[key])) {
            throw new Error('Recovered action differs at '+key+': '+entity.uuid);
        }
    }
    for (const key of ['BTTNamedTriggerToTrigger','BTTShellTaskActionScript','BTTShellTaskActionConfig',
                       'BTTAdditionalActionData']) {
        if (expected[key] === undefined) continue;
        const value = x => key === 'BTTAdditionalActionData' && typeof x === 'string' ? JSON.parse(x) : x;
        if (fingerprint(value(actual[key])) !== fingerprint(value(expected[key]))) {
            throw new Error('Recovered action payload differs at '+key+': '+entity.uuid);
        }
    }
}

function actionSnapshot(plan, get, fingerprint) {
    validateActionPlan(plan);
    const snapshot = {};
    for (const button of plan.buttons) {
        const item = get(button.uuid);
        if (!item || Number(item.BTTTriggerType) !== 773 ||
            ![366,-1].includes(Number(item.BTTPredefinedActionType === undefined ? 366 : item.BTTPredefinedActionType))) {
            throw new Error('Expected an empty standard button: '+button.uuid);
        }
        const allowed = new Map(plan.entities.filter(e => e.parent === button.uuid).map(e => [e.uuid,e]));
        for (const action of [...(item.BTTMenuItemActions || []), ...(item.BTTAdditionalActions || [])]) {
            if (!allowed.has(action.BTTUUID)) throw new Error('Button already has a different action: '+button.uuid);
            checkRecoveredAction(action, allowed.get(action.BTTUUID), fingerprint);
        }
        snapshot[button.uuid] = item;
    }
    for (const id of [...plan.dependency_sources, ...plan.dependencies.map(d => d.uuid),
                      ...plan.entities.map(e => e.uuid)]) {
        if (!(id in snapshot)) snapshot[id] = get(id);
    }
    for (const id of plan.dependency_sources) {
        if (!snapshot[id]) throw new Error('Named-trigger recovery source disappeared: '+id);
    }
    for (const entity of plan.entities) {
        if (!snapshot[entity.uuid]) continue;
        checkRecoveredAction(snapshot[entity.uuid], entity, fingerprint);
        if (entity.parent && ![...(snapshot[entity.parent].BTTMenuItemActions || []),
                                ...(snapshot[entity.parent].BTTAdditionalActions || [])]
            .some(a => a.BTTUUID === entity.uuid)) {
            throw new Error('Existing recovery action belongs elsewhere: '+entity.uuid);
        }
    }
    return snapshot;
}

function verifyRecoveredPlan(plan, get, fingerprint) {
    for (const entity of plan.entities) checkRecoveredAction(get(entity.uuid), entity, fingerprint);
    for (const button of plan.buttons) {
        const before = plan.snapshot[button.uuid], after = get(button.uuid);
        if (!after) throw new Error('Parent button disappeared: '+button.uuid);
        const expected = plan.entities.filter(e => e.parent === button.uuid);
        const actions = [...(after.BTTMenuItemActions || []), ...(after.BTTAdditionalActions || [])];
        if (actions.length !== expected.length || expected.some(e => !actions.some(a => a.BTTUUID === e.uuid))) {
            throw new Error('Action association was not restored: '+button.uuid);
        }
        // Compare the existing button with only newly added actions removed.
        const clean = JSON.parse(JSON.stringify(after));
        for (const key of ['BTTMenuItemActions','BTTAdditionalActions']) {
            const previousIDs = new Set((before[key] || []).map(a => a.BTTUUID));
            if (key in before) clean[key] = (clean[key] || []).filter(a => previousIDs.has(a.BTTUUID));
            else delete clean[key];
        }
        if (fingerprint(clean) !== fingerprint(before)) throw new Error('Existing button changed: '+button.uuid);
    }
    for (const id of [...plan.dependency_sources, ...plan.dependencies.map(d => d.uuid)]) {
        if (plan.snapshot[id] && fingerprint(get(id)) !== fingerprint(plan.snapshot[id])) {
            throw new Error('Existing named trigger changed: '+id);
        }
    }
    return 'Verified recovered button associations and named triggers; no actions executed.';
}

function applyRecoveredPlan(btt, plan, get, fingerprint) {
    validateActionPlan(plan);
    for (const [id, before] of Object.entries(plan.snapshot)) {
        if (fingerprint(get(id)) !== fingerprint(before)) throw new Error('BTT changed during backup: '+id);
    }
    let count = 0;
    for (const entity of plan.entities) {
        if (plan.snapshot[entity.uuid]) continue;
        if (entity.parent) btt.add_new_trigger(JSON.stringify(entity.node), {parent_uuid:entity.parent});
        else btt.add_new_trigger(JSON.stringify(entity.node));
        count++;
    }
    verifyRecoveredPlan(plan, get, fingerprint);
    return 'Created '+count+' missing action/dependency records individually; verified runtime associations.';
}

function run(argv) {
    function read(path) {
        const raw = $.NSString.stringWithContentsOfFileEncodingError(path,$.NSUTF8StringEncoding,null);
        if (!raw) throw new Error('Cannot read recovery input.');
        return ObjC.unwrap(raw);
    }
    const base = argv[1]+'/macbook/bettertouchtool/';
    const common = new Function(read(base+'btt_common.js')+'\nreturn BTTCommon;')();
    const lookup = new Function(read(base+'restore_media.js')+'\nreturn restoreGet;')();
    const btt = Application('/Applications/BetterTouchTool.app'), plan = common.readJSON(argv[2]);
    const get = id => lookup(btt,id);
    if (argv[0] === 'snapshot') return JSON.stringify(actionSnapshot(plan,get,common.fingerprint));
    if (argv[0] === 'apply') return applyRecoveredPlan(btt,plan,get,common.fingerprint);
    if (argv[0] === 'verify') return verifyRecoveredPlan(plan,get,common.fingerprint);
    throw new Error('Unknown action repair mode.');
}
