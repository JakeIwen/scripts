// Read-only BTT definition export. Loading this file makes no application calls.
ObjC.import('Foundation');

function exportRead(path) {
    const value = $.NSString.stringWithContentsOfFileEncodingError(
        path, $.NSUTF8StringEncoding, null);
    if (!value) throw new Error('Cannot read required BTT Guard input.');
    return ObjC.unwrap(value);
}
function exportLibrary(repo, file, expression) {
    const source = exportRead(repo + '/macbook/bettertouchtool/' + file);
    return new Function(source + '\nreturn ' + expression + ';')();
}
function run(argv) {
    if (!Array.isArray(argv) || argv.length !== 2)
        throw new Error('Expected repository root and private input JSON path.');
    const common = exportLibrary(argv[0], 'btt_common.js', 'BTTCommon');
    const get = exportLibrary(argv[0], 'restore_media.js', 'restoreGet');
    const input = common.readJSON(argv[1]);
    if (!input || typeof input !== 'object' || Array.isArray(input) ||
        Object.keys(input).length !== 1 || !Array.isArray(input.ids) ||
        input.ids.length === 0 ||
        input.ids.some(id => typeof id !== 'string' || !id) ||
        new Set(input.ids).size !== input.ids.length)
        throw new Error('Invalid BTT Guard export request.');
    const btt = Application('/Applications/BetterTouchTool.app');
    return JSON.stringify(input.ids.map(id => {
        const definition = get(btt, id);
        if (!definition) throw new Error('Required BTT definition is unavailable: ' + id);
        return definition;
    }));
}
