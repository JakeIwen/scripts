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

from btt_common import backup_configuration, connect, current_database, records
from btt_guard.installer_checkpoint import after_install, before_install

DIRECTORY = Path(__file__).resolve().parent
BTTCLI = '/Applications/BetterTouchTool.app/Contents/SharedSupport/bin/bttcli'
CONDITIONS = DIRECTORY / 'text_editing_conditions.js'
BACKUPS = DIRECTORY / '.local/text-editing-key-backups'
NAMESPACE = uuid.UUID('5d0f6a3e-2b8c-4f71-9e2a-7c4b1d9e6f30')
PASSTHROUGH = 'BTTKeyboardShortcutPerformDefaultOnAdvancedConditionMismatch'
KEYBOARD, APP_CHANGED = 0, 779
SHIFT, COMMAND = 1 << 17, 1 << 20

# Code editors already wrap selections and cut lines; terminals treat keys as input.
EXCLUDED_APPS = (
    'com.microsoft.VSCode', 'com.microsoft.VSCodeInsiders', 'com.todesktop.230313mzl4w4u92',
    'com.exafunction.windsurf', 'dev.zed.Zed', 'dev.zed.Zed-Preview', 'com.sublimetext.4',
    'com.sublimetext.3', 'com.apple.dt.Xcode', 'com.barebones.bbedit', 'com.panic.Nova',
    'com.coteditor.CotEditor', 'org.vim.MacVim', 'org.gnu.Emacs',
    'com.apple.Terminal', 'com.googlecode.iterm2', 'com.mitchellh.ghostty', 'dev.warp.Warp-Stable',
    'net.kovidgoyal.kitty', 'org.alacritty', 'com.github.wez.wezterm')
EXCLUDED_PREFIXES = ('com.jetbrains.',)

WRAP_KEYS = (("'", 39, 0), ('"', 39, SHIFT), ('`', 50, 0), ('(', 25, SHIFT), ('[', 33, 0), ('{', 33, SHIFT))
CUT_KEY = (7, COMMAND)
# ctrl-A, ctrl-shift-E, shift-Right as BTT keycode lists: select the line and its break.
CUT_LINE_SHORTCUTS = ('59,0', '59,56,14', '56,124')


def selection_condition(length_pattern):
    apps = ', '.join(json.dumps(app) for app in EXCLUDED_APPS)
    prefixes = ' OR '.join('BTTActiveAppBundleIdentifier BEGINSWITH ' + json.dumps(p) for p in EXCLUDED_PREFIXES)
    # BTT's focused-element dump marks settable attributes "(W)": read-only text
    # (terminal output, web pages) and apps without a text tree never match.
    details = ("focused_element_details MATCHES '(?s)(?=.*AXSelectedText [(]W[)]:  \").*"
               "AXSelectedTextRange [(]W[)]:  \"pos=[0-9]+ len=" + length_pattern + "\".*'")
    return 'NOT (BTTActiveAppBundleIdentifier IN {' + apps + '} OR ' + prefixes + ') AND ' + details


WRAP_CONDITION = selection_condition('[1-9][0-9]*')
CUT_CONDITION = selection_condition('0')


def _focused(selected_text, selected_range):
    return ('<AXApplication: "App">\n <AXWindow: "Window">\n  <AXTextArea>\n\nAttributes:\n'
            '   AXFocused:  "1"\n   ' + selected_text + '\n   ' + selected_range +
            '\n   AXRole:  "AXTextArea"\n   AXValue (W):  "alpha beta"\n')


