// Guarded installer for an owned Media submenu (Lighting, Mac) and any top-level dropdowns
// it shows; writes only its own UUIDs. The Python launcher (media_submenu.py) owns the
// structure: a flat list of items, each naming its parent (null for a top-level floating
// menu), plus the noun, title and backup prefix its messages use.
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
// BTT answers get_trigger for an unknown UUID with an empty object.
function readTrigger(btt, id) {
    const value = JSON.parse(btt.get_trigger(id) || '{}');
    return pick(Array.isArray(value) ? value : [value], id);
}
// The Media menu plus every owned top-level menu that already exists.
function readTrees(btt, payload) {
    const media = readTrigger(btt, payload.media_uuid);
    if (!media || Number(media.BTTTriggerType) !== 767 || !enabled(media) || !Array.isArray(media.BTTMenuItems)) {
        throw new Error('Cannot read complete enabled Media menu; no changes made.');
    }
    const menus = payload.items.filter(item => item.BTTTriggerParentUUID === null)
        .map(item => readTrigger(btt, item.BTTUUID)).filter(Boolean);
    return [media, ...menus];
}
// Every node in the trees by UUID, with its parent (null for a root) and how often it occurs.
function locate(trees) {
    const index = new Map();
    (function add(nodes, parent) {
        for (const node of nodes) {
            const seen = index.get(node.BTTUUID);
            index.set(node.BTTUUID, {node, parent, count: seen ? seen.count + 1 : 1});
            add(node.BTTMenuItems || [], node.BTTUUID);
        }
    })(trees, null);
    return index;
}
const CONFIG_KEYS = ['BTTMenuElementIdentifier', 'BTTMenuElementTooltip', 'BTTMenuItemText', 'BTTMenuItemMinWidth',
    'BTTMenuItemMaxWidth', 'BTTMenuItemMinHeight', 'BTTMenuItemMaxHeight'];
