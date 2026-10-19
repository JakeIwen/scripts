// Guarded installer for one standard item in the existing Media > Tools submenu.
ObjC.import('Foundation');

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
    const matches = items.filter(item => item.BTTUUID === id);
    if (matches.length > 1) throw new Error('Duplicate UUID: ' + id);
    return matches[0];
}
function getTrigger(btt, id) {
    const value = JSON.parse(btt.get_trigger(id));
    const result = pick(Array.isArray(value) ? value : [value], id);
    if (!result) throw new Error('BTT did not return required item: ' + id);
    return result;
}
function actionMatches(actual, expected, spec) {
    if (!actual || !enabled(actual) || Number(actual.BTTTriggerType) !== 773) return false;
    const actions = actual.BTTMenuItemActions || [];
    const target = expected.BTTMenuItemActions[0];
    return actions.length === 1 && enabled(actions[0]) &&
        Number(actions[0].BTTPredefinedActionType) === 206 &&
        actions[0].BTTShellTaskActionScript === target.BTTShellTaskActionScript &&
        actions[0].BTTShellTaskActionConfig === target.BTTShellTaskActionConfig &&
        (actual.BTTMenuConfig || {}).BTTMenuElementIdentifier === spec.identifier;
}
function orderSignature(items) {
    return JSON.stringify(items.map(item => [item.BTTUUID, item.BTTOrder || 0])
        .sort((a, b) => a[0].localeCompare(b[0])));
}
function liveAppearance(tools, previous, desired, spec) {
    const peer = spec.appearancePeerUUID ? pick(tools.BTTMenuItems, spec.appearancePeerUUID) : undefined;
    const reference = (peer && enabled(peer) ? peer : tools).BTTMenuConfig || {};
    const match = String(reference.BTTMenuAttributedText || '').match(/\\fs(\d+)/);
    const height = Number(reference.BTTMenuItemMaxHeight);
    if (!match || Number(match[1]) < 16 || Number(match[1]) > 80 ||
        !Number.isFinite(height) || height < 24 || height > 80) {
        throw new Error('Cannot determine live Tools font/height; no changes made.');
    }
    const width = Math.max(100, Math.round(spec.menuName.length * Number(match[1]) / 2 * .57 + 24));
    const config = Object.assign({}, (previous || desired).BTTMenuConfig, {
        BTTMenuElementIdentifier: spec.identifier,
        BTTMenuItemText: spec.menuName,
        BTTMenuAttributedText: desired.BTTMenuConfig.BTTMenuAttributedText.replace(/\\fs\d+/g, '\\fs' + match[1]),
        BTTMenuItemMinHeight: height, BTTMenuItemMaxHeight: height,
        BTTMenuItemMinWidth: width, BTTMenuItemMaxWidth: width,
        BTTMenuTextMinimumScaleFactor: 1,
        BTTMenuItemVisibleWhileActive: 1, BTTMenuItemVisibleWhileInactive: 1,
    });
    if (config.BTTMenuItemSizing) {
        config.BTTMenuItemSizing = Object.assign({}, config.BTTMenuItemSizing, {
            BTTMenuItemMinHeight: height, BTTMenuItemMaxHeight: height,
            BTTMenuItemMinWidth: width, BTTMenuItemMaxWidth: width,
        });
    }
    return config;
}
function appearanceMatches(actual, expected, spec, common) {
    const actualConfig = (actual || {}).BTTMenuConfig || {};
    const expectedConfig = expected.BTTMenuConfig;
    const keys = ['BTTMenuItemMinHeight', 'BTTMenuItemMaxHeight', 'BTTMenuItemMinWidth',
        'BTTMenuItemMaxWidth', 'BTTMenuTextMinimumScaleFactor', 'BTTMenuItemVisibleWhileActive',
        'BTTMenuItemVisibleWhileInactive', 'BTTMenuItemSizing'];
    const font = value => (String(value.BTTMenuAttributedText || '').match(/\\fs(\d+)/) || [])[1];
    return font(actualConfig) === font(expectedConfig) &&
        keys.every(key => common.fingerprint(actualConfig[key]) === common.fingerprint(expectedConfig[key])) &&
        (actualConfig.BTTMenuItemText === spec.menuName ||
         String(actualConfig.BTTMenuAttributedText || '').includes(spec.menuName));
}
function itemMatches(actual, expected, spec, common) {
    return actionMatches(actual, expected, spec) && appearanceMatches(actual, expected, spec, common);
}
function readMenus(btt, payload) {
    const media = getTrigger(btt, payload.media_uuid);
    if (Number(media.BTTTriggerType) !== 767 || !enabled(media) || !Array.isArray(media.BTTMenuItems)) {
        throw new Error('Cannot read complete enabled Media menu; no changes made.');
    }
    const tools = pick(media.BTTMenuItems, payload.tools_uuid);
    if (!tools || Number(tools.BTTTriggerType) !== 774 || !enabled(tools) ||
        !Array.isArray(tools.BTTMenuItems)) {
        throw new Error('Cannot read complete enabled Tools submenu; no changes made.');
    }
    return {media, tools};
}
function rejectConflict(tools, desired, spec) {
    const previous = pick(tools.BTTMenuItems, desired.BTTUUID);
    const collision = tools.BTTMenuItems.some(item => item.BTTUUID !== desired.BTTUUID &&
        (item.BTTMenuName === spec.menuName ||
         (item.BTTMenuConfig || {}).BTTMenuElementIdentifier === spec.identifier));
    if ((previous && Number(previous.BTTTriggerType) !== 773) || collision) {
        throw new Error(spec.conflictMessage);
    }
    return previous;
}
function inspectResult(tools, previous, desired, spec, common) {
    const result = {
        toolsUUID: tools.BTTUUID,
        installed: itemMatches(previous, desired, spec, common),
    };
    result.desiredConfig = desired.BTTMenuConfig;
    result.items = tools.BTTMenuItems.map(item => ({
        uuid: item.BTTUUID, name: item.BTTMenuName, type: item.BTTTriggerType,
    }));
    return JSON.stringify(result, null, 2);
}
function backup(common, media, directory, prefix) {
    return common.backup({media}, directory + '/' + prefix + ObjC.unwrap($.NSUUID.UUID.UUIDString), {
        create: 'Cannot create private backup; no changes made.',
        verify: 'Backup verification failed; no changes made.',
    });
}
function verifyBackupRace(btt, payload, before, common) {
    const freshMedia = getTrigger(btt, payload.media_uuid);
    if (common.fingerprint(freshMedia) !== common.fingerprint(before.media)) {
        throw new Error('Tools changed during backup; no changes made.');
    }
}
function applyChange(btt, tools, previous, desired, spec) {
    desired.BTTTriggerParentUUID = tools.BTTUUID;
    if (!previous) {
        desired.BTTOrder = Math.max(-1,
            ...tools.BTTMenuItems.map(item => Number(item.BTTOrder || 0))) + 1;
        btt.add_new_trigger(JSON.stringify(desired), {parent_uuid: tools.BTTUUID});
    } else if (actionMatches(previous, desired, spec)) {
        // Style-only repair avoids reimporting actions or child trees.
        btt.update_menu_item(desired.BTTUUID, {json: JSON.stringify(desired.BTTMenuConfig), persist: true});
    } else {
        const updated = Object.assign({}, previous, desired);
        updated.BTTOrder = previous.BTTOrder;
        btt.update_trigger(desired.BTTUUID, {
            trigger_parent_uuid: tools.BTTUUID, json: JSON.stringify(updated),
        });
    }
}
function withoutOwned(common, root, toolsID, ownedID) {
    const copy = common.clone(root);
    const tools = pick(copy.BTTMenuItems, toolsID);
    tools.BTTMenuItems = tools.BTTMenuItems.filter(item => item.BTTUUID !== ownedID);
    return copy;
}
function verifyChange(btt, payload, before, previous, desired, spec, common, backupPath) {
    const afterMedia = getTrigger(btt, payload.media_uuid);
    const after = pick(afterMedia.BTTMenuItems || [], before.tools.BTTUUID);
    const actual = after && pick(after.BTTMenuItems || [], desired.BTTUUID);
    if (!after || !itemMatches(actual, desired, spec, common)) {
        throw new Error('Button verification failed. Backup: ' + backupPath);
    }
    if (orderSignature(after.BTTMenuItems.filter(item => item.BTTUUID !== desired.BTTUUID)) !==
        orderSignature(before.tools.BTTMenuItems.filter(item => item.BTTUUID !== desired.BTTUUID))) {
        throw new Error('Unexpected change to other Tools entries. Backup: ' + backupPath);
    }
    if (common.fingerprint(withoutOwned(common, afterMedia,
        before.tools.BTTUUID, desired.BTTUUID)) !== common.fingerprint(withoutOwned(common,
        before.media, before.tools.BTTUUID, desired.BTTUUID))) {
        throw new Error('Unexpected change outside ' + spec.menuName + '. Backup: ' + backupPath);
    }
    if (previous && actionMatches(previous, desired, spec) &&
        common.fingerprint(actual.BTTMenuItemActions) !== common.fingerprint(previous.BTTMenuItemActions)) {
        throw new Error('Action changed during appearance repair. Backup: ' + backupPath);
    }
    return actual;
}
// ToolsInstallerReceipt in btt_guard/model.py is consumed only by the Python launcher.
function installerReceipt(message, actual, parent, touched) {
    const entities = [{uuid: actual.BTTUUID, parent, node: actual}];
    for (const action of actual.BTTMenuItemActions || []) {
        entities.push({uuid: action.BTTUUID, parent: actual.BTTUUID,
            node: Object.assign({}, action, {BTTTriggerType: -1})});
    }
    return JSON.stringify({message, wrote: touched.length > 0, touched_uuids: [...new Set(touched)],
        saved_plan: {entities, configChanges: [{uuid: actual.BTTUUID, patch: actual.BTTMenuConfig}],
            media: {BTTMenuItems: []}}});
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
    const common = loadCommon(argv[1]);
    const payload = common.readJSON(argv[0]);
    const spec = JSON.parse(argv[4]);
    const refreshRequested = argv[5] === 'refresh';
    const receiptRequested = argv[6] === 'receipt';
    const desired = payload.item;
    const btt = Application('/Applications/BetterTouchTool.app');
    const before = readMenus(btt, payload);
    let previous = pick(before.tools.BTTMenuItems, desired.BTTUUID);
    desired.BTTMenuConfig = liveAppearance(before.tools, previous, desired, spec);
    previous = rejectConflict(before.tools, desired, spec);
    if (argv[3] === 'inspect') return inspectResult(before.tools, previous, desired, spec, common);
    if (itemMatches(previous, desired, spec, common)) {
        if (refreshRequested) refreshMedia(btt, payload.media_uuid);
        const message = 'Already configured: ' + spec.menuPath + '.' +
            (refreshRequested ? ' Media menu refreshed; open Tools again.' : '');
        return receiptRequested ? installerReceipt(message, previous, before.tools.BTTUUID, []) : message;
    }
    const touched = [desired.BTTUUID];
    if (!previous || !actionMatches(previous, desired, spec)) {
        for (const item of [previous, desired]) {
            for (const action of (item && item.BTTMenuItemActions) || []) touched.push(action.BTTUUID);
        }
    }
    const backupPath = backup(common, before.media, argv[2], spec.backupPrefix);
    verifyBackupRace(btt, payload, before, common);
    applyChange(btt, before.tools, previous, desired, spec);
    const actual = verifyChange(btt, payload, before, previous, desired, spec, common, backupPath);
    if (refreshRequested) refreshMedia(btt, payload.media_uuid);
    const message = 'Verified configuration: ' + spec.menuPath + '. Backup: ' + backupPath +
        (refreshRequested ? ' Media menu refreshed; open Tools again.' : '');
    return receiptRequested ? installerReceipt(message, actual, before.tools.BTTUUID, touched) : message;
}
