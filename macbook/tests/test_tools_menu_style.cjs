const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const code=fs.readFileSync(path.join(__dirname,'../bettertouchtool/one_off_fixes/2026-09-18-repair-tools-menu-placement.js'),'utf8');
const MEDIA='D9B0ED12-C4BE-4E74-B0DA-0CC3BE092289',TOOLS='FC3F8235-F102-55C4-8432-B6ADFB0D9992';
const AUDIO='9FEF3881-D2EC-5F35-A63A-D57A97A5099A',CONVERT='C819638D-72E5-53E2-A94C-B7790C26DB47',STRIP='B6C11844-111C-5283-B497-C88063BBC131';
const LEGACY='7EA51E57-335B-5A4F-8B04-B6D5F8325EA0';
function clone(x){return JSON.parse(JSON.stringify(x));}
function setup(failure){
    function item(id,order,title){return {BTTUUID:id,BTTTriggerParentUUID:TOOLS,BTTTriggerType:773,BTTOrder:order,
        BTTMenuName:title,BTTMenuConfig:{BTTMenuItemMinHeight:60,BTTMenuItemMaxHeight:60,
            BTTMenuItemMinWidth:100,BTTMenuItemMaxWidth:120,BTTMenuAttributedText:'{\\rtf1\\fs30 two\\line lines}',
            BTTMenuElementIdentifier:title,customColor:'preserve'},
        BTTMenuItemActions:[{BTTUUID:'action-'+id,BTTPredefinedActionType:206,BTTShellTaskActionScript:'keep '+id}]};}
    const tools={BTTUUID:TOOLS,BTTTriggerParentUUID:MEDIA,BTTTriggerType:774,BTTOrder:18,BTTMenuName:'image-tools',
        BTTMenuConfig:{BTTMenuItemMinHeight:40,BTTMenuItemMaxHeight:40,BTTMenuAttributedText:'{\\rtf1\\fs50 Tools}'},
        BTTMenuItems:[item(CONVERT,0,'convert-video'),item(AUDIO,0,'audio'),
            {BTTUUID:'back',BTTTriggerParentUUID:TOOLS,BTTTriggerType:777,BTTOrder:0},
            {BTTUUID:LEGACY,BTTTriggerParentUUID:TOOLS,BTTTriggerType:774,BTTOrder:0,BTTEnabled:0,
                BTTMenuItems:[{BTTUUID:'old-child',BTTTriggerType:773}]},item(STRIP,1,'strip')]};
    const root={BTTUUID:MEDIA,BTTTriggerType:767,BTTMenuConfig:{BTTMenuFrameWidth:3736,BTTMenuFrameHeight:120},
        BTTMenuItems:[{BTTUUID:'unrelated',BTTMenuConfig:{font:'unchanged'},BTTOrder:17},tools]};
    const original=clone(root),files=new Map(),events=[];let reads=0;
    function $(value){return {value,stringByExpandingTildeInPath:value,writeToFileAtomicallyEncodingError(file){
        events.push('backup');files.set(file,value);return failure!=='backup';}};}
    $.NSString={stringWithContentsOfFileEncodingError:file=>failure==='readback'?'{}':files.get(file)};
    $.NSUUID={UUID:{UUIDString:'test-backup'}};
    $.NSFileManager={defaultManager:{createDirectoryAtPathWithIntermediateDirectoriesAttributesError(dir,recursive,attrs){
        assert.equal(attrs.value.NSFilePosixPermissions,448);return failure!=='directory';},
        setAttributesOfItemAtPathError(attrs){assert.equal(attrs.value.NSFilePosixPermissions,384);return failure!=='permissions';}}};
    const btt={get_trigger(){reads++;const result=clone(root);result.BTTMenuItems.reverse();
        if(failure==='race'&&reads===2)result.BTTMenuConfig.BTTMenuFrameWidth++;
        return JSON.stringify(reads%2?result:[result]);},update_trigger(id,{json,trigger_parent_uuid}){
        assert.equal(trigger_parent_uuid,TOOLS);assert(events.includes('backup'));events.push('update');
        const patch=JSON.parse(json);assert(Object.keys(patch).every(k=>['BTTTriggerParentUUID','BTTOrder','BTTMenuConfig'].includes(k)));
        const target=tools.BTTMenuItems.find(x=>x.BTTUUID===id);assert(target);Object.assign(target,patch);
        target.BTTLastUpdatedAt=1234; if(failure==='side-effect')root.BTTMenuConfig.BTTMenuFrameHeight++;
    }};
    const context=vm.createContext({$,ObjC:{import(){},unwrap:x=>x},Application:()=>btt});
    vm.runInContext(code,context);
    return {run:mode=>context.run(['--run-historical',...(mode?[mode]:[])]),root,tools,original,events,files};
}

