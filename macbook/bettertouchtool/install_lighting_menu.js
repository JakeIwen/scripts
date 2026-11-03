// Guarded installer for Media > Lighting (the 💡 submenu); writes only its own UUIDs.
ObjC.import('Foundation');

// enabled, pick and refreshMedia repeat install_tools_menu_item.js's private helpers.
function loadCommon(path) {
    const source = $.NSString.stringWithContentsOfFileEncodingError(path, $.NSUTF8StringEncoding, null);
    if (!source) throw new Error('Cannot read ' + path);
    eval(ObjC.unwrap(source));
    return BTTCommon;
}
function enabled(item) {
    return ['BTTEnabled', 'BTTEnabled2'].every(key => item[key] === undefined || Number(item[key]) === 1);
}
function pick(items, id) {
    const matches = (items || []).filter(item => item.BTTUUID === id);
    if (matches.length > 1) throw new Error('Duplicate UUID: ' + id);
    return matches[0];
}
function readMedia(btt, id) {
    const value = JSON.parse(btt.get_trigger(id));
    const media = pick(Array.isArray(value) ? value : [value], id);
    if (!media || Number(media.BTTTriggerType) !== 767 || !enabled(media) || !Array.isArray(media.BTTMenuItems)) {
        throw new Error('Cannot read complete enabled Media menu; no changes made.');
    }
    return media;
}
function parentsOf(media, id) {
    const parents = [];
    (function walk(node) {
        for (const child of node.BTTMenuItems || []) {
            if (child.BTTUUID === id) parents.push(node.BTTUUID);
            walk(child);
        }
    })(media);
    return parents;
}
const CONFIG_KEYS = ['BTTMenuElementIdentifier', 'BTTMenuElementTooltip', 'BTTMenuItemText', 'BTTMenuItemMinWidth',
    'BTTMenuItemMaxWidth', 'BTTMenuItemMinHeight', 'BTTMenuItemMaxHeight'];
