// Runs one large BTT write over Apple Events. BTT's socket CLI truncates big messages, so
// the Python caller (btt_apple_events.py) passes the command and parameters through a
// private file whose path is the only argument. Nothing from the payload is ever printed.
ObjC.import('Foundation');
function run(argv) {
    const text = $.NSString.stringWithContentsOfFileEncodingError(argv[0], $.NSUTF8StringEncoding, null);
    if (!text) throw new Error('Cannot read the payload file.');
    const request = JSON.parse(ObjC.unwrap(text));
    const p = request.parameters;
    const btt = Application('/Applications/BetterTouchTool.app');
    // One branch per command the installers send; anything else is refused, not guessed at.
    if (request.command === 'add_new_trigger') {
        const extra = p.parent_uuid ? {parent_uuid: p.parent_uuid} : {};
        btt.add_new_trigger(p.json, extra);
    } else if (request.command === 'update_menu_item') {
        btt.update_menu_item(p.uuid, {json: p.json, persist: p.persist === 'true'});
    } else {
        throw new Error('Unsupported large-write command.');
    }
    return 'ok';
}
