// Run with: osascript -l JavaScript macbook/bettertouchtool/install_notes.js
// Uses supported BTT APIs only. Writes a verified private backup before edits.
ObjC.import('Foundation');

const NOTES_MEDIA = 'D9B0ED12-C4BE-4E74-B0DA-0CC3BE092289';
const NOTES_MENUS = [
    {mode: 'recent', title: 'Recent Notes', source: '0F8AEB59-60C1-4B1F-A356-26AD45E3A0A3',
        menu: 'DAFF4296-EE87-5CCC-A682-50B2865D6C5A', action: '0DF4DB92-B303-537C-8033-97FA2496B956'},
    {mode: 'pinned', title: 'Pinned Notes', source: '0C21404E-6EBD-41F5-97D0-2D18DD60A386',
        menu: '1DF07B14-A4BB-5533-8679-BE46692E70AD', action: 'E926CC69-92FA-55A9-985B-A39372D86EF5'}
];
const NOTES_RETIRED_ROWS = [
    {uuid: 'ED1BCDA1-CE66-4C6A-B9A9-2F6DF7690141', type: 777, parent: NOTES_MENUS[0].source},
    {uuid: '15C3F446-52BB-47DA-9129-9989239B43E0', type: 777, parent: NOTES_MENUS[1].source},
    {uuid: 'F2101ED6-5B28-4309-88F8-A01B012D6013', type: 773, parent: NOTES_MENUS[1].source}
];

