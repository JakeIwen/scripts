const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {execFileSync} = require('node:child_process');
const folder = path.join(__dirname, '../bettertouchtool');
execFileSync('/usr/bin/python3', [path.join(folder, 'install_rhythm_menu.py')]);
const payload = JSON.parse(fs.readFileSync(path.join(__dirname, '../build/Rhythm Practice Tools.json')));
const code = fs.readFileSync(path.join(folder, 'install_rhythm_menu.js'), 'utf8');

function setup({backupFails = false, badReadback = false, race = false} = {}) {
    const sibling = {BTTUUID:'strip',BTTTriggerType:773,BTTOrder:3,BTTMenuName:'strip-image-metadata',
        BTTMenuItemActions:[{BTTShellTaskActionScript:'keep existing command'}]};
    const tools = {BTTUUID:payload.tools_uuid,BTTTriggerType:774,BTTMenuName:'image-tools',
        BTTMenuConfig:{BTTMenuAttributedText:'{\\rtf1\\fs28 Tools}',BTTMenuItemMaxHeight:40},
        BTTMenuItems:[sibling,{BTTUUID:'audio',BTTTriggerType:773,BTTOrder:7}]};
    const media = {BTTUUID:payload.media_uuid,BTTTriggerType:767,BTTMenuItems:[tools,
        {BTTUUID:'entry-omitted-by-filtered-api',BTTTriggerType:774,BTTMenuItems:[{BTTUUID:'nested-private-data'}]}]};
    let snapshot, writes = 0, reads = 0, appearanceWrites=0;
    const refreshes=[];
    const $ = value => ({writeToFileAtomicallyEncodingError() {snapshot=JSON.parse(value);return !backupFails;}});
    $.NSString={stringWithContentsOfFileEncodingError: p => p==='payload' ? JSON.stringify(payload) : JSON.stringify(snapshot)};
    $.NSUUID={UUID:{UUIDString:'test-backup'}};
    $.NSFileManager={defaultManager:{createDirectoryAtPathWithIntermediateDirectoriesAttributesError:()=>true,
        setAttributesOfItemAtPathError:()=>true}};
    const btt = {
        get_trigger(id) {
            assert.equal(id,payload.media_uuid); reads++;
            if (race && reads===2) tools.BTTMenuItems.push({BTTUUID:'concurrent-change',BTTOrder:9});
            return JSON.stringify([media]);
        },
        get_triggers() {throw Error('Filtered child queries must not be used');},
        add_new_trigger(json,{parent_uuid}) {
            assert.equal(parent_uuid,tools.BTTUUID); writes++;
            const item=JSON.parse(json);assert.equal(item.BTTOrder,8);
            if (badReadback)item.BTTMenuItemActions=[];
            tools.BTTMenuItems.push(item);
        },
        update_trigger(id,{json,trigger_parent_uuid}) {
            assert.equal(trigger_parent_uuid,tools.BTTUUID);writes++;
            const i=tools.BTTMenuItems.findIndex(x=>x.BTTUUID===id);
            tools.BTTMenuItems[i]={...tools.BTTMenuItems[i],...JSON.parse(json)};
        },
        update_menu_item(id,{json,persist}) {
            assert.equal(persist,true);writes++;appearanceWrites++;
            tools.BTTMenuItems.find(x=>x.BTTUUID===id).BTTMenuConfig=JSON.parse(json);
        },
        trigger_action(json){refreshes.push(JSON.parse(json));}
    };
    const context=vm.createContext({$,ObjC:{import(){},unwrap:x=>x},Application:()=>btt});
    vm.runInContext(code,context);
    return {run: (mode,refresh)=>context.run(['payload','/build',mode||'apply',refresh?'refresh':'']),media,tools,sibling,
        writes:()=>writes,snapshot:()=>snapshot,appearanceWrites:()=>appearanceWrites,refreshes};
}

test('appends one Tools item with full verified backup, preserves siblings and reruns as no-op',()=>{
    const env=setup(),before=JSON.stringify(env.sibling);
    assert.match(env.run(),/Verified configuration/);
    assert.equal(env.writes(),1);
    assert.equal(JSON.stringify(env.sibling),before);
    assert.equal(env.snapshot().media.BTTMenuItems[1].BTTMenuItems[0].BTTUUID,'nested-private-data');
    assert.match(env.run(),/Already configured/);
    assert.equal(env.writes(),1);
});

