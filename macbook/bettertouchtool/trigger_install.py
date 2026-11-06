"""Guarded install of top-level BTT triggers under fixed UUIDs, through BTT's socket.

Shared by the keyboard-trigger installers (install_text_editing_keys.py,
install_finder_paste.py). Each trigger is created disabled, checked in the API
readback and in the saved store, and only then enabled; a failure leaves every
trigger of the plan disabled.
"""
from __future__ import annotations

from contextlib import closing
import json
import subprocess
import time
from typing import NamedTuple

from btt_common import active_triggers, backup_configuration, connect, records
import btt_socket
import text_editing_conditions as conditions

PASSTHROUGH = 'BTTKeyboardShortcutPerformDefaultOnAdvancedConditionMismatch'
KEYBOARD = 0
SHIFT, COMMAND = 1 << 17, 1 << 20
GLOBAL = 'BT.G'


def disjoint_trigger_pairs():
    """Trigger pairs that may share a key because their conditions never overlap.

    Owned by the editing-keys installer (Finder file focus vs writable text focus);
    resolved lazily because both installers import this module.
    """
    from install_text_editing_keys import DISJOINT_TRIGGER_PAIRS
    return DISJOINT_TRIGGER_PAIRS


class RuntimeHook(NamedTuple):
    """Optional JS an installer splices into runtime_action's script.

    before runs ahead of the source reads; after runs once `result` holds the
    outcome (a parsed {ok, ...} object, or a synthesized failure when the
    runtime threw or returned nothing usable). warning_title, when set, titles
    the notification for an ok outcome that carries a warning.
    """
    before: str = ''
    after: str = ''
    warning_title: str | None = None


def runtime_action(function, entry, options, title, runtime, *, hook=None):
    """A Real-JS action running one runtime entry, which returns an {ok, result|error} outcome.

    hook(entry, options) may return a RuntimeHook; the shared transport turns any
    thrown, rejected or malformed runtime outcome into a failed result.
    """
    call = '\n%s(%s);' % (entry, json.dumps(options))
    extra = hook(entry, options) if hook else RuntimeHook()
    notify_title = json.dumps(title) if extra.warning_title is None else (
        f'result.ok ? {json.dumps(extra.warning_title)} : {json.dumps(title)}')
    # display_notification uses the unnamed body parameter, not "text":
    # https://docs.folivora.ai/docs/scripting/cli/ (command #6).
    # A script that fails inside runJXA comes back as {NSLocalizedDescription} instead.
    # Only a hook that consumes failures needs normalized outcomes. Preserve the
    # existing no-hook transport verbatim (Finder's installed action is a contract).
    reads = f'const sources = await Promise.all({json.dumps(list(runtime))}.map(path => readFile(path)));'
    outcome = f'JSON.parse(await runJXA(sources.join("\\n") + {json.dumps(call)}))'
    load = f'    {reads}\n    const result = {outcome};\n'
    if extra.after:
        load = (f'    let result;\n    try {{\n        {reads}\n'
                f'        result = {outcome};\n'
                '        if (result === null || typeof result !== "object") throw new Error("Runtime returned no outcome");\n'
                '    } catch (error) {\n'
                '        result = {ok: false, error: String(error && error.message || error)};\n'
                '    }\n')
    script = (f'async function {function}() {{\n'
              f'{extra.before}{load}'
              f'{extra.after}'
              f'    const error = result.error || result.NSLocalizedDescription || result.warning;\n'
              f'    if (!result.ok || result.warning) await callBTT("display_notification", {{title: {notify_title}, "": error}});\n'
              f'    return result.ok ? result.result : error;\n}}')
    # Separate contexts: triggers sharing a function name must not share state.
    return {'BTTScriptType': 3, 'BTTScriptLocation': 0, 'BTTAppleScriptUsePath': False,
            'BTTAppleScriptRunInBackground': True, 'BTTActionJSRunInSeparateContext': True,
            'BTTScriptFunctionToCall': function, 'BTTAppleScriptString': script}


