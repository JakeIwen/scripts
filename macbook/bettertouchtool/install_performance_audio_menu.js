// Uses BTT's API only. A Tools button opens an independent vertical floating menu.
ObjC.import("Foundation");

function readJSON(path) {
    const text = $.NSString.stringWithContentsOfFileEncodingError(path, $.NSUTF8StringEncoding, null);
    if (!text) throw new Error("Cannot read " + path);
    return JSON.parse(ObjC.unwrap(text));
}
function isEnabled(item) {
    return ["BTTEnabled", "BTTEnabled2"].every(key => item[key] === undefined || Number(item[key]) === 1);
}
function isHiddenMenuItem(item) {
    // Disabled items can disappear from a parent query. Other BTT exports omit
    // enabled fields even when disabled; explicit visibility provides readback.
    if (!item || !isEnabled(item)) return true;
    const config = item.BTTMenuConfig || {};
    return config.BTTMenuItemVisibleWhileActive !== undefined &&
        config.BTTMenuItemVisibleWhileInactive !== undefined &&
        Number(config.BTTMenuItemVisibleWhileActive) === 0 &&
        Number(config.BTTMenuItemVisibleWhileInactive) === 0;
}
function summary(item) {
    const config = item.BTTMenuConfig || {};
    return {uuid: item.BTTUUID, name: item.BTTMenuName || item.BTTTriggerName,
        type: item.BTTTriggerType, enabled: isEnabled(item),
        layout: config.BTTMenuLayoutDirection};
}
function actionData(action) {
    const data = action.BTTAdditionalActionData || {};
    return typeof data === "string" ? JSON.parse(data) : data;
}
function matchesItem(actual, expected) {
    if (!actual || !isEnabled(actual) || actual.BTTUUID !== expected.BTTUUID ||
        Number(actual.BTTTriggerType) !== Number(expected.BTTTriggerType)) return false;
    const actions = actual.BTTMenuItemActions || [];
    const desired = expected.BTTMenuItemActions || [];
    return actions.length === desired.length && desired.every((wanted, index) => {
        const action = actions[index];
        const type = Number(wanted.BTTPredefinedActionType);
        if (Number(action.BTTPredefinedActionType) !== type || !isEnabled(action)) return false;
        if (type === 386) return actionData(action).BTTMenuActionMenuID === actionData(wanted).BTTMenuActionMenuID;
        return type === 206 && action.BTTShellTaskActionScript === wanted.BTTShellTaskActionScript &&
            action.BTTShellTaskActionConfig === wanted.BTTShellTaskActionConfig;
    });
}
function matchesLayout(item, layout) {
    const config = item.BTTMenuConfig || {};
    return Object.keys(layout).every(key => Number(config[key] === undefined ? 0 : config[key]) === layout[key]);
}

