#!/usr/bin/env python3
"""Restore only RPS's missing named-trigger action; never execute a deployment."""
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

from btt_common import ROOT, MEDIA, connect, current_database, records, check_entity, backup_configuration

BUTTON = '103D7824-47C1-4B1B-9106-71E4997BCB58'
ACTION = '525212E9-F6F3-4CF0-A9F7-86C1A52EEB30'
SYNC = 'DA200AE7-BB01-4AB5-89A7-CF1587728D7D'
NAME = 'Sync RPi Scripts'
COMMAND = str(ROOT / 'pi/sync_scripts.sh')
WORKER = Path(__file__).with_suffix('.js')
ENTITY = {'uuid': ACTION, 'parent': BUTTON, 'node': {
    'BTTUUID': ACTION, 'BTTTriggerType': -1, 'BTTOrder': 0,
    'BTTPredefinedActionType': 248, 'BTTNamedTriggerToTrigger': NAME}}


def preflight(database):
    saved = records(database)
    main, button, sync = (saved.get(uid) for uid in (MEDIA, BUTTON, SYNC))
    if not main or not button or button['parent'] != MEDIA or button['ZGESTURETYPE'] != 773:
        raise RuntimeError('Expected RPS button is missing or reparented; no changes made.')
    preset = main['ZBELONGSTOPRESET2']
    if button['ZBELONGSTOPRESET2'] != preset or button['ZACTION'] != 366:
        raise RuntimeError('RPS parent action/preset changed; no changes made.')
    if not sync or sync['ZBELONGSTOPRESET2'] != preset or sync['ZGESTURETYPE'] != 643 or \
            sync['ZACTION'] != 206 or sync['ZLAUNCHPATH'] != COMMAND:
        raise RuntimeError('Current sync trigger no longer points to the expected script; no changes made.')
    with closing(connect(database)) as connection:
        eligible = list(connection.execute('''SELECT t.ZUNIQUEIDENTIFIER
            FROM ZBTTBASEENTITY t JOIN ZBTTBASEENTITY p ON t.ZBELONGSTOPRESET2=p.Z_PK
            WHERE t.ZGESTURETYPE=643 AND t.ZGESTURECONFIG=? AND p.ZACTIVATED<>0
              AND COALESCE(t.ZISENABLED,1)<>0 AND COALESCE(t.ZENABLEDNEW,1)<>0''', (NAME,)))
        if [r[0] for r in eligible] != [SYNC]:
            raise RuntimeError('Sync named-trigger resolution is disabled or ambiguous; no changes made.')
        disabled = connection.execute('''SELECT count(*) FROM ZBTTBASEENTITY
            WHERE ZUNIQUEIDENTIFIER IN (?,?) AND (ZISENABLED=0 OR ZENABLEDNEW=0)''',
            (MEDIA, BUTTON)).fetchone()[0]
        if disabled:
            raise RuntimeError('Media or RPS is disabled; no changes made.')
    children = [r for r in saved.values() if r['parent'] == BUTTON]
    action = saved.get(ACTION)
    if action:
        error = check_entity(action, ENTITY, preset)
        if error or len(children) != 1:
            raise RuntimeError('Existing RPS action differs from the expected link; refusing to overwrite it.')
        with closing(connect(database)) as connection:
            row = connection.execute('SELECT ZISENABLED,ZENABLEDNEW FROM ZBTTBASEENTITY WHERE ZUNIQUEIDENTIFIER=?',
                                     (ACTION,)).fetchone()
            if any(value == 0 for value in row):
                raise RuntimeError('Existing RPS action is disabled; refusing to replace it.')
        return True
    if children:
        raise RuntimeError('RPS has other saved actions; refusing to overwrite them.')
    return False


def worker(mode, argument=''):
    result = subprocess.run(['/usr/bin/osascript', '-l', 'JavaScript', str(WORKER), mode, str(ROOT), str(argument)],
                            capture_output=True, text=True, timeout=60)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or 'BTT API request failed.')
    return result.stdout.strip()




def repair(inspect=False):
    os.umask(0o077)
    database = current_database()
    installed = preflight(database)
    if inspect:
        print(json.dumps({'rps_link_saved': installed, 'sync_script': COMMAND, 'will_execute_sync': False}))
        return
    if installed:
        print('RPS link is already saved correctly. No changes or sync performed.')
        return
    if not Path(COMMAND).is_file():
        raise RuntimeError('Sync script is missing; no changes made.')
    snapshot = json.loads(worker('snapshot'))
    backup = backup_configuration(database, snapshot, prefix='btt-rps-backup-')
    print('Verified BTT configuration snapshot: '+str(backup.parent), flush=True)
    if preflight(database):
        print('RPS was repaired while preparing the backup; no changes or sync performed.')
        return
    try:
        print(worker('apply', backup), flush=True)
        deadline = time.monotonic()+5
        while not preflight(database):
            if time.monotonic() >= deadline:
                raise RuntimeError('BTT has not saved the action to disk; repair is not verified.')
            time.sleep(.1)
    except (RuntimeError, subprocess.SubprocessError) as error:
        raise RuntimeError(str(error)+'\nBackup: '+str(backup.parent)) from error
    print('Verified saved RPS -> Sync RPi Scripts link. No deployment or BTT restart performed.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inspect', action='store_true', help='read-only saved-configuration check')
    parser.add_argument('--with-escape', action='store_true', help='also install visible-dropdown-only Escape dismissal')
    args = parser.parse_args()
    repair(args.inspect)
    if args.with_escape:
        from install_menu_escape import install
        install(args.inspect)


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, OSError, ValueError, sqlite3.Error, subprocess.SubprocessError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
