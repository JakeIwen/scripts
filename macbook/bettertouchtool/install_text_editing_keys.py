#!/usr/bin/env python3
"""Install system-wide editing keys through BTT's local socket API.

Cmd-X with no selection cuts the caret's line; ' " ` ( [ { typed over a
selection wrap it. Each key passes through to the app unless BTT sees an
editable selection, so code editors and terminals keep their own behavior.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
import uuid

from btt_common import active_triggers, backup_configuration, connect, current_database, records
from btt_guard.installer_checkpoint import after_install, before_install
import btt_socket
import text_editing_conditions as conditions

DIRECTORY = Path(__file__).resolve().parent
BACKUPS = DIRECTORY / '.local/text-editing-key-backups'
NAMESPACE = uuid.UUID('5d0f6a3e-2b8c-4f71-9e2a-7c4b1d9e6f30')
PASSTHROUGH = 'BTTKeyboardShortcutPerformDefaultOnAdvancedConditionMismatch'
KEYBOARD, APP_CHANGED = 0, 779
SHIFT, COMMAND = 1 << 17, 1 << 20
# The runtime: text_editing_keys.js builds on text_selection.js's AX helpers.
RUNTIME = tuple(str(DIRECTORY / name) for name in ('text_selection.js', 'text_editing_keys.js'))

WRAP_KEYS = (("'", 39, 0), ('"', 39, SHIFT), ('`', 50, 0), ('(', 25, SHIFT), ('[', 33, 0), ('{', 33, SHIFT))
CUT_KEY = (7, COMMAND)
# ctrl-A, ctrl-shift-E, shift-Right as BTT keycode lists: select the line and its break.
CUT_LINE_SHORTCUTS = ('59,0', '59,56,14', '56,124')

# Paragraph keys follow each app's own idea of a line (Firefox: the visual line);
# shift-Right takes the line break so the line is removed rather than emptied.
SELECT_LINE = """    for (const shortcut of %s) {
        await trigger_action({json: JSON.stringify({BTTActionCategory: 0, BTTIsPureAction: true,
            BTTPredefinedActionType: 264, BTTShortcutToSend: shortcut,
            BTTAdditionalActionData: {BTTActionSendKeyboardShortcutIncludeCurrentModifiers: false}}),
            wait_for_reply: true});
    }
