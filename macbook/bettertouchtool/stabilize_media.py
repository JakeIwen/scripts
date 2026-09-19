#!/usr/bin/env python3
"""Persist Media repairs through BTT APIs; check SQLite and verify after restart."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import plistlib
import sqlite3
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]
WORKER = Path(__file__).with_suffix('.js')
MEDIA = 'D9B0ED12-C4BE-4E74-B0DA-0CC3BE092289'


def current_database() -> Path:
    with Path('/Applications/BetterTouchTool.app/Contents/Info.plist').open('rb') as f:
        info = plistlib.load(f)
    name = 'btt_data_store.version_' + info['CFBundleShortVersionString'].replace('.', '_') + '_build_' + info['CFBundleVersion']
    result = Path.home() / 'Library/Application Support/BetterTouchTool' / name
    if not result.is_file():
        raise RuntimeError('Cannot identify current BTT database; no changes made.')
    return result


def connect(path):
    connection = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True, timeout=2)
    connection.row_factory = sqlite3.Row
    return connection


def records(path):
    connection = connect(path)
    try:
        rows = list(connection.execute('''SELECT Z_PK,ZUNIQUEIDENTIFIER,ZPARENT,ZBELONGSTOPRESET2,
            ZGESTURETYPE,ZACTION,ZORDER,ZICONDATA3,ZACTIONDATA,ZLAUNCHPATH,ZADDITIONALACTIONSTRING
            FROM ZBTTBASEENTITY'''))
        by_pk = {r['Z_PK']: r['ZUNIQUEIDENTIFIER'] for r in rows}
        result = {}
        for row in rows:
            uid = row['ZUNIQUEIDENTIFIER']
            if not uid:
                continue
            if uid in result:
                raise RuntimeError('Duplicate stored BTT UUID; refusing changes.')
            record = dict(row)
            record['parent'] = by_pk.get(row['ZPARENT'])
            record['config'] = json.loads(bytes(row['ZICONDATA3'])[1:]) if row['ZICONDATA3'] else {}
            result[uid] = record
        return result
    finally:
        connection.close()


def check_entity(record, entity, preset):
    node = entity['node']
    if record['parent'] != entity['parent'] or record['ZGESTURETYPE'] != int(node['BTTTriggerType']):
        return 'incorrect saved parent/type'
    if record['ZBELONGSTOPRESET2'] != preset:
        return 'incorrect saved preset'
    if record['ZORDER'] != int(node.get('BTTOrder', 0)):
        return 'incorrect saved order'
    action = node.get('BTTPredefinedActionType')
    if action is not None and int(action) != record['ZACTION']:
        return 'incorrect saved action'
    for key in ('BTTMenuItemMinWidth','BTTMenuItemMaxWidth','BTTMenuItemMinHeight','BTTMenuItemMaxHeight'):
        wanted = node.get('BTTMenuConfig', {}).get(key)
        if wanted is not None and record['config'].get(key) != wanted:
            return 'incorrect saved size'
    wanted_data = node.get('BTTAdditionalActionData')
    if wanted_data:
        wanted_data = json.loads(wanted_data) if isinstance(wanted_data, str) else wanted_data
        saved_data = json.loads(record['ZACTIONDATA']) if record['ZACTIONDATA'] else {}
        if any(saved_data.get(k) != v for k, v in wanted_data.items()):
            return 'incorrect saved action payload'
    for key, column in [('BTTNamedTriggerToTrigger','ZLAUNCHPATH'),('BTTTerminalCommand','ZLAUNCHPATH'),
                        ('BTTShellTaskActionScript','ZLAUNCHPATH'),('BTTShellTaskActionConfig','ZADDITIONALACTIONSTRING')]:
        if key in node and node[key] != record[column]:
            return 'incorrect saved action command/configuration'
    return None


def disk_errors(path, plan):
    saved = records(path)
    preset = saved[MEDIA]['ZBELONGSTOPRESET2']
    errors = []
    for entity in plan['entities']:
        record = saved.get(entity['uuid'])
        reason = check_entity(record, entity, preset) if record else 'not saved'
        if reason:
            errors.append(entity['uuid'] + ': ' + reason)
    for change in plan['configChanges']:
        record = saved.get(change['uuid'])
        if not record or any(record['config'].get(k) != v for k, v in change['patch'].items()):
            errors.append(change['uuid'] + ': layout not saved')
    for item in plan['media']['BTTMenuItems']:
        record = saved.get(item['BTTUUID'])
        if not record or record['parent'] != MEDIA or record['ZORDER'] != int(item.get('BTTOrder',0)):
            errors.append(item['BTTUUID'] + ': existing main-menu position changed')
    return errors


def pending_config_changes(saved, changes):
    """Keep the full expectations for verification; send only unsaved keys."""
    result = []
    for change in changes:
        record = saved.get(change['uuid'])
        if not record:
            raise RuntimeError(change['uuid'] + ': layout target missing; no changes made.')
        patch = {key: value for key, value in change['patch'].items()
                 if record['config'].get(key) != value}
        if patch:
            result.append({'uuid': change['uuid'], 'patch': patch})
    return result


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
    directory = Path(tempfile.mkdtemp(prefix='btt-persistent-backup-',dir=ROOT/'tmp'))
    plan_path = directory/'plan.json'
    plan_path.write_text(json.dumps(plan,ensure_ascii=False,indent=2)+'\n')
    if json.loads(plan_path.read_text()) != plan:
        raise RuntimeError('Plan backup verification failed; no changes made.')
    source = connect(database)
    try:
        with sqlite3.connect(directory/'configuration.sqlite') as dest:
            source.backup(dest)
            if dest.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                raise RuntimeError('Configuration backup verification failed; no changes made.')
    finally:
        source.close()
    print('Verified full configuration backup: '+str(directory),flush=True)
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
