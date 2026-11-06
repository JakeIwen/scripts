#!/usr/bin/env python3
"""Install the Finder paste key through BTT's local socket API.

Cmd-V in Finder saves a clipboard image, such as a ctrl-cmd-shift-4 screenshot, as a
PNG in the folder a paste would use; copied files and text paste as before. The key
passes through to the app unless Finder is frontmost with a non-text focus.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

from btt_common import current_database, records
from btt_guard.installer_checkpoint import after_install, before_install
from editing_key_ids import FINDER_PASTE_ID as TRIGGER
import finder_paste_condition as condition
import trigger_install as triggers

DIRECTORY = Path(__file__).resolve().parent
BACKUPS = DIRECTORY / '.local/finder-paste-backups'
LABEL = 'Finder paste triggers'
PASTE_KEY = (9, triggers.COMMAND)  # Cmd-V
# finder_paste.js uses text_selection.js's AX helpers and text_menu_paste.js's Edit-menu lookup.
RUNTIME = tuple(str(DIRECTORY / name) for name in ('text_selection.js', 'text_menu_paste.js', 'finder_paste.js'))


def action(options=None):
    """The trigger's action; options override the pasteboard name and the destination folder."""
    return triggers.runtime_action('pasteInFinder', 'runFinderPaste', options or {}, 'Finder paste stopped', RUNTIME)


def definitions():
    """Wire records sent to add_new_trigger; the trigger is created disabled.

    It is global: add_new_trigger files every new trigger under Global whatever its
    BTTAppBundleIdentifier, so the condition is what keeps it to Finder.
    """
    return [triggers.keyboard_trigger(TRIGGER, *PASTE_KEY, condition.CONDITION,
                                      'Finder paste: Cmd-V saves a clipboard image as a PNG', action())]


def inspect(database, plan):
    saved = records(database)
    for wanted in plan:
        record = saved.get(wanted['BTTUUID'])
        state = 'missing' if not record else 'enabled' if record['ZENABLEDNEW'] == 1 else 'disabled'
        print('%-9s %s (%s)' % (state, wanted['BTTGestureNotes'], wanted['BTTUUID']))
    for uid in triggers.conflicts(database, plan):
        print('conflict  another enabled shortcut uses Cmd-V: ' + uid)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--apply', action='store_true', help='install or repair, then enable')
    mode.add_argument('--disable', action='store_true', help='disable the Finder paste key, keeping it saved')
    mode.add_argument('--inspect', action='store_true', help='show saved state (default)')
    parser.add_argument('--no-checkpoint', action='store_true', help='do not advance BTT Guard after verification')
    args = parser.parse_args(argv)
    os.umask(0o077)
    database, plan = current_database(), definitions()
    if not (args.apply or args.disable):
        inspect(database, plan)
        return 0
    errors = condition.truth_table_errors()
    if errors:
        raise RuntimeError('The trigger condition failed its truth table; no changes made:\n' + '\n'.join(errors))
    if args.apply and triggers.conflicts(database, plan):
        raise RuntimeError('Another enabled shortcut uses Cmd-V; no changes made. Run --inspect.')
    baseline = before_install(no_checkpoint=args.no_checkpoint)
    if args.disable:
        touched = triggers.set_enabled(plan, 0)
        triggers.wait_saved(database, [d for d in plan if d['BTTUUID'] in records(database)], 0, LABEL)
        print('Finder paste key disabled.' if touched else 'Finder paste key was already disabled or not installed.')
    else:
        touched = triggers.install(database, plan, LABEL, BACKUPS, 'finder-paste-')
        print('Verified saved Finder paste key.' if touched else 'Finder paste key already installed, saved and enabled.')
    after_install(baseline, touched, 'install_finder_paste', wrote=bool(touched))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (RuntimeError, OSError, ValueError, sqlite3.Error, subprocess.SubprocessError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
