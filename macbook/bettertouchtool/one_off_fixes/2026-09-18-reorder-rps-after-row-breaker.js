// ARCHIVED: September 18 ordering request; later manual ordering takes precedence.
// Move RPS immediately after Media's Row Breaker through BTT's API.
ObjC.import('Foundation');

const MEDIA = 'D9B0ED12-C4BE-4E74-B0DA-0CC3BE092289';
const RPS = '103D7824-47C1-4B1B-9106-71E4997BCB58';

function readJSON(path) {
    const value = $.NSString.stringWithContentsOfFileEncodingError(
        path, $.NSUTF8StringEncoding, null);
    if (!value) throw new Error('Cannot read ' + path);
    return JSON.parse(ObjC.unwrap(value));
}

function saveVerifiedBackup(snapshot) {
    const unique = ObjC.unwrap($.NSUUID.UUID.UUIDString);
    const directory = ObjC.unwrap($('~/dev/scripts/tmp/btt-media-reorder-' + unique)
        .stringByExpandingTildeInPath);
    const manager = $.NSFileManager.defaultManager;
    // The exported menu may include private paste text or action credentials.
    // Create its owner-only directory before writing any of those contents.
    if (!manager.createDirectoryAtPathWithIntermediateDirectoriesAttributesError(
        directory, false, $({NSFilePosixPermissions: 448}), null)) {
        throw new Error('Cannot create private backup directory; no changes made.');
    }
    const path = directory + '/backup.json';
    const text = JSON.stringify(snapshot, null, 2);
    if (!$(text).writeToFileAtomicallyEncodingError(path, true, $.NSUTF8StringEncoding, null) ||
        !manager.setAttributesOfItemAtPathError($({NSFilePosixPermissions: 384}), path, null) ||
        JSON.stringify(readJSON(path)) !== JSON.stringify(snapshot)) {
        throw new Error('Backup write/verification failed; no changes made.');
    }
    return path;
}

function orderOf(item) {
    const order = Number(item.BTTOrder === undefined ? 0 : item.BTTOrder);
    if (!Number.isInteger(order) || order < 0) {
        throw new Error('Invalid item order; no automatic repair attempted.');
    }
    return order;
}

function ordered(items) {
    if (!Array.isArray(items) || !items.length) throw new Error('No Media items returned.');
    const ids = new Set();
    const orders = new Set();
    items.forEach(item => {
        if (!item.BTTUUID || ids.has(item.BTTUUID) || orders.has(orderOf(item))) {
            throw new Error('Ambiguous UUIDs/orders; refusing to reorder.');
        }
        if (item.BTTTriggerParentUUID && item.BTTTriggerParentUUID !== MEDIA) {
            throw new Error('BTT returned an item outside Media; refusing changes.');
        }
        ids.add(item.BTTUUID);
        orders.add(orderOf(item));
    });
    return items.slice().sort((a, b) => orderOf(a) - orderOf(b));
}

function plan(items) {
    const result = ordered(items);
    const orderSlots = result.map(orderOf);
    const rps = result.filter(x => x.BTTUUID === RPS);
    const breakers = result.filter(x => Number(x.BTTTriggerType) === 801);
    if (rps.length !== 1 || breakers.length !== 1 ||
        Number(rps[0].BTTTriggerType) !== 773) {
        throw new Error('Expected RPS and exactly one Row Breaker.');
    }
    result.splice(result.indexOf(rps[0]), 1);
    result.splice(result.indexOf(breakers[0]) + 1, 0, rps[0]);
    // Reuse the existing order slots, including gaps, to limit changes to the
    // moved item and the items it passes. All other relative order is preserved.
    return result.map((item, index) => ({uuid: item.BTTUUID, order: orderSlots[index]}));
}

function run(argv) {
    const historical = argv[0] === '--run-historical';
    if (historical) argv = argv.slice(1);
    const mode = argv[0] || '--preview';
    if (mode !== '--preview' && !historical) throw new Error('ARCHIVED ordering repair: review one_off_fixes/README.md before --run-historical.');
    if (!['--apply', '--preview', '--restore'].includes(mode) ||
        (mode === '--restore' && !argv[1])) {
        throw new Error('Usage: reorder_media.js [--apply | --preview | --restore BACKUP.json]');
    }
    const btt = Application('/Applications/BetterTouchTool.app');
    function getMedia() {
        // get_triggers(parent) can omit configured children (observed for the
        // text-tools submenu). The complete parent export is authoritative.
        const exported = JSON.parse(btt.get_trigger(MEDIA));
        const menus = Array.isArray(exported) ? exported : [exported];
        const matches = menus.filter(x => x && x.BTTUUID === MEDIA);
        if (matches.length !== 1 || Number(matches[0].BTTTriggerType) !== 767 ||
            !Array.isArray(matches[0].BTTMenuItems)) {
            throw new Error('Cannot obtain a complete Media menu export.');
        }
        ordered(matches[0].BTTMenuItems);
        return matches[0];
    }
    const mediaExport = getMedia();
    const original = ordered(mediaExport.BTTMenuItems);
    const previous = original.map(x => ({uuid: x.BTTUUID, order: orderOf(x)}));
    let desired;
    if (mode === '--restore') {
        const saved = readJSON(argv[1]);
        if (saved.menu !== MEDIA || !Array.isArray(saved.before)) throw new Error('Invalid backup.');
        desired = saved.before;
    } else {
        desired = plan(original);
    }
    const currentIDs = previous.map(x => x.uuid).sort();
    if (JSON.stringify(currentIDs) !== JSON.stringify(desired.map(x => x.uuid).sort()) ||
        new Set(desired.map(x => x.order)).size !== desired.length ||
        desired.some(x => !Number.isInteger(x.order) || x.order < 0)) {
        throw new Error('Item membership/order differs from backup or plan; refusing changes.');
    }
    const changes = desired.filter(x => previous.find(p => p.uuid === x.uuid).order !== x.order);
    if (mode === '--preview') return JSON.stringify({changes, desired}, null, 2);
    if (!changes.length) return 'Requested order is already set; no changes needed.';

    const backup = saveVerifiedBackup({version: 2, menu: MEDIA, before: previous,
        after: desired, mediaExport});
    console.log('Full Media menu and original order backed up and verified: ' + backup);
    const freshOrder = ordered(getMedia().BTTMenuItems)
        .map(x => ({uuid: x.BTTUUID, order: orderOf(x)}));
    if (JSON.stringify(freshOrder) !== JSON.stringify(previous)) {
        throw new Error('Media changed during backup; no changes made. Backup: ' + backup);
    }
    try {
        changes.forEach(change => {
            const item = original.find(x => x.BTTUUID === change.uuid);
            btt.update_trigger(change.uuid, {trigger_parent_uuid: MEDIA,
                json: JSON.stringify(Object.assign({}, item, {BTTOrder: change.order}))});
        });
        const actual = ordered(getMedia().BTTMenuItems)
            .map(x => ({uuid: x.BTTUUID, order: orderOf(x)}));
        const expected = desired.slice().sort((a, b) => a.order - b.order);
        if (JSON.stringify(actual) !== JSON.stringify(expected)) {
            throw new Error('BTT returned an unexpected order after updating.');
        }
    } catch (error) {
        throw new Error(String(error) + '\nUpdate may be partial. Restore with this script using --restore ' + backup);
    }
    return mode === '--restore' ? 'Previous order restored and verified.' :
        'Verified: RPS is immediately after the Row Breaker. All other items retain their relative order.';
}
