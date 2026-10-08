// Generic BTT Guard runtime repair worker. Never executes configured actions.
ObjC.import('Foundation');

const REPAIR_COLLECTIONS = ['BTTMenuItems', 'BTTMenuItemActions', 'BTTAdditionalActions'];
const REPAIR_PAYLOAD_FIELDS = ['BTTAdditionalActionData', 'BTTNamedTriggerToTrigger',
    'BTTShellTaskActionScript', 'BTTShellTaskActionConfig', 'BTTTerminalCommand',
    'BTTShortcutToSend', 'BTTAppleScriptString', 'BTTTriggerName',
    'BTTLayoutIndependentActionChar', 'BTTDelayNextActionBy'];

function repairValidatePlan(plan) {
    if (!plan || Number(plan.schema_version) !== 1 || typeof plan.checkpoint_id !== 'string' ||
        !Array.isArray(plan.operations) || !Array.isArray(plan.conflicts)) {
        throw new Error('Invalid BTT Guard repair plan.');
    }
    if (plan.conflicts.length) throw new Error('Repair plan has unresolved conflicts; no changes made.');
    const ids = new Set(), operationIDs = new Set(plan.operations.map(operation => operation.uuid));
    for (const operation of plan.operations) {
        const node = operation.definition;
        if (!operation.uuid || ids.has(operation.uuid) || !['create', 'reattach'].includes(operation.mode) ||
            !node || typeof node !== 'object' || node.BTTUUID !== operation.uuid ||
            !(operation.parent === null || typeof operation.parent === 'string')) {
            throw new Error('Invalid or duplicate BTT Guard repair operation.');
        }
        if (operation.mode === 'reattach' && operation.parent === null)
            throw new Error('Invalid root reattachment operation.');
        if (operation.parent !== null && node.BTTTriggerParentUUID !== operation.parent)
            throw new Error('Repair definition has the wrong explicit parent.');
        if (operation.parent === null && node.BTTTriggerParentUUID !== undefined)
            throw new Error('Repair root definition unexpectedly has a parent.');
        if (Number(node.BTTTriggerType) === -1 && !node.BTTTriggerClass)
            throw new Error('Repair action definition lacks its inherited class.');
        if (REPAIR_COLLECTIONS.some(key => key in node))
            throw new Error('Repair definitions must be flat records.');
        ids.add(operation.uuid);
    }
    const available = new Set();
    for (const operation of plan.operations) {
        if (operation.parent !== null && operationIDs.has(operation.parent) && !available.has(operation.parent))
            throw new Error('Repair operations are not parent-first.');
        available.add(operation.uuid);
    }
}

function repairSnapshot(plan, get) {
    repairValidatePlan(plan);
    const snapshot = {}, queue = [];
    for (const operation of plan.operations) {
        queue.push(operation.uuid);
        if (operation.parent !== null) queue.push(operation.parent);
    }
    while (queue.length) {
        const uuid = queue.shift();
        if (!uuid || Object.prototype.hasOwnProperty.call(snapshot, uuid)) continue;
        const value = get(uuid);
        snapshot[uuid] = value;
        if (value && typeof value.BTTTriggerParentUUID === 'string') queue.push(value.BTTTriggerParentUUID);
    }
    const plannedCreates = new Set(plan.operations.filter(x => x.mode === 'create').map(x => x.uuid));
    for (const operation of plan.operations) {
        const value = snapshot[operation.uuid];
        if (operation.mode === 'create' && value !== null)
            throw new Error('Create target already exists at runtime; no changes made.');
        if (operation.mode === 'reattach' && (!value || value.BTTTriggerParentUUID))
            throw new Error('Reattach target is missing or already owned; no changes made.');
        if (operation.parent !== null && !snapshot[operation.parent] && !plannedCreates.has(operation.parent))
            throw new Error('Repair parent is missing at runtime; no changes made.');
    }
    return snapshot;
}