""" % json.dumps(list(CUT_LINE_SHORTCUTS))


def stable_uuid(name):
    return str(uuid.uuid5(NAMESPACE, name)).upper()


def _action(function, entry, options, title, before=''):
    """A Real-JS action running one runtime entry, which returns an {ok, result|error} outcome."""
    call = '\n%s(%s);' % (entry, json.dumps(options))
    # A script that fails inside runJXA comes back as {NSLocalizedDescription} instead.
    script = (f'async function {function}() {{\n{before}'
              f'    const sources = await Promise.all({json.dumps(list(RUNTIME))}.map(path => readFile(path)));\n'
              f'    const result = JSON.parse(await runJXA(sources.join("\\n") + {json.dumps(call)}));\n'
              f'    const error = result.error || result.NSLocalizedDescription;\n'
              f'    if (!result.ok) await callBTT("display_notification", {{title: {json.dumps(title)}, "": error}});\n'
              f'    return result.ok ? result.result : error;\n}}')
    # Separate contexts: all six wrap entries share one function name.
    return {'BTTScriptType': 3, 'BTTScriptLocation': 0, 'BTTAppleScriptUsePath': False,
            'BTTAppleScriptRunInBackground': True, 'BTTActionJSRunInSeparateContext': True,
            'BTTScriptFunctionToCall': function, 'BTTAppleScriptString': script}


def _keyboard(name, keycode, modifiers, condition, notes, action):
    return {'BTTUUID': stable_uuid(name), 'BTTTriggerType': KEYBOARD,
            'BTTTriggerClass': 'BTTTriggerTypeKeyboardShortcut', 'BTTAppBundleIdentifier': 'BT.G',
            'BTTEnabled': 0, 'BTTEnabled2': 0, 'BTTActionCategory': 0, 'BTTTriggerOnDown': 1,
            'BTTShortcutKeyCode': keycode, 'BTTShortcutModifierKeys': modifiers, 'BTTShortcutScope': 0,
            'BTTShortcutAdvancedModifierKeys': '0', 'BTTAdditionalConfiguration': str(modifiers),
            'BTTAutoAdaptToKeyboardLayout': 0, 'BTTGestureNotes': notes,
            'BTTTriggerConditionsFormat': condition, PASSTHROUGH: True,
            'BTTAdditionalDataJSON': {PASSTHROUGH: True},
            'BTTPredefinedActionType': 281, 'BTTAdditionalActionData': action}


def definitions():
    """Wire records sent to add_new_trigger; every trigger is created disabled."""
    directory = str(DIRECTORY)
    result = [_keyboard('wrap ' + char, keycode, modifiers, conditions.WRAP_CONDITION,
                        'Editing keys: wrap the selection in ' + char,
                        _action('wrapSelectedText', 'runEditingKey',
                                {'kind': 'wrap', 'open': char, 'directory': directory}, 'Wrap stopped'))
              for char, keycode, modifiers in WRAP_KEYS]
    result.append(_keyboard('cut line', *CUT_KEY, conditions.CUT_CONDITION,
                            'Editing keys: Cmd-X with no selection cuts the line',
                            _action('cutCurrentLine', 'runEditingKey', {'kind': 'cut-line', 'directory': directory},
                                    'Cut line stopped', before=SELECT_LINE)))
    result.append({'BTTUUID': stable_uuid('prepare text accessibility'), 'BTTTriggerType': APP_CHANGED,
                   'BTTTriggerClass': 'BTTTriggerTypeOtherTriggers', 'BTTAppBundleIdentifier': 'BT.G',
                   'BTTEnabled': 0, 'BTTEnabled2': 0, 'BTTActionCategory': 0,
                   'BTTGestureNotes': 'Editing keys: expose Electron text to Accessibility on app switch',
                   'BTTPredefinedActionType': 281,
                   'BTTAdditionalActionData': _action('prepareTextAccessibility', 'runAppSwitch',
                                                      {'excluded': list(conditions.EXCLUDED_APPS)},
                                                      'Editing keys stopped')})
    return result


def _action_data(value):
    data = json.loads(value) if isinstance(value, (str, bytes)) else dict(value or {})
    # API exports alias this key; the saved record and the editor use BTTAppleScriptString.
    if 'BTTScriptString' in data and 'BTTAppleScriptString' not in data:
        data['BTTAppleScriptString'] = data.pop('BTTScriptString')
    return data


def export_errors(wanted, actual):
    keys = ['BTTTriggerType', 'BTTPredefinedActionType']
    if wanted['BTTTriggerType'] == KEYBOARD:
        keys += ['BTTShortcutKeyCode', 'BTTShortcutModifierKeys', 'BTTTriggerOnDown']
    errors = [key for key in keys if actual.get(key) != wanted[key]]
    data = _action_data(actual.get('BTTAdditionalActionData'))
    errors += ['action ' + key for key, value in wanted['BTTAdditionalActionData'].items() if data.get(key) != value]
    if any(actual.get(key) for key in ('BTTActionsToExecute', 'BTTAdditionalActions', 'BTTMenuItemActions')):
        errors.append('unexpected extra actions')
    if wanted['BTTTriggerType'] == KEYBOARD:
        if not (actual.get(PASSTHROUGH) or (actual.get('BTTAdditionalDataJSON') or {}).get(PASSTHROUGH)):
            errors.append('pass-through')
        format_ = actual.get('BTTTriggerConditionsFormat') or actual.get('BTTTriggerConditionsFormatReadOnly')
        if not format_ or conditions.canonical(format_) != conditions.canonical(wanted['BTTTriggerConditionsFormat']):
            errors.append('condition')
    return errors


def saved_errors(database, plan, enabled):
    """Disk state after BTT's asynchronous save; enabled=None skips the enable flags."""
    saved = records(database)
    with closing(connect(database)) as connection:
        fields = {row['ZUNIQUEIDENTIFIER']: dict(row) for row in connection.execute(
            '''SELECT t.ZUNIQUEIDENTIFIER, t.ZKEYCODE, t.ZMODIFIERKEYS, t.ZTRIGGERONDOWN, t.ZISENABLED,
                      t.ZCONDITIONS IS NOT NULL AS conditioned, p.ZACTIVATED AS activated
               FROM ZBTTBASEENTITY t LEFT JOIN ZBTTBASEENTITY p ON t.ZBELONGSTOPRESET2=p.Z_PK''')
            if row['ZUNIQUEIDENTIFIER']}
    errors = []
    for wanted in plan:
        uid = wanted['BTTUUID']
        record, field = saved.get(uid), fields.get(uid)
        if not record:
            errors.append(uid + ': not saved')
            continue
        data = _action_data(record['ZACTIONDATA'] or '{}')
        if (record['parent'] is not None or record['ZGESTURETYPE'] != wanted['BTTTriggerType']
                or record['ZACTION'] != 281 or not field['activated']
                or any(data.get(k) != v for k, v in wanted['BTTAdditionalActionData'].items())):
            errors.append(uid + ': saved scope/action differs')
        elif wanted['BTTTriggerType'] == KEYBOARD and (
                (field['ZKEYCODE'], field['ZMODIFIERKEYS'], field['ZTRIGGERONDOWN'])
                != (wanted['BTTShortcutKeyCode'], wanted['BTTShortcutModifierKeys'], 1)
                or not field['conditioned'] or not record['config'].get(PASSTHROUGH)):
            errors.append(uid + ': saved key/condition/pass-through differs')
        elif enabled is not None and (record['ZENABLEDNEW'] != enabled
                                      or field['ZISENABLED'] not in (None, enabled)):
            errors.append(uid + ': saved enabled state differs')
    return errors


