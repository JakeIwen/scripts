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
function actionMatches(actual, expected) {
    if (!actual || !enabled(actual) || Number(actual.BTTTriggerType) !== 773) return false;
    const actions = actual.BTTMenuItemActions || [];
    const target = expected.BTTMenuItemActions[0];
    return actions.length === 1 && enabled(actions[0]) && Number(actions[0].BTTPredefinedActionType) === 206 &&
        actions[0].BTTShellTaskActionScript === target.BTTShellTaskActionScript &&
        actions[0].BTTShellTaskActionConfig === target.BTTShellTaskActionConfig &&
        (actual.BTTMenuConfig || {}).BTTMenuElementIdentifier === 'rhythm-practice';
}
function canonical(value) {
    if (Array.isArray(value)) {
        const result=value.map(canonical);
        if(result.every(x=>x && x.BTTUUID))result.sort((a,b)=>a.BTTUUID.localeCompare(b.BTTUUID));
        return result;
    }
    if(value && typeof value==='object') {
        const result={};Object.keys(value).sort().forEach(k=>{
            if(!['BTTLastUpdatedAt','BTTLastChangeUUID'].includes(k))result[k]=canonical(value[k]);
        });return result;
    }
    return value;
}
function fingerprint(value){return JSON.stringify(canonical(value));}
function appearance(tools, previous, desired) {
    const peer=pick(tools.BTTMenuItems,'554C41C9-C964-5875-B916-EF9D2DFB2004');
    const reference=(peer && enabled(peer)?peer:tools).BTTMenuConfig || {};
    const match=String(reference.BTTMenuAttributedText||'').match(/\\fs(\d+)/);
    const height=Number(reference.BTTMenuItemMaxHeight);
    if(!match || Number(match[1])<16 || Number(match[1])>80 || !Number.isFinite(height) || height<24 || height>80)
        throw new Error('Cannot determine live Tools font/height; no changes made.');
    const width=Math.max(100,Math.round('Rhythm Practice'.length*Number(match[1])/2*.57+24));
    const config=Object.assign({},(previous||desired).BTTMenuConfig,{
        BTTMenuElementIdentifier:'rhythm-practice',BTTMenuItemText:'Rhythm Practice',
        BTTMenuAttributedText:desired.BTTMenuConfig.BTTMenuAttributedText.replace(/\\fs\d+/g,'\\fs'+match[1]),
        BTTMenuItemMinHeight:height,BTTMenuItemMaxHeight:height,
        BTTMenuItemMinWidth:width,BTTMenuItemMaxWidth:width,BTTMenuTextMinimumScaleFactor:1,
        BTTMenuItemVisibleWhileActive:1,BTTMenuItemVisibleWhileInactive:1
    });
    if(config.BTTMenuItemSizing)config.BTTMenuItemSizing=Object.assign({},config.BTTMenuItemSizing,{
        BTTMenuItemMinHeight:height,BTTMenuItemMaxHeight:height,BTTMenuItemMinWidth:width,BTTMenuItemMaxWidth:width});
    return config;
}
function matches(actual,expected){
    const a=(actual||{}).BTTMenuConfig||{},e=expected.BTTMenuConfig;
    const keys=['BTTMenuItemMinHeight','BTTMenuItemMaxHeight','BTTMenuItemMinWidth','BTTMenuItemMaxWidth',
        'BTTMenuTextMinimumScaleFactor','BTTMenuItemVisibleWhileActive','BTTMenuItemVisibleWhileInactive','BTTMenuItemSizing'];
    const font=x=>(String(x.BTTMenuAttributedText||'').match(/\\fs(\d+)/)||[])[1];
    return actionMatches(actual,expected) && font(a)===font(e) &&
        keys.every(k=>fingerprint(a[k])===fingerprint(e[k])) &&
        (a.BTTMenuItemText==='Rhythm Practice' || String(a.BTTMenuAttributedText||'').includes('Rhythm Practice'));
}
function refresh(btt,id){
    btt.trigger_action(JSON.stringify({BTTPredefinedActionType:387,BTTAdditionalActionData:{
        BTTMenuActionMenuID:id,BTTMenuActionReleaseFromMemory:1,
        BTTMenuActionTriggerHoveredOnHide:0,BTTMenuActionCloseSubmenuOnHide:1}}));
    btt.trigger_action(JSON.stringify({BTTPredefinedActionType:386,BTTAdditionalActionData:{BTTMenuActionMenuID:id}}));
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
    desired.BTTMenuConfig=appearance(tools,previous,desired);
    if ((previous && Number(previous.BTTTriggerType) !== 773) || tools.BTTMenuItems.some(x =>
        x.BTTUUID !== desired.BTTUUID && (x.BTTMenuName === 'Rhythm Practice' ||
                                        (x.BTTMenuConfig || {}).BTTMenuElementIdentifier === 'rhythm-practice'))) {
        throw new Error('Conflicting Rhythm Practice item; no changes made.');
    }
    if (argv[2] === 'inspect') {
        return JSON.stringify({toolsUUID: tools.BTTUUID, installed: matches(previous, desired),
            desiredConfig:desired.BTTMenuConfig,
            items: tools.BTTMenuItems.map(x => ({uuid: x.BTTUUID, name: x.BTTMenuName, type: x.BTTTriggerType}))}, null, 2);
    }
    if (matches(previous, desired)) {
        if(argv[3]==='refresh')refresh(btt,payload.media_uuid);
        return 'Already configured: Media > Tools > Rhythm Practice.'+(argv[3]==='refresh'?' Media menu refreshed; open Tools again.':'');
    }
    const manager = $.NSFileManager.defaultManager;
    const directory = argv[1] + '/btt-before-rhythm-practice-' + ObjC.unwrap($.NSUUID.UUID.UUIDString);
    if (!manager.createDirectoryAtPathWithIntermediateDirectoriesAttributesError(directory, false,
        $({NSFilePosixPermissions: 448}), null)) throw new Error('Cannot create private backup; no changes made.');
    const backup = directory + '/backup.json';
    const snapshot = {media};
    if (!$(JSON.stringify(snapshot, null, 2)).writeToFileAtomicallyEncodingError(backup, true, $.NSUTF8StringEncoding, null) ||
        !manager.setAttributesOfItemAtPathError($({NSFilePosixPermissions: 384}), backup, null) ||
        JSON.stringify(readJSON(backup)) !== JSON.stringify(snapshot)) {
        throw new Error('Backup verification failed; no changes made.');
    }
    if (fingerprint(get(payload.media_uuid))!==fingerprint(media)) {
        throw new Error('Tools changed during backup; no changes made.');
    }
    desired.BTTTriggerParentUUID = tools.BTTUUID;
    if (!previous) {
        // Append after existing Tools entries without the implicit order=0 default.
        desired.BTTOrder = Math.max(-1, ...tools.BTTMenuItems.map(x => Number(x.BTTOrder || 0))) + 1;
        btt.add_new_trigger(JSON.stringify(desired), {parent_uuid: tools.BTTUUID});
    } else if(actionMatches(previous,desired)) {
        // Appearance API persists style without reimporting actions or child trees.
        btt.update_menu_item(desired.BTTUUID,{json:JSON.stringify(desired.BTTMenuConfig),persist:true});
    } else {
        const updated = Object.assign({}, previous, desired);
        updated.BTTOrder = previous.BTTOrder;
        btt.update_trigger(desired.BTTUUID, {trigger_parent_uuid: tools.BTTUUID, json: JSON.stringify(updated)});
    }
    const afterMedia=get(payload.media_uuid);
    const after = pick(afterMedia.BTTMenuItems || [], tools.BTTUUID);
    if (!after || !matches(pick(after.BTTMenuItems || [], desired.BTTUUID), desired)) {
        throw new Error('Button verification failed. Backup: ' + backup);
    }
    if (orderSignature(after.BTTMenuItems.filter(x => x.BTTUUID !== desired.BTTUUID)) !==
        orderSignature(tools.BTTMenuItems.filter(x => x.BTTUUID !== desired.BTTUUID))) {
        throw new Error('Unexpected change to other Tools entries. Backup: ' + backup);
    }
    const withoutOwned=root=>{const copy=JSON.parse(JSON.stringify(root));const t=pick(copy.BTTMenuItems,tools.BTTUUID);t.BTTMenuItems=t.BTTMenuItems.filter(x=>x.BTTUUID!==desired.BTTUUID);return copy;};
    if(fingerprint(withoutOwned(afterMedia))!==fingerprint(withoutOwned(media)))
        throw new Error('Unexpected change outside Rhythm Practice. Backup: '+backup);
    if(previous && actionMatches(previous,desired) &&
       fingerprint(pick(after.BTTMenuItems,desired.BTTUUID).BTTMenuItemActions)!==fingerprint(previous.BTTMenuItemActions))
        throw new Error('Action changed during appearance repair. Backup: '+backup);
    if(argv[3]==='refresh')refresh(btt,payload.media_uuid);
    return 'Verified configuration: Media > Tools > Rhythm Practice. Backup: ' + backup+
        (argv[3]==='refresh'?' Media menu refreshed; open Tools again.':'');
}
