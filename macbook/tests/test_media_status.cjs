const test = require('node:test'), assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path'), vm = require('node:vm');
const dir = path.join(__dirname, '../bettertouchtool');
const script = fs.readFileSync(path.join(dir, 'play_status.js'), 'utf8');
const installer = fs.readFileSync(path.join(dir, 'tune_media_status.js'), 'utf8');
const helpers = require('./btt_test_support.cjs').withCommon(fs.readFileSync(path.join(dir, 'fix_menu_sizes.js'), 'utf8'));

for (const [result, expected] of [['', ''], [' \n', ''],
    ['A Movie\n01:02 / 1:23:45', 'A Movie\n01:02 / 1:23:45'],
    ['Paused\n01:02 / 1:23:45', 'Paused\n01:02 / 1:23:45'],
    ['__BTT_PLAY_STATUS_UNAVAILABLE__', 'Status unavailable'], [null, '']]) {
    test('status display: ' + JSON.stringify(result), async () => {
        const context = vm.createContext({runShellScript: async options => {
            assert(options.script.includes("'play_status 2>/dev/null' 2>/dev/null"));
            assert(options.script.includes('BatchMode=yes'));
            assert(options.script.includes('pi@vanpi.lan'));
            return result;
        }});
        vm.runInContext(script, context);
        assert.deepEqual(JSON.parse(await context.itemScript('unused')), {BTTMenuItemText: expected});
    });
}
test('failed query never renders exception or stderr text in the button', async () => {
    const context = vm.createContext({runShellScript: async () => {throw Error('curl: (7) Failed to connect');}});
    vm.runInContext(script, context);
    assert.deepEqual(JSON.parse(await context.itemScript('unused')), {BTTMenuItemText:'Status unavailable'});
});

function fixture(failure) {
    const original = {BTTUUID:'D9B0ED12-C4BE-4E74-B0DA-0CC3BE092289', BTTTriggerType:767,
        BTTMenuConfig:{BTTMenuVerticalSpacing:5,BTTMenuHorizontalSpacing:5,BTTMenuDisableDrag:0,
            BTTMenuSizingBehavior:3,BTTMenuModifierKeys:1835008},
        BTTMenuItems:[{BTTUUID:'BDFEAEF1-6961-4B05-9A62-E70C54332C4C',BTTTriggerType:773,BTTOrder:6,
            BTTMenuConfig:{BTTMenuElementIdentifier:'PPause',BTTMenuItemScriptActive:1,
                BTTMenuScriptUpdateInterval:2,BTTMenuItemMinWidth:80,BTTMenuItemMaxWidth:250,
                BTTMenuScriptSettings:{BTTScriptType:3,BTTAppleScriptString:'ssh pi@vanpi.local play_status',
                    BTTScriptFunctionToCall:'itemScript',BTTScriptLocation:0}},
            BTTMenuItemActions:[{BTTUUID:'play-action',BTTNamedTriggerToTrigger:'PlayPause'}]},
            {BTTUUID:'submenu',BTTTriggerType:774,BTTMenuItems:[{BTTUUID:'child'}]}]};
    let current=structuredClone(original),reads=0;const events=[];
    const context=vm.createContext({ObjC:{import(){}}});vm.runInContext(helpers+'\n'+installer,context);
    const btt={get_trigger(){reads++;if(failure==='race'&&reads===2)current.BTTMenuConfig.BTTMenuHorizontalSpacing=7;
        return JSON.stringify([current]);},update_menu_item(uuid,{json,persist}){
        assert.equal(persist,true);events.push('write');
        const item=uuid===current.BTTUUID?current:current.BTTMenuItems.find(x=>x.BTTUUID===uuid);
        Object.assign(item.BTTMenuConfig,JSON.parse(json));
        if(failure==='readback')current.BTTMenuItems[0].BTTMenuItemActions=[];
    },update_trigger(){throw Error('must not replace trigger trees');}};
    return {original,current:()=>structuredClone(current),events,
        run(inspect=false){return context.statusApply(btt,script,snapshot=>{
            events.push('backup');assert.deepEqual(JSON.parse(JSON.stringify(snapshot.media)),current);
            if(failure==='backup')throw Error('backup failed');return '/private/backup.json';
        },context.sizeFingerprint,inspect);},
        setSpacing(value){current.BTTMenuConfig.BTTMenuVerticalSpacing=value;}};
}
test('backup before two style-only writes; preserve all other data; rerun does not halve again',()=>{
    const f=fixture();assert.match(f.run(),/Saved clean playback status/);
    assert.deepEqual(f.events,['backup','write','write']);
    const expected=structuredClone(f.original);expected.BTTMenuConfig.BTTMenuVerticalSpacing=2.5;
    expected.BTTMenuItems[0].BTTMenuConfig.BTTMenuScriptSettings.BTTAppleScriptString=script;
    assert.deepEqual(f.current(),expected);assert.match(f.run(),/Already configured/);
    assert.deepEqual(f.events,['backup','write','write']);
});
test('inspect is read-only',()=>{const f=fixture();assert.equal(JSON.parse(f.run(true)).changes,2);assert.deepEqual(f.events,[]);});
for(const failure of ['race','backup'])test(failure+' prevents mutations',()=>{
    const f=fixture(failure);assert.throws(()=>f.run());assert.deepEqual(f.events,['backup']);
});
test('unexpected manual spacing is not overwritten',()=>{
    const f=fixture();f.setSpacing(7);assert.throws(()=>f.run(),/row spacing differs/);assert.deepEqual(f.events,[]);
});
test('lost action fails readback',()=>{const f=fixture('readback');assert.throws(()=>f.run(),/Readback differs/);});