def conflicts(database, plan):
    ours = {d['BTTUUID'] for d in plan}
    keys = {(d['BTTShortcutKeyCode'], d['BTTShortcutModifierKeys']) for d in plan if d['BTTTriggerType'] == KEYBOARD}
    return sorted(row['ZUNIQUEIDENTIFIER'] for row in active_triggers(database, KEYBOARD)
                  if row['ZUNIQUEIDENTIFIER'] not in ours and (row['ZKEYCODE'], row['ZMODIFIERKEYS'] or 0) in keys)


def wait_saved(database, plan, enabled):
    deadline = time.monotonic() + 45
    while True:
        errors = saved_errors(database, plan, enabled)
        if not errors:
            return
        if time.monotonic() >= deadline:
            raise RuntimeError('BTT has not saved the editing keys: ' + '; '.join(errors))
        time.sleep(0.5)


def set_enabled(plan, value):
    """Flip only the enable flags; returns the UUIDs written."""
    touched = []
    for wanted in plan:
        current = btt_socket.exported_trigger(wanted['BTTUUID'])
        # Exports omit BTTEnabled when it is 1.
        if current is None or int(current.get('BTTEnabled', 1)) == value:
            continue
        btt_socket.call('update_trigger', uuid=wanted['BTTUUID'],
                        json=json.dumps({'BTTEnabled': value, 'BTTEnabled2': value}))
        current = btt_socket.exported_trigger(wanted['BTTUUID'])
        if current is None or int(current.get('BTTEnabled', 1)) != value:
            raise RuntimeError('BTT did not apply the enable flag to ' + wanted['BTTUUID'])
        touched.append(wanted['BTTUUID'])
    return touched