function fontSize(config) {
    return (String(config.BTTMenuAttributedText || '').match(/\\fs(\d+)/) || [])[1];
}
// Icon settings count only on items that set them (the Mac submenu's SF Symbol), so items
// without an icon are not restyled over defaults BTT may store.
const ICON_KEYS = ['BTTMenuItemSFSymbolName', 'BTTMenuItemShowIcon', 'BTTMenuItemIconType', 'BTTMenuItemIconPosition'];
function configMatches(actual, desired, common) {
    const config = actual.BTTMenuConfig || {};
    const wanted = desired.BTTMenuConfig;
    const keys = CONFIG_KEYS.concat(ICON_KEYS.filter(key => key in wanted));
    return keys.every(key => common.fingerprint(config[key]) === common.fingerprint(wanted[key])) &&
        fontSize(config) === fontSize(wanted);
}
function actionData(action) {
    const data = action.BTTAdditionalActionData;
    return typeof data === 'string' ? JSON.parse(data) : (data || {});
}
// BTT may give a new action its own UUID, so actions match by what they do. Exports may
// spell a flag as 1 or true, so flags and numbers compare as numbers.
function actionMatches(actual, wanted) {
    if (!actual || !enabled(actual)) return false;
    const same = (value, expected) => typeof expected === 'string' ? value === expected : Number(value) === Number(expected);
    const data = actionData(actual);
    return Number(actual.BTTPredefinedActionType) === wanted.BTTPredefinedActionType &&
        actual.BTTShellTaskActionScript === wanted.BTTShellTaskActionScript &&
        actual.BTTShellTaskActionConfig === wanted.BTTShellTaskActionConfig &&
        Object.entries(actionData(wanted)).every(([key, value]) => same(data[key], value));
}
function actionsMatch(actual, desired) {
    const actions = actual.BTTMenuItemActions || [];
    const wanted = desired.BTTMenuItemActions || [];
    return actions.length === wanted.length && wanted.every((action, index) => actionMatches(actions[index], action));
}
// The whole saved shape, checked after writing.
function savedAs(index, desired, common) {
    const found = index.get(desired.BTTUUID);
    return Boolean(found) && found.count === 1 && found.parent === desired.BTTTriggerParentUUID &&
        enabled(found.node) && Number(found.node.BTTTriggerType) === desired.BTTTriggerType &&
        found.node.BTTMenuName === desired.BTTMenuName && Number(found.node.BTTOrder || 0) === desired.BTTOrder &&
        configMatches(found.node, desired, common) && actionsMatch(found.node, desired);
}
// A new item without a BTTOrder goes after its parent's last item, or, for a new
// top-level menu, at the launcher's menu_order.
function newOrder(index, node, payload) {
    if (node.BTTTriggerParentUUID === null) {
        if (!Number.isInteger(payload.menu_order)) throw new Error('No position for a new top-level menu.');
        return payload.menu_order;
    }
    const parent = index.get(node.BTTTriggerParentUUID);
    if (!parent) throw new Error('No position for ' + node.BTTUUID + ' under a missing parent.');
    return Math.max(-1, ...(parent.node.BTTMenuItems || []).map(item => Number(item.BTTOrder || 0))) + 1;
}
function rejectNameClash(index, node, owned) {
    const parent = index.get(node.BTTTriggerParentUUID);
    const identifier = node.BTTMenuConfig.BTTMenuElementIdentifier;
    if (parent && (parent.node.BTTMenuItems || []).some(item => !owned.has(item.BTTUUID) &&
        (item.BTTMenuConfig || {}).BTTMenuElementIdentifier === identifier)) {
        throw new Error('Another item beside ' + node.BTTMenuName + ' is already named ' + identifier +
            '; no changes made.');
    }
}
// One step per owned item, in the launcher's order (parents first). Items that were moved,
// retyped or disabled are refused here, so a step only ever adds an item or repairs its
// label or actions. Installed items keep their saved position, so a hand reorder survives.
function plan(trees, payload, common) {
    const noun = payload.noun;
    const index = locate(trees);
    const owned = new Set(payload.items.map(item => item.BTTUUID));
    const unlinked = payload.unlinked_actions || [];
    return payload.items.map(desired => {
        const node = common.clone(desired);
        rejectNameClash(index, node, owned);
        const found = index.get(node.BTTUUID);
        if (!found) {
            if (node.BTTOrder === undefined) node.BTTOrder = newOrder(index, node, payload);
            return {node, add: true};
        }
        const previous = found.node;
        if (found.count > 1 || found.parent !== node.BTTTriggerParentUUID) {
            throw new Error(noun + ' item ' + node.BTTUUID + ' was moved elsewhere; no changes made.');
        }
        if (Number(previous.BTTTriggerType) !== node.BTTTriggerType || !enabled(previous)) {
            throw new Error(noun + ' item ' + node.BTTUUID + ' is disabled or has another type; ' +
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
    const parent = step.node.BTTTriggerParentUUID;
    if (step.add && parent === null) {
        const json = Object.assign({}, step.node);
        delete json.BTTTriggerParentUUID;
        btt.add_new_trigger(JSON.stringify(json));
    } else if (step.add) {
        btt.add_new_trigger(JSON.stringify(step.node), {parent_uuid: parent});
    }
    if (step.config) btt.update_menu_item(id, {json: JSON.stringify(step.node.BTTMenuConfig), persist: true});
    if (step.actions) {
        for (const action of step.node.BTTMenuItemActions || []) {
            btt.add_new_trigger(JSON.stringify(Object.assign({}, action, {BTTTriggerType: -1})), {parent_uuid: id});
        }
        for (const action of step.previous.BTTMenuItemActions || []) btt.delete_trigger(action.BTTUUID);
    }
}
// The parts of a tree this installer doesn't own. Owned nodes drop out but their unowned
// children stay in place, so items added by hand inside an owned menu still count.
function foreign(node, owned) {
    const items = [];
    for (const child of node.BTTMenuItems || []) {
        const kept = foreign(child, owned);
        if (owned.has(child.BTTUUID)) items.push(...kept.BTTMenuItems);
        else items.push(kept);
    }
    return Object.assign({}, node, {BTTMenuItems: items});
}
function foreignView(trees, steps) {
    const owned = new Set(steps.map(step => step.node.BTTUUID));
    const [media, ...menus] = trees;
    return {media: foreign(media, owned), menus: menus.flatMap(menu => foreign(menu, owned).BTTMenuItems)};
}
function verify(btt, payload, before, steps, common, backupPath) {
    const after = readTrees(btt, payload);
    const index = locate(after);
    if (!steps.every(step => savedAs(index, step.node, common))) {
        throw new Error(payload.noun + ' menu verification failed. Backup: ' + backupPath);
    }
    if (common.fingerprint(foreignView(after, steps)) !== common.fingerprint(foreignView(before, steps))) {
        throw new Error('Unexpected change outside the ' + payload.noun + ' items. Backup: ' + backupPath);
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
    if (!['noun', 'title', 'backup_prefix'].every(key => typeof payload[key] === 'string' && payload[key])) {
        throw new Error('Payload lacks its noun, title or backup prefix; no changes made.');
    }
    const btt = Application('/Applications/BetterTouchTool.app');
    const before = readTrees(btt, payload);
    const steps = plan(before, payload, common);
    const todo = steps.filter(step => step.add || step.config || step.actions);
    if (mode === 'inspect') {
        return JSON.stringify({mode, installed: !todo.length, pending: todo.map(step => ({
            uuid: step.node.BTTUUID, name: step.node.BTTMenuName, order: step.node.BTTOrder,
            change: describe(step)}))}, null, 2);
    }
    let index = locate(before);
    let message = 'Already configured: ' + payload.title + '.';
    if (todo.length) {
        const backupPath = common.backup({trees: before}, backupDirectory + '/' + payload.backup_prefix +
            ObjC.unwrap($.NSUUID.UUID.UUIDString), {
            create: 'Cannot create private backup; no changes made.',
            verify: 'Backup verification failed; no changes made.',
        });
        if (common.fingerprint(readTrees(btt, payload)) !== common.fingerprint(before)) {
            throw new Error('Media changed during backup; no changes made.');
        }
        try {
            for (const step of todo) write(btt, step);
        } catch (error) {
            throw new Error(error.message + ' Backup: ' + backupPath);
        }
        index = verify(btt, payload, before, steps, common, backupPath);
        message = 'Verified configuration: ' + payload.title + '. Backup: ' + backupPath;
    }
    if (refreshFlag === 'refresh') {
        refreshMedia(btt, payload.media_uuid);
        message += ' Media menu refreshed.';
    }
    return receipt(message, index, steps, todo);
}