function run(argv) {
    const definition = readJSON(argv[0]);
    const dropdown = definition.dropdown;
    const launcher = definition.launcher;
    const btt = Application("/Applications/BetterTouchTool.app");
    function get(query) {
        const items = JSON.parse(btt.get_triggers(query));
        if (!Array.isArray(items)) throw new Error("Unexpected BTT response; refusing changes.");
        return items;
    }
    function childItems(id) { return get({trigger_parent_uuid: id}); }
    function one(items, id) {
        const found = items.filter(item => item.BTTUUID === id);
        if (found.length > 1) throw new Error("Duplicate UUID returned: " + id);
        return found[0];
    }
    const menus = get({trigger_id: 767});
    const parent = one(menus, definition.parent);
    if (!parent || Number(parent.BTTTriggerType) !== 767 || !isEnabled(parent)) {
        throw new Error("Expected enabled Media menu; no changes made. " + JSON.stringify(menus.map(summary)));
    }
    const children = childItems(parent.BTTUUID);
    const toolsMatches = children.filter(item => item.BTTUUID === definition.tools_uuid || item.BTTMenuName === "Tools");
    const tools = toolsMatches.length === 1 ? toolsMatches[0] : null;
    const toolsItems = tools ? childItems(tools.BTTUUID) : [];
    const legacy = one(toolsItems, definition.legacy_uuid);
    const existingLauncher = one(toolsItems, launcher.BTTUUID);
    const existingDropdown = one(menus, dropdown.BTTUUID);
    const dropdownItems = existingDropdown ? childItems(dropdown.BTTUUID) : [];
    if (argv[2] === "inspect") {
        console.log(JSON.stringify({parent: summary(parent), children: children.map(summary),
            tools: tools && summary(tools), toolsItems: toolsItems.map(summary),
            dropdown: existingDropdown && summary(existingDropdown),
            dropdownItems: dropdownItems.map(summary)}, null, 2));
        return;
    }
    if (!tools || Number(tools.BTTTriggerType) !== 774 || !isEnabled(tools)) {
        throw new Error("Expected exactly one enabled Tools submenu; no changes made. Media children: " + JSON.stringify(children.map(summary)));
    }
    if ((legacy && Number(legacy.BTTTriggerType) !== 774) ||
        (existingLauncher && Number(existingLauncher.BTTTriggerType) !== 773) ||
        (existingDropdown && Number(existingDropdown.BTTTriggerType) !== 767) ||
        toolsItems.some(item => item.BTTMenuName === "Performance Audio" &&
            item.BTTUUID !== definition.legacy_uuid && item.BTTUUID !== launcher.BTTUUID)) {
        throw new Error("Conflicting Performance Audio item; refusing to replace an unknown item.");
    }
    const legacyItems = legacy ? childItems(legacy.BTTUUID) : [];
    const snapshot = JSON.stringify({parent, children, toolsItems, legacyItems,
        dropdown: existingDropdown || null, dropdownItems}, null, 2);
    const backup = argv[1] + "/btt-before-performance-audio-" + Date.now() + ".json";
    if (!$(snapshot).writeToFileAtomicallyEncodingError(backup, true, $.NSUTF8StringEncoding, null) ||
        !$.NSFileManager.defaultManager.setAttributesOfItemAtPathError(
            $({NSFilePosixPermissions: 384}), backup, null)) {
        throw new Error("Could not save a private backup; no changes made.");
    }
    function upsertItem(wanted, actual, parentID) {
        wanted.BTTTriggerParentUUID = parentID;
        if (!actual) {
            btt.add_new_trigger(JSON.stringify(wanted), {parent_uuid: parentID});
        } else if (!matchesItem(actual, wanted)) {
            const replacement = Object.assign({}, actual, wanted);
            if (actual.BTTOrder === undefined) delete replacement.BTTOrder;
            else replacement.BTTOrder = actual.BTTOrder;
            btt.update_trigger(wanted.BTTUUID, {json: JSON.stringify(replacement)});
        }
    }
    if (!existingDropdown) {
        // No parent_uuid: this is a genuine top-level menu with its own layout.
        btt.add_new_trigger(JSON.stringify(dropdown));
    } else {
        if (!isEnabled(existingDropdown) || !matchesLayout(existingDropdown, definition.dropdown_layout)) {
            btt.update_trigger(dropdown.BTTUUID, {json: JSON.stringify({BTTEnabled: 1,
                BTTMenuConfig: Object.assign({}, existingDropdown.BTTMenuConfig || {}, definition.dropdown_layout)})});
        }
        for (const wanted of dropdown.BTTMenuItems) {
            upsertItem(wanted, one(dropdownItems, wanted.BTTUUID), dropdown.BTTUUID);
        }
    }
    const savedDropdown = one(get({trigger_id: 767}), dropdown.BTTUUID);
    const savedItems = childItems(dropdown.BTTUUID);
    if (!savedDropdown || Number(savedDropdown.BTTTriggerType) !== 767 || !isEnabled(savedDropdown) ||
        !matchesLayout(savedDropdown, definition.dropdown_layout) ||
        !dropdown.BTTMenuItems.every(item => matchesItem(one(savedItems, item.BTTUUID), item))) {
        throw new Error("Dropdown verification failed; old submenu remains available. Backup: " + backup);
    }
    if (!existingLauncher && legacy && legacy.BTTOrder !== undefined) launcher.BTTOrder = legacy.BTTOrder;
    upsertItem(launcher, existingLauncher, tools.BTTUUID);
    if (!matchesItem(one(childItems(tools.BTTUUID), launcher.BTTUUID), launcher)) {
        throw new Error("Launcher verification failed; old submenu remains available. Backup: " + backup);
    }
    // Retire the old horizontal submenu only after its replacement is verified.
    // Keep it and all its descendants intact for recovery; never delete them.
    if (legacy && !isHiddenMenuItem(legacy)) {
        btt.update_trigger(legacy.BTTUUID, {json: JSON.stringify({
            BTTEnabled: 0, BTTEnabled2: 0,
            BTTMenuConfig: Object.assign({}, legacy.BTTMenuConfig || {}, {
                BTTMenuItemVisibleWhileActive: 0, BTTMenuItemVisibleWhileInactive: 0,
            }),
        })});
        const retired = one(childItems(tools.BTTUUID), legacy.BTTUUID);
        if (!isHiddenMenuItem(retired)) throw new Error("Could not hide the old submenu. Backup: " + backup);
    }
    console.log("Verified configuration: Media > Tools > Performance Audio opens a separate vertical dropdown. " +
        "Close and reopen Tools to use the new button. Backup: " + backup);
}