# (case, app, focused_element_details, wrap fires, cut fires); shapes copied from live dumps.
CONDITION_CASES = (
    ('selection', 'com.apple.TextEdit',
     _focused('AXSelectedText (W):  "beta"', 'AXSelectedTextRange (W):  "pos=6 len=4"'), True, False),
    ('caret', 'com.apple.TextEdit',
     _focused('AXSelectedText (W):  ""', 'AXSelectedTextRange (W):  "pos=8 len=0"'), False, True),
    ('selection starting with a quote', 'com.google.Chrome',
     _focused('AXSelectedText (W):  ""hi""', 'AXSelectedTextRange (W):  "pos=3 len=4"'), True, False),
    ('multi-line selection', 'org.mozilla.firefox',
     _focused('AXSelectedText (W):  "a\nb"', 'AXSelectedTextRange (W):  "pos=0 len=13"'), True, False),
    ('read-only text', 'com.apple.mail',
     _focused('AXSelectedText:  ""', 'AXSelectedTextRange (W):  "pos=8 len=0"'), False, False),
    ('no text tree yet', 'org.whispersystems.signal-desktop',
     '<AXApplication: "Signal">\n <AXWindow: "Signal">\n\nAttributes:\n   AXRole:  "AXWindow"\n', False, False),
    ('code editor selection', 'com.microsoft.VSCode',
     _focused('AXSelectedText (W):  "beta"', 'AXSelectedTextRange (W):  "pos=6 len=4"'), False, False),
    ('JetBrains caret', 'com.jetbrains.pycharm',
     _focused('AXSelectedText (W):  ""', 'AXSelectedTextRange (W):  "pos=8 len=0"'), False, False),
    ('terminal caret', 'com.googlecode.iterm2',
     _focused('AXSelectedText (W):  ""', 'AXSelectedTextRange (W):  "pos=8 len=0"'), False, False),
    ('nothing focused', '', '', False, False),
)


def stable_uuid(name):
    return str(uuid.uuid5(NAMESPACE, name)).upper()


def _runtime_script(function, call, notify=None, before=''):
    """A Real-JS entry that runs one text_selection.js function in BTT's JXA engine."""
    report = ('    if (!result.ok) await callBTT(\'display_notification\', {title: %s, \'\': result.error});\n'
              '    return result.ok ? result.result : result.error;\n' % json.dumps(notify)
              if notify else '    return result;\n')
    parse = 'JSON.parse(%s)' if notify else '%s'
    return ('async function %s() {\n%s'
            '    const source = await readFile(%s);\n'
            '    const result = %s;\n%s}') % (
        function, before, json.dumps(str(DIRECTORY / 'text_selection.js')),
        parse % ('await runJXA(source + %s)' % json.dumps('\n' + call + ';')), report)


# Paragraph keys follow each app's own idea of a line (Firefox: the visual line);
# shift-Right takes the line break so the line is removed rather than emptied.
SELECT_LINE = '''    for (const shortcut of %s) {
        await trigger_action({json: JSON.stringify({BTTActionCategory: 0, BTTIsPureAction: true,
            BTTPredefinedActionType: 264, BTTShortcutToSend: shortcut,
            BTTAdditionalActionData: {BTTActionSendKeyboardShortcutIncludeCurrentModifiers: false}}),
            wait_for_reply: true});
    }
''' % json.dumps(list(CUT_LINE_SHORTCUTS))


def _action(function, script):
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
    result = []
    for char, keycode, modifiers in WRAP_KEYS:
        options = {'kind': 'wrap', 'open': char, 'directory': str(DIRECTORY)}
        script = _runtime_script('wrapSelectedText', 'runTextTransform(%s)' % json.dumps(options), 'Wrap stopped')
        result.append(_keyboard('wrap ' + char, keycode, modifiers, WRAP_CONDITION,
                                'Editing keys: wrap the selection in ' + char,
                                _action('wrapSelectedText', script)))
    options = {'kind': 'cut-line', 'directory': str(DIRECTORY)}
    script = _runtime_script('cutCurrentLine', 'runTextTransform(%s)' % json.dumps(options), 'Cut line stopped',
                             before=SELECT_LINE)
    result.append(_keyboard('cut line', *CUT_KEY, CUT_CONDITION, 'Editing keys: Cmd-X with no selection cuts the line',
                            _action('cutCurrentLine', script)))
    prepare = _runtime_script('prepareTextAccessibility',
                              'prepareFocusedAppText(%s)' % json.dumps({'excluded': list(EXCLUDED_APPS)}))
    result.append({'BTTUUID': stable_uuid('prepare text accessibility'), 'BTTTriggerType': APP_CHANGED,
                   'BTTTriggerClass': 'BTTTriggerTypeOtherTriggers', 'BTTAppBundleIdentifier': 'BT.G',
                   'BTTEnabled': 0, 'BTTEnabled2': 0, 'BTTActionCategory': 0,
                   'BTTGestureNotes': 'Editing keys: expose Electron text to Accessibility on app switch',
                   'BTTPredefinedActionType': 281,
                   'BTTAdditionalActionData': _action('prepareTextAccessibility', prepare)})
    return result


