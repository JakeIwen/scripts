// Restore individual records, with the root disabled until save verification.
ObjC.import('Foundation');

function restoreGet(btt, uuid) {
    const raw = btt.get_trigger(uuid);
    if (!raw || raw === 'not found') return null;
    const parsed = JSON.parse(raw);
    if (parsed === null) return null;
    const list = Array.isArray(parsed) ? parsed : [parsed];
    if (list.some(x => !x || typeof x !== 'object' || (Object.keys(x).length && x.BTTUUID !== uuid))) {
        throw new Error('Unexpected BTT readback; refusing to assume the record is absent.');
    }
    const matches = list.filter(x => x.BTTUUID === uuid);
    if (matches.length > 1) throw new Error('Duplicate runtime UUID: '+uuid);
    return matches[0] || null;
}

function restoreStep(btt, plan, mode) {
    const root = plan.entities[0];
    if (!root || root.parent !== null || root.node.BTTTriggerType !== 767) throw new Error('Invalid recovery root.');
    if (mode === 'preflight' || mode === 'root') {
        const current = restoreGet(btt, root.uuid);
        if (plan.resume_origin) {
            if (!current || Number(current.BTTEnabled) !== 0 || Number(current.BTTTriggerType) !== 767 ||
                !(plan.already_saved || []).includes(root.uuid)) {
                throw new Error('Expected the previously verified disabled recovery root.');
            }
            const expected = plan.runtime_root_config || root.node.BTTMenuConfig || {};
            const actual = current.BTTMenuConfig || {};
            const differences = Object.keys(expected).filter(key =>
                !['BTTLastChangeUUID', 'BTTLastUpdatedAt'].includes(key) &&
                JSON.stringify(expected[key]) !== JSON.stringify(actual[key]));
            if (differences.length) {
                throw new Error('Runtime recovery root configuration changed at '+differences.slice(0, 6).join(', ')+
                    '; no changes made.');
            }
            return 'Reusing the verified disabled Media root; no duplicate created.';
        }
        if (current) throw new Error('Media still exists at runtime; no restoration attempted.');
        if (mode === 'preflight') return 'Runtime Media is absent.';
        const node = Object.assign({}, root.node, {BTTEnabled: 0, BTTEnabled2: 0});
        btt.add_new_trigger(JSON.stringify(node));
        return 'Created disabled Media root; checking its saved preset before adding children.';
    }
    const current = restoreGet(btt, root.uuid);
    if (!current || Number(current.BTTEnabled) !== 0) throw new Error('Expected the disabled recovery root.');
    if (mode === 'children') {
        let created = 0;
        for (const entity of plan.entities.slice(1)) {
            if (plan.resume_origin && (plan.already_saved || []).includes(entity.uuid)) {
                if (!restoreGet(btt, entity.uuid)) throw new Error('Previously saved record disappeared: '+entity.uuid);
                continue; // Python verified it; never replace it with backup data.
            }
            if (restoreGet(btt, entity.uuid)) throw new Error('Record appeared during recovery: '+entity.uuid);
            btt.add_new_trigger(JSON.stringify(entity.node), {parent_uuid: entity.parent});
            created++;
        }
        return 'Created '+created+' menu items/actions individually; checking saved records.';
    }
    if (mode === 'enable') {
        btt.update_trigger(root.uuid, {json: JSON.stringify({BTTEnabled: 1, BTTEnabled2: 1})});
        const enabled = restoreGet(btt, root.uuid);
        if (!enabled || Number(enabled.BTTEnabled === undefined ? 1 : enabled.BTTEnabled) !== 1) {
            throw new Error('Could not enable the verified restored menu.');
        }
        return 'Enabled the restored Media menu.';
    }
    throw new Error('Unknown restore step.');
}

function run(argv) {
    const raw = $.NSString.stringWithContentsOfFileEncodingError(argv[1], $.NSUTF8StringEncoding, null);
    if (!raw) throw new Error('Cannot read recovery plan.');
    return restoreStep(Application('/Applications/BetterTouchTool.app'), JSON.parse(ObjC.unwrap(raw)), argv[0]);
}