function notesShellQuote(text) { return "'" + text.replace(/'/g, "'\"'\"'") + "'"; }
function notesRTF(text, size, centered) {
    // Titles here are fixed ASCII labels, never note contents.
    return '{\\rtf1\\ansi{\\fonttbl{\\f0 Helvetica;}}' +
        '{\\colortbl;\\red255\\green255\\blue255;}' + (centered ? '\\pard\\qc' : '') +
        '\\f0\\fs' + (2 * size) + ' \\cf1 ' +
        text.replace(/\\/g, '\\\\').replace(/[{}]/g, '\\$&').replace(/\r\n|\r|\n/g, '\\line ') + '}';
}
function notesReadJSON(path) {
    const value = $.NSString.stringWithContentsOfFileEncodingError(path, $.NSUTF8StringEncoding, null);
    if (!value) throw new Error('Cannot read ' + path);
    return JSON.parse(ObjC.unwrap(value));
}
function notesBackup(snapshot, directory) {
    const manager = $.NSFileManager.defaultManager;
    if (!manager.createDirectoryAtPathWithIntermediateDirectoriesAttributesError(
        directory, false, $({NSFilePosixPermissions: 448}), null)) {
        throw new Error('Cannot create private backup directory; no BTT changes made.');
    }
    const path = directory + '/backup.json';
    if (!$(JSON.stringify(snapshot, null, 2)).writeToFileAtomicallyEncodingError(
        path, true, $.NSUTF8StringEncoding, null) ||
        !manager.setAttributesOfItemAtPathError($({NSFilePosixPermissions: 384}), path, null) ||
        JSON.stringify(notesReadJSON(path)) !== JSON.stringify(snapshot)) {
        throw new Error('Backup verification failed; no BTT changes made.');
    }
    return path;
}
function notesMenuDefinition(spec, provider) {
    const command = '/usr/bin/python3 -B ' + notesShellQuote(provider) + ' ' + spec.mode;
    const functionName = spec.mode === 'recent' ? 'retrieveRecentNotesDropdown' : 'retrievePinnedNotesDropdown';
    const statusName = 'notes_menu_' + spec.mode + '_status';
    return {
        BTTUUID: spec.menu, BTTTriggerType: 767,
        BTTTriggerClass: 'BTTTriggerTypeFloatingMenu', BTTEnabled: 1,
        BTTAppBundleIdentifier: 'BT.G', BTTActionCategory: 0, BTTMenuAvailability: 0,
        BTTMenuName: 'Media ' + spec.title + ' Dropdown',
        BTTTriggerName: 'Floating Menu: Media ' + spec.title + ' Dropdown',
        BTTMenuItems: [],
        BTTMenuConfig: {
            BTTMenuElementIdentifier: 'media-notes-' + spec.mode + '-dropdown',
            BTTMenuLayoutDirection: 6, BTTMenuSizingBehavior: 3,
            BTTMenuFrameWidth: 400, BTTMenuFrameHeight: 40,
            BTTMenuHorizontalAlignment: 0, BTTMenuVerticalAlignment: 0,
            BTTMenuHorizontalSpacing: 0, BTTMenuVerticalSpacing: 4,
            BTTMenuPositioningType: 1, BTTMenuPositionRelativeTo: 7,
            BTTMenuAnchorMenu: 0, BTTMenuAnchorRelation: 0,
            BTTMenuOffsetX: 0, BTTMenuOffsetY: -4,
            BTTMenuVisibility: 1, BTTMenuCloseOnOutsideClick: 1,
            BTTMenuCloseAfterAction: 1, BTTMenuCloseOnMoveMouseAway: 0,
            BTTMenuWindowResizable: 0, BTTMenuWindowLevel: 3,
            BTTMenuOpacityActive: 1, BTTMenuOpacityInactive: 1,
            BTTMenuDisableDrag: 1, BTTMenuKeepCached: 0,
            BTTMenuModifierKeys: 0, BTTMenuModifierMode: -1,
            BTTMenuItemMinWidth: 380, BTTMenuItemMaxWidth: 380,
            BTTMenuItemMinHeight: 40, BTTMenuItemMaxHeight: 40,
            BTTMenuItemVisibleWhileActive: 1, BTTMenuItemVisibleWhileInactive: 1,
            BTTMenuItemBackgroundType: 4,
            BTTMenuItemBackgroundColor: '50, 50, 60, 245',
            BTTMenuItemBackgroundColorDark: '50, 50, 60, 245',
            BTTMenuAttributedText: notesRTF('Note', 16),
            BTTMenuItemScriptActive: 1, BTTMenuCategoryContentScript: 1,
            BTTMenuScriptAlwaysRunOnAppear: 1, BTTMenuScriptAlwaysRunOnFirstLoad: 0,
            BTTMenuItemScriptRunWhileMenuIsHidden: 0, BTTMenuScriptUpdateInterval: 0,
            BTTMenuScriptSettings: {
                BTTScriptType: 3, BTTScriptLocation: 0, BTTAppleScriptUsePath: false,
                BTTJavaScriptUseIsolatedContext: false, BTTScriptFunctionToCall: functionName,
                BTTAppleScriptString: 'async function ' + functionName + '() {\n' +
                    ' try {\n' +
                    '  const output = await runShellScript({script: ' + JSON.stringify(command) + '});\n' +
                    '  const rows = JSON.parse(output);\n' +
                    '  if (!Array.isArray(rows)) throw new Error("Invalid Notes menu output");\n' +
                    '  const firstTitle = rows[0] && rows[0].title;\n' +
                    '  const text = typeof firstTitle === "object" ? firstTitle.text : firstTitle;\n' +
                    '  if (String(text).startsWith("Notes unavailable")) throw new Error(text);\n' +
                    '  await set_string_variable({variable_name: ' + JSON.stringify(statusName) +
                    ', to: JSON.stringify({state: "ok", count: rows.filter(x => x.action).length})});\n' +
                    '  return JSON.stringify(rows);\n}'
                    + ' catch (error) {\n' +
                    '  await set_string_variable({variable_name: ' + JSON.stringify(statusName) +
                    ', to: JSON.stringify({state: "error", message: String(error)})});\n' +
                    '  return JSON.stringify([{title: "Notes could not load — run installer --inspect"}]);\n' +
                    ' }\n}'
            }
        }
    };
}
function notesLauncher(spec, existing) {
    const config = Object.assign({}, existing.BTTMenuConfig || {});
    const label = spec.title.replace(' ', '\n');
    Object.keys(config).forEach(key => { if (key.startsWith('BTTMenuScript')) delete config[key]; });
    Object.assign(config, {
        BTTMenuElementIdentifier: spec.title,
        BTTMenuAttributedText: notesRTF(label, 14, true), BTTMenuItemText: label,
        BTTMenuItemScriptActive: 0, BTTMenuCategoryContentScript: 0,
        BTTMenuScriptAlwaysRunOnAppear: 0, BTTMenuScriptAlwaysRunOnFirstLoad: 0,
        BTTMenuItemScriptRunWhileMenuIsHidden: 0, BTTMenuScriptUpdateInterval: 0,
        BTTMenuScriptRunOnItemHover: 0, BTTMenuScriptRunOnMenuHover: 0,
        BTTMenuScriptSettings: {BTTScriptType: 3, BTTScriptLocation: 0,
            BTTAppleScriptString: '', BTTScriptFunctionToCall: ''},
        BTTMenuItemMinWidth: 54, BTTMenuItemMaxWidth: 78,
        BTTMenuItemMinHeight: 40, BTTMenuItemMaxHeight: 40,
        BTTMenuItemVisibleWhileActive: 1, BTTMenuItemVisibleWhileInactive: 1
    });
    return {
        BTTUUID: spec.source, BTTTriggerParentUUID: NOTES_MEDIA,
        BTTTriggerType: 773, BTTTriggerClass: 'BTTTriggerTypeFloatingMenu',
        BTTTriggerTypeDescriptionReadOnly: 'Standard Item', BTTTriggerTypeDescription: 'Standard Item',
        BTTEnabled: 1, BTTEnabled2: 1, BTTOrder: existing.BTTOrder || 0,
        BTTActionCategory: 0, BTTPredefinedActionType: 366,
        BTTMenuName: spec.title, BTTTriggerName: 'Menu Item: ' + spec.title,
        BTTMenuConfig: config, BTTMenuItems: [],
        BTTMenuItemActions: [{BTTUUID: spec.action, BTTTriggerParentUUID: spec.source,
            BTTTriggerClass: 'BTTTriggerTypeFloatingMenu', BTTEnabled: 1, BTTOrder: 0,
            BTTActionCategory: 0, BTTPredefinedActionType: 386,
            BTTPredefinedActionName: 'Show Floating Menu',
            BTTAdditionalActionData: {BTTMenuActionMenuID: spec.menu,
                BTTMenuActionMenuName: 'media-notes-' + spec.mode + '-dropdown',
                BTTMenuActionRestorePosition: true, BTTMenuActionActivateKeyboardFocus: 0}}]
    };
}
function notesPick(items, id) {
    const matches = items.filter(x => x.BTTUUID === id);
    if (matches.length > 1) throw new Error('Duplicate menu UUID: ' + id);
    return matches[0];
}
function notesCheckMenu(actual, expected) {
    if (!actual || Number(actual.BTTTriggerType) !== 767 ||
        (actual.BTTEnabled !== undefined && Number(actual.BTTEnabled) !== 1)) return false;
    const c = actual.BTTMenuConfig || {}, e = expected.BTTMenuConfig;
    return ['BTTMenuLayoutDirection', 'BTTMenuSizingBehavior', 'BTTMenuFrameWidth',
        'BTTMenuFrameHeight', 'BTTMenuVisibility', 'BTTMenuItemScriptActive',
        'BTTMenuPositionRelativeTo', 'BTTMenuPositioningType', 'BTTMenuAnchorMenu',
        'BTTMenuAnchorRelation', 'BTTMenuOpacityActive', 'BTTMenuOpacityInactive',
        'BTTMenuScriptAlwaysRunOnAppear', 'BTTMenuItemScriptRunWhileMenuIsHidden',
        'BTTMenuScriptUpdateInterval'].every(k => Number(c[k] || 0) === Number(e[k])) &&
        c.BTTMenuElementIdentifier === e.BTTMenuElementIdentifier &&
        (c.BTTMenuScriptSettings || {}).BTTAppleScriptString === e.BTTMenuScriptSettings.BTTAppleScriptString;
}
function notesCheckLauncher(actual, expected) {
    if (!actual || Number(actual.BTTTriggerType) !== 773 ||
        (actual.BTTEnabled !== undefined && Number(actual.BTTEnabled) !== 1) ||
        Number(actual.BTTOrder || 0) !== Number(expected.BTTOrder)) return false;
    const c = actual.BTTMenuConfig || {}, action = (actual.BTTMenuItemActions || [])
        .find(x => x.BTTUUID === expected.BTTMenuItemActions[0].BTTUUID);
    let data = action && action.BTTAdditionalActionData;
    if (typeof data === 'string') data = JSON.parse(data);
    return Number(c.BTTMenuItemScriptActive || 0) === 0 &&
        Number(c.BTTMenuScriptAlwaysRunOnAppear || 0) === 0 &&
        c.BTTMenuElementIdentifier === expected.BTTMenuName &&
        typeof c.BTTMenuAttributedText === 'string' &&
        /\\fs28\b/.test(c.BTTMenuAttributedText) &&
        c.BTTMenuItemText === expected.BTTMenuConfig.BTTMenuItemText &&
        Number(c.BTTMenuItemMinWidth) === 54 && Number(c.BTTMenuItemMaxWidth) === 78 &&
        action && Number(action.BTTPredefinedActionType) === 386 && data &&
        Number(data.BTTMenuActionRestorePosition) === 1 &&
        data.BTTMenuActionMenuID === expected.BTTMenuItemActions[0].BTTAdditionalActionData.BTTMenuActionMenuID;
}

