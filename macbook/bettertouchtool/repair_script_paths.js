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
        btt.update_trigger(target.uuid, {json: JSON.stringify({[target.field]: target.after})});
        count++;
    }
    for (const target of plan.targets) {
        const expected = Object.assign({}, originals.get(target.uuid), {[target.field]: target.after});
        if (fingerprint(pathGet(btt, target.uuid)) !== fingerprint(expected)) {
            throw new Error('Action readback differs beyond its path update: '+target.uuid);
        }
    }
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
    if (argv[0] !== 'apply') throw new Error('Unknown path-repair mode.');
    const common = new Function(read(argv[1]+'/macbook/bettertouchtool/btt_common.js')+'\nreturn BTTCommon;')();
    return pathApply(btt, plan, common.fingerprint);
}
