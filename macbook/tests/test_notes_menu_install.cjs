const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../bettertouchtool/install_notes.js'), 'utf8');
const mediaID = 'D9B0ED12-C4BE-4E74-B0DA-0CC3BE092289';
const recentID = '0F8AEB59-60C1-4B1F-A356-26AD45E3A0A3';
const pinnedID = '0C21404E-6EBD-41F5-97D0-2D18DD60A386';

function setup(failure) {
    const clone = x => JSON.parse(JSON.stringify(x));
    const oldChildren = [
        {BTTUUID:'15C3F446-52BB-47DA-9129-9989239B43E0', BTTTriggerType:777, BTTTriggerParentUUID:pinnedID},
        {BTTUUID:'F2101ED6-5B28-4309-88F8-A01B012D6013', BTTTriggerType:773, BTTTriggerParentUUID:pinnedID,
            BTTMenuItemActions:[{BTTUUID:'old-action', payload:'kept in backup'}]}];
    const media = {BTTUUID:mediaID, BTTTriggerType:767, BTTMenuItems:[
        {BTTUUID:'unrelated', BTTTriggerType:773, BTTOrder:0, custom:'keep'},
        {BTTUUID:recentID, BTTTriggerType:774, BTTOrder:26,
            BTTMenuConfig:{BTTMenuItemScriptActive:1, BTTMenuScriptSettings:{BTTAppleScriptString:'JSON title bug'}},
            BTTMenuItems:[{BTTUUID:'ED1BCDA1-CE66-4C6A-B9A9-2F6DF7690141',
                BTTTriggerType:777, BTTTriggerParentUUID:recentID}]},
        {BTTUUID:pinnedID, BTTTriggerType:774, BTTOrder:27, BTTMenuConfig:{}, BTTMenuItems:oldChildren}
    ]};
    const initial = clone(media);
    const roots = new Map([[mediaID, media]]), files = new Map(), events = [], orphans = new Map();
    const variables = new Map(); let shown = '', clock = 0;
    function find(id) {
        if (orphans.has(id)) return orphans.get(id);
        if (roots.has(id)) return roots.get(id);
        for (const root of roots.values()) {
            for (const child of root.BTTMenuItems || []) {
                if (child.BTTUUID === id) return child;
                const inner = (child.BTTMenuItems || []).find(x => x.BTTUUID === id);
                if (inner) return inner;
            }
        }
    }
    let mediaReads = 0;
    const btt = {
        get_trigger(id) {
            if(id===mediaID && ++mediaReads===2 && failure==='race') media.BTTMenuItems[0].BTTOrder=5;
            const value=find(id); return JSON.stringify(value ? [value] : []);
        },
        get_triggers(query) {
            // Child queries are deliberately filtered: installer must not use them.
            assert.equal(query.trigger_id,767);return JSON.stringify([...roots.values()]);
        },
        add_new_trigger(json) {events.push('add');const item=JSON.parse(json);roots.set(item.BTTUUID,item);},
        update_menu_item(id,{json,persist}) {
            assert.equal(persist,true);events.push('update:'+id);find(id).BTTMenuConfig=JSON.parse(json);
        },
        update_trigger(id,{json,trigger_parent_uuid}) {
            events.push('update:' + id);const item=find(id);assert(item, 'must not recreate deleted children');
            const patch=JSON.parse(json);
            if (patch.BTTTriggerParentUUID) assert.equal(trigger_parent_uuid,patch.BTTTriggerParentUUID);
            // Actual BTT promotes old children to roots when changing a submenu to a button.
            if (Number(patch.BTTTriggerType)===773 && Array.isArray(patch.BTTMenuItems)) {
                for (const child of item.BTTMenuItems || []) {
                    child.BTTTriggerParentUUID=null;orphans.set(child.BTTUUID,child);
                }
            }
            Object.assign(item,patch);
            if(failure==='launcher') item.BTTMenuConfig.BTTMenuItemScriptActive=1;
        },
        delete_trigger(id) {events.push('delete:'+id);assert(orphans.has(id));orphans.delete(id);},
        set_string_variable(name,{to}) {variables.set(name,to);},
        get_string_variable(name) {return name==='visible_floating_menu_identifiers' ? shown : (variables.get(name)||'');},
        execute_assigned_actions_for_trigger(id) {
            const item=find(id);const data=item.BTTMenuItemActions[0].BTTAdditionalActionData;
            const menu=find(data.BTTMenuActionMenuID),config=menu.BTTMenuConfig;
            assert.equal(config.BTTMenuPositionRelativeTo,7);
            assert.equal(data.BTTMenuActionRestorePosition,true);
            const mode=id===recentID?'recent':'pinned';
            shown=failure==='invisible'?'':config.BTTMenuElementIdentifier;
            variables.set('notes_menu_'+mode+'_status',JSON.stringify(failure==='content'
                ? {state:'error',message:'Notes access denied'} : {state:'ok',count:mode==='recent'?10:23}));
        },
        trigger_action(json) {assert.equal(JSON.parse(json).BTTPredefinedActionType,387);shown='';}
    };
    function $(value) {return {value,stringByExpandingTildeInPath:value,
        writeToFileAtomicallyEncodingError(file) {
            events.push('backup-write');if(failure==='backup')return false;
            files.set(file,value);return true;
        }};}
    $.NSString={stringWithContentsOfFileEncodingError(file){events.push('backup-read');return files.get(file);}};
    let serial=0;$.NSUUID={UUID:{get UUIDString(){return 'test-'+(++serial);}}};
    $.NSFileManager={defaultManager:{
        createDirectoryAtPathWithIntermediateDirectoriesAttributesError(dir,recursive,attrs) {
            assert.equal(attrs.value.NSFilePosixPermissions,448);events.push('mkdir');return true;
        },setAttributesOfItemAtPathError(attrs) {assert.equal(attrs.value.NSFilePosixPermissions,384);return true;}
    }};
    function Application(){return btt;}
    Application.currentApplication=()=>({doShellScript(command){
        if(failure==='preflight')throw Error('Notes database denied');
        return JSON.stringify({mode:command.includes('pinned')?'pinned':'recent',count:10});
    }});
    const context=vm.createContext({$,ObjC:{import(){},unwrap(x){return x;}},Application,
        console:{log(){}},Date:{now(){return clock;}},delay(seconds){clock+=seconds*1000;}});
    vm.runInContext(source,context);
    return {run:()=>vm.runInContext('run([])',context),roots,files,events,initial,media,orphans};
}

