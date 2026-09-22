// Worker for repair_rps.py. Only creates the missing child action; never runs it.
ObjC.import('Foundation');
const RPS_MEDIA = 'D9B0ED12-C4BE-4E74-B0DA-0CC3BE092289';
const RPS_BUTTON = '103D7824-47C1-4B1B-9106-71E4997BCB58';
const RPS_ACTION = '525212E9-F6F3-4CF0-A9F7-86C1A52EEB30';
const RPS_SYNC = 'DA200AE7-BB01-4AB5-89A7-CF1587728D7D';

function rpsAction() {
    return {BTTUUID:RPS_ACTION,BTTTriggerParentUUID:RPS_BUTTON,
        BTTTriggerType:-1,BTTTriggerClass:'BTTTriggerTypeFloatingMenu',
        BTTEnabled:1,BTTEnabled2:1,BTTOrder:0,BTTActionCategory:0,
        BTTPredefinedActionType:248,
        BTTPredefinedActionName:'Trigger Named Trigger (Configured in Other Tab)',
        BTTNamedTriggerToTrigger:'Sync RPi Scripts'};
}
function rpsButton(media) {
    if (!media || media.BTTUUID!==RPS_MEDIA || Number(media.BTTTriggerType)!==767 ||
        !Array.isArray(media.BTTMenuItems)) throw new Error('Cannot export complete Media menu.');
    const matches=media.BTTMenuItems.filter(x=>x.BTTUUID===RPS_BUTTON);
    if(matches.length!==1 || Number(matches[0].BTTTriggerType)!==773)throw new Error('RPS button missing or changed.');
    return matches[0];
}
function rpsApply(btt, snapshot, fingerprint) {
    function get(id) {
        const raw=JSON.parse(btt.get_trigger(id));
        const hits=(Array.isArray(raw)?raw:[raw]).filter(x=>x&&x.BTTUUID===id);
        if(hits.length!==1)throw new Error('Cannot export '+id);
        return hits[0];
    }
    const before=get(RPS_MEDIA),sync=get(RPS_SYNC),button=rpsButton(before);
    if(fingerprint(before)!==fingerprint(snapshot.media) || fingerprint(sync)!==fingerprint(snapshot.sync)) {
        throw new Error('BTT changed during backup; no changes made.');
    }
    if((button.BTTMenuItemActions||[]).length || (button.BTTAdditionalActions||[]).length) {
        throw new Error('RPS already has runtime actions; refusing to duplicate or replace them.');
    }
    btt.add_new_trigger(JSON.stringify(rpsAction()),{parent_uuid:RPS_BUTTON});
    const after=get(RPS_MEDIA),restored=rpsButton(after),actions=restored.BTTMenuItemActions||[];
    if(actions.length!==1 || actions[0].BTTUUID!==RPS_ACTION ||
        Number(actions[0].BTTPredefinedActionType)!==248 ||
        actions[0].BTTNamedTriggerToTrigger!=='Sync RPi Scripts' ||
        Number(actions[0].BTTActionCategory||0)!==0 || Number(actions[0].BTTOrder||0)!==0 ||
        Number(actions[0].BTTEnabled===undefined?1:actions[0].BTTEnabled)!==1) {
        throw new Error('Restored RPS action failed API readback.');
    }
    // Ignore only the deliberately added action when checking the existing tree.
    if(Object.prototype.hasOwnProperty.call(button,'BTTMenuItemActions'))restored.BTTMenuItemActions=[];
    else delete restored.BTTMenuItemActions;
    if(fingerprint(after)!==fingerprint(before) || fingerprint(get(RPS_SYNC))!==fingerprint(sync)) {
        throw new Error('Unexpected change outside the restored RPS action.');
    }
    return 'Created and verified the RPS action. No sync executed.';
}
function run(argv) {
    const mode=argv[0],repo=argv[1];
    function read(path) {
        const text=$.NSString.stringWithContentsOfFileEncodingError(path,$.NSUTF8StringEncoding,null);
        if(!text)throw new Error('Cannot read '+path);
        return ObjC.unwrap(text);
    }
    const btt=Application('/Applications/BetterTouchTool.app');
    if(mode==='snapshot') {
        function get(id) {
            const raw=JSON.parse(btt.get_trigger(id));
            const hits=(Array.isArray(raw)?raw:[raw]).filter(x=>x&&x.BTTUUID===id);
            if(hits.length!==1)throw new Error('Cannot export '+id);
            return hits[0];
        }
        const media=get(RPS_MEDIA);rpsButton(media);
        return JSON.stringify({media,sync:get(RPS_SYNC)});
    }
    if(mode!=='apply')throw new Error('Unknown worker mode.');
    const fingerprint=new Function(read(repo+'/macbook/bettertouchtool/btt_common.js')+
        '\nreturn BTTCommon.fingerprint;')();
    return rpsApply(btt,JSON.parse(read(argv[2])),fingerprint);
}
