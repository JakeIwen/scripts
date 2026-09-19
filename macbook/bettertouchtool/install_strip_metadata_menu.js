// Invoked by install_strip_metadata_menu.py. Uses the installed BTT scripting dictionary.
ObjC.import("Foundation");

function readJSON(path) {
    const text = $.NSString.stringWithContentsOfFileEncodingError(path, $.NSUTF8StringEncoding, null);
    if (!text) throw new Error("Cannot read " + path);
    return JSON.parse(ObjC.unwrap(text));
}

function isEnabled(trigger) {
    // BTT's documented default is enabled; exports may omit default fields.
    return trigger.BTTEnabled === undefined || Number(trigger.BTTEnabled) === 1;
}

function menuSummary(trigger) {
    return {
        uuid: trigger.BTTUUID,
        name: trigger.BTTMenuName || trigger.BTTTriggerName,
        type: trigger.BTTTriggerType,
        enabled: trigger.BTTEnabled === undefined ? "omitted (default: 1)" : trigger.BTTEnabled,
    };
}

function run(argv) {
    const definition = readJSON(argv[0]);
    const btt = Application("/Applications/BetterTouchTool.app");
    function get(query) {
        const value = JSON.parse(btt.get_triggers(query));
        if (!Array.isArray(value)) throw new Error("Unexpected BTT response; no changes made.");
        return value;
    }
    // Select the exact UUID ourselves instead of assuming the complete query
    // response consists of exactly one record.
    const menus = get({trigger_id: 767});
    if (argv[2] === "inspect") {
        console.log(JSON.stringify(menus.map(menuSummary), null, 2));
        return;
    }
    const parents = menus.filter(x => x.BTTUUID === definition.BTTTriggerParentUUID);
    if (parents.length !== 1 || Number(parents[0].BTTTriggerType) !== 767 || !isEnabled(parents[0])) {
        throw new Error("Expected enabled Floating Menu " + definition.BTTTriggerParentUUID +
            "; no changes made. BTT returned: " + JSON.stringify(menus.map(menuSummary)));
    }
    const parent = parents[0];
    const children = get({trigger_parent_uuid: parent.BTTUUID});
    const matches = children.filter(x => x.BTTUUID === definition.BTTUUID || x.BTTMenuName === "Tools");
    if (matches.length > 1) throw new Error("Multiple Tools items found; refusing to guess.");
    if (matches.length && Number(matches[0].BTTTriggerType) !== 774) {
        throw new Error("Existing Tools item is not a submenu; no changes made.");
    }
    const snapshot = JSON.stringify({parent, children}, null, 2);
    const backup = argv[1] + "/btt-before-strip-metadata-" + Date.now() + ".json";
    if (!$(snapshot).writeToFileAtomicallyEncodingError(backup, true, $.NSUTF8StringEncoding, null)) {
        throw new Error("Could not save backup; no changes made.");
    }
    if (!$.NSFileManager.defaultManager.setAttributesOfItemAtPathError(
        $({NSFilePosixPermissions: 384}), backup, null)) {
        throw new Error("Could not restrict backup permissions; no changes made.");
    }
    let toolsID;
    if (!matches.length) {
        // BTT appends the new item. Avoid stale explicit root order values.
        btt.add_new_trigger(JSON.stringify(definition), {parent_uuid: parent.BTTUUID});
        toolsID = definition.BTTUUID;
    } else {
        toolsID = matches[0].BTTUUID;
        const items = get({trigger_parent_uuid: toolsID});
        const existing = items.filter(x => x.BTTUUID === definition.BTTMenuItems[1].BTTUUID);
        if (!existing.length) {
            const item = definition.BTTMenuItems[1];
            item.BTTTriggerParentUUID = toolsID;
            delete item.BTTOrder;
            btt.add_new_trigger(JSON.stringify(item), {parent_uuid: toolsID});
        }
    }
    const actual = get({trigger_parent_uuid: toolsID});
    const strip = actual.find(x => x.BTTUUID === definition.BTTMenuItems[1].BTTUUID);
    const expectedScript = definition.BTTMenuItems[1].BTTMenuItemActions[0].BTTShellTaskActionScript;
    const action = strip && (strip.BTTMenuItemActions || []).find(x =>
        Number(x.BTTPredefinedActionType) === 206 && x.BTTShellTaskActionScript === expectedScript);
    const installedTools = get({trigger_parent_uuid: parent.BTTUUID}).find(x => x.BTTUUID === toolsID);
    if (!installedTools || !isEnabled(installedTools) || !strip || !isEnabled(strip) || !action) {
        throw new Error("BTT did not return the expected item/action. Backup: " + backup);
    }
    console.log("Verified: " + parent.BTTMenuName + " > Tools > Strip Metadata. Backup: " + backup);
}
