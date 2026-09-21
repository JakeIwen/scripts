#!/usr/bin/env python3
"""Install Escape dismissal for Media's persistent dropdowns, never its main bar."""
from __future__ import annotations

from contextlib import closing
import json
import os
from pathlib import Path
import subprocess
import time
import uuid

from stabilize_media import ROOT, MEDIA, connect, current_database, records
from repair_rps import backup_configuration

SHORTCUT = str(uuid.uuid5(uuid.UUID('bd9b2717-342e-482e-b7a4-9abdc4177937'), 'media-dropdown-escape')).upper()
MENUS = [
    ('DAFF4296-EE87-5CCC-A682-50B2865D6C5A', 'media-notes-recent-dropdown'),
    ('1DF07B14-A4BB-5533-8679-BE46692E70AD', 'media-notes-pinned-dropdown'),
    ('C697E709-678E-56B9-9C17-4464F8E92145', 'performance-audio-tools-dropdown'),
]
WORKER = Path(__file__).with_suffix('.js')
PASSTHROUGH = 'BTTKeyboardShortcutPerformDefaultOnAdvancedConditionMismatch'


def definition(order=0):
    names = [value for pair in MENUS for value in pair]
    condition = ' OR '.join('visible_floating_menu_identifiers CONTAINS '+json.dumps(value) for value in names)
    script = 'async function dismissMediaDropdowns() {\n'
    script += ' const menus = '+json.dumps([uid for uid, _ in MENUS])+';\n'
    script += ''' await Promise.all(menus.map(id => trigger_action({json: JSON.stringify({
  BTTPredefinedActionType: 387,
  BTTAdditionalActionData: {BTTMenuActionMenuID: id,
   BTTMenuActionTriggerHoveredOnHide: 0, BTTMenuActionCloseSubmenuOnHide: 1,
   BTTMenuActionReleaseFromMemory: 0}
 })})));
 return 'Dismissed Media dropdowns';
}'''
    return {'names': names, 'definition': {
        'BTTUUID': SHORTCUT, 'BTTTriggerType': 0, 'BTTTriggerClass': 'BTTTriggerTypeKeyboardShortcut',
        'BTTAppBundleIdentifier': 'BT.G', 'BTTEnabled': 0, 'BTTEnabled2': 0,
        'BTTActionCategory': 0, 'BTTOrder': order, 'BTTTriggerOnDown': 1,
        'BTTShortcutKeyCode': 53, 'BTTShortcutModifierKeys': 0, 'BTTShortcutScope': 0,
        'BTTShortcutAdvancedModifierKeys': '0', 'BTTAdditionalConfiguration': '0',
        'BTTAutoAdaptToKeyboardLayout': 0,
        'BTTGestureNotes': 'Escape: dismiss visible Media dropdowns; otherwise pass through',
        'BTTTriggerConditionsFormat': condition, PASSTHROUGH: True,
        'BTTAdditionalDataJSON': {PASSTHROUGH: True},
        'BTTPredefinedActionType': 281,
        'BTTAdditionalActionData': {'BTTScriptType': 3, 'BTTScriptLocation': 0,
            'BTTAppleScriptUsePath': False, 'BTTAppleScriptRunInBackground': True,
            'BTTScriptFunctionToCall': 'dismissMediaDropdowns', 'BTTAppleScriptString': script},
    }}


def preflight(database):
    saved = records(database)
    preset = saved[MEDIA]['ZBELONGSTOPRESET2']
    for uid, name in MENUS:
        row = saved.get(uid)
        if not row or row['ZGESTURETYPE'] != 767 or row['parent'] is not None or \
                row['ZBELONGSTOPRESET2'] != preset or row['config'].get('BTTMenuElementIdentifier') != name:
            raise RuntimeError('Expected Media dropdown changed: '+name+'; no Escape changes made.')
    with closing(connect(database)) as connection:
        conflicts = list(connection.execute('''SELECT t.ZUNIQUEIDENTIFIER FROM ZBTTBASEENTITY t
            JOIN ZBTTBASEENTITY p ON t.ZBELONGSTOPRESET2=p.Z_PK
            WHERE t.ZGESTURETYPE=0 AND t.ZKEYCODE=53 AND COALESCE(t.ZMODIFIERKEYS,0)=0
              AND t.ZUNIQUEIDENTIFIER<>? AND p.ZACTIVATED<>0
              AND COALESCE(t.ZISENABLED,1)<>0 AND COALESCE(t.ZENABLEDNEW,1)<>0''', (SHORTCUT,)))
        if conflicts:
            raise RuntimeError('An existing plain-Escape shortcut needs review; no Escape changes made.')
    existing = saved.get(SHORTCUT)
    order = existing['ZORDER'] if existing else max(
        [r['ZORDER'] or 0 for r in saved.values() if r['ZBELONGSTOPRESET2'] == preset and r['ZGESTURETYPE'] == 0]+[-1])+1
    return preset, definition(order), bool(existing)