test('private verified backup precedes scoped migration; two vertical menus and fixed titles',()=>{
    const s=setup();assert.match(s.run(),/Verified/);
    assert(s.events.indexOf('backup-read') < s.events.indexOf('add'));
    const backup=JSON.parse([...s.files.values()][0]);assert.deepEqual(backup.media,s.initial);
    assert.deepEqual(s.media.BTTMenuItems[0],s.initial.BTTMenuItems[0]);
    for(const item of s.media.BTTMenuItems.slice(1)) {
        assert.equal(item.BTTTriggerType,773);assert.equal(item.BTTMenuConfig.BTTMenuItemScriptActive,0);
        assert.equal(item.BTTMenuItemActions.length,1);assert.equal(item.BTTMenuItems.length,0);
        const menu=s.roots.get(item.BTTMenuItemActions[0].BTTAdditionalActionData.BTTMenuActionMenuID);
        assert.equal(menu.BTTMenuConfig.BTTMenuLayoutDirection,6);
        assert.equal(menu.BTTMenuConfig.BTTMenuPositionRelativeTo,7);
        assert.equal(menu.BTTMenuConfig.BTTMenuOpacityActive,1);
        assert.equal(menu.BTTMenuConfig.BTTMenuOpacityInactive,1);
        assert.equal(menu.BTTMenuConfig.BTTMenuScriptUpdateInterval,0);
        assert.equal(menu.BTTMenuConfig.BTTMenuItemScriptRunWhileMenuIsHidden,0);
        assert.equal(menu.BTTMenuConfig.BTTMenuScriptAlwaysRunOnAppear,1);
        assert.match(menu.BTTMenuConfig.BTTMenuScriptSettings.BTTAppleScriptString,/notes_menu.py/);
    }
    assert.deepEqual(s.media.BTTMenuItems.map(x=>x.BTTOrder),[0,26,27]);
    assert.equal(s.events.filter(x=>x.startsWith('delete:')).length,3);
    assert.equal(s.orphans.size,0);
    const writes=s.events.length;assert.match(s.run(),/already configured/);assert.equal(s.events.length,writes);
});
test('already installed menus clean up only the backed-up disabled leftovers',()=>{
    const s=setup();s.run();
    const saved=JSON.parse([...s.files.values()][0]);
    for(const row of saved.retiredRows) {
        row.BTTTriggerParentUUID=null;
        row.BTTEnabled=0;
        row.BTTMenuConfig={BTTMenuItemScriptActive:0,
            BTTMenuItemVisibleWhileActive:0,BTTMenuItemVisibleWhileInactive:0};
        s.orphans.set(row.BTTUUID,row);
    }
    s.orphans.set('unrelated-orphan',{BTTUUID:'unrelated-orphan'});
    s.events.length=0;
    assert.match(s.run(),/Removed 3 obsolete/);
    assert.equal(s.events.filter(x=>x.startsWith('delete:')).length,3);
    assert(!s.events.some(x=>x==='add'||x.startsWith('update:')));
    assert(s.events.indexOf('backup-read')<s.events.findIndex(x=>x.startsWith('delete:')));
    assert(s.orphans.has('unrelated-orphan'));
});
for(const failure of ['backup','preflight','race']) test(failure+' prevents all BTT writes',()=>{
    const s=setup(failure);assert.throws(s.run);
    assert(!s.events.some(x=>x==='add'||x.startsWith('update:')));
});
test('failed launcher verification is reported rather than success',()=>{
    const s=setup('launcher');assert.throws(s.run,/Could not verify/);
});
test('settings are not reported as successful if the dropdown stays invisible',()=>{
    const s=setup('invisible');assert.throws(s.run,/did not become visible and ready/);
});
test('content errors from BTT runtime are surfaced',()=>{
    const s=setup('content');assert.throws(s.run,/Notes access denied/);
});
test('real content scripts parse rows and publish distinct readiness status',async()=>{
    const s=setup();s.run();
    const roots=[...s.roots.values()].filter(x=>x.BTTUUID!==mediaID);
    const names=new Set();
    for(const menu of roots) {
        const settings=menu.BTTMenuConfig.BTTMenuScriptSettings;names.add(settings.BTTScriptFunctionToCall);
        const statuses=[];
        const rows=[{title:{text:'A note',size:16},action:{js:'open me'}}];
        const runtime=vm.createContext({runShellScript:async()=>JSON.stringify(rows),
            set_string_variable:async value=>statuses.push(value)});
        vm.runInContext(settings.BTTAppleScriptString,runtime);
        const output=await vm.runInContext(settings.BTTScriptFunctionToCall+'()',runtime);
        assert.deepEqual(JSON.parse(output),rows);
        assert.deepEqual(JSON.parse(statuses[0].to),{state:'ok',count:1});
        assert.match(statuses[0].variable_name,/notes_menu_(recent|pinned)_status/);
    }
    assert.equal(names.size,2);
});
