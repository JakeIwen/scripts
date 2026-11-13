const {test} = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const {execFileSync} = require('node:child_process');
const {shellArguments} = require('./btt_test_support.cjs');

const folder = path.join(__dirname, '../bettertouchtool');
const payload = JSON.parse(execFileSync('/usr/bin/python3', ['-c',
    'import json; from install_mac_menu import definition; print(json.dumps(definition()))'], {
    cwd: folder, env: {...process.env, PYTHONDONTWRITEBYTECODE: '1'}, encoding: 'utf8',
}));
const [submenu, back, up, down] = payload.items;
const tile = path.resolve(__dirname, '../scripts/iterm_tile.js');

test('the Mac submenu sits on the Media bar with a laptop symbol and a Go Back item', () => {
    assert.deepEqual([submenu.BTTTriggerType, submenu.BTTTriggerParentUUID, submenu.BTTOrder],
        [774, payload.media_uuid, undefined]);
    assert.equal(submenu.BTTMenuConfig.BTTMenuItemSFSymbolName, 'laptopcomputer');
    assert.equal(submenu.BTTMenuConfig.BTTMenuItemShowIcon, 1);
    assert.equal(back.BTTTriggerType, 777);
    assert.ok([back, up, down].every(item => item.BTTTriggerParentUUID === submenu.BTTUUID));
    assert.deepEqual([payload.noun, payload.title, payload.backup_prefix], ['Mac', 'Media > Mac', 'btt-mac-menu-backup-']);
});

test('iTerm↑ tiles on the external monitor and iTerm↓ on the MacBook display', () => {
    for (const [item, name, arrow, target] of [[up, 'iTerm↑', '8593', 'external'], [down, 'iTerm↓', '8595', 'builtin']]) {
        assert.equal(item.BTTMenuName, name);
        assert.equal(item.BTTMenuConfig.BTTMenuElementIdentifier, name);
        const [action] = item.BTTMenuItemActions;
        assert.equal(action.BTTPredefinedActionType, 206);
        assert.deepEqual(shellArguments(action.BTTShellTaskActionScript),
            ['/usr/bin/osascript', '-l', 'JavaScript', tile, target]);
        // Two lines, the arrow under "iTerm", with no "?" fallback after the \uc0 escape.
        assert.match(item.BTTMenuConfig.BTTMenuAttributedText, new RegExp(`\\\\cf2 iTerm\\\\uc0\\\\u8232 \\\\u${arrow} }$`));
    }
    assert.deepEqual([up.BTTOrder, down.BTTOrder], [1, 2]);
});