def disk_check(database, plan, preset, require_enabled=False):
    row = records(database).get(SHORTCUT)
    if not row:
        return False
    wanted = plan['definition']
    if row['parent'] is not None or row['ZBELONGSTOPRESET2'] != preset or row['ZGESTURETYPE'] != 0 or row['ZACTION'] != 281:
        raise RuntimeError('Escape shortcut was stored with the wrong scope/action.')
    data = json.loads(row['ZACTIONDATA']) if row['ZACTIONDATA'] else {}
    if any(data.get(k) != v for k, v in wanted['BTTAdditionalActionData'].items()):
        raise RuntimeError('Escape script did not persist correctly.')
    if not row['config'].get(PASSTHROUGH):
        raise RuntimeError('Escape passthrough did not persist; shortcut will not be enabled.')
    with closing(connect(database)) as connection:
        fields = connection.execute('''SELECT ZKEYCODE,ZMODIFIERKEYS,ZTRIGGERONDOWN,ZACTIONCATEGORY,
            ZCONDITIONS,ZISENABLED,ZENABLEDNEW FROM ZBTTBASEENTITY WHERE ZUNIQUEIDENTIFIER=?''', (SHORTCUT,)).fetchone()
    if fields['ZKEYCODE'] != 53 or fields['ZMODIFIERKEYS'] != 0 or fields['ZTRIGGERONDOWN'] != 1 or \
            (fields['ZACTIONCATEGORY'] or 0) != 0 or not fields['ZCONDITIONS']:
        raise RuntimeError('Escape key/visibility condition did not persist; shortcut will not be enabled.')
    return not require_enabled or (fields['ZISENABLED'] == 1 and fields['ZENABLEDNEW'] == 1)


def worker(mode, path):
    result = subprocess.run(['/usr/bin/osascript', '-l', 'JavaScript', str(WORKER), mode, str(path)],
                            capture_output=True, text=True, timeout=45)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or 'Escape API request failed.')
    return result.stdout.strip()


def wait_saved(database, plan, preset, enabled=False):
    deadline = time.monotonic()+5
    while not disk_check(database, plan, preset, enabled):
        if time.monotonic() >= deadline:
            raise RuntimeError('Escape shortcut has not been saved to disk.')
        time.sleep(.1)


def install(inspect=False):
    os.umask(0o077)
    database = current_database()
    preset, plan, exists = preflight(database)
    if inspect:
        print(json.dumps({'escape_shortcut_saved': exists, 'menus': [name for _, name in MENUS]}))
        return
    if exists:
        disk_check(database, plan, preset)
    path = backup_configuration(database, plan, prefix='btt-menu-escape-backup-')
    print('Verified full BTT backup for Escape: '+str(path.parent), flush=True)
    _, fresh, now_exists = preflight(database)
    if fresh != plan or exists != now_exists:
        raise RuntimeError('Shortcut configuration changed during backup; no Escape changes made.')
    # Truth-table test and disabled API/disk checks precede enabling any key hook.
    try:
        worker('condition-test', path)
        worker('verify' if exists else 'create', path)
        wait_saved(database, plan, preset)
        worker('enable', path)
        wait_saved(database, plan, preset, enabled=True)
    except (RuntimeError, subprocess.SubprocessError) as error:
        try:
            worker('disable', path)
            disabled = 'The Escape shortcut was disabled.'
        except (RuntimeError, subprocess.SubprocessError) as disable_error:
            disabled = 'Could not confirm Escape is disabled: '+str(disable_error)
        raise RuntimeError(str(error)+'\n'+disabled+'\nEscape activation failed; backup: '+str(path.parent)) from error
    print('Verified saved Escape dismissal for Recent Notes, Pinned Notes and Performance Audio. No sync executed.')


if __name__ == '__main__':
    install()