def install(database, plan):
    """Create or recreate disabled, verify runtime and disk, then enable; returns written UUIDs."""
    current = {d['BTTUUID']: btt_socket.exported_trigger(d['BTTUUID']) for d in plan}
    stale = [d for d in plan if current[d['BTTUUID']] is not None and export_errors(d, current[d['BTTUUID']])]
    missing = [d for d in plan if current[d['BTTUUID']] is None]
    if not stale and not missing and not saved_errors(database, plan, 1):
        return []
    BACKUPS.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = backup_configuration(database, {'definitions': plan}, prefix='keys-', directory_root=BACKUPS)
    print('Verified private configuration backup: ' + str(path), flush=True)
    touched = []
    try:
        for wanted in stale:
            # Only this installer's own UUIDs are ever deleted, and only to rewrite them.
            btt_socket.call('delete_trigger', uuid=wanted['BTTUUID'])
            if btt_socket.exported_trigger(wanted['BTTUUID']) is not None:
                raise RuntimeError('BTT did not remove the outdated editing key ' + wanted['BTTUUID'])
        for wanted in stale + missing:
            btt_socket.call('add_new_trigger', json=json.dumps(wanted))
            touched.append(wanted['BTTUUID'])
        for wanted in plan:
            actual = btt_socket.exported_trigger(wanted['BTTUUID'])
            errors = export_errors(wanted, actual) if actual else ['not created']
            if errors:
                raise RuntimeError('Editing key readback failed (%s): %s' % (wanted['BTTGestureNotes'], ', '.join(errors)))
        wait_saved(database, plan, None)
        touched += set_enabled(plan, 1)
        for wanted in plan:
            if export_errors(wanted, btt_socket.exported_trigger(wanted['BTTUUID'])):
                raise RuntimeError('Enabling changed an editing key: ' + wanted['BTTGestureNotes'])
        wait_saved(database, plan, 1)
    except (RuntimeError, subprocess.SubprocessError, ValueError) as error:
        try:
            set_enabled(plan, 0)
            state = 'All editing keys are disabled.'
        except (RuntimeError, subprocess.SubprocessError, ValueError) as disable_error:
            state = 'Could not confirm the editing keys are disabled: ' + str(disable_error)
        raise RuntimeError('%s\n%s\nBackup: %s' % (error, state, path.parent)) from error
    return sorted(set(touched))


def inspect(database, plan):
    saved = records(database)
    for wanted in plan:
        record = saved.get(wanted['BTTUUID'])
        state = 'missing' if not record else 'enabled' if record['ZENABLEDNEW'] == 1 else 'disabled'
        print('%-9s %s' % (state, wanted['BTTGestureNotes']))
    for uid in conflicts(database, plan):
        print('conflict  another enabled shortcut uses one of these keys: ' + uid)
    print('Excluded apps: %d bundle ids plus %s*' % (len(conditions.EXCLUDED_APPS),
                                                     ', '.join(conditions.EXCLUDED_PREFIXES)))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--apply', action='store_true', help='install or repair, then enable')
    mode.add_argument('--disable', action='store_true', help='disable every editing key, keeping them saved')
    mode.add_argument('--inspect', action='store_true', help='show saved state (default)')
    parser.add_argument('--no-checkpoint', action='store_true', help='do not advance BTT Guard after verification')
    args = parser.parse_args(argv)
    os.umask(0o077)
    database, plan = current_database(), definitions()
    if not (args.apply or args.disable):
        inspect(database, plan)
        return 0
    errors = conditions.truth_table_errors()
    if errors:
        raise RuntimeError('Trigger conditions failed their truth table; no changes made:\n' + '\n'.join(errors))
    if args.apply and conflicts(database, plan):
        raise RuntimeError('Another enabled shortcut uses one of these keys; no changes made. Run --inspect.')
    baseline = before_install(no_checkpoint=args.no_checkpoint)
    if args.disable:
        touched = set_enabled(plan, 0)
        wait_saved(database, [d for d in plan if d['BTTUUID'] in records(database)], 0)
        print('Editing keys disabled.' if touched else 'Editing keys were already disabled or not installed.')
    else:
        touched = install(database, plan)
        print('Verified saved editing keys.' if touched else 'Editing keys already installed, saved and enabled.')
    after_install(baseline, touched, 'install_text_editing_keys', wrote=bool(touched))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (RuntimeError, OSError, ValueError, sqlite3.Error, subprocess.SubprocessError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