function notesStyleLabels(btt, repo) {
    const labels = [
        {uuid: NOTES_MENUS[0].source, text: 'Recent\nNotes'},
        {uuid: NOTES_MENUS[1].source, text: 'Pinned\nNotes'},
        {uuid: 'FC3F8235-F102-55C4-8432-B6ADFB0D9992', text: 'Tools'},
        {uuid: '70C72D0E-14A4-4CCB-B0D3-A323BD4EFC57', text: 'Find My'}
    ];
    function getMedia() {
        const value = JSON.parse(btt.get_trigger(NOTES_MEDIA));
        const menu = notesPick(Array.isArray(value) ? value : [value], NOTES_MEDIA);
        if (!menu || Number(menu.BTTTriggerType) !== 767 || !Array.isArray(menu.BTTMenuItems)) {
            throw new Error('Cannot export Media; no label changes made.');
        }
        return menu;
    }
    const before = getMedia();
    const changes = labels.map(label => {
        const item = notesPick(before.BTTMenuItems, label.uuid);
        if (!item || ![773, 774].includes(Number(item.BTTTriggerType))) {
            throw new Error('Cannot identify button ' + label.text.replace('\n', ' ') + '; no changes made.');
        }
        const config = Object.assign({}, item.BTTMenuConfig || {}, {
            BTTMenuAttributedText: notesRTF(label.text, 14, true),
            BTTMenuItemText: label.text
        });
        return {label, item, config};
    }).filter(x => x.item.BTTMenuConfig.BTTMenuAttributedText !== x.config.BTTMenuAttributedText ||
        x.item.BTTMenuConfig.BTTMenuItemText !== x.label.text);
    if (!changes.length) return 'The four buttons already have the requested compact labels.';
    const backup = notesBackup({media: before}, repo + '/tmp/btt-labels-backup-' + ObjC.unwrap($.NSUUID.UUID.UUIDString));
    console.log('Verified Media backup: ' + backup);
    changes.forEach(x => btt.update_menu_item(x.label.uuid, {
        json: JSON.stringify(x.config), persist: true}));
    const after = getMedia();
    const order = menu => JSON.stringify(menu.BTTMenuItems.map(x => [x.BTTUUID, x.BTTOrder || 0])
        .sort((a, b) => a[0].localeCompare(b[0])));
    if (order(after) !== order(before)) throw new Error('Unexpected item-order change. Backup: ' + backup);
    labels.forEach(label => {
        const item = notesPick(after.BTTMenuItems, label.uuid), c = item && item.BTTMenuConfig;
        if (!c || c.BTTMenuItemText !== label.text || !/\\fs28\b/.test(c.BTTMenuAttributedText || '') ||
            (label.text.includes('\n') && !/\\(?:line|par)\b|\\\n/.test(c.BTTMenuAttributedText))) {
            throw new Error('Label verification failed for ' + label.text.replace('\n', ' ') + '. Backup: ' + backup);
        }
    });
    return 'Verified: Recent Notes, Pinned Notes, Tools and Find My use 14-point labels; both Notes labels use two lines. Backup: ' + backup;
}