def keyboard_trigger(uid, keycode, modifiers, condition, notes, action, app=GLOBAL):
    """A disabled keyboard trigger that lets the key through when its condition fails."""
    return {'BTTUUID': uid, 'BTTTriggerType': KEYBOARD,
            'BTTTriggerClass': 'BTTTriggerTypeKeyboardShortcut', 'BTTAppBundleIdentifier': app,
            'BTTEnabled': 0, 'BTTEnabled2': 0, 'BTTActionCategory': 0, 'BTTTriggerOnDown': 1,
            'BTTShortcutKeyCode': keycode, 'BTTShortcutModifierKeys': modifiers, 'BTTShortcutScope': 0,
            'BTTShortcutAdvancedModifierKeys': '0', 'BTTAdditionalConfiguration': str(modifiers),
            'BTTAutoAdaptToKeyboardLayout': 0, 'BTTGestureNotes': notes,
            'BTTTriggerConditionsFormat': condition, PASSTHROUGH: True,
            'BTTAdditionalDataJSON': {PASSTHROUGH: True},
            'BTTPredefinedActionType': 281, 'BTTAdditionalActionData': action}


def action_data(value):
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
    data = action_data(actual.get('BTTAdditionalActionData'))
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
        data = action_data(record['ZACTIONDATA'] or '{}')
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
    """Other enabled shortcuts in an active preset, in any app, on one of the plan's keys."""
    ours = {d['BTTUUID'] for d in plan}
    disjoint = disjoint_trigger_pairs()
    keys = {(d['BTTShortcutKeyCode'], d['BTTShortcutModifierKeys']): d['BTTUUID']
            for d in plan if d['BTTTriggerType'] == KEYBOARD}
    return sorted(row['ZUNIQUEIDENTIFIER'] for row in active_triggers(database, KEYBOARD)
                  if row['ZUNIQUEIDENTIFIER'] not in ours
                  and (key := (row['ZKEYCODE'], row['ZMODIFIERKEYS'] or 0)) in keys
                  and frozenset({row['ZUNIQUEIDENTIFIER'], keys[key]}) not in disjoint)


def wait_saved(database, plan, enabled, label):
    deadline = time.monotonic() + 45
    while True:
        errors = saved_errors(database, plan, enabled)
        if not errors:
            return
        if time.monotonic() >= deadline:
            raise RuntimeError('BTT has not saved the %s: %s' % (label, '; '.join(errors)))
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


def install(database, plan, label, backups, prefix):
    """Create or recreate disabled, verify runtime and disk, then enable; returns written UUIDs."""
    current = {d['BTTUUID']: btt_socket.exported_trigger(d['BTTUUID']) for d in plan}
    stale = [d for d in plan if current[d['BTTUUID']] is not None and export_errors(d, current[d['BTTUUID']])]
    missing = [d for d in plan if current[d['BTTUUID']] is None]
    if not stale and not missing and not saved_errors(database, plan, 1):
        return []
    backups.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = backup_configuration(database, {'definitions': plan}, prefix=prefix, directory_root=backups)
    print('Verified private configuration backup: ' + str(path), flush=True)
    touched = []
    try:
        for wanted in stale:
            # Only the plan's own UUIDs are ever deleted, and only to rewrite them.
            btt_socket.call('delete_trigger', uuid=wanted['BTTUUID'])
            if btt_socket.exported_trigger(wanted['BTTUUID']) is not None:
                raise RuntimeError('BTT did not remove the outdated trigger ' + wanted['BTTUUID'])
        for wanted in stale + missing:
            btt_socket.call('add_new_trigger', json=json.dumps(wanted))
            touched.append(wanted['BTTUUID'])
        for wanted in plan:
            actual = btt_socket.exported_trigger(wanted['BTTUUID'])
            errors = export_errors(wanted, actual) if actual else ['not created']
            if errors:
                raise RuntimeError('Readback failed (%s): %s' % (wanted['BTTGestureNotes'], ', '.join(errors)))
        wait_saved(database, plan, None, label)
        touched += set_enabled(plan, 1)
        for wanted in plan:
            if export_errors(wanted, btt_socket.exported_trigger(wanted['BTTUUID'])):
                raise RuntimeError('Enabling changed a trigger: ' + wanted['BTTGestureNotes'])
        wait_saved(database, plan, 1, label)
    except (RuntimeError, subprocess.SubprocessError, ValueError) as error:
        try:
            set_enabled(plan, 0)
            state = 'All %s are disabled.' % label
        except (RuntimeError, subprocess.SubprocessError, ValueError) as disable_error:
            state = 'Could not confirm the %s are disabled: %s' % (label, disable_error)
        raise RuntimeError('%s\n%s\nBackup: %s' % (error, state, path.parent)) from error
    return sorted(set(touched))
