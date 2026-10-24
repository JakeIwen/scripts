// Patch individual action command fields only; never replace menu trees.
ObjC.import('Foundation');

function pathGet(btt, uuid) {
    const raw = JSON.parse(btt.get_trigger(uuid));
    const matches = (Array.isArray(raw) ? raw : [raw]).filter(x => x && x.BTTUUID === uuid);
    if (matches.length !== 1) throw new Error('Cannot uniquely export action '+uuid);
    return matches[0];
}

function pathSnapshot(btt, plan) {
    return plan.targets.map(target => {
        const trigger = pathGet(btt, target.uuid);
        if (Number(trigger.BTTPredefinedActionType) !== target.action ||
            ![target.before, target.after].includes(trigger[target.field])) {
            throw new Error('Action changed since saved-path inspection: '+target.uuid);
        }
        return {uuid: target.uuid, trigger};
    });
}

function pathEnabledPatch(original) {
    const patch = {};
    for (const key of ['BTTEnabled', 'BTTEnabled2']) {
        if (Object.prototype.hasOwnProperty.call(original, key)) patch[key] = original[key];
    }
    return patch;
}

function pathExpected(plan, target) {
    const matches = plan.snapshot.filter(entry => entry.uuid === target.uuid);
    if (matches.length !== 1) throw new Error('Incomplete action backup: '+target.uuid);
    return Object.assign({}, matches[0].trigger, {[target.field]: target.after});
}

function pathVerify(btt, plan, fingerprint) {
    for (const target of plan.targets) {
        const expected = pathExpected(plan, target), actual = pathGet(btt, target.uuid);
        if (fingerprint(actual) !== fingerprint(expected)) {
            const keys = [...new Set(Object.keys(expected).concat(Object.keys(actual)))];
            const changed = keys.filter(key => fingerprint({[key]:actual[key]}) !==
                fingerprint({[key]:expected[key]}));
            throw new Error('Action readback differs beyond its path update: '+target.uuid+
                ' (fields: '+changed.join(', ')+')');
        }
    }
}

function pathRestoreEnabled(btt, plan, fingerprint) {
    const repairs = [], observed = [];
    // Compare every target before writing. Only omission-induced 0 -> 1 drift
    // is recoverable; custom edits and any other mismatch require review.
    for (const target of plan.targets) {
        const expected = pathExpected(plan, target), actual = pathGet(btt, target.uuid);
        observed.push({uuid:target.uuid, trigger:actual});
        const flags = pathEnabledPatch(expected), repaired = Object.assign({}, actual, flags);
        if (fingerprint(repaired) !== fingerprint(expected))
            throw new Error('Non-enablement drift; no changes made: '+target.uuid);
        const changed = Object.keys(flags).filter(key => actual[key] !== flags[key]);
        if (changed.some(key => Number(flags[key]) !== 0 || Number(actual[key]) !== 1))
            throw new Error('Unexpected enabled state; no changes made: '+target.uuid);
        if (changed.length) repairs.push({uuid:target.uuid, actual, flags});
    }
    // Recheck the full set before the first write, including unchanged targets.
    for (const entry of observed) {
        if (fingerprint(pathGet(btt, entry.uuid)) !== fingerprint(entry.trigger))
            throw new Error('Action changed during recovery; no changes made: '+entry.uuid);
    }
    for (const repair of repairs)
        btt.update_trigger(repair.uuid, {json:JSON.stringify(repair.flags)});
    pathVerify(btt, plan, fingerprint);
    return 'Restored '+repairs.length+' disabled states; all command edits verified. No actions executed.';
}

function pathApply(btt, plan, fingerprint) {
    if (plan.snapshot.length !== plan.targets.length) throw new Error('Incomplete action backup.');
    const originals = new Map(plan.snapshot.map(x => [x.uuid, x.trigger]));
    // Recheck ALL actions before the first write; no partially applied stale plan.
    for (const target of plan.targets) {
        const original = originals.get(target.uuid);
        if (!original || fingerprint(pathGet(btt, target.uuid)) !== fingerprint(original)) {
            throw new Error('Action changed during backup: '+target.uuid+'; no changes made.');
        }
    }
    let count = 0;
    for (const target of plan.targets) {
        if (originals.get(target.uuid)[target.field] === target.after) continue;
        // BTT can default an omitted enabled flag to 1 even for field-only updates.
        const patch = Object.assign(pathEnabledPatch(originals.get(target.uuid)), {[target.field]: target.after});
        btt.update_trigger(target.uuid, {json: JSON.stringify(patch)});
        count++;
    }
    pathVerify(btt, plan, fingerprint);
    return 'Updated '+count+' action command fields; verified runtime readback. No actions executed.';
}

function run(argv) {
    function read(path) {
        const result = $.NSString.stringWithContentsOfFileEncodingError(path, $.NSUTF8StringEncoding, null);
        if (!result) throw new Error('Cannot read helper input.');
        return ObjC.unwrap(result);
    }
    const btt = Application('/Applications/BetterTouchTool.app'), plan = JSON.parse(read(argv[2]));
    if (argv[0] === 'snapshot') return JSON.stringify(pathSnapshot(btt, plan));
    const common = new Function(read(argv[1]+'/macbook/bettertouchtool/btt_common.js')+'\nreturn BTTCommon;')();
    if (argv[0] === 'verify') { pathVerify(btt, plan, common.fingerprint); return 'All runtime command edits verified.'; }
    if (argv[0] === 'restore-enabled') return pathRestoreEnabled(btt, plan, common.fingerprint);
    if (argv[0] !== 'apply') throw new Error('Unknown path-repair mode.');
    return pathApply(btt, plan, common.fingerprint);
}
