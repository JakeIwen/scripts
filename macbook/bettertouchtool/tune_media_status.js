// Install the playback-status content script and halve Media's 5px row gap.
// Uses only persistent style updates; never imports/replaces trigger trees.
ObjC.import('Foundation');
const STATUS_MEDIA = 'D9B0ED12-C4BE-4E74-B0DA-0CC3BE092289';
const STATUS_ITEM = 'BDFEAEF1-6961-4B05-9A62-E70C54332C4C';

function statusPlan(media, source) {
    if (!media || media.BTTUUID !== STATUS_MEDIA || Number(media.BTTTriggerType) !== 767 ||
        !Array.isArray(media.BTTMenuItems) || !media.BTTMenuConfig) {
        throw new Error('Cannot export the complete Media menu; no changes made.');
    }
    const matches = media.BTTMenuItems.filter(x => x.BTTUUID === STATUS_ITEM);
    if (matches.length !== 1 || Number(matches[0].BTTTriggerType) !== 773 ||
        (matches[0].BTTMenuConfig || {}).BTTMenuElementIdentifier !== 'PPause') {
        throw new Error('Playback-status item is missing or changed; no changes made.');
    }
    const config = matches[0].BTTMenuConfig, settings = config.BTTMenuScriptSettings;
    if (Number(config.BTTMenuItemScriptActive) !== 1 || !settings || Number(settings.BTTScriptType) !== 3 ||
        !String(settings.BTTAppleScriptString).includes('play_status')) {
        throw new Error('Unexpected playback-status script; no changes made.');
    }
    const spacing = Number(media.BTTMenuConfig.BTTMenuVerticalSpacing);
    if (spacing !== 5 && spacing !== 2.5) {
        throw new Error('Media row spacing differs from the inspected 5px value; no changes made.');
    }
    const changes = [];
    if (spacing !== 2.5) changes.push({uuid: STATUS_MEDIA, patch: {BTTMenuVerticalSpacing: 2.5}});
    if (settings.BTTAppleScriptString !== source || settings.BTTScriptFunctionToCall !== 'itemScript') {
        const updated = Object.assign({}, settings, {BTTAppleScriptString: source, BTTScriptFunctionToCall: 'itemScript'});
        changes.push({uuid: STATUS_ITEM, patch: {BTTMenuScriptSettings: updated}});
    }
    return changes;
}

function statusApply(btt, source, backup, fingerprint, inspect) {
    function get() {
        const raw = JSON.parse(btt.get_trigger(STATUS_MEDIA));
        const hits = (Array.isArray(raw) ? raw : [raw]).filter(x => x && x.BTTUUID === STATUS_MEDIA);
        if (hits.length !== 1) throw new Error('Cannot uniquely export Media.');
        return hits[0];
    }
    const original = get(), changes = statusPlan(original, source);
    if (inspect) return JSON.stringify({rowSpacingBefore: original.BTTMenuConfig.BTTMenuVerticalSpacing,
        rowSpacingAfter: 2.5, changedItems: changes.map(x => x.uuid), changes: changes.length});
    if (!changes.length) return 'Already configured: clean playback status and 2.5px row spacing.';
    const expected = JSON.parse(JSON.stringify(original));
    for (const change of changes) {
        const item = change.uuid === STATUS_MEDIA ? expected : expected.BTTMenuItems.find(x => x.BTTUUID === change.uuid);
        Object.assign(item.BTTMenuConfig, change.patch);
    }
    const path = backup({version: 1, purpose: 'Clean PPause output and halve Media vertical spacing',
        media: original, changes});
    if (fingerprint(get()) !== fingerprint(original)) {
        throw new Error('Media changed during backup; no changes made. Backup: ' + path);
    }
    try {
        for (const change of changes) {
            btt.update_menu_item(change.uuid, {json: JSON.stringify(change.patch), persist: true});
        }
        if (fingerprint(get()) !== fingerprint(expected)) throw new Error('Readback differs from the requested changes.');
    } catch (error) {
        throw new Error(String(error) + ' Backup: ' + path);
    }
    return 'Verified full Media backup: ' + path + '\n' +
        'Saved clean playback status and row spacing 5 -> 2.5. Actions, sizes, positions and drag handling unchanged. No restart needed.';
}

function run(argv) {
    if (argv.length > 1 || (argv.length === 1 && argv[0] !== '--inspect')) {
        throw new Error('Usage: tune_media_status.js [--inspect]');
    }
    const repo = ObjC.unwrap($('~/dev/scripts').stringByExpandingTildeInPath);
    function read(file) {
        const text = $.NSString.stringWithContentsOfFileEncodingError(
            repo + '/macbook/bettertouchtool/' + file, $.NSUTF8StringEncoding, null);
        if (!text) throw new Error('Cannot read ' + file);
        return ObjC.unwrap(text);
    }
    const shared = new Function(read('btt_common.js') + '\nreturn BTTCommon;')();
    const backup = shared.backup, fingerprint = shared.fingerprint;
    const directory = repo + '/tmp/btt-status-spacing-backup-' + ObjC.unwrap($.NSUUID.UUID.UUIDString);
    return statusApply(Application('/Applications/BetterTouchTool.app'), read('play_status.js'),
        snapshot => backup(snapshot, directory), fingerprint, argv[0] === '--inspect');
}
