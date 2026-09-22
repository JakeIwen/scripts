// Worker for stabilize_media.py. Does not write BTT's database directly.
ObjC.import('Foundation');
ObjC.import('AppKit');

function stableRead(path) {
    const value=$.NSString.stringWithContentsOfFileEncodingError(path,$.NSUTF8StringEncoding,null);
    if(!value)throw new Error('Cannot read '+path);
    return ObjC.unwrap(value);
}
function stableLibrary(repo,file,expression) {
    return new Function(stableRead(repo+'/macbook/bettertouchtool/'+file)+'\nreturn '+expression+';')();
}
function stableScreen() {
    const screens=$.NSScreen.screens, mouse=$.NSEvent.mouseLocation;
    let screen=$.NSScreen.mainScreen;
    for(let i=0;i<Number(screens.count);i++) {
        const candidate=screens.objectAtIndex(i),r=candidate.frame;
        if(mouse.x>=r.origin.x && mouse.x<r.origin.x+r.size.width &&
           mouse.y>=r.origin.y && mouse.y<r.origin.y+r.size.height) {screen=candidate;break;}
    }
    const frame=screen && screen.visibleFrame;
    if(!frame || !frame.size)throw new Error('Cannot read the GUI screen size; run from Terminal in the logged-in desktop session.');
    if(!(frame.size.width>0 && frame.size.height>0))throw new Error('Cannot determine usable screen size.');
    return {width:Math.floor(frame.size.width),height:Math.floor(frame.size.height)};
}
function stableFlatten(item,parent,kind,result) {
    const node=JSON.parse(JSON.stringify(item));
    for(const key of ['BTTMenuItems','BTTMenuItemActions','BTTAdditionalActions'])delete node[key];
    if(kind==='action') {
        // Some backups export a compact action-only descriptor, not a trigger
        // record. add_new_trigger needs the category even for type -1 children.
        // Preserve the UUID, payload, enabled state and action category.
        node.BTTTriggerType=-1;
        if(!node.BTTTriggerClass)node.BTTTriggerClass='BTTTriggerTypeFloatingMenu';
        delete node.BTTIsPureAction;
    }
    node.BTTTriggerParentUUID=parent;
    result.push({uuid:node.BTTUUID,parent,kind,node});
    for(const key of ['BTTMenuItems','BTTMenuItemActions','BTTAdditionalActions']) {
        for(const child of item[key]||[])stableFlatten(child,item.BTTUUID,
            key==='BTTMenuItems'?'item':'action',result);
    }
}
function stableCompactOrder(item) {
    // Recreated collections get dense indices in their original display order.
    // This avoids carrying old action indices like119 into a one-action item.
    for(const key of ['BTTMenuItems','BTTMenuItemActions','BTTAdditionalActions']) {
        if(!item[key])continue;
        item[key].sort((a,b)=>Number(a.BTTOrder||0)-Number(b.BTTOrder||0));
        item[key].forEach((child,index)=>{child.BTTOrder=index;stableCompactOrder(child);});
    }
}
function stableLayout(config,screen,main,fullHeight) {
    const patch={BTTMenuSizingBehavior:main||!fullHeight?3:1,
        BTTMenuFrameHeight:main||!fullHeight?40:screen.height,
        BTTMenuFrameMaxHeight:screen.height,
        BTTMenuFrameMaxWidth:main?screen.width:Math.min(Number(config.BTTMenuFrameWidth||400),screen.width),
        BTTMenuPositionPreventOffscreen:1};
    if(main)Object.assign(patch,{BTTMenuPositioningType:1,BTTMenuPositionRelativeTo:1,
        BTTMenuAnchorMenu:0,BTTMenuAnchorRelation:0,BTTMenuOffsetX:0,BTTMenuOffsetY:0,
        BTTMenuOffsetXUnit:0,BTTMenuOffsetYUnit:0,BTTMenuFrameWidth:screen.width,
        BTTMenuWindowResizable:0});
    // Preserve existing drag handling. On this BTT 6.826 setup, adding
    // DisableDrag=1 broke held-modifier clicks; reverting only that flag fixed
    // them. Fixed screen anchoring does not require changing input handling.
    return patch;
}
function run(argv) {
    const mode=argv[0],repo=argv[1];
    const btt=Application('/Applications/BetterTouchTool.app');
    const recovery=stableLibrary(repo,'lib/media_recovery.js',
        '{media:CONTROL_MEDIA,speakers:CONTROL_SPEAKERS,archives:controlsArchives,restore:controlsRestoreSpeaker}');
    const notes=stableLibrary(repo,'install_notes.js','{specs:NOTES_MENUS,launcher:notesLauncher,check:notesCheckOpening}');
    const sizes=stableLibrary(repo,'fix_menu_sizes.js','{edits:sizeEdits,set:sizeSet}');
    function get(id) {
        const raw=JSON.parse(btt.get_trigger(id));
        const hits=(Array.isArray(raw)?raw:[raw]).filter(x=>x&&x.BTTUUID===id);
        if(hits.length!==1)throw new Error('Cannot export '+id);
        return hits[0];
    }
    if(mode==='plan') {
        const media=get(recovery.media),screen=stableScreen(),fullHeight=argv[2]==='full-height';
        const archives=recovery.archives(repo),entities=[],configChanges=[],dropdowns=[],sources=[];
        for(const spec of recovery.speakers) {
            const current=media.BTTMenuItems.find(x=>x.BTTUUID===spec.uuid);
            if(!current || Number(current.BTTTriggerType)!==774)throw new Error('Expected '+spec.title+' submenu.');
            const archive=archives.find(a=>(a.root.BTTMenuItems||[]).some(x=>x.BTTUUID===spec.uuid &&
                (x.BTTMenuItems||[]).length>=spec.minimum));
            if(!archive)throw new Error('No complete backup for '+spec.title);
            const wanted=recovery.restore(current,archive.root.BTTMenuItems.find(x=>x.BTTUUID===spec.uuid),sizes);
            stableCompactOrder(wanted);
            for(const child of wanted.BTTMenuItems||[])stableFlatten(child,spec.uuid,'item',entities);
            sources.push({uuid:spec.uuid,path:archive.path});
        }
        for(const spec of notes.specs) {
            const current=media.BTTMenuItems.find(x=>x.BTTUUID===spec.source);
            if(!current || Number(current.BTTTriggerType)!==773)throw new Error('Expected '+spec.title+' button.');
            const wanted=notes.launcher(spec,current);
            for(const action of wanted.BTTMenuItemActions)stableFlatten(action,spec.source,'action',entities);
            configChanges.push({uuid:spec.source,patch:{BTTMenuItemMinWidth:54,BTTMenuItemMaxWidth:78,
                BTTMenuItemScriptActive:0,BTTMenuScriptAlwaysRunOnAppear:0}});
        }
        const dropdownIDs=notes.specs.map(x=>x.menu).concat('C697E709-678E-56B9-9C17-4464F8E92145');
        for(const id of dropdownIDs) {
            const menu=get(id);dropdowns.push(menu);
            configChanges.push({uuid:id,patch:stableLayout(menu.BTTMenuConfig||{},screen,false,fullHeight)});
        }
        configChanges.push({uuid:recovery.media,patch:stableLayout(media.BTTMenuConfig||{},screen,true,false)});
        return JSON.stringify({version:1,media,dropdowns,entities,configChanges,screen,sources});
    }
    const plan=JSON.parse(stableRead(argv[2]));
    if(mode==='apply') {
        for(const entity of plan.create) {
            btt.add_new_trigger(JSON.stringify(entity.node),{parent_uuid:entity.parent});
        }
        const changes=plan.applyConfigChanges || plan.configChanges;
        for(const change of changes) {
            btt.update_menu_item(change.uuid,{json:JSON.stringify(change.patch),persist:true});
        }
        return 'Created '+plan.create.length+' missing records individually; saved '+changes.length+
            ' changed layout configurations with persist=true.';
    }
    if(mode==='verify') {
        const media=get(recovery.media);
        for(const spec of recovery.speakers) {
            const group=media.BTTMenuItems.find(x=>x.BTTUUID===spec.uuid);
            if(!group || (group.BTTMenuItems||[]).length<spec.minimum)throw new Error(spec.title+' still lacks its saved submenu.');
        }
        for(const spec of notes.specs) {
            const button=media.BTTMenuItems.find(x=>x.BTTUUID===spec.source);
            if(!button || !(button.BTTMenuItemActions||[]).some(x=>x.BTTUUID===spec.action && Number(x.BTTPredefinedActionType)===386)) {
                throw new Error(spec.title+' still lacks its saved Show Menu action.');
            }
        }
        return notes.check(btt);
    }
    throw new Error('Unknown worker mode.');
}
