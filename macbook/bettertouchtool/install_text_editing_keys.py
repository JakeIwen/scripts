#!/usr/bin/env python3
"""Install system-wide editing keys through BTT's local socket API.

Cmd-X with no selection cuts the caret's line, Cmd-C copies it and leaves the
caret where it was; tagged Cmd-V pastes above the line; ' " ` ( [ { over a
selection wrap it. Each key passes
through to the app unless BTT sees an editable selection, so code editors and
terminals keep their own behavior.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import uuid

from btt_common import current_database, records
from btt_guard.installer_checkpoint import after_install, before_install
import text_editing_conditions as conditions
import trigger_install as triggers
from trigger_install import COMMAND, SHIFT, conflicts, set_enabled

DIRECTORY = Path(__file__).resolve().parent
BACKUPS = DIRECTORY / '.local/text-editing-key-backups'
LABEL = 'editing keys'
NAMESPACE = uuid.UUID('5d0f6a3e-2b8c-4f71-9e2a-7c4b1d9e6f30')
APP_CHANGED = 779
# The runtime: text_editing_keys.js builds on text_selection.js's AX helpers.
RUNTIME = tuple(str(DIRECTORY / name) for name in ('text_selection.js', 'text_editing_keys.js'))

WRAP_KEYS = (("'", 39, 0), ('"', 39, SHIFT), ('`', 50, 0), ('(', 25, SHIFT), ('[', 33, 0), ('{', 33, SHIFT))
CUT_KEY = (7, COMMAND)
COPY_KEY = (8, COMMAND)
PASTE_KEY = (9, COMMAND)


def stable_uuid(name):
    return str(uuid.uuid5(NAMESPACE, name)).upper()


def _action(function, entry, options, title):
    return triggers.runtime_action(function, entry, options, title, RUNTIME,
                                   line_clipboard_variable=options.get('lineClipboardVariable'))


def _keyboard(name, keycode, modifiers, condition, notes, action):
    return triggers.keyboard_trigger(stable_uuid(name), keycode, modifiers, condition, notes, action)


def definitions():
    """Wire records sent to add_new_trigger; every trigger is created disabled."""
    directory = str(DIRECTORY)
    result = [_keyboard('wrap ' + char, keycode, modifiers, conditions.WRAP_CONDITION,
                        'Editing keys: wrap the selection in ' + char,
                        _action('wrapSelectedText', 'runEditingKey',
                                {'kind': 'wrap', 'open': char, 'directory': directory}, 'Wrap stopped'))
              for char, keycode, modifiers in WRAP_KEYS]
    for name, key, notes, kind, function, title in (
            ('cut line', CUT_KEY, 'Cmd-X with no selection cuts the line', 'cut-line', 'cutCurrentLine', 'Cut line stopped'),
            ('copy line', COPY_KEY, 'Cmd-C with no selection copies the line', 'copy-line', 'copyCurrentLine',
             'Copy line stopped'),
            ('paste line', PASTE_KEY, 'Cmd-V with a line copy pastes above the line', 'paste-line', 'pasteCaretLine',
             'Paste line stopped')):
        condition = conditions.PASTE_CONDITION if kind == 'paste-line' else conditions.CARET_CONDITION
        result.append(_keyboard(name, *key, condition,
                                'Editing keys: ' + notes, _action(function, 'runEditingKey',
                                {'kind': kind, 'directory': directory,
                                 'lineClipboardVariable': conditions.LINE_CLIPBOARD_VARIABLE}, title)))
    result.append({'BTTUUID': stable_uuid('prepare text accessibility'), 'BTTTriggerType': APP_CHANGED,
                   'BTTTriggerClass': 'BTTTriggerTypeOtherTriggers', 'BTTAppBundleIdentifier': 'BT.G',
                   'BTTEnabled': 0, 'BTTEnabled2': 0, 'BTTActionCategory': 0,
                   'BTTGestureNotes': 'Editing keys: expose Electron text to Accessibility on app switch',
                   'BTTPredefinedActionType': 281,
                   'BTTAdditionalActionData': _action('prepareTextAccessibility', 'runAppSwitch',
                                                      {'excluded': list(conditions.EXCLUDED_APPS)},
                                                      'Editing keys stopped')})
    return result


def install(database, plan):
    """Create or recreate disabled, verify runtime and disk, then enable; returns written UUIDs."""
    return triggers.install(database, plan, LABEL, BACKUPS, 'keys-')


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
        triggers.wait_saved(database, [d for d in plan if d['BTTUUID'] in records(database)], 0, LABEL)
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
