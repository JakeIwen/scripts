// Repair explicit min > max item-size conflicts through BTT's supported API.
// Default: Media + its nested menu items. Full verified backup precedes edits.
ObjC.import('Foundation');

const SIZE_MENU_UUID = 'D9B0ED12-C4BE-4E74-B0DA-0CC3BE092289';
const SIZE_COMMON = typeof BTTCommon === 'object' ? BTTCommon : (function () {
    const path = ObjC.unwrap($('~/dev/scripts/macbook/bettertouchtool/btt_common.js').stringByExpandingTildeInPath);
    const source = $.NSString.stringWithContentsOfFileEncodingError(path, $.NSUTF8StringEncoding, null);
    if (!source) throw new Error('Cannot load shared BTT safety helpers.');
    return new Function(ObjC.unwrap(source) + '\nreturn BTTCommon;')();
})();
function sizeClone(value) { return SIZE_COMMON.clone(value); }
function sizeCanonical(value) { return SIZE_COMMON.canonical(value); }
function sizeFingerprint(value) { return SIZE_COMMON.fingerprint(value); }
function sizeNodes(root) {
    const result = new Map();
    function visit(item, parent) {
        if (!item || !item.BTTUUID || result.has(item.BTTUUID)) {
            throw new Error('Missing or duplicated menu UUID; refusing changes.');
        }
        if (item.BTTTriggerParentUUID && item.BTTTriggerParentUUID !== parent) {
            throw new Error('Inconsistent parent for ' + item.BTTUUID + '; refusing changes.');
        }
        result.set(item.BTTUUID, {item, parent});
        (item.BTTMenuItems || []).forEach(child => visit(child, item.BTTUUID));
    }
    visit(root, null);
    return result;
}
function sizeGet(object, path) { return path.reduce((x, k) => x === undefined ? undefined : x[k], object); }
function sizeSet(object, path, value) {
    const parent = sizeGet(object, path.slice(0, -1));
    if (!parent || typeof parent !== 'object') throw new Error('Size field path no longer exists.');
    parent[path[path.length - 1]] = value;
}
function sizeEdits(config, path) {
    if (!config || typeof config !== 'object' || Array.isArray(config)) return [];
    const edits = [];
    ['Width', 'Height'].forEach(axis => {
        const minKey = 'BTTMenuItemMin' + axis, maxKey = 'BTTMenuItemMax' + axis;
        const min = config[minKey], max = config[maxKey];
        // Zero/negative bounds can represent defaults/unlimited sizing. Never
        // guess their meaning; fix only explicit positive, finite conflicts.
        if (min !== undefined && max !== undefined && min !== null && max !== null &&
            Number.isFinite(Number(min)) && Number.isFinite(Number(max)) &&
            Number(min) > 0 && Number(max) > 0 && Number(min) > Number(max)) {
            edits.push({path: path.concat(maxKey), minPath: path.concat(minKey),
                minValue: min, before: max, after: Number(min)});
        }
    });
    Object.keys(config).forEach(key => {
        if (config[key] && typeof config[key] === 'object' && !Array.isArray(config[key])) {
            edits.push(...sizeEdits(config[key], path.concat(key)));
        }
    });
    return edits;
}
function sizePlan(root) {
    const changes = [];
    sizeNodes(root).forEach(({item, parent}) => {
        const edits = sizeEdits(item.BTTMenuConfig, []);
        if (edits.length) changes.push({uuid: item.BTTUUID, parent,
            label: item.BTTMenuName || (item.BTTMenuConfig || {}).BTTMenuElementIdentifier || item.BTTUUID,
            edits});
    });
    return changes;
}
function sizeReadJSON(path) { return SIZE_COMMON.readJSON(path); }
function sizeBackup(snapshot) {
    const directory = ObjC.unwrap($('~/dev/scripts/tmp/btt-sizing-backup-' +
        ObjC.unwrap($.NSUUID.UUID.UUIDString)).stringByExpandingTildeInPath);
    return SIZE_COMMON.backup(snapshot, directory);
}
function sizeRestorePlan(root, saved) {
    if (saved.version !== 1 || saved.menu !== SIZE_MENU_UUID || !saved.mediaExport ||
        !Array.isArray(saved.changes)) throw new Error('Not a compatible sizing backup.');
    const nodes = sizeNodes(root), originals = sizeNodes(saved.mediaExport);
    return saved.changes.map(change => {
        const node = nodes.get(change.uuid), original = originals.get(change.uuid);
        if (!node || !original || node.parent !== change.parent) {
            throw new Error('A repaired item was removed or reparented; refusing restore.');
        }
        const edits = change.edits.filter(edit => {
            if (!Array.isArray(edit.path) || !/^BTTMenuItemMax(?:Width|Height)$/.test(edit.path[edit.path.length - 1]) ||
                sizeGet(original.item.BTTMenuConfig, edit.path) !== edit.before ||
                sizeGet(node.item.BTTMenuConfig, edit.minPath) !== edit.minValue) {
                throw new Error('Size settings changed since the backup; refusing restore.');
            }
            const value = sizeGet(node.item.BTTMenuConfig, edit.path);
            if (value === edit.before) return false;
            if (value !== edit.after) throw new Error('A repaired size was edited again; refusing to overwrite it.');
            return true;
        }).map(edit => Object.assign({}, edit, {before: edit.after, after: edit.before}));
        return Object.assign({}, change, {edits});
    }).filter(change => change.edits.length);
}
function run(argv) {
    const mode = argv[0] || '--apply';
    if (!['--apply', '--inspect', '--restore'].includes(mode) ||
        (mode === '--restore' ? argv.length !== 2 : argv.length > 1)) {
        throw new Error('Usage: fix_menu_sizes.js [--apply | --inspect | --restore BACKUP.json]');
    }
    const btt = Application('/Applications/BetterTouchTool.app');
    function getRoot() {
        const value = JSON.parse(btt.get_trigger(SIZE_MENU_UUID));
        const matches = (Array.isArray(value) ? value : [value])
            .filter(x => x && x.BTTUUID === SIZE_MENU_UUID);
        if (matches.length !== 1 || Number(matches[0].BTTTriggerType) !== 767 ||
            !Array.isArray(matches[0].BTTMenuItems)) throw new Error('Cannot export the complete Media menu.');
        return matches[0];
    }
    const original = getRoot(), nodes = sizeNodes(original);
    const changes = mode === '--restore' ? sizeRestorePlan(original, sizeReadJSON(argv[1])) : sizePlan(original);
    const summary = changes.map(x => ({uuid: x.uuid, label: x.label,
        edits: x.edits.map(e => ({field: e.path.join('.'), before: e.before, after: e.after}))}));
    if (mode === '--inspect') return JSON.stringify({items: nodes.size, conflicts: summary.length, changes: summary}, null, 2);
    if (!changes.length) return 'No sizing changes needed.';

    const expected = sizeClone(original), expectedNodes = sizeNodes(expected);
    changes.forEach(change => change.edits.forEach(edit => {
        sizeSet(expectedNodes.get(change.uuid).item.BTTMenuConfig, edit.path, edit.after);
    }));
    const backup = sizeBackup({version: 1, menu: SIZE_MENU_UUID, mediaExport: original, changes});
    console.log('Verified full Media backup: ' + backup);
    if (sizeFingerprint(getRoot()) !== sizeFingerprint(original)) {
        throw new Error('Media changed during backup; no changes made. Backup: ' + backup);
    }
    let updated = 0;
    try {
        changes.forEach(change => {
            // This API explicitly saves appearance properties without importing
            // trigger trees or replacing their child/action relationships.
            btt.update_menu_item(change.uuid, {
                json: JSON.stringify(expectedNodes.get(change.uuid).item.BTTMenuConfig), persist: true
            });
            updated++;
        });
        const actual = getRoot();
        if (sizeFingerprint(actual) !== sizeFingerprint(expected)) {
            throw new Error('Readback differs from the size-only changes; review the saved backup.');
        }
        if (mode !== '--restore' && sizePlan(actual).length) throw new Error('Size conflicts remain after applying.');
    } catch (error) {
        throw new Error(String(error) + ' Updated ' + updated + '/' + changes.length +
            ' items. Backup: ' + backup + '. Restore with --restore BACKUP.json.');
    }
    return (mode === '--restore' ? 'Restored previous bounds on ' : 'Repaired conflicting bounds on ') +
        updated + ' items; verified all other exported settings unchanged. Backup: ' + backup;
}
