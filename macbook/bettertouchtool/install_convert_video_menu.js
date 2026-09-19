// Use complete get_trigger exports: get_triggers(parent) can omit menu entries.
ObjC.import('Foundation');

function readJSON(path) {
    const value = $.NSString.stringWithContentsOfFileEncodingError(path, $.NSUTF8StringEncoding, null);
    if (!value) throw new Error('Cannot read ' + path);
    return JSON.parse(ObjC.unwrap(value));
}
function enabled(item) {
    return ['BTTEnabled', 'BTTEnabled2'].every(key => item[key] === undefined || Number(item[key]) === 1);
}
function pick(items, id) {
    const matches = items.filter(x => x.BTTUUID === id);
    if (matches.length > 1) throw new Error('Duplicate UUID: ' + id);
    return matches[0];
}
function matches(actual, expected) {
    if (!actual || !enabled(actual) || Number(actual.BTTTriggerType) !== 773) return false;
    const actions = actual.BTTMenuItemActions || [];
    const target = expected.BTTMenuItemActions[0];
    return actions.length === 1 && enabled(actions[0]) && Number(actions[0].BTTPredefinedActionType) === 206 &&
        actions[0].BTTShellTaskActionScript === target.BTTShellTaskActionScript &&
        actions[0].BTTShellTaskActionConfig === target.BTTShellTaskActionConfig &&
        (actual.BTTMenuConfig || {}).BTTMenuElementIdentifier === 'convert-video';
}
function orderSignature(items) {
    return JSON.stringify(items.map(x => [x.BTTUUID, x.BTTOrder || 0]).sort((a, b) => a[0].localeCompare(b[0])));
}
function run(argv) {
    const payload = readJSON(argv[0]), desired = payload.item;
    const btt = Application('/Applications/BetterTouchTool.app');
    function get(id) {
        const raw = btt.get_trigger(id);
        const value = JSON.parse(raw);
        const result = pick(Array.isArray(value) ? value : [value], id);
        if (!result) throw new Error('BTT did not return required item: ' + id);
        return result;
    }
    const media = get(payload.media_uuid);
    if (Number(media.BTTTriggerType) !== 767 || !enabled(media) || !Array.isArray(media.BTTMenuItems)) {
        throw new Error('Cannot read complete enabled Media menu; no changes made.');
    }
    const tools = pick(media.BTTMenuItems, payload.tools_uuid);
    if (!tools || Number(tools.BTTTriggerType) !== 774 || !enabled(tools) || !Array.isArray(tools.BTTMenuItems)) {
        throw new Error('Cannot read complete enabled Tools submenu; no changes made.');
    }
    const previous = pick(tools.BTTMenuItems, desired.BTTUUID);
    if ((previous && Number(previous.BTTTriggerType) !== 773) || tools.BTTMenuItems.some(x =>
        x.BTTUUID !== desired.BTTUUID && (x.BTTMenuName === 'Convert Video' ||
                                        (x.BTTMenuConfig || {}).BTTMenuElementIdentifier === 'convert-video'))) {
        throw new Error('Conflicting Convert Video item; no changes made.');
    }
    if (argv[2] === 'inspect') {
        return JSON.stringify({toolsUUID: tools.BTTUUID, installed: matches(previous, desired),
            items: tools.BTTMenuItems.map(x => ({uuid: x.BTTUUID, name: x.BTTMenuName, type: x.BTTTriggerType}))}, null, 2);
    }
    if (matches(previous, desired)) return 'Already configured: Media > Tools > Convert Video.';
    const manager = $.NSFileManager.defaultManager;
    const directory = argv[1] + '/btt-before-convert-video-' + ObjC.unwrap($.NSUUID.UUID.UUIDString);
    if (!manager.createDirectoryAtPathWithIntermediateDirectoriesAttributesError(directory, false,
        $({NSFilePosixPermissions: 448}), null)) throw new Error('Cannot create private backup; no changes made.');
    const backup = directory + '/backup.json';
    const snapshot = {media};
    if (!$(JSON.stringify(snapshot, null, 2)).writeToFileAtomicallyEncodingError(backup, true, $.NSUTF8StringEncoding, null) ||
        !manager.setAttributesOfItemAtPathError($({NSFilePosixPermissions: 384}), backup, null) ||
        JSON.stringify(readJSON(backup)) !== JSON.stringify(snapshot)) {
        throw new Error('Backup verification failed; no changes made.');
    }
    const freshTools = pick(get(payload.media_uuid).BTTMenuItems || [], tools.BTTUUID);
    if (!freshTools || orderSignature(freshTools.BTTMenuItems || []) !== orderSignature(tools.BTTMenuItems)) {
        throw new Error('Tools changed during backup; no changes made.');
    }
    desired.BTTTriggerParentUUID = tools.BTTUUID;
    if (!previous) {
        // Append one item, without rewriting the parent tree or its ordering.
        btt.add_new_trigger(JSON.stringify(desired), {parent_uuid: tools.BTTUUID});
    } else {
        const updated = Object.assign({}, previous, desired);
        updated.BTTOrder = previous.BTTOrder;
        btt.update_trigger(desired.BTTUUID, {trigger_parent_uuid: tools.BTTUUID, json: JSON.stringify(updated)});
    }
    const after = pick(get(payload.media_uuid).BTTMenuItems || [], tools.BTTUUID);
    if (!after || !matches(pick(after.BTTMenuItems || [], desired.BTTUUID), desired)) {
        throw new Error('Button verification failed. Backup: ' + backup);
    }
    if (orderSignature(after.BTTMenuItems.filter(x => x.BTTUUID !== desired.BTTUUID)) !==
        orderSignature(tools.BTTMenuItems.filter(x => x.BTTUUID !== desired.BTTUUID))) {
        throw new Error('Unexpected change to other Tools entries. Backup: ' + backup);
    }
    return 'Verified configuration: Media > Tools > Convert Video. Backup: ' + backup;
}
