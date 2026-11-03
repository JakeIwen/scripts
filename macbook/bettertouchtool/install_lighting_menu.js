// Guarded installer for Media > Lighting (the 💡 submenu); writes only its own UUIDs.
ObjC.import('Foundation');

// loadCommon, enabled, pick and refreshMedia repeat install_tools_menu_item.js's private helpers.
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
// Every node under Media by UUID, with its parent and how often the UUID occurs.
function locate(media) {
    const index = new Map();
    (function walk(node) {
        for (const child of node.BTTMenuItems || []) {
            const seen = index.get(child.BTTUUID);
            index.set(child.BTTUUID, {node: child, parent: node.BTTUUID, count: seen ? seen.count + 1 : 1});
            walk(child);
        }
    })(media);
    return index;
}
const CONFIG_KEYS = ['BTTMenuElementIdentifier', 'BTTMenuElementTooltip', 'BTTMenuItemText', 'BTTMenuItemMinWidth',
    'BTTMenuItemMaxWidth', 'BTTMenuItemMinHeight', 'BTTMenuItemMaxHeight'];
function fontSize(config) {
    return (String(config.BTTMenuAttributedText || '').match(/\\fs(\d+)/) || [])[1];
}
function configMatches(actual, desired, common) {
    const config = actual.BTTMenuConfig || {};
    return CONFIG_KEYS.every(key => common.fingerprint(config[key]) === common.fingerprint(desired.BTTMenuConfig[key])) &&
        fontSize(config) === fontSize(desired.BTTMenuConfig);
}
// BTT may give a new action its own UUID, so actions match by what they run.
function actionsMatch(actual, desired) {
    const actions = actual.BTTMenuItemActions || [];
    const wanted = desired.BTTMenuItemActions || [];
    return actions.length === wanted.length && wanted.every((action, index) => enabled(actions[index]) &&
        Number(actions[index].BTTPredefinedActionType) === action.BTTPredefinedActionType &&
        actions[index].BTTShellTaskActionScript === action.BTTShellTaskActionScript &&
        actions[index].BTTShellTaskActionConfig === action.BTTShellTaskActionConfig);
}
// The whole saved shape, checked after writing.
function savedAs(index, desired, common) {
    const found = index.get(desired.BTTUUID);
    return Boolean(found) && found.count === 1 && found.parent === desired.BTTTriggerParentUUID &&
        enabled(found.node) && Number(found.node.BTTTriggerType) === desired.BTTTriggerType &&
        found.node.BTTMenuName === desired.BTTMenuName && Number(found.node.BTTOrder || 0) === desired.BTTOrder &&
        configMatches(found.node, desired, common) && actionsMatch(found.node, desired);
}
// One step per owned item, the submenu first. Items that were moved, retyped or disabled
// are refused here, so a step only ever adds an item or repairs its label or actions.
// Installed items keep their saved position, so a hand reorder in BTT survives a rerun.
function plan(media, payload, common) {
    const submenuID = payload.submenu.BTTUUID;
    const identifier = payload.submenu.BTTMenuConfig.BTTMenuElementIdentifier;
    if (media.BTTMenuItems.some(item => item.BTTUUID !== submenuID &&
        (item.BTTMenuConfig || {}).BTTMenuElementIdentifier === identifier)) {
        throw new Error('Another Media item is already named ' + identifier + '; no changes made.');
    }
    const index = locate(media);
    const unlinked = payload.unlinked_actions || [];
    const nextOrder = Math.max(-1, ...media.BTTMenuItems.map(item => Number(item.BTTOrder || 0))) + 1;
    const wanted = [Object.assign({}, payload.submenu, {BTTOrder: nextOrder, BTTTriggerParentUUID: media.BTTUUID}),
        ...payload.children.map(child => Object.assign({}, child, {BTTTriggerParentUUID: submenuID}))];
    return wanted.map(desired => {
        const node = common.clone(desired);
        const found = index.get(node.BTTUUID);
        if (!found) return {node, add: true};
        const previous = found.node;
        if (found.count > 1 || found.parent !== node.BTTTriggerParentUUID) {
            throw new Error('Lighting item ' + node.BTTUUID + ' was moved elsewhere in Media; no changes made.');
        }
        if (Number(previous.BTTTriggerType) !== node.BTTTriggerType || !enabled(previous)) {
            throw new Error('Lighting item ' + node.BTTUUID + ' is disabled or has another type; ' +
                'enable or delete it in BTT. No changes made.');
        }
        node.BTTOrder = Number(previous.BTTOrder || 0);
        return {node, previous, config: !configMatches(previous, node, common),
            actions: !actionsMatch(previous, node) ||
                (previous.BTTMenuItemActions || []).some(action => unlinked.includes(action.BTTUUID))};
    });
}
// A full update_trigger saves an item's new actions without their preset link, so an
// installed item is restyled with update_menu_item and its actions replaced one by one.
function write(btt, step) {
    const id = step.node.BTTUUID;
    if (step.add) btt.add_new_trigger(JSON.stringify(step.node), {parent_uuid: step.node.BTTTriggerParentUUID});
    if (step.config) btt.update_menu_item(id, {json: JSON.stringify(step.node.BTTMenuConfig), persist: true});
    if (step.actions) {
        for (const action of step.node.BTTMenuItemActions || []) {
            btt.add_new_trigger(JSON.stringify(Object.assign({}, action, {BTTTriggerType: -1})), {parent_uuid: id});
        }
        for (const action of step.previous.BTTMenuItemActions || []) btt.delete_trigger(action.BTTUUID);
    }
}
// Everything in Media this installer doesn't own: other top-level items, and items added by
// hand to the submenu (steps[0]).
function foreign(media, steps) {
    const submenuID = steps[0].node.BTTUUID;
    const owned = steps.map(step => step.node.BTTUUID);
    const submenu = pick(media.BTTMenuItems, submenuID);
    return {media: media.BTTMenuItems.filter(item => item.BTTUUID !== submenuID),
        submenu: ((submenu && submenu.BTTMenuItems) || []).filter(item => !owned.includes(item.BTTUUID))};
}
function verify(btt, before, steps, common, backupPath) {
    const after = readMedia(btt, before.BTTUUID);
    const index = locate(after);
    if (!steps.every(step => savedAs(index, step.node, common))) {
        throw new Error('Lighting menu verification failed. Backup: ' + backupPath);
    }
    if (common.fingerprint(foreign(after, steps)) !== common.fingerprint(foreign(before, steps))) {
        throw new Error('Unexpected change outside the Lighting items. Backup: ' + backupPath);
    }
    return index;
}
// saved_plan is checked by btt_common.disk_errors before Guard accepts the edits;
// touched_uuids lists every record this run wrote, old and new actions included.
function receipt(message, index, steps, written) {
    const actionsOf = node => (node && node.BTTMenuItemActions) || [];
    const entities = [];
    for (const step of steps) {
        const found = index.get(step.node.BTTUUID);
        if (!found) continue;
        entities.push({uuid: step.node.BTTUUID, parent: found.parent,
            node: Object.assign({}, found.node, {BTTMenuItems: undefined})});
        for (const action of actionsOf(found.node)) {
            entities.push({uuid: action.BTTUUID, parent: step.node.BTTUUID,
                node: Object.assign({}, action, {BTTTriggerType: -1})});
        }
    }
    const touched = written.flatMap(step => [step.node.BTTUUID, ...(step.add || step.actions ?
        [...actionsOf(step.previous), ...actionsOf(index.get(step.node.BTTUUID).node)].map(action => action.BTTUUID) : [])]);
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
function describe(step) {
    return step.add ? 'add' : [step.config && 'label', step.actions && 'actions'].filter(Boolean).join('+');
}
function run(argv) {
    const [payloadPath, commonPath, backupDirectory, mode, refreshFlag] = argv;
    if (!['inspect', 'apply'].includes(mode)) throw new Error('Explicit inspect/apply mode required.');
    const common = loadCommon(commonPath);
    const payload = common.readJSON(payloadPath);
    const btt = Application('/Applications/BetterTouchTool.app');
    const before = readMedia(btt, payload.media_uuid);
    const steps = plan(before, payload, common);
    const todo = steps.filter(step => step.add || step.config || step.actions);
    if (mode === 'inspect') {
        return JSON.stringify({mode, installed: !todo.length, order: steps[0].node.BTTOrder,
            pending: todo.map(step => ({uuid: step.node.BTTUUID, name: step.node.BTTMenuName,
                change: describe(step)}))}, null, 2);
    }
    let index = locate(before);
    let message = 'Already configured: Media > Lighting.';
    if (todo.length) {
        const backupPath = common.backup({media: before}, backupDirectory + '/btt-lighting-menu-backup-' +
            ObjC.unwrap($.NSUUID.UUID.UUIDString), {
            create: 'Cannot create private backup; no changes made.',
            verify: 'Backup verification failed; no changes made.',
        });
        if (common.fingerprint(readMedia(btt, before.BTTUUID)) !== common.fingerprint(before)) {
            throw new Error('Media changed during backup; no changes made.');
        }
        try {
            for (const step of todo) write(btt, step);
        } catch (error) {
            throw new Error(error.message + ' Backup: ' + backupPath);
        }
        index = verify(btt, before, steps, common, backupPath);
        message = 'Verified configuration: Media > Lighting. Backup: ' + backupPath;
    }
    if (refreshFlag === 'refresh') {
        refreshMedia(btt, before.BTTUUID);
        message += ' Media menu refreshed.';
    }
    return receipt(message, index, steps, todo);
}