test('matches parent size/font and puts Convert between Audio and Strip, preserving actions/other menus',()=>{
    const env=setup();assert.match(env.run(),/40 px high, 25 pt/);
    const active=env.tools.BTTMenuItems.filter(x=>x.BTTUUID!==LEGACY).sort((a,b)=>a.BTTOrder-b.BTTOrder);
    assert.deepEqual(active.map(x=>x.BTTUUID),['back',AUDIO,CONVERT,STRIP]);
    for(const id of [AUDIO,CONVERT,STRIP]){
        const now=active.find(x=>x.BTTUUID===id),old=env.original.BTTMenuItems[1].BTTMenuItems.find(x=>x.BTTUUID===id);
        assert.equal(now.BTTMenuConfig.BTTMenuItemMinHeight,40);assert.equal(now.BTTMenuConfig.BTTMenuItemMaxHeight,40);
        assert.match(now.BTTMenuConfig.BTTMenuAttributedText,/\\fs50/);
        assert.doesNotMatch(now.BTTMenuConfig.BTTMenuItemText,/\n/);
        assert.equal(now.BTTMenuConfig.customColor,'preserve');assert.deepEqual(now.BTTMenuItemActions,old.BTTMenuItemActions);
    }
    assert.deepEqual(env.root.BTTMenuConfig,env.original.BTTMenuConfig);
    assert.deepEqual(env.root.BTTMenuItems[0],env.original.BTTMenuItems[0]);
    assert.deepEqual(env.tools.BTTMenuItems.find(x=>x.BTTUUID===LEGACY),env.original.BTTMenuItems[1].BTTMenuItems.find(x=>x.BTTUUID===LEGACY));
    assert.equal(env.events.filter(x=>x==='update').length,3);
    const savedTools=JSON.parse([...env.files.values()][0]).mediaExport.BTTMenuItems.find(x=>x.BTTUUID===TOOLS);
    assert.equal(savedTools.BTTMenuItems.find(x=>x.BTTUUID===LEGACY).BTTMenuItems[0].BTTUUID,'old-child');
    const before=env.events.length;assert.match(env.run(),/already match/);assert.equal(env.events.length,before);
});
test('uses current parent typography rather than hardcoding historical measurements',()=>{
    const env=setup();env.tools.BTTMenuConfig.BTTMenuItemMaxHeight=44;
    env.tools.BTTMenuConfig.BTTMenuAttributedText='{\\rtf1\\fs44 Tools}';
    assert.match(env.run(),/44 px high, 22 pt/);
});
test('inspect performs no backup or updates',()=>{
    const env=setup();const plan=JSON.parse(env.run('--inspect'));assert.equal(plan.changes.length,3);assert.equal(env.events.length,0);
});
for(const failure of ['directory','backup','readback','permissions','race'])test(failure+' prevents all writes',()=>{
    const env=setup(failure);assert.throws(()=>env.run());assert(!env.events.includes('update'));
});
test('detects unexpected changes outside Tools styling/order',()=>{
    assert.throws(()=>setup('side-effect').run(),/planned Tools-only changes/);
});
test('refuses a missing target rather than recreating it',()=>{
    const env=setup();env.tools.BTTMenuItems=env.tools.BTTMenuItems.filter(x=>x.BTTUUID!==CONVERT);
    assert.throws(()=>env.run(),/Expected one menu item/);assert.equal(env.events.length,0);
});
