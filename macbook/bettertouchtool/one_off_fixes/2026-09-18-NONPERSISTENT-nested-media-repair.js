// ARCHIVED / NONPERSISTENT: this nested-import approach did not survive restart.
// Use btt.py repair persistence for current recovery. Retained for reference.
// Restore missing speaker-menu contents/Notes actions, narrow Notes, add Screen Cap.
// All writes use BTT's API with complete item trees and a verified private backup.
ObjC.import('Foundation');

const CONTROL_MEDIA = 'D9B0ED12-C4BE-4E74-B0DA-0CC3BE092289';
const CONTROL_CAPTURE = '387CC8CA-A232-5437-9099-893AB5587BAF';
const CONTROL_CAPTURE_ACTION = 'FE40E6A0-2028-5BCF-9BB6-B1AD69261002';
const CONTROL_SPEAKERS = [
    {uuid:'D6D639BB-B1D1-4C90-85E3-B8F5C8310E56', title:'rear spk', minimum:4},
    {uuid:'F3CC5F44-0D08-49DD-8B5C-3D6C1C022759', title:'Sonos', minimum:9}
];

function controlsRead(path) {
    const value = $.NSString.stringWithContentsOfFileEncodingError(path, $.NSUTF8StringEncoding, null);
    if (!value) throw new Error('Cannot read ' + path);
    return ObjC.unwrap(value);
}
function controlsClone(value) { return JSON.parse(JSON.stringify(value)); }
function controlsPayload(value) { return typeof value === 'string' ? JSON.parse(value) : value; }
function controlsVerifyTree(actual, wanted) {
    if (!actual || actual.BTTUUID !== wanted.BTTUUID) throw new Error('Missing restored item: ' + wanted.BTTUUID);
    for (const key of ['BTTTriggerType','BTTOrder','BTTPredefinedActionType']) {
        if (wanted[key] !== undefined && !(key === 'BTTPredefinedActionType' && Number(wanted[key]) === 366) &&
            Number(actual[key] === undefined ? 0 : actual[key]) !== Number(wanted[key])) {
            throw new Error('Restored item differs at ' + wanted.BTTUUID + ':' + key);
        }
    }
    if (actual.BTTTriggerParentUUID && actual.BTTTriggerParentUUID !== wanted.BTTTriggerParentUUID) {
        throw new Error('Unexpected parent for restored item: ' + wanted.BTTUUID);
    }
    for (const key of ['BTTNamedTriggerToTrigger','BTTTerminalCommand','BTTShellTaskActionScript',
        'BTTShellTaskActionConfig','BTTShortcutToSend','BTTGenericActionConfig','BTTGenericActionConfig2']) {
        if (wanted[key] !== undefined && actual[key] !== wanted[key]) {
            throw new Error('Action payload differs at ' + wanted.BTTUUID + ':' + key);
        }
    }
    if (wanted.BTTAdditionalActionData) {
        const data = controlsPayload(actual.BTTAdditionalActionData || {});
        const expected = controlsPayload(wanted.BTTAdditionalActionData);
        for (const key of Object.keys(expected)) {
            if (JSON.stringify(data[key]) !== JSON.stringify(expected[key])) {
                throw new Error('Action data differs at ' + wanted.BTTUUID + ':' + key);
            }
        }
    }
    const c = actual.BTTMenuConfig || {}, expectedConfig = wanted.BTTMenuConfig || {};
    for (const key of ['BTTMenuItemMinWidth','BTTMenuItemMaxWidth','BTTMenuItemMinHeight',
        'BTTMenuItemMaxHeight','BTTMenuItemCloseOnClick']) {
        if (expectedConfig[key] !== undefined && Number(c[key]) !== Number(expectedConfig[key])) {
            throw new Error('Menu appearance differs at ' + wanted.BTTUUID + ':' + key);
        }
    }
    if (expectedConfig.BTTMenuItemText !== undefined && c.BTTMenuItemText !== expectedConfig.BTTMenuItemText) {
        throw new Error('Menu text differs at ' + wanted.BTTUUID);
    }
    for (const key of ['BTTMenuItems','BTTMenuItemActions','BTTAdditionalActions']) {
        for (const child of wanted[key] || []) {
            controlsVerifyTree((actual[key] || []).find(x => x.BTTUUID === child.BTTUUID), child);
        }
    }
}
function controlsRestoreSpeaker(current, archived, sizeHelpers) {
    const restored = controlsClone(current);
    function merge(live, saved) {
        for (const key of ['BTTMenuItems','BTTMenuItemActions','BTTAdditionalActions']) {
            const archivedItems = saved[key] || [];
            if (!archivedItems.length) continue;
            const children = live[key] || (live[key] = []);
            for (const old of archivedItems) {
                const existing = children.find(x => x.BTTUUID === old.BTTUUID);
                if (existing) merge(existing, old);
                else children.push(controlsClone(old));
            }
        }
    }
    merge(restored, archived);
    function fixSizes(item) {
        for (const edit of sizeHelpers.edits(item.BTTMenuConfig, [])) {
            sizeHelpers.set(item.BTTMenuConfig, edit.path, edit.after);
        }
        (item.BTTMenuItems || []).forEach(fixSizes);
    }
    fixSizes(restored);
    return restored;
}
function controlsCaptureButton(order, repo, notes) {
    return {BTTUUID:CONTROL_CAPTURE, BTTTriggerParentUUID:CONTROL_MEDIA, BTTOrder:order,
        BTTTriggerClass:'BTTTriggerTypeFloatingMenu', BTTTriggerType:773, BTTEnabled:1,
        BTTMenuName:'Screen Cap', BTTMenuConfig:{
            BTTMenuElementIdentifier:'media-diagnostic-screen-cap',
            BTTMenuItemText:'Screen\nCap', BTTMenuAttributedText:notes.rtf('Screen\nCap',14,true),
            BTTMenuItemMinWidth:54, BTTMenuItemMaxWidth:78,
            BTTMenuItemMinHeight:40, BTTMenuItemMaxHeight:40,
            BTTMenuItemVisibleWhileActive:1, BTTMenuItemVisibleWhileInactive:1,
            BTTMenuItemCloseOnClick:0, BTTMenuItemScriptActive:0,
            BTTMenuItemBackgroundType:4, BTTMenuItemBackgroundTypeDark:4,
            BTTMenuItemBackgroundColor:'108.442, 96.000, 190.435, 166.991',
            BTTMenuItemBackgroundColorDark:'108.442, 96.000, 190.435, 166.991'
        }, BTTMenuItemActions:[{BTTUUID:CONTROL_CAPTURE_ACTION,
            BTTTriggerParentUUID:CONTROL_CAPTURE, BTTTriggerClass:'BTTTriggerTypeFloatingMenu',
            BTTEnabled:1, BTTOrder:0, BTTActionCategory:0, BTTPredefinedActionType:206,
            BTTShellTaskActionScript:'/bin/zsh ' + notes.quote(repo + '/macbook/scripts/capture_btt_menu.zsh'),
            BTTShellTaskActionConfig:'/bin/zsh:::-c:::-:::'}]};
}
function controlsArchives(repo) {
    const fm = $.NSFileManager.defaultManager, directory=repo+'/tmp';
    const names=ObjC.deepUnwrap(fm.contentsOfDirectoryAtPathError(directory,null));
    if (!Array.isArray(names)) throw new Error('Cannot list recovery backups.');
    return names.filter(x => /^btt-(?:notes|labels|sizing)-backup-/.test(x)).map(name => {
        const path=directory+'/'+name+'/backup.json';
        try {
            const data=JSON.parse(controlsRead(path));
            const root=data.media || data.mediaExport;
            const attrs=fm.attributesOfItemAtPathError(path,null);
            const modified=Number(attrs.objectForKey('NSFileModificationDate').timeIntervalSince1970);
            return {path,root,modified};
        } catch (_) {return null;}
    }).filter(x => x && x.root && x.root.BTTUUID===CONTROL_MEDIA)
        .sort((a,b)=>b.modified-a.modified);
}
function run(argv) {
    const historical = argv[0] === '--run-historical';
    if (historical) argv = argv.slice(1);
    if (!historical && argv[0] !== '--inspect') throw new Error('ARCHIVED / NONPERSISTENT: review one_off_fixes/README.md; use btt.py repair persistence instead.');
    if (argv.length && argv[0]!=='--inspect') throw new Error('Usage: repair_media_controls.js [--inspect]');
    const repo=ObjC.unwrap($('~/dev/scripts').stringByExpandingTildeInPath);
    const notes=new Function(controlsRead(repo+'/macbook/bettertouchtool/install_notes.js')+
        '\nreturn {specs:NOTES_MENUS,launcher:notesLauncher,backup:notesBackup,checkOpening:notesCheckOpening,rtf:notesRTF,quote:notesShellQuote};')();
    const sizeHelpers=new Function(controlsRead(repo+'/macbook/bettertouchtool/fix_menu_sizes.js')+
        '\nreturn {edits:sizeEdits,set:sizeSet,fingerprint:sizeFingerprint};')();
    const btt=Application('/Applications/BetterTouchTool.app');
    function get(id) {
        const raw=JSON.parse(btt.get_trigger(id));
        const matches=(Array.isArray(raw)?raw:[raw]).filter(x=>x&&x.BTTUUID===id);
        if(matches.length!==1)throw new Error('Cannot uniquely export '+id);
        return matches[0];
    }
    const media=get(CONTROL_MEDIA);
    if(Number(media.BTTTriggerType)!==767 || !Array.isArray(media.BTTMenuItems))throw new Error('Invalid Media export.');
    const archives=controlsArchives(repo), changes=[], sources=[];
    for (const spec of CONTROL_SPEAKERS) {
        const current=media.BTTMenuItems.find(x=>x.BTTUUID===spec.uuid);
        if(!current || Number(current.BTTTriggerType)!==774)throw new Error('Expected existing '+spec.title+' submenu.');
        const source=archives.find(a=>(a.root.BTTMenuItems||[]).some(x=>x.BTTUUID===spec.uuid &&
            (x.BTTMenuItems||[]).length>=spec.minimum));
        if(!source)throw new Error('No complete recovery backup for '+spec.title+'; no changes made.');
        const restored=controlsRestoreSpeaker(current,source.root.BTTMenuItems.find(x=>x.BTTUUID===spec.uuid),sizeHelpers);
        changes.push(restored);sources.push({title:spec.title,path:source.path});
    }
    for(const spec of notes.specs) {
        const current=media.BTTMenuItems.find(x=>x.BTTUUID===spec.source);
        if(!current || Number(current.BTTTriggerType)!==773)throw new Error('Expected installed '+spec.title+' launcher.');
        const destination=get(spec.menu);
        if(Number(destination.BTTTriggerType)!==767 || Number(destination.BTTMenuConfig.BTTMenuLayoutDirection)!==6) {
            throw new Error('Notes dropdown destination is missing or not vertical.');
        }
        const wanted=Object.assign(controlsClone(current),notes.launcher(spec,current));
        // Keep additional user-created actions while repairing the owned show action.
        wanted.BTTMenuItemActions=(current.BTTMenuItemActions||[]).filter(x=>x.BTTUUID!==spec.action)
            .concat(wanted.BTTMenuItemActions);
        changes.push(wanted);
    }
    const existingCap=media.BTTMenuItems.find(x=>x.BTTUUID===CONTROL_CAPTURE);
    if(!existingCap && media.BTTMenuItems.some(x=>x.BTTMenuName==='Screen Cap'))throw new Error('Another Screen Cap item already exists.');
    const capture=controlsCaptureButton(existingCap ? Number(existingCap.BTTOrder||0) :
        Math.max(...media.BTTMenuItems.map(x=>Number(x.BTTOrder||0)))+1,repo,notes);
    const updates=changes.filter(x=>sizeHelpers.fingerprint(x)!==sizeHelpers.fingerprint(media.BTTMenuItems.find(i=>i.BTTUUID===x.BTTUUID)));
    if(argv[0]==='--inspect')return JSON.stringify({restoreSources:sources,updates:updates.map(x=>({uuid:x.BTTUUID,
        children:(x.BTTMenuItems||[]).length,actions:(x.BTTMenuItemActions||[]).length})),screenshotExists:!!existingCap},null,2);
    const backup=notes.backup({media,recoverySources:sources},repo+'/tmp/btt-controls-backup-'+ObjC.unwrap($.NSUUID.UUID.UUIDString));
    console.log('Verified full current Media backup: '+backup);
    if(sizeHelpers.fingerprint(get(CONTROL_MEDIA))!==sizeHelpers.fingerprint(media))throw new Error('Media changed during backup; no changes made.');
    for(const wanted of updates) {
        btt.update_trigger(wanted.BTTUUID,{trigger_parent_uuid:CONTROL_MEDIA,json:JSON.stringify(wanted)});
        controlsVerifyTree(get(wanted.BTTUUID),wanted);
    }
    if(!existingCap)btt.add_new_trigger(JSON.stringify(capture),{parent_uuid:CONTROL_MEDIA});
    else if(sizeHelpers.fingerprint(existingCap)!==sizeHelpers.fingerprint(capture)) {
        btt.update_trigger(CONTROL_CAPTURE,{trigger_parent_uuid:CONTROL_MEDIA,
            json:JSON.stringify(Object.assign({},existingCap,capture))});
    }
    controlsVerifyTree(get(CONTROL_CAPTURE),capture);
    const after=get(CONTROL_MEDIA);
    for(const item of media.BTTMenuItems) {
        const actual=after.BTTMenuItems.find(x=>x.BTTUUID===item.BTTUUID);
        if(!actual || Number(actual.BTTOrder||0)!==Number(item.BTTOrder||0))throw new Error('An existing item moved/disappeared. Backup: '+backup);
        if(!changes.some(x=>x.BTTUUID===item.BTTUUID) && item.BTTUUID!==CONTROL_CAPTURE) {
            controlsVerifyTree(actual,item);
        }
    }
    console.log('Restored speaker menu contents and Notes show actions; Notes width is 54–78 px; Screen Cap added.');
    console.log('Screenshots: '+repo+'/tmp/btt-screenshots (also copied to clipboard).');
    return notes.checkOpening(btt)+'. Backup: '+backup;
}