function notesCheckOpening(btt) {
    const results = [];
    // Leave Recent Notes visible on success. Poll readiness, never a fixed wait.
    NOTES_MENUS.slice().reverse().forEach(spec => {
        const statusName = 'notes_menu_' + spec.mode + '_status';
        btt.set_string_variable(statusName, {to: ''});
        btt.execute_assigned_actions_for_trigger(spec.source);
        const deadline = Date.now() + 5000;
        let visible = '', status = null;
        while (Date.now() < deadline) {
            visible = String(btt.get_string_variable('visible_floating_menu_identifiers', {default: ''}) || '');
            const raw = btt.get_string_variable(statusName, {default: ''});
            status = raw ? JSON.parse(raw) : null;
            if (status && status.state === 'error') throw new Error(spec.title + ': ' + status.message);
            const shown = visible.includes(spec.menu) || visible.includes('media-notes-' + spec.mode + '-dropdown');
            if (shown && status && status.state === 'ok') {
                results.push(spec.title + ': visible, ' + status.count + ' notes');
                break;
            }
            delay(0.05);
        }
        if (!results.some(x => x.startsWith(spec.title + ':'))) {
            throw new Error(spec.title + ' did not become visible and ready. ' +
                'Status=' + JSON.stringify(status) + '; visible menus=' + visible);
        }
        if (spec.mode === 'pinned') btt.trigger_action(JSON.stringify({BTTPredefinedActionType: 387,
            BTTAdditionalActionData: {BTTMenuActionMenuID: spec.menu}}));
    });
    return results.join('; ');
}

