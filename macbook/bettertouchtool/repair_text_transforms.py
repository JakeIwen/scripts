#!/usr/bin/env python3
"""Install selection-only text transforms through BTT's local socket API."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time

from btt_common import backup_configuration, connect, current_database, records
from btt_guard.installer_checkpoint import before_install, after_install, parse_edit_receipt

DIRECTORY = Path(__file__).resolve().parent
TEXT_MENU = 'F08B0C3E-B629-441B-BFAE-3E1E43F390AA'
CASE_LIBRARY = Path.home() / 'dev/case-anything/dist/cjs/index.cjs'


def worker(mode, argument=None):
    """The JS producer owns TextRepairPlan; Python persists it without rewriting it."""
    result = subprocess.run(
        ['/usr/bin/osascript', '-l', 'JavaScript', str(DIRECTORY / 'repair_text_transforms.js'),
         mode, str(DIRECTORY), str(CASE_LIBRARY), *([str(argument)] if argument else [])],
        capture_output=True, text=True, timeout=90)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or 'BTT text-transform worker failed.')
    return result.stdout.strip()


def saved_errors(database, plan):
    saved = records(database)
    connection = connect(database)
    try:
        enabled = dict(connection.execute('SELECT ZUNIQUEIDENTIFIER,ZENABLEDNEW FROM ZBTTBASEENTITY'))
    finally:
        connection.close()
    errors = []
    for edit in plan['edits']:
        uid = edit['before']['BTTUUID']
        record, patch = saved.get(uid), edit['patch']
        if (not record or record['parent'] != edit['parent'] or enabled.get(uid) != patch['BTTEnabled']
                or record['ZORDER'] != edit['before']['BTTOrder']):
            errors.append(uid + ': parent/order/enabled state not saved')
        elif 'BTTPredefinedActionType' in patch:
            data = json.loads(record['ZACTIONDATA']) if record['ZACTIONDATA'] else {}
            # Unlike API exports, saved actions must retain BTTAppleScriptString.
            if record['ZACTION'] != 281 or data != json.loads(patch['BTTAdditionalActionData']):
                errors.append(uid + ': script not saved')
    return errors


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--inspect', action='store_true')
    parser.add_argument('--no-checkpoint', action='store_true', help='do not advance BTT Guard after verification')
    args = parser.parse_args(argv)
    if args.apply and args.inspect:
        parser.error('--apply and --inspect are mutually exclusive')
    os.umask(0o077)
    if not CASE_LIBRARY.is_file():
        raise RuntimeError('The existing case-anything library is missing; no changes made.')
    database = current_database()
    saved = records(database)
    items = [r for r in saved.values() if r['parent'] == TEXT_MENU and r['ZGESTURETYPE'] == 773]
    if len(items) != 9:
        raise RuntimeError('Expected exactly nine text-transform buttons; no changes made.')
    if not args.apply:
        for item in sorted(items, key=lambda r: r['ZORDER']):
            actions = sorted((r for r in saved.values() if r['parent'] == item['ZUNIQUEIDENTIFIER']),
                             key=lambda r: r['ZORDER'])
            print(item['config'].get('BTTMenuElementIdentifier'), [r['ZACTION'] for r in actions])
        print('Preview: replace clipboard-paste actions with Accessibility selection transforms; disable copy/delay.')
        return 0
    plan = json.loads(worker('plan'))
    baseline = before_install(no_checkpoint=args.no_checkpoint)
    if all(edit['matches'] for edit in plan['edits']) and not saved_errors(database, plan):
        print('All nine transforms already installed and saved.')
        after_install(baseline, [], 'repair_text_transforms', wrote=False)
        return 0
    parent = DIRECTORY / '.local/text-transform-backups'
    parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = backup_configuration(database, plan, prefix='selection-', directory_root=parent)
    print('Verified private configuration backup: ' + str(path), flush=True)
    receipt = parse_edit_receipt(worker('apply', path))
    print(receipt['message'], flush=True)
    deadline = time.monotonic() + 45
    while saved_errors(database, plan):
        if time.monotonic() >= deadline:
            raise RuntimeError('Runtime updated; disk save is pending. Re-run --apply to verify, without restarting BTT.')
        time.sleep(1)
    print('Verified saved action scripts and disabled copy/delay actions. No text selections were modified by installation.')
    after_install(baseline, receipt['touched_uuids'], 'repair_text_transforms', wrote=receipt['wrote'])
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (RuntimeError, OSError, ValueError, sqlite3.Error, subprocess.SubprocessError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
