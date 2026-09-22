// ARCHIVED: fixed three-item Tools layout from September 18, not the current menu.
// Default execution is blocked; --inspect is read-only.
// Match Tools action height/font to the parent Tools button; order Convert Video
// between the other two actions. Full verified private backup precedes any edit.
ObjC.import('Foundation');

const TOOLS_MEDIA = 'D9B0ED12-C4BE-4E74-B0DA-0CC3BE092289';
const TOOLS_PARENT = 'FC3F8235-F102-55C4-8432-B6ADFB0D9992';
const TOOLS_LEGACY = '7EA51E57-335B-5A4F-8B04-B6D5F8325EA0';
const TOOLS_ACTIONS = [
    {uuid:'9FEF3881-D2EC-5F35-A63A-D57A97A5099A', title:'Performance Audio'},
    {uuid:'C819638D-72E5-53E2-A94C-B7790C26DB47', title:'Convert Video'},
    {uuid:'B6C11844-111C-5283-B497-C88063BBC131', title:'Strip Metadata'}
];
const TOOLS_VOLATILE = new Set(['BTTLastUpdatedAt','BTTLastChangeUUID']);

function toolsClone(x) { return JSON.parse(JSON.stringify(x)); }
function toolsCanonical(x) {
    if (Array.isArray(x)) {
        const result=x.map(toolsCanonical);
        if(result.length && result.every(v=>v && typeof v==='object' && v.BTTUUID))
            result.sort((a,b)=>a.BTTUUID.localeCompare(b.BTTUUID));
        return result;
    }
    if(x && typeof x==='object') {
        const result={};
        Object.keys(x).sort().forEach(k=>{if(!TOOLS_VOLATILE.has(k))result[k]=toolsCanonical(x[k]);});
        return result;
    }
    return x;
}
function toolsFingerprint(x) {return JSON.stringify(toolsCanonical(x));}
function toolsPick(items,id) {
    const found=(items||[]).filter(x=>x.BTTUUID===id);
    if(found.length!==1)throw new Error('Expected one menu item '+id+'; no changes made.');
    return found[0];
}
function toolsVisible(x) {
    const c=x.BTTMenuConfig||{};
    return x.BTTUUID!==TOOLS_LEGACY &&
        ['BTTEnabled','BTTEnabled2'].every(k=>x[k]===undefined||Number(x[k])===1) &&
        !(Number(c.BTTMenuItemVisibleWhileActive)===0 && Number(c.BTTMenuItemVisibleWhileInactive)===0);
}
function toolsLabel(text,halfPoints) {
    return '{\\rtf1\\ansi{\\fonttbl{\\f0 HelveticaNeue;}}'+
        '{\\colortbl;\\red255\\green255\\blue255;}\\pard\\qc\\f0\\fs'+halfPoints+
        ' \\cf1 '+text+'}';
}
function toolsPlan(media) {
    const tools=toolsPick(media.BTTMenuItems,TOOLS_PARENT);
    if(Number(tools.BTTTriggerType)!==774 || !Array.isArray(tools.BTTMenuItems))
        throw new Error('Cannot read complete Tools submenu; no changes made.');
    const c=tools.BTTMenuConfig||{};
    const height=Number(c.BTTMenuItemMaxHeight);
    const fontMatch=String(c.BTTMenuAttributedText||'').match(/\\fs(\d+)/);
    if(!Number.isFinite(height)||height<24||height>80||!fontMatch)
        throw new Error('Cannot determine parent Tools height and RTF font size; no changes made.');
    const halfPoints=Number(fontMatch[1]);
    if(halfPoints<16||halfPoints>80)throw new Error('Unexpected parent font size; no changes made.');
    TOOLS_ACTIONS.forEach(spec=>{
        const item=toolsPick(tools.BTTMenuItems,spec.uuid);
        if(Number(item.BTTTriggerType)!==773||!toolsVisible(item))
            throw new Error('Expected enabled Tools action '+spec.title+'; no changes made.');
    });
    const ordered=tools.BTTMenuItems.filter(toolsVisible).slice().sort((a,b)=>Number(a.BTTOrder||0)-Number(b.BTTOrder||0));
    const back=ordered.filter(x=>Number(x.BTTTriggerType)===777);
    const body=ordered.filter(x=>Number(x.BTTTriggerType)!==777 && x.BTTUUID!==TOOLS_ACTIONS[1].uuid);
    // Keep the two existing actions in their relative order, moving Convert only.
    const anchors=body.filter(x=>x.BTTUUID===TOOLS_ACTIONS[0].uuid || x.BTTUUID===TOOLS_ACTIONS[2].uuid);
    body.splice(body.indexOf(anchors[0])+1,0,toolsPick(tools.BTTMenuItems,TOOLS_ACTIONS[1].uuid));
    const sequence=back.concat(body), orders=new Map(sequence.map((x,i)=>[x.BTTUUID,i]));
    const changes=[];
    for(const item of tools.BTTMenuItems) {
        const patch={};
        if(orders.has(item.BTTUUID) && Number(item.BTTOrder||0)!==orders.get(item.BTTUUID))
            patch.BTTOrder=orders.get(item.BTTUUID);
        const spec=TOOLS_ACTIONS.find(x=>x.uuid===item.BTTUUID);
        if(spec) {
            const width=Math.max(100,Math.round(spec.title.length*(halfPoints/2)*0.57+24));
            const config=Object.assign({},item.BTTMenuConfig||{}, {
                BTTMenuItemMinHeight:height, BTTMenuItemMaxHeight:height,
                BTTMenuItemMinWidth:width, BTTMenuItemMaxWidth:width,
                BTTMenuAttributedText:toolsLabel(spec.title,halfPoints), BTTMenuItemText:spec.title,
                BTTMenuTextMinimumScaleFactor:1
            });
            // Some exports nest sizing properties; update them consistently too.
            if(config.BTTMenuItemSizing && typeof config.BTTMenuItemSizing==='object') {
                config.BTTMenuItemSizing=Object.assign({},config.BTTMenuItemSizing,{
                    BTTMenuItemMinHeight:height,BTTMenuItemMaxHeight:height,
                    BTTMenuItemMinWidth:width,BTTMenuItemMaxWidth:width});
            }
            if(toolsFingerprint(config)!==toolsFingerprint(item.BTTMenuConfig||{}))patch.BTTMenuConfig=config;
        }
        if(Object.keys(patch).length)changes.push({uuid:item.BTTUUID,patch});
    }
    return {height,fontPoints:halfPoints/2,order:sequence.map(x=>x.BTTUUID),changes};
}
function toolsReadJSON(path) {
    const text=$.NSString.stringWithContentsOfFileEncodingError(path,$.NSUTF8StringEncoding,null);
    if(!text)throw new Error('Cannot read '+path);
    return JSON.parse(ObjC.unwrap(text));
}
function toolsBackup(snapshot) {
    const directory=ObjC.unwrap($('~/dev/scripts/tmp/btt-tools-style-'+ObjC.unwrap($.NSUUID.UUID.UUIDString)).stringByExpandingTildeInPath);
    const fm=$.NSFileManager.defaultManager,path=directory+'/backup.json';
    if(!fm.createDirectoryAtPathWithIntermediateDirectoriesAttributesError(directory,false,$({NSFilePosixPermissions:448}),null)||
        !$(JSON.stringify(snapshot,null,2)).writeToFileAtomicallyEncodingError(path,true,$.NSUTF8StringEncoding,null)||
        !fm.setAttributesOfItemAtPathError($({NSFilePosixPermissions:384}),path,null)||
        toolsFingerprint(toolsReadJSON(path))!==toolsFingerprint(snapshot))
        throw new Error('Private backup verification failed; no changes made.');
    return path;
}
function run(argv) {
    const historical = argv[0] === '--run-historical';
    if (historical) argv = argv.slice(1);
    if (!historical && argv[0] !== '--inspect') throw new Error('ARCHIVED Tools repair: review one_off_fixes/README.md before --run-historical.');
    if(argv.length>1 || (argv.length && !['--apply','--inspect'].includes(argv[0])))
        throw new Error('Usage: fix_tools_menu.js [--apply | --inspect]');
    const btt=Application('/Applications/BetterTouchTool.app');
    function getRoot() {
        const value=JSON.parse(btt.get_trigger(TOOLS_MEDIA));
        const root=toolsPick(Array.isArray(value)?value:[value],TOOLS_MEDIA);
        if(Number(root.BTTTriggerType)!==767||!Array.isArray(root.BTTMenuItems))
            throw new Error('Cannot read full Media export; no changes made.');
        return root;
    }
    const original=getRoot(),plan=toolsPlan(original);
    if(argv[0]==='--inspect')return JSON.stringify(plan,null,2);
    if(!plan.changes.length)return 'Tools height, text size and order already match.';
    const expected=toolsClone(original),items=toolsPick(expected.BTTMenuItems,TOOLS_PARENT).BTTMenuItems;
    plan.changes.forEach(change=>Object.assign(toolsPick(items,change.uuid),change.patch));
    const backup=toolsBackup({version:1,mediaExport:original,plan});
    if(toolsFingerprint(getRoot())!==toolsFingerprint(original))throw new Error('Media changed during backup; no changes made.');
    for(const change of plan.changes) {
        btt.update_trigger(change.uuid,{trigger_parent_uuid:TOOLS_PARENT,
            json:JSON.stringify(Object.assign({BTTTriggerParentUUID:TOOLS_PARENT},change.patch))});
    }
    const actual=getRoot();
    if(toolsFingerprint(actual)!==toolsFingerprint(expected))
        throw new Error('Readback differs from the planned Tools-only changes. Backup: '+backup);
    if(toolsPlan(actual).changes.length)throw new Error('Tools style/order did not settle. Backup: '+backup);
    return 'Verified Tools: '+plan.height+' px high, '+plan.fontPoints+' pt single-line text; Convert Video between the other two actions. Backup: '+backup;
}