function repairPreflight(plan, get, fingerprint) {
    repairValidatePlan(plan);
    const snapshot = plan.runtime_snapshot;
    if (!snapshot || typeof snapshot !== 'object' || Array.isArray(snapshot))
        throw new Error('Verified runtime snapshot is required; no changes made.');
    for (const operation of plan.operations) {
        if (!Object.prototype.hasOwnProperty.call(snapshot, operation.uuid) ||
            (operation.parent !== null && !Object.prototype.hasOwnProperty.call(snapshot, operation.parent))) {
            throw new Error('Runtime snapshot is incomplete; no changes made.');
        }
    }
    for (const [uuid, before] of Object.entries(snapshot)) {
        if (fingerprint(get(uuid)) !== fingerprint(before))
            throw new Error('BTT changed after the verified snapshot: '+uuid);
    }
    const available = new Set(Object.entries(snapshot).filter(([, value]) => value).map(([uuid]) => uuid));
    for (const operation of plan.operations) {
        const before = snapshot[operation.uuid];
        if (operation.mode === 'create' && before !== null)
            throw new Error('Create target was present in the verified snapshot.');
        if (operation.mode === 'reattach' && (!before || before.BTTTriggerParentUUID))
            throw new Error('Reattach target was not detached in the verified snapshot.');
        if (operation.parent !== null && !available.has(operation.parent))
            throw new Error('Repair parent was not available in parent-first order.');
        available.add(operation.uuid);
    }
    return snapshot;
}

function repairStripOwned(value, owned, reference) {
    if (Array.isArray(value)) {
        const filtered = value.filter(item => !item || !owned.has(item.BTTUUID));
        const references = Array.isArray(reference) ? reference : [];
        return filtered.map((item, index) => {
            const match = item && item.BTTUUID
                ? references.find(candidate => candidate && candidate.BTTUUID === item.BTTUUID)
                : references[index];
            return repairStripOwned(item, owned, match);
        });
    }
    if (!value || typeof value !== 'object') return value;
    const result = {}, referenceObject = reference && typeof reference === 'object' ? reference : {};
    for (const [key, item] of Object.entries(value)) {
        const cleaned = repairStripOwned(REPAIR_COLLECTIONS.includes(key) && !Array.isArray(item) ? [] : item,
                                         owned, referenceObject[key]);
        if (REPAIR_COLLECTIONS.includes(key) && !(key in referenceObject) &&
            Array.isArray(cleaned) && cleaned.length === 0) continue;
        result[key] = cleaned;
    }
    return result;
}

function repairNumber(value, key, fallback) {
    return Number(value[key] === undefined ? fallback : value[key]);
}

function repairPayload(value, key) {
    const payload = value[key];
    if (key === 'BTTDelayNextActionBy') return Number(payload);
    if (key !== 'BTTAdditionalActionData' || typeof payload !== 'string') return payload;
    try { return JSON.parse(payload); }
    catch (_) { throw new Error('Created record has invalid BTTAdditionalActionData JSON.'); }
}

function repairActionTypeMatches(actual, definition) {
    const found = actual.BTTPredefinedActionType === undefined
        ? null : Number(actual.BTTPredefinedActionType);
    const wanted = definition.BTTPredefinedActionType === undefined
        ? null : Number(definition.BTTPredefinedActionType);
    if ([null, -1, 366].includes(wanted)) return [null, -1, 366].includes(found);
    return found === wanted;
}

function repairHasChild(parent, uuid) {
    return !!parent && REPAIR_COLLECTIONS.some(key =>
        Array.isArray(parent[key]) && parent[key].some(child => child && child.BTTUUID === uuid));
}

function repairParentMatches(actual, operation, get) {
    const declared = actual && actual.BTTTriggerParentUUID;
    if (operation.parent === null) return declared === undefined || declared === null || declared === '';
    if (declared !== undefined && declared !== null && declared !== '') return declared === operation.parent;
    return repairHasChild(get(operation.parent), operation.uuid);
}

function repairCheckCreated(actual, operation, get, fingerprint) {
    if (!actual || actual.BTTUUID !== operation.uuid)
        throw new Error('Created record is missing: '+operation.uuid);
    const definition = operation.definition;
    const expectedType = Number(definition.BTTTriggerType);
    if (actual.BTTTriggerType === undefined ? expectedType !== -1
        : Number(actual.BTTTriggerType) !== expectedType)
        throw new Error('Created record differs at BTTTriggerType: '+operation.uuid);
    for (const [key, fallback] of [['BTTEnabled',1], ['BTTActionCategory',0], ['BTTOrder',0]]) {
        if (repairNumber(actual,key,fallback) !== repairNumber(definition,key,fallback))
            throw new Error('Created record differs at '+key+': '+operation.uuid);
    }
    if (!repairActionTypeMatches(actual, definition))
        throw new Error('Created record differs at BTTPredefinedActionType: '+operation.uuid);
    for (const key of REPAIR_PAYLOAD_FIELDS) {
        if (definition[key] !== undefined &&
            fingerprint(repairPayload(actual,key)) !== fingerprint(repairPayload(definition,key)))
            throw new Error('Created record differs at '+key+': '+operation.uuid);
    }
    if (!repairParentMatches(actual, operation, get))
        throw new Error('Created record has the wrong parent: '+operation.uuid);
}