function run(argv) {
    if (argv.length && !['--inspect', '--labels-only'].includes(argv[0])) {
        throw new Error('Usage: install_notes.js [--inspect | --labels-only]');
    }
    const btt = Application('/Applications/BetterTouchTool.app');
    const current = Application.currentApplication(); current.includeStandardAdditions = true;
    const repo = ObjC.unwrap($('~/dev/scripts').stringByExpandingTildeInPath);
    if (argv[0] === '--labels-only') return notesStyleLabels(btt, repo);
    const provider = repo + '/macbook/scripts/notes_menu.py';
    function getOptional(id) {
        const raw = btt.get_trigger(id);
        if (raw === undefined || raw === null || String(raw).trim() === '' ||
            /^(not found|missing value)$/i.test(String(raw).trim())) return null;
        const value = JSON.parse(raw);
        if (!value) return null;
        return notesPick(Array.isArray(value) ? value : [value], id) || null;
    }
    function getOne(id) {
        const item = getOptional(id);
        if (!item) throw new Error('BTT did not return expected item: ' + id);
        return item;
    }
    const media = getOne(NOTES_MEDIA);
    if (Number(media.BTTTriggerType) !== 767 || !Array.isArray(media.BTTMenuItems)) {
        throw new Error('Cannot export complete Media menu; no changes made.');
    }
    const roots = JSON.parse(btt.get_triggers({trigger_id: 767}));
    if (!Array.isArray(roots)) throw new Error('Unexpected menu-list response; no changes made.');
    const entries = NOTES_MENUS.map(spec => {
        const original = notesPick(media.BTTMenuItems, spec.source);
        if (!original || ![773, 774].includes(Number(original.BTTTriggerType))) {
            throw new Error('Cannot identify existing ' + spec.title + ' button; no changes made.');
        }
        const previous = notesPick(roots, spec.menu);
        const definition = notesMenuDefinition(spec, provider);
        if (previous && (Number(previous.BTTTriggerType) !== 767 ||
            previous.BTTMenuConfig.BTTMenuElementIdentifier !== definition.BTTMenuConfig.BTTMenuElementIdentifier)) {
            throw new Error('Conflicting Notes dropdown UUID; no changes made.');
        }
        return {spec, original, previous: previous || null, definition,
            launcher: notesLauncher(spec, original)};
    });
    const counts = NOTES_MENUS.map(spec => JSON.parse(current.doShellScript(
        '/usr/bin/python3 -B ' + notesShellQuote(provider) + ' ' + spec.mode + ' --check')));
    const retired = NOTES_RETIRED_ROWS.map(spec => ({spec, item: getOptional(spec.uuid)}))
        .filter(x => x.item);
    retired.forEach(({spec, item}) => {
        if (Number(item.BTTTriggerType) !== spec.type ||
            (item.BTTTriggerParentUUID && item.BTTTriggerParentUUID !== spec.parent)) {
            throw new Error('Former Notes row was repurposed; refusing cleanup: ' + spec.uuid);
        }
    });
    if (argv[0] === '--inspect') return JSON.stringify({notes: counts, menus: entries.map(e => ({
        title: e.spec.title, dropdownReady: notesCheckMenu(e.previous, e.definition),
        buttonReady: notesCheckLauncher(e.original, e.launcher),
        runtimeStatus: btt.get_string_variable('notes_menu_' + e.spec.mode + '_status', {default: ''})})),
        visibleMenus: btt.get_string_variable('visible_floating_menu_identifiers', {default: ''}),
        retiredRows: retired.length}, null, 2);
    if (!retired.length && entries.every(e => notesCheckMenu(e.previous, e.definition) && notesCheckLauncher(e.original, e.launcher))) {
        return 'Notes dropdowns already configured. ' + notesCheckOpening(btt);
    }
    const dir = repo + '/tmp/btt-notes-backup-' + ObjC.unwrap($.NSUUID.UUID.UUIDString);
    const backup = notesBackup({media, previousDropdowns: entries.map(e => e.previous),
        retiredRows: retired.map(x => x.item)}, dir);
    console.log('Verified full Media backup: ' + backup);
    // Catch concurrent menu rearrangement before any writes.
    const signature = x => JSON.stringify(x.BTTMenuItems.map(i => [i.BTTUUID, i.BTTOrder || 0])
        .sort((a, b) => a[0].localeCompare(b[0])));
    if (signature(getOne(NOTES_MEDIA)) !== signature(media)) {
        throw new Error('Media order changed during backup; no changes made.');
    }
    // Create/verify both working destinations before changing either launcher.
    entries.forEach(e => {
        if (!e.previous) btt.add_new_trigger(JSON.stringify(e.definition));
        else if (!notesCheckMenu(e.previous, e.definition)) {
            btt.update_menu_item(e.spec.menu, {json: JSON.stringify(e.definition.BTTMenuConfig), persist: true});
        }
        if (!notesCheckMenu(getOne(e.spec.menu), e.definition)) {
            throw new Error('Dropdown verification failed. Original buttons kept. Backup: ' + backup);
        }
    });
    entries.forEach(e => {
        // Retire old submenu-only rows (Back and the obsolete hardcoded note).
        // Do this before replacing the parent: update_trigger can create missing
        // triggers, so do not update obsolete IDs after an importer removes them.
        (e.original.BTTMenuItems || []).forEach(child => {
            btt.update_trigger(child.BTTUUID, {trigger_parent_uuid: e.spec.source,
                json: JSON.stringify(Object.assign({}, child, {BTTTriggerParentUUID: e.spec.source, BTTEnabled: 0, BTTEnabled2: 0,
                BTTMenuConfig: Object.assign({}, child.BTTMenuConfig || {}, {
                    BTTMenuItemScriptActive: 0, BTTMenuItemVisibleWhileActive: 0,
                    BTTMenuItemVisibleWhileInactive: 0})}))});
        });
        if (!notesCheckLauncher(e.original, e.launcher)) {
            btt.update_trigger(e.spec.source, {trigger_parent_uuid: NOTES_MEDIA,
                json: JSON.stringify(Object.assign({}, e.original, e.launcher))});
        }
        if (!notesCheckLauncher(getOne(e.spec.source), e.launcher)) {
            throw new Error('Could not verify ' + e.spec.title + ' launcher. Backup: ' + backup);
        }
    });
    const after = getOne(NOTES_MEDIA);
    if (signature(after) !== signature(media)) throw new Error('Unexpected Media order change. Backup: ' + backup);
    let removed = 0;
    // Converting774 ->773 can detach its old rows into the top-level list.
    // Remove only the three exact obsolete IDs after replacements are verified.
    retired.forEach(({spec}) => {
        const item = getOptional(spec.uuid);
        if (!item) return;
        const c = item.BTTMenuConfig || {};
        if (Number(item.BTTTriggerType) !== spec.type ||
            (item.BTTTriggerParentUUID && item.BTTTriggerParentUUID !== spec.parent) ||
            Number(c.BTTMenuItemVisibleWhileActive) !== 0 ||
            Number(c.BTTMenuItemVisibleWhileInactive) !== 0) {
            throw new Error('Former Notes row is not safely retired; kept it. Backup: ' + backup);
        }
        btt.delete_trigger(spec.uuid);
        if (getOptional(spec.uuid)) throw new Error('Could not remove retired Notes row. Backup: ' + backup);
        removed++;
    });
    return 'Verified: Recent Notes and Pinned Notes have fixed titles and open independent vertical dropdowns. ' +
        'Removed ' + removed + ' obsolete Back/GuitarSongs rows (recoverable from backup). ' +
        notesCheckOpening(btt) + '. Backup: ' + backup;
}
