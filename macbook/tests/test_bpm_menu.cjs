const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {execFileSync} = require('node:child_process');
const folder = path.join(__dirname, '../bettertouchtool');
execFileSync('/usr/bin/python3', [path.join(folder, 'install_bpm_menu.py')]);
const payload = JSON.parse(fs.readFileSync(path.join(__dirname, '../build/BPM Over Time Tools.json')));
const code = fs.readFileSync(path.join(folder, 'install_bpm_menu.js'), 'utf8');

function setup({backupFails = false, badReadback = false, race = false} = {}) {
    const sibling = {BTTUUID:'strip',BTTTriggerType:773,BTTOrder:3,BTTMenuName:'strip-image-metadata',
        BTTMenuItemActions:[{BTTShellTaskActionScript:'keep existing command'}]};
    const tools = {BTTUUID:payload.tools_uuid,BTTTriggerType:774,BTTMenuName:'image-tools',
        BTTMenuItems:[sibling,{BTTUUID:'audio',BTTTriggerType:773,BTTOrder:7}]};
    const media = {BTTUUID:payload.media_uuid,BTTTriggerType:767,BTTMenuItems:[tools,
        {BTTUUID:'entry-omitted-by-filtered-api',BTTTriggerType:774,BTTMenuItems:[{BTTUUID:'nested-private-data'}]}]};
    let snapshot, writes = 0, reads = 0;
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
        }
    };
    const context=vm.createContext({$,ObjC:{import(){},unwrap:x=>x},Application:()=>btt});
    vm.runInContext(code,context);
    return {run: mode=>context.run(['payload','/build',mode||'apply']),media,tools,sibling,
        writes:()=>writes,snapshot:()=>snapshot};
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
    const collision=setup();collision.tools.BTTMenuItems.push({BTTUUID:'foreign',BTTMenuName:'BPM Over Time'});
    assert.throws(()=>collision.run(),/Conflicting/);assert.equal(collision.writes(),0);
});

test('inspect exposes only menu identity/state, not action commands',()=>{
    const env=setup();const result=env.run('inspect');
    assert.equal(JSON.parse(result).installed,false);
    assert.doesNotMatch(result,/keep existing command/);assert.equal(env.writes(),0);
});