test('refreshes only the owned button while preserving its position',()=>{
    const env=setup();env.run();
    const item=env.tools.BTTMenuItems.find(x=>x.BTTUUID===payload.item.BTTUUID);
    item.BTTOrder=27;item.BTTMenuItemActions[0].BTTShellTaskActionScript='old';
    env.run();
    assert.equal(env.tools.BTTMenuItems.find(x=>x.BTTUUID===payload.item.BTTUUID).BTTOrder,27);
    assert.equal(env.writes(),2);
});

test('backup failure and concurrent menu edit fail before mutations',()=>{
    for(const options of [{backupFails:true},{race:true}]){
        const env=setup(options);assert.throws(()=>env.run(),/no changes made/);assert.equal(env.writes(),0);
    }
});

test('detects failed readback and name collisions rather than claiming success',()=>{
    const failed=setup({badReadback:true});assert.throws(()=>failed.run(),/verification failed/);
    const collision=setup();collision.tools.BTTMenuItems.push({BTTUUID:'foreign',BTTMenuName:'Rhythm Practice'});
    assert.throws(()=>collision.run(),/Conflicting/);assert.equal(collision.writes(),0);
});

test('inspect exposes only menu identity/state, not action commands',()=>{
    const env=setup();const result=env.run('inspect');
    assert.equal(JSON.parse(result).installed,false);
    assert.doesNotMatch(result,/keep existing command/);assert.equal(env.writes(),0);
});

test('repairs oversized existing font using appearance API, retaining action and order',()=>{
    const env=setup();env.run();
    const item=env.tools.BTTMenuItems.find(x=>x.BTTUUID===payload.item.BTTUUID);
    const actions=JSON.stringify(item.BTTMenuItemActions);
    item.BTTMenuConfig.BTTMenuAttributedText='{\\rtf1\\fs50 Rhythm Practice}';
    item.BTTMenuConfig.BTTMenuItemMinWidth=238;item.BTTMenuConfig.BTTMenuItemMaxWidth=238;
    item.BTTMenuConfig.BTTMenuItemVisibleWhileInactive=0;
    env.run();
    assert.equal(env.appearanceWrites(),1);
    assert.match(item.BTTMenuConfig.BTTMenuAttributedText,/\\fs28\b/);
    assert.equal(item.BTTMenuConfig.BTTMenuItemMaxWidth,144);
    assert.equal(item.BTTMenuConfig.BTTMenuItemVisibleWhileInactive,1);
    assert.equal(JSON.stringify(item.BTTMenuItemActions),actions);
    assert.equal(item.BTTOrder,8);
});

test('refresh releases only Media cache without firing hovered item, even on otherwise no-op',()=>{
    const env=setup();env.run();env.run('apply',true);
    assert.equal(env.writes(),1);
    assert.deepEqual(env.refreshes.map(x=>x.BTTPredefinedActionType),[387,386]);
    assert.ok(env.refreshes.every(x=>x.BTTAdditionalActionData.BTTMenuActionMenuID===payload.media_uuid));
    assert.equal(env.refreshes[0].BTTAdditionalActionData.BTTMenuActionReleaseFromMemory,1);
    assert.equal(env.refreshes[0].BTTAdditionalActionData.BTTMenuActionTriggerHoveredOnHide,0);
});

test('matches live BPM sibling font rather than obsolete defaults',()=>{
    const env=setup();env.tools.BTTMenuItems.push({BTTUUID:'554C41C9-C964-5875-B916-EF9D2DFB2004',
        BTTTriggerType:773,BTTOrder:2,BTTMenuConfig:{BTTMenuAttributedText:'{\\rtf1\\fs32 BPM Over Time}',BTTMenuItemMaxHeight:42}});
    env.run();const item=env.tools.BTTMenuItems.find(x=>x.BTTUUID===payload.item.BTTUUID);
    assert.match(item.BTTMenuConfig.BTTMenuAttributedText,/\\fs32\b/);assert.equal(item.BTTMenuConfig.BTTMenuItemMaxHeight,42);
});
