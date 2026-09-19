const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../bettertouchtool/fix_menu_sizes.js'), 'utf8');
const media = 'D9B0ED12-C4BE-4E74-B0DA-0CC3BE092289';
const clone = x => JSON.parse(JSON.stringify(x));

function setup(failure) {
    const root = {BTTUUID:media, BTTTriggerType:767, BTTMenuConfig:{
        BTTMenuFrameWidth:3736, BTTMenuFrameHeight:120, BTTMenuLayoutDirection:0}, BTTMenuItems:[
        {BTTUUID:'folder', BTTTriggerParentUUID:media, BTTTriggerType:774, BTTOrder:4,
            BTTMenuConfig:{BTTMenuItemMinWidth:50, BTTMenuItemMaxWidth:40, BTTMenuItemMinHeight:40,
                BTTMenuItemMaxHeight:40, BTTMenuAttributedText:'two-line label', BTTMenuElementIdentifier:'folder'},
            BTTMenuItems:[{BTTUUID:'child', BTTTriggerParentUUID:'folder', BTTTriggerType:773, BTTOrder:3,
                BTTMenuConfig:{BTTMenuItemSizing:{BTTMenuItemMinWidth:60, BTTMenuItemMaxWidth:40}},
                BTTMenuItemActions:[{BTTUUID:'action', BTTOrder:0, payload:'preserve'}]}]},
        {BTTUUID:'notes', BTTTriggerParentUUID:media, BTTTriggerType:773, BTTOrder:8,
            BTTMenuConfig:{BTTMenuItemMinWidth:90,BTTMenuItemMaxWidth:130,BTTMenuItemMinHeight:40,BTTMenuItemMaxHeight:40,
                BTTMenuAttributedText:'Recent\\line Notes',BTTMenuItemText:'Recent\nNotes'}},
        {BTTUUID:'breaker',BTTTriggerParentUUID:media,BTTTriggerType:801,BTTOrder:6},
        {BTTUUID:'height',BTTTriggerParentUUID:media,BTTTriggerType:773,BTTOrder:10,
            BTTMenuConfig:{BTTMenuItemMinWidth:40,BTTMenuItemMaxWidth:40,BTTMenuItemMinHeight:60,BTTMenuItemMaxHeight:30}},
        {BTTUUID:'default',BTTTriggerParentUUID:media,BTTTriggerType:773,BTTOrder:11,
            BTTMenuConfig:{BTTMenuItemMinWidth:60,BTTMenuItemMaxWidth:0}}
    ]};
    const original = clone(root), events = [], files = new Map(); let serial=0,reads=0,updates=0;
    function $(value){return {value,stringByExpandingTildeInPath:value,writeToFileAtomicallyEncodingError(file){
        events.push('backup-write');if(failure==='write')return false;files.set(file,value);return true;}};}
    $.NSString={stringWithContentsOfFileEncodingError(file){events.push('backup-read');return failure==='readback'?'{}':files.get(file);}};
    $.NSUUID={UUID:{get UUIDString(){return 'fixture-'+(++serial);}}};
    $.NSFileManager={defaultManager:{createDirectoryAtPathWithIntermediateDirectoriesAttributesError(dir,parents,attrs){
        assert.equal(attrs.value.NSFilePosixPermissions,448);return true;},setAttributesOfItemAtPathError(attrs){
        assert.equal(attrs.value.NSFilePosixPermissions,384);return failure!=='permissions';}}};
    function find(item,id){if(item.BTTUUID===id)return item;for(const child of item.BTTMenuItems||[]){const x=find(child,id);if(x)return x;}}
    const btt={get_trigger(){reads++;const result=clone(root);result.BTTMenuItems.reverse();
        if(failure==='race'&&reads===2)result.BTTMenuConfig.BTTMenuFrameHeight=130;
        return JSON.stringify(reads%2?[result]:result);},update_menu_item(id,{json,persist}){
        const item=find(root,id);assert(item);const patch=JSON.parse(json);
        assert.equal(persist,true);
        assert(!patch.BTTMenuItems&&!patch.BTTMenuItemActions,'style API must not receive trigger trees');
        assert(events.includes('backup-read'));events.push('update');updates++;
        item.BTTMenuConfig=patch;item.BTTLastUpdatedAt=1234+updates;
        if(failure==='side-effect')root.BTTMenuConfig.BTTMenuFrameHeight=666;
    }};
    const context=vm.createContext({$,ObjC:{import(){},unwrap(x){return x;}},Application(){return btt;},console:{log(){}}});
    vm.runInContext(source,context);
    function run(argv=[]){context.args=argv;return vm.runInContext('run(args)',context);}
    return {run,root,original,files,events,context};
}

test('fixes only invalid positive bounds; preserves custom sizes, parent, order, font, actions and row break',()=>{
    const s=setup();assert.match(s.run(),/Repaired conflicting bounds on 3 items/);
    assert.equal(s.root.BTTMenuItems[0].BTTMenuConfig.BTTMenuItemMaxWidth,50);
    assert.equal(s.root.BTTMenuItems[0].BTTMenuItems[0].BTTMenuConfig.BTTMenuItemSizing.BTTMenuItemMaxWidth,60);
    assert.equal(s.root.BTTMenuItems[3].BTTMenuConfig.BTTMenuItemMaxHeight,60);
    assert.deepEqual(s.root.BTTMenuConfig,s.original.BTTMenuConfig);
    assert.deepEqual(s.root.BTTMenuItems[1],s.original.BTTMenuItems[1]);
    assert.deepEqual(s.root.BTTMenuItems[2],s.original.BTTMenuItems[2]);
    assert.deepEqual(s.root.BTTMenuItems[4],s.original.BTTMenuItems[4]);
    assert.deepEqual(JSON.parse([...s.files.values()][0]).mediaExport.BTTMenuItems.slice().sort((a,b)=>a.BTTUUID.localeCompare(b.BTTUUID)),
        s.original.BTTMenuItems.slice().sort((a,b)=>a.BTTUUID.localeCompare(b.BTTUUID)));
    const n=s.events.length;assert.match(s.run(),/No sizing changes/);assert.equal(s.events.length,n);
});
test('inspect makes no backup or changes',()=>{
    const s=setup();const report=JSON.parse(s.run(['--inspect']));assert.equal(report.conflicts,3);assert.equal(s.events.length,0);
});
for(const failure of ['write','readback','permissions','race']) test(failure+' prevents updates',()=>{
    const s=setup(failure);assert.throws(s.run);assert(!s.events.includes('update'));
});
test('detects unexpected changes outside size fields',()=>{
    assert.throws(setup('side-effect').run,/differs from the size-only changes/);
});
test('restore touches only changed maxima and preserves later text edits',()=>{
    const s=setup();s.run();const backup=[...s.files.keys()][0];
    s.root.BTTMenuItems[0].BTTMenuConfig.BTTMenuAttributedText='later user edit';
    assert.match(s.run(['--restore',backup]),/Restored previous bounds on 3 items/);
    assert.equal(s.root.BTTMenuItems[0].BTTMenuConfig.BTTMenuItemMaxWidth,40);
    assert.equal(s.root.BTTMenuItems[0].BTTMenuConfig.BTTMenuAttributedText,'later user edit');
});
test('restore refuses to overwrite a subsequently resized item',()=>{
    const s=setup();s.run();const backup=[...s.files.keys()][0];
    s.root.BTTMenuItems[0].BTTMenuConfig.BTTMenuItemMaxWidth=88;
    assert.throws(()=>s.run(['--restore',backup]),/edited again/);
});