function fontSize(config) {
    return (String((config || {}).BTTMenuAttributedText || '').match(/\\fs(\d+)/) || [])[1];
}
// BTT may give a new action its own UUID, so actions match by what they run.
function actionMatches(actual, wanted) {
    return actual && enabled(actual) &&
        Number(actual.BTTPredefinedActionType) === wanted.BTTPredefinedActionType &&
        actual.BTTShellTaskActionScript === wanted.BTTShellTaskActionScript &&
        actual.BTTShellTaskActionConfig === wanted.BTTShellTaskActionConfig;
}
function styleMatches(actual, desired, common) {
    if (!actual || !enabled(actual) || Number(actual.BTTTriggerType) !== desired.BTTTriggerType ||
        actual.BTTMenuName !== desired.BTTMenuName || Number(actual.BTTOrder || 0) !== desired.BTTOrder) {
        return false;
    }
    const config = actual.BTTMenuConfig || {};
    return CONFIG_KEYS.every(key => common.fingerprint(config[key]) === common.fingerprint(desired.BTTMenuConfig[key])) &&
        fontSize(config) === fontSize(desired.BTTMenuConfig);
}
// unlinked lists saved actions the launcher found without the Master preset link.
function actionsMatch(actual, desired, unlinked) {
    const actions = (actual && actual.BTTMenuItemActions) || [];
    const wanted = desired.BTTMenuItemActions || [];
    return actions.length === wanted.length && !actions.some(action => unlinked.includes(action.BTTUUID)) &&
        wanted.every((action, index) => actionMatches(actions[index], action));
}
function nodeMatches(actual, desired, common, unlinked) {
    return styleMatches(actual, desired, common) && actionsMatch(actual, desired, unlinked);
}
// Each entry keeps its saved position once installed, so a hand reorder in BTT survives a rerun.
function plan(media, payload, common) {
    const id = payload.submenu.BTTUUID;
    const owned = [id, ...payload.children.map(child => child.BTTUUID)];
    const identifier = payload.submenu.BTTMenuConfig.BTTMenuElementIdentifier;
    if (media.BTTMenuItems.some(item => item.BTTUUID !== id &&
        (item.BTTMenuConfig || {}).BTTMenuElementIdentifier === identifier)) {
        throw new Error('Another Media item is already named ' + identifier + '; no changes made.');
    }
    const expected = {[id]: media.BTTUUID};
    payload.children.forEach(child => { expected[child.BTTUUID] = id; });
    for (const uuid of owned) {
        const parents = parentsOf(media, uuid);
        if (parents.length > 1 || (parents.length && parents[0] !== expected[uuid])) {
            throw new Error('Lighting item ' + uuid + ' was moved elsewhere in Media; no changes made.');
        }
    }
    const previous = pick(media.BTTMenuItems, id);
    if (previous && (Number(previous.BTTTriggerType) !== 774 || !Array.isArray(previous.BTTMenuItems))) {
        throw new Error('Lighting UUID holds an unreadable or non-submenu item; no changes made.');
    }
    const submenu = common.clone(payload.submenu);
    submenu.BTTOrder = previous ? Number(previous.BTTOrder || 0) :
        Math.max(-1, ...media.BTTMenuItems.map(item => Number(item.BTTOrder || 0))) + 1;
    const children = payload.children.map(child => {
        const existing = previous && pick(previous.BTTMenuItems, child.BTTUUID);
        if (existing && Number(existing.BTTTriggerType) !== child.BTTTriggerType) {
            throw new Error('Lighting item ' + child.BTTUUID + ' has a foreign type; no changes made.');
        }
        const node = Object.assign(common.clone(child), {BTTTriggerParentUUID: id});
        if (existing) node.BTTOrder = Number(existing.BTTOrder || 0);
        return {node, previous: existing};
    });
    submenu.BTTTriggerParentUUID = media.BTTUUID;
    return {submenu: {node: submenu, previous}, children, owned};
}
function pending(steps, common, unlinked) {
    return [steps.submenu, ...steps.children].filter(step => !nodeMatches(step.previous, step.node, common, unlinked));
}
// A full update_trigger saves an item's new actions without their preset link (and could
// reimport a submenu's children), so existing items are restyled in place and their
// actions replaced individually.
function write(btt, step, common, unlinked) {
    const id = step.node.BTTUUID;
    if (!step.previous) {
        btt.add_new_trigger(JSON.stringify(step.node), {parent_uuid: step.node.BTTTriggerParentUUID});
        return;
    }
    if (!styleMatches(step.previous, step.node, common)) {
        btt.update_menu_item(id, {json: JSON.stringify(step.node.BTTMenuConfig), persist: true});
    }
    if (!actionsMatch(step.previous, step.node, unlinked)) {
        for (const action of step.node.BTTMenuItemActions || []) {
            btt.add_new_trigger(JSON.stringify(Object.assign({}, action, {BTTTriggerType: -1})), {parent_uuid: id});
        }
        for (const action of step.previous.BTTMenuItemActions || []) btt.delete_trigger(action.BTTUUID);
    }
}
function foreign(media, steps) {
    const id = steps.submenu.node.BTTUUID;
    const submenu = pick(media.BTTMenuItems, id);
    return {media: media.BTTMenuItems.filter(item => item.BTTUUID !== id),
        submenu: ((submenu && submenu.BTTMenuItems) || []).filter(item => !steps.owned.includes(item.BTTUUID))};
}
function verify(btt, before, steps, common, unlinked, backupPath) {
    const after = readMedia(btt, before.BTTUUID);
    const submenu = pick(after.BTTMenuItems, steps.submenu.node.BTTUUID);
    if (!nodeMatches(submenu, steps.submenu.node, common, unlinked) || steps.children.some(step =>
        !nodeMatches(pick(submenu.BTTMenuItems, step.node.BTTUUID), step.node, common, unlinked))) {
        throw new Error('Lighting menu verification failed. Backup: ' + backupPath);
    }
    if (common.fingerprint(foreign(after, steps)) !== common.fingerprint(foreign(before, steps))) {
        throw new Error('Unexpected change outside the Lighting items. Backup: ' + backupPath);
    }
    return after;
}
// The receipt's saved_plan is checked by btt_common.disk_errors before Guard accepts the edits.
function receipt(message, media, steps, touched) {
    const submenu = pick(media.BTTMenuItems, steps.submenu.node.BTTUUID);
    const entities = [];
    if (submenu) {
        entities.push({uuid: submenu.BTTUUID, parent: media.BTTUUID,
            node: Object.assign({}, submenu, {BTTMenuItems: undefined})});
        for (const step of steps.children) {
            const child = pick(submenu.BTTMenuItems, step.node.BTTUUID);
            if (!child) continue;
            entities.push({uuid: child.BTTUUID, parent: submenu.BTTUUID, node: child});
            for (const action of child.BTTMenuItemActions || []) {
                entities.push({uuid: action.BTTUUID, parent: child.BTTUUID,
                    node: Object.assign({}, action, {BTTTriggerType: -1})});
            }
        }
    }
    return JSON.stringify({message, wrote: touched.length > 0, touched_uuids: [...new Set(touched)],
        saved_plan: {entities, configChanges: [], media: {BTTMenuItems: []}}});
}
function refreshMedia(btt, id) {
    btt.trigger_action(JSON.stringify({BTTPredefinedActionType: 387, BTTAdditionalActionData: {
        BTTMenuActionMenuID: id, BTTMenuActionReleaseFromMemory: 1,
        BTTMenuActionTriggerHoveredOnHide: 0, BTTMenuActionCloseSubmenuOnHide: 1,
    }}));
    btt.trigger_action(JSON.stringify({BTTPredefinedActionType: 386,
        BTTAdditionalActionData: {BTTMenuActionMenuID: id}}));
}
function run(argv) {
    const [payloadPath, commonPath, backupDirectory, mode, refreshFlag] = argv;
    if (!['inspect', 'apply'].includes(mode)) throw new Error('Explicit inspect/apply mode required.');
    const common = loadCommon(commonPath);
    const payload = common.readJSON(payloadPath);
    const btt = Application('/Applications/BetterTouchTool.app');
    const before = readMedia(btt, payload.media_uuid);
    const steps = plan(before, payload, common);
    const unlinked = payload.unlinked_actions || [];
    const todo = pending(steps, common, unlinked);
    if (mode === 'inspect') {
        return JSON.stringify({mode, installed: todo.length === 0, order: steps.submenu.node.BTTOrder,
            pending: todo.map(step => ({uuid: step.node.BTTUUID, name: step.node.BTTMenuName,
                change: step.previous ? 'update' : 'add'}))}, null, 2);
    }
    const refresh = refreshFlag === 'refresh';
    if (!todo.length) {
        if (refresh) refreshMedia(btt, before.BTTUUID);
        return receipt('Already configured: Media > Lighting.', before, steps, []);
    }
    const backupPath = common.backup({media: before}, backupDirectory + '/btt-lighting-menu-backup-' +
        ObjC.unwrap($.NSUUID.UUID.UUIDString), {
        create: 'Cannot create private backup; no changes made.',
        verify: 'Backup verification failed; no changes made.',
    });
    if (common.fingerprint(readMedia(btt, before.BTTUUID)) !== common.fingerprint(before)) {
        throw new Error('Media changed during backup; no changes made.');
    }
    for (const step of todo) write(btt, step, common, unlinked);
    const after = verify(btt, before, steps, common, unlinked, backupPath);
    const submenu = pick(after.BTTMenuItems, steps.submenu.node.BTTUUID);
    const touched = [];
    for (const step of todo) {
        touched.push(step.node.BTTUUID);
        // A replaced action list makes both the old and the saved action UUIDs edits.
        if (Number(step.node.BTTTriggerType) !== 774) {
            for (const item of [step.previous, pick(submenu.BTTMenuItems, step.node.BTTUUID)]) {
                for (const action of (item && item.BTTMenuItemActions) || []) touched.push(action.BTTUUID);
            }
        }
    }
    if (refresh) refreshMedia(btt, before.BTTUUID);
    return receipt('Verified configuration: Media > Lighting. Backup: ' + backupPath +
        (refresh ? ' Media menu refreshed.' : ''), after, steps, touched);
}