def evaluate(predicate, cases):
    result = subprocess.run(['/usr/bin/osascript', '-l', 'JavaScript', str(CONDITIONS),
                             json.dumps({'predicate': predicate, 'cases': cases})],
                            capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise RuntimeError('Condition could not be evaluated: ' + (result.stderr.strip() or predicate))
    return json.loads(result.stdout)


def condition_errors():
    variables = [{'BTTActiveAppBundleIdentifier': app, 'focused_element_details': details}
                 for _, app, details, _, _ in CONDITION_CASES]
    errors = []
    for label, predicate, column in (('wrap', WRAP_CONDITION, 3), ('cut', CUT_CONDITION, 4)):
        for case, actual in zip(CONDITION_CASES, evaluate(predicate, variables)['results']):
            if actual != case[column]:
                errors.append('%s condition %s on: %s' % (label, 'fires' if actual else 'misses', case[0]))
    return errors


def btt(command, **parameters):
    result = subprocess.run([BTTCLI, command, *('%s=%s' % item for item in parameters.items())],
                            capture_output=True, text=True, timeout=15)
    if result.returncode:
        raise RuntimeError('BTT socket command failed: ' + command)
    return result.stdout.strip()


def exported(uid):
    value = json.loads(btt('get_trigger', uuid=uid) or '{}')
    hits = [x for x in (value if isinstance(value, list) else [value])
            if isinstance(x, dict) and x.get('BTTUUID') == uid]
    if len(hits) > 1:
        raise RuntimeError('Cannot uniquely export editing key ' + uid)
    return hits[0] if hits else None


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
        if not format_ or evaluate(format_, [])['format'] != evaluate(wanted['BTTTriggerConditionsFormat'], [])['format']:
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
    with closing(connect(database)) as connection:
        rows = connection.execute('''SELECT t.ZUNIQUEIDENTIFIER, t.ZKEYCODE, COALESCE(t.ZMODIFIERKEYS,0) AS mods
            FROM ZBTTBASEENTITY t JOIN ZBTTBASEENTITY p ON t.ZBELONGSTOPRESET2=p.Z_PK
            WHERE t.ZGESTURETYPE=0 AND p.ZACTIVATED<>0
              AND COALESCE(t.ZISENABLED,1)<>0 AND COALESCE(t.ZENABLEDNEW,1)<>0''').fetchall()
    return sorted(row['ZUNIQUEIDENTIFIER'] for row in rows
                  if row['ZUNIQUEIDENTIFIER'] not in ours and (row['ZKEYCODE'], row['mods']) in keys)


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
        current = exported(wanted['BTTUUID'])
        if current is None or int(current.get('BTTEnabled', 1)) == value:
            continue
        btt('update_trigger', uuid=wanted['BTTUUID'], json=json.dumps({'BTTEnabled': value, 'BTTEnabled2': value}))
        current = exported(wanted['BTTUUID'])
        if current is None or int(current.get('BTTEnabled', 1)) != value:
            raise RuntimeError('BTT did not apply the enable flag to ' + wanted['BTTUUID'])
        touched.append(wanted['BTTUUID'])
    return touched


def install(database, plan):
    """Create or recreate disabled, verify runtime and disk, then enable; returns written UUIDs."""
    current = {d['BTTUUID']: exported(d['BTTUUID']) for d in plan}
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
            btt('delete_trigger', uuid=wanted['BTTUUID'])
            if exported(wanted['BTTUUID']) is not None:
                raise RuntimeError('BTT did not remove the outdated editing key ' + wanted['BTTUUID'])
        for wanted in stale + missing:
            btt('add_new_trigger', json=json.dumps(wanted))
            touched.append(wanted['BTTUUID'])
        for wanted in plan:
            actual = exported(wanted['BTTUUID'])
            errors = export_errors(wanted, actual) if actual else ['not created']
            if errors:
                raise RuntimeError('Editing key readback failed (%s): %s' % (wanted['BTTGestureNotes'], ', '.join(errors)))
        wait_saved(database, plan, None)
        touched += set_enabled(plan, 1)
        for wanted in plan:
            if export_errors(wanted, exported(wanted['BTTUUID'])):
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
    print('Excluded apps: %d bundle ids plus %s*' % (len(EXCLUDED_APPS), ', '.join(EXCLUDED_PREFIXES)))


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
    errors = condition_errors()
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
