// Diagnostic rollback of the Disable Drag setting added by stabilize_media.
// User confirmed this one-setting rollback restores clicks on BTT 6.826.
// No menu trees are imported. --undo can reintroduce that click regression.
// Default: restore the prior/default drag handling. --undo: re-enable the lock.
ObjC.import('Foundation');
const CLICK_MEDIA = 'D9B0ED12-C4BE-4E74-B0DA-0CC3BE092289';

function clicksTest(btt, backup, fingerprint, undo) {
    function get() {
        const raw = JSON.parse(btt.get_trigger(CLICK_MEDIA));
        const hits = (Array.isArray(raw) ? raw : [raw]).filter(x => x && x.BTTUUID === CLICK_MEDIA);
        if (hits.length !== 1 || Number(hits[0].BTTTriggerType) !== 767 ||
            !hits[0].BTTMenuConfig || !Array.isArray(hits[0].BTTMenuItems)) {
            throw new Error('Cannot export the complete Media menu; no changes made.');
        }
        return hits[0];
    }
    const original = get(), value = original.BTTMenuConfig.BTTMenuDisableDrag;
    const before = value === undefined ? 0 : Number(value), after = undo ? 1 : 0;
    if (![0, 1].includes(before)) throw new Error('Unexpected Disable Drag value; no changes made.');
    if (before === after) return 'Disable Drag is already ' + after + '; no changes made.';
    const expected = JSON.parse(JSON.stringify(original));
    expected.BTTMenuConfig.BTTMenuDisableDrag = after;
    const path = backup({version: 1, purpose: 'Media click diagnostic: Disable Drag only',
        media: original, before, after});
    if (fingerprint(get()) !== fingerprint(original)) {
        throw new Error('Media changed during backup; no changes made. Backup: ' + path);
    }
    btt.update_menu_item(CLICK_MEDIA, {json: JSON.stringify({BTTMenuDisableDrag: after}), persist: true});
    if (fingerprint(get()) !== fingerprint(expected)) {
        throw new Error('Readback differs from the one-setting change. Backup: ' + path);
    }
    return 'Verified full Media backup: ' + path + '\n' +
        (undo ? 'Restored Disable Drag = 1.' :
            'Set only Disable Drag = 0. Hold Ctrl+Option+Command as usual and click Recent Notes.') +
        '\nVerified menu contents, shortcut, position and sizes unchanged. No restart performed.';
}

function run(argv) {
    if (argv[0] !== '--run-historical') throw new Error('ARCHIVED click repair: review one_off_fixes/README.md; --undo REINTRODUCES THE CLICK BUG.');
    argv = argv.slice(1);
    if (argv.length > 1 || (argv.length === 1 && argv[0] !== '--undo')) {
        throw new Error('Usage: 2026-09-18-restore-modifier-clicks.js --run-historical [--undo]');
    }
    const repo = ObjC.unwrap($('~/dev/scripts').stringByExpandingTildeInPath);
    function library(file, expression) {
        const text = $.NSString.stringWithContentsOfFileEncodingError(
            repo + '/macbook/bettertouchtool/' + file, $.NSUTF8StringEncoding, null);
        if (!text) throw new Error('Cannot read helper ' + file);
        return new Function(ObjC.unwrap(text) + '\nreturn ' + expression + ';')();
    }
    const backup = library('btt_common.js', 'BTTCommon.backup');
    const fingerprint = library('btt_common.js', 'BTTCommon.fingerprint');
    const directory = repo + '/tmp/btt-click-test-backup-' + ObjC.unwrap($.NSUUID.UUID.UUIDString);
    return clicksTest(Application('/Applications/BetterTouchTool.app'),
        snapshot => backup(snapshot, directory), fingerprint, argv[0] === '--undo');
}