function repairVerify(plan, get, fingerprint) {
    repairValidatePlan(plan);
    const snapshot = plan.runtime_snapshot;
    if (!snapshot || typeof snapshot !== 'object') throw new Error('Verified runtime snapshot is required.');
    const owned = new Set(plan.operations.map(operation => operation.uuid));
    for (const operation of plan.operations) {
        const actual = get(operation.uuid);
        if (operation.mode === 'create') {
            repairCheckCreated(actual, operation, get, fingerprint);
            continue;
        }
        const expected = JSON.parse(JSON.stringify(snapshot[operation.uuid]));
        expected.BTTTriggerParentUUID = operation.parent;
        if (Number(operation.definition.BTTTriggerType) === -1)
            expected.BTTOrder = Number(operation.definition.BTTOrder || 0);
        if (!repairParentMatches(actual, operation, get))
            throw new Error('Reattached record has the wrong parent: '+operation.uuid);
        const actualWithoutParent = JSON.parse(JSON.stringify(actual));
        const expectedWithoutParent = JSON.parse(JSON.stringify(expected));
        delete actualWithoutParent.BTTTriggerParentUUID;
        delete expectedWithoutParent.BTTTriggerParentUUID;
        if (fingerprint(repairStripOwned(actualWithoutParent, owned, expectedWithoutParent)) !==
            fingerprint(repairStripOwned(expectedWithoutParent, owned, expectedWithoutParent))) {
            throw new Error('Reattached record changed beyond its parent: '+operation.uuid);
        }
    }
    for (const [uuid, before] of Object.entries(snapshot)) {
        if (!before || owned.has(uuid)) continue;
        const after = get(uuid);
        if (fingerprint(repairStripOwned(after, owned, before)) !==
            fingerprint(repairStripOwned(before, owned, before))) {
            throw new Error('Protected runtime record changed: '+uuid);
        }
    }
    return 'Verified BTT Guard repair associations and protected runtime records; no actions executed.';
}

function repairApply(btt, plan, get, fingerprint) {
    const snapshot = repairPreflight(plan, get, fingerprint);
    let created = 0, reattached = 0;
    for (const operation of plan.operations) {
        if (fingerprint(get(operation.uuid)) !== fingerprint(snapshot[operation.uuid]))
            throw new Error('Repair target changed immediately before write: '+operation.uuid);
        if (operation.mode === 'create') {
            if (operation.parent === null) btt.add_new_trigger(JSON.stringify(operation.definition));
            else btt.add_new_trigger(JSON.stringify(operation.definition), {parent_uuid: operation.parent});
            created++;
        } else {
            const patch = {BTTTriggerParentUUID: operation.parent};
            // Action order is relative to its parent/category collection, so an
            // orphan reattachment restores that relationship metadata too.
            if (Number(operation.definition.BTTTriggerType) === -1)
                patch.BTTOrder = Number(operation.definition.BTTOrder || 0);
            btt.update_trigger(operation.uuid, {trigger_parent_uuid: operation.parent,
                json: JSON.stringify(patch)});
            reattached++;
        }
    }
    repairVerify(plan, get, fingerprint);
    return 'Created '+created+' records and reattached '+reattached+
        '; verified runtime state without executing actions.';
}

function repairLibrary(repo, file, expression) {
    const path = repo+'/macbook/bettertouchtool/'+file;
    const source = $.NSString.stringWithContentsOfFileEncodingError(path, $.NSUTF8StringEncoding, null);
    if (!source) throw new Error('Cannot load BTT Guard helper '+file);
    return new Function(ObjC.unwrap(source)+'\nreturn '+expression+';')();
}

function run(argv) {
    const mode = argv[0], repo = argv[1];
    const common = repairLibrary(repo, 'btt_common.js', 'BTTCommon');
    const getHelper = repairLibrary(repo, 'restore_media.js', 'restoreGet');
    const plan = common.readJSON(argv[2]);
    const btt = Application('/Applications/BetterTouchTool.app');
    const get = uuid => getHelper(btt, uuid);
    if (mode === 'snapshot') return JSON.stringify(repairSnapshot(plan, get));
    if (mode === 'apply') return repairApply(btt, plan, get, common.fingerprint);
    if (mode === 'verify') return repairVerify(plan, get, common.fingerprint);
    throw new Error('Unknown BTT Guard repair mode.');
}
