#!/usr/bin/env python3
"""Persist Media repairs through BTT APIs; check SQLite and verify after restart."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time

WORKER = Path(__file__).with_suffix('.js')
from btt_common import (ROOT, MEDIA, current_database, connect, records, check_entity, disk_errors,
                        pending_config_changes, backup_configuration)


def worker(mode, argument, timeout=60):
    proc = subprocess.run(['/usr/bin/osascript','-l','JavaScript',str(WORKER),mode,str(ROOT),str(argument)],
                          capture_output=True, text=True, timeout=timeout)
    if proc.returncode:
        raise RuntimeError(proc.stderr.strip() or 'BTT scripting command failed.')
    return proc.stdout.strip()


def running():
    proc = subprocess.run(['/usr/bin/pgrep','-x','BetterTouchTool'], stdout=subprocess.DEVNULL,
                          stderr=subprocess.PIPE, text=True)
    if proc.returncode not in (0,1):
        raise RuntimeError('Cannot inspect BTT process: ' + proc.stderr.strip())
    return proc.returncode == 0


def restart_btt():
    proc = subprocess.run(['/usr/bin/osascript','-e','tell application "BetterTouchTool" to quit'],
                          capture_output=True, text=True, timeout=25)
    if proc.returncode:
        raise RuntimeError('BTT would not quit cleanly: ' + proc.stderr.strip())
    deadline = time.monotonic() + 20
    while running():
        if time.monotonic() >= deadline:
            raise RuntimeError('BTT did not exit; no force quit was attempted.')
        time.sleep(.1)
    subprocess.run(['/usr/bin/open','-g','-a','BetterTouchTool'], check=True, timeout=15)
    deadline = time.monotonic() + 20
    while not running():
        if time.monotonic() >= deadline:
            raise RuntimeError('BTT did not restart.')
        time.sleep(.1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inspect', action='store_true')
    parser.add_argument('--full-height-dropdowns', action='store_true', help='fallback to usable screen-height dropdowns')
    args = parser.parse_args()
    os.umask(0o077)
    database = current_database()
    plan = json.loads(worker('plan','full-height' if args.full_height_dropdowns else 'content'))
    saved = records(database)
    preset = saved[MEDIA]['ZBELONGSTOPRESET2']
    plan['create'] = []
    for entity in plan['entities']:
        record = saved.get(entity['uuid'])
        if record:
            error = check_entity(record,entity,preset)
            if error:
                raise RuntimeError(entity['uuid'] + ': ' + error + '; refusing to overwrite an existing record.')
        else:
            plan['create'].append(entity)
    plan['applyConfigChanges'] = pending_config_changes(saved, plan['configChanges'])
    if args.inspect:
        print(json.dumps({'missing_records':len(plan['create']),'screen':plan['screen'],
                          'layout_updates':len(plan['applyConfigChanges'])},indent=2))
        return
    plan_path = backup_configuration(database, plan, prefix='btt-persistent-backup-', filename='plan.json')
    directory = plan_path.parent
    print('Verified BTT configuration snapshot: '+str(directory),flush=True)
    print(worker('apply',plan_path),flush=True)
    # Give BTT's normal save callbacks a chance to finish; do not assume an API
    # export is proof that restored children exist in its persistent store.
    deadline = time.monotonic()+4
    errors = disk_errors(database,plan)
    while errors and time.monotonic()<deadline:
        time.sleep(.1)
        errors = disk_errors(database,plan)
    print('Restarting BTT cleanly to test persistence.',flush=True)
    restart_btt()
    errors = disk_errors(current_database(),plan)
    if errors:
        raise RuntimeError('Repair did not survive restart:\n'+'\n'.join(errors)+'\nBackup: '+str(directory))
    print('Verified database after restart: submenu rows/actions and layout are saved.',flush=True)
    print(worker('verify',plan_path),flush=True)
    print('Media is anchored at the screen top-left. Heights use '+
          ('content sizing (dropdown fallback: screen height).' if args.full_height_dropdowns else 'content sizing.'))


if __name__=='__main__':
    try:
        main()
    except (RuntimeError,OSError,sqlite3.Error,subprocess.SubprocessError,ValueError) as exc:
        print(str(exc),file=sys.stderr)
        raise SystemExit(1)
