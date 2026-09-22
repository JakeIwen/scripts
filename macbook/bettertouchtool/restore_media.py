#!/usr/bin/env python3
"""Restore a deleted Media tree through BTT, never by editing its live database."""
from __future__ import annotations

import argparse
from copy import deepcopy
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time

from btt_common import (ROOT, MEDIA, backup_configuration, check_entity, connect,
                        current_database, records)

WORKER = Path(__file__).with_suffix('.js')
COLLECTIONS = ('BTTMenuItems', 'BTTMenuItemActions', 'BTTAdditionalActions')


def restore_records(database):
    saved = records(database)
    with closing(connect(database)) as connection:
        for row in connection.execute('SELECT ZUNIQUEIDENTIFIER,ZSHORTCUT,ZISENABLED,ZENABLEDNEW FROM ZBTTBASEENTITY'):
            if row['ZUNIQUEIDENTIFIER'] in saved:
                saved[row['ZUNIQUEIDENTIFIER']].update(dict(row))
    return saved


def entity_error(row, entity, preset_pk):
    node = entity['node']
    if node.get('BTTPredefinedActionType') == 264 and row['ZACTION'] == -1:
        # BTT exports shortcut-to-send as action264 but stores it as action-1
        # plus ZSHORTCUT. Require the actual shortcut, not just the type.
        if not node.get('BTTShortcutToSend') or row.get('ZSHORTCUT') != node['BTTShortcutToSend']:
            return 'incorrect saved keyboard shortcut'
        entity = {**entity, 'node': {**node, 'BTTPredefinedActionType': -1}}
    return check_entity(row, entity, preset_pk)


def descendants(saved, root):
    found = {root}
    while True:
        expanded = found | {uid for uid, row in saved.items() if row['parent'] in found}
        if expanded == found:
            return found
        found = expanded


def flatten(item, parent=None, action=False):
    node = deepcopy(item)
    for key in COLLECTIONS:
        node.pop(key, None)
    for key in ('BTTLastUpdatedAt', 'BTTLastChangeUUID', 'BTTFloatingMenuRenderedPreview',
                'BTTTriggerTypeDescriptionReadOnly', 'BTTIsPureAction'):
        node.pop(key, None)
    if action:
        node['BTTTriggerType'] = -1
    node.setdefault('BTTTriggerClass', 'BTTTriggerTypeFloatingMenu')
    if parent:
        node['BTTTriggerParentUUID'] = parent
    else:
        node.pop('BTTTriggerParentUUID', None)
        node['BTTAppBundleIdentifier'] = 'BT.G'
    result = [{'uuid': node['BTTUUID'], 'parent': parent, 'node': node}]
    for key in COLLECTIONS:
        for child in item.get(key, []):
            result.extend(flatten(child, node['BTTUUID'], key != 'BTTMenuItems'))
    return result


def build_plan(source):
    source = Path(source).resolve()
    archive = json.loads(source.read_text(encoding='utf-8'))
    menu = archive.get('media', {})
    if menu.get('BTTUUID') != MEDIA or menu.get('BTTTriggerType') != 767 or not menu.get('BTTMenuItems'):
        raise RuntimeError('Backup does not contain the expected complete Media menu.')
    saved = restore_records(source.parent/'configuration.sqlite')
    entities = flatten(menu)
    by_id = {entity['uuid']: entity for entity in entities}
    if len(by_id) != len(entities) or set(by_id) != descendants(saved, MEDIA):
        raise RuntimeError('Export and SQLite backup disagree about Media membership.')
    preset_pk = saved[MEDIA]['ZBELONGSTOPRESET2']
    with closing(connect(source.parent/'configuration.sqlite')) as connection:
        preset = connection.execute('SELECT ZUNIQUEIDENTIFIER,ZNAME3 FROM ZBTTBASEENTITY WHERE Z_PK=?',
                                    (preset_pk,)).fetchone()
    if not preset or not preset[0] or not preset[1]:
        raise RuntimeError('Cannot determine the original preset.')
    for entity in entities:
        uid, node = entity['uuid'], entity['node']
        error = entity_error(saved[uid], entity, preset_pk)
        if error:
            raise RuntimeError(uid+': inconsistent backup: '+error)
        # Use the saved preset image paths, not BTT's export-time embedded image
        # conversion. This avoids the previous false image-readback mismatch.
        node['BTTMenuConfig'] = deepcopy(saved[uid]['config'])
        node['BTTTriggerBelongsToPreset'] = preset[1]
    # The newest appearance backup is taken BEFORE applying its tiny patch.
    # Include that last requested update, but only known appearance properties.
    for change in archive.get('configChanges', []):
        if change['uuid'] not in by_id or not all(key.startswith((
                'BTTMenuItemIcon', 'BTTMenuItemImage', 'BTTMenuItemResizeImage',
                'BTTMenuItemPadding', 'BTTMenuAttributedText', 'BTTMenuItemShow',
                'BTTMenuElementTooltip')) for key in change['patch']):
            raise RuntimeError('Backup contains a non-appearance update; refusing to replay it.')
        by_id[change['uuid']]['node']['BTTMenuConfig'].update(change['patch'])
    for entity in entities:
        config = entity['node']['BTTMenuConfig']
        for suffix in ('', 'Dark'):
            if config.get('BTTMenuItemIconType'+suffix) == 7:
                path = Path(config.get('BTTMenuItemIconPresetPath'+suffix, ''))
                if not path.is_absolute() or not path.is_file():
                    raise RuntimeError('A required preset icon file is missing: '+entity['uuid'])
    # Runtime get_trigger exports omit editor state present in SQLite (expanded
    # configuration sections, selected tab, change UUID, etc.). Compare against
    # the export from this SAME backup, never the raw SQLite config projection.
    runtime_root_config = deepcopy(menu.get('BTTMenuConfig', {}))
    for change in archive.get('configChanges', []):
        if change['uuid'] == MEDIA:
            runtime_root_config.update(change['patch'])
    return {'source': str(source), 'preset_uuid': preset[0], 'preset_name': preset[1],
            'runtime_root_config': runtime_root_config,
            'entities': entities, 'main_items': len(menu['BTTMenuItems'])}


def latest_source():
    candidates = sorted((ROOT/'tmp').glob('btt-transport-icons-backup-*/backup.json'),
                        key=lambda p: p.stat().st_mtime, reverse=True)
    if not candidates:
        raise RuntimeError('No appearance backup found; supply --source BACKUP.json.')
    # Fail on a damaged newest backup, rather than silently using an older one.
    return candidates[0]


def preflight(database, plan, resume=False):
    saved = restore_records(database)
    present = [entity for entity in plan['entities'] if entity['uuid'] in saved]
    if present and not resume:
        raise RuntimeError('Media or one of its records already exists; refusing to duplicate/overwrite it.')
    if any(uid != MEDIA and r['ZGESTURETYPE'] == 767 and r['config'].get('BTTMenuElementIdentifier') == 'Media'
           for uid, r in saved.items()):
        raise RuntimeError('Another Media menu exists; resolve the name/modifier collision first.')
    with closing(connect(database)) as connection:
        preset = connection.execute('SELECT Z_PK,ZNAME3,ZACTIVATED FROM ZBTTBASEENTITY WHERE ZUNIQUEIDENTIFIER=?',
                                    (plan['preset_uuid'],)).fetchone()
    if not preset or preset['ZNAME3'] != plan['preset_name'] or preset['ZACTIVATED'] != 2:
        raise RuntimeError('The original preset must still be the active Master preset; no changes made.')
    if resume:
        if MEDIA not in saved or saved[MEDIA]['ZISENABLED'] != 0:
            raise RuntimeError('Resume requires the disabled root from the interrupted recovery.')
        if descendants(saved, MEDIA) - {e['uuid'] for e in plan['entities']}:
            raise RuntimeError('Recovery root contains unexpected children; no changes made.')
        errors = saved_errors(database, plan, preset['Z_PK'], present)
        if errors:
            raise RuntimeError('Existing recovery records differ; refusing to overwrite:\n'+'\n'.join(errors[:8]))
    return saved, preset['Z_PK']


def resume_origin(plan):
    """Only resume records whose creation was preceded by our verified backup."""
    candidates = sorted((ROOT/'tmp').glob('btt-media-restore-backup-*/plan.json'),
                        key=lambda p: p.stat().st_mtime, reverse=True)
    for path in candidates:
        previous = json.loads(path.read_text(encoding='utf-8'))
        if all(previous.get(key) == plan.get(key) for key in ('source', 'preset_uuid', 'entities')):
            before = restore_records(path.parent/'configuration.sqlite')
            if not any(e['uuid'] in before for e in plan['entities']):
                return str(path.parent)
    raise RuntimeError('No matching pre-creation recovery backup; refusing to adopt an existing menu.')


def worker(mode, path):
    result = subprocess.run(['/usr/bin/osascript', '-l', 'JavaScript', str(WORKER), mode, str(path)],
                            capture_output=True, text=True, timeout=120)
    if result.returncode:
        # Do not include arbitrary BTT result payloads or embedded commands.
        raise RuntimeError('BTT restoration step '+mode+' failed: '+result.stderr.strip()[-1200:])
    return result.stdout.strip()


def saved_errors(database, plan, preset_pk, entities=None, enabled=False):
    saved = restore_records(database)
    errors = []
    for entity in entities if entities is not None else plan['entities']:
        row = saved.get(entity['uuid'])
        error = entity_error(row, entity, preset_pk) if row else 'not saved'
        if error:
            errors.append(entity['uuid']+': '+error)
            continue
        expected = entity['node']['BTTMenuConfig']
        differences = [k for k, v in expected.items()
                       if k not in ('BTTLastChangeUUID', 'BTTLastUpdatedAt') and row['config'].get(k) != v]
        if differences:
            errors.append(entity['uuid']+': menu configuration differs at '+', '.join(differences[:6]))
        expected_enabled = int(enabled) if entity['uuid'] == MEDIA else int(entity['node'].get('BTTEnabled', 1))
        if row['ZISENABLED'] != expected_enabled:
            errors.append(entity['uuid']+': enabled state differs')
    return errors


def wait_saved(database, plan, preset_pk, entities=None, enabled=False):
    start = time.monotonic()
    deadline = start+60
    next_notice = start+5
    while True:
        errors = saved_errors(database, plan, preset_pk, entities, enabled)
        if not errors:
            return
        if time.monotonic() >= deadline:
            raise RuntimeError('Restore not verified in saved configuration:\n'+'\n'.join(errors[:8]))
        if time.monotonic() >= next_notice:
            print('Waiting for BTT to finish saving; '+str(len(errors))+' record checks pending...', flush=True)
            next_notice = time.monotonic()+10
        time.sleep(.25)  # Conditional save polling, not a fixed UI delay.


def verify_unrelated(database, before, plan):
    after = restore_records(database)
    expected_new = {e['uuid'] for e in plan['entities']} - set(before)
    if set(after)-set(before) != expected_new or any(after.get(uid) != row for uid, row in before.items()):
        raise RuntimeError('Existing saved records changed during restore; stop and inspect the safety backup.')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, help='appearance backup.json (defaults to newest)')
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--resume', action='store_true', help='resume an exact disabled partial recovery; never overwrite it')
    args = parser.parse_args(argv)
    plan = build_plan(args.source or latest_source())
    database = current_database()
    before, preset_pk = preflight(database, plan, resume=args.resume)
    if args.resume:
        plan['resume_origin'] = resume_origin(plan)
        plan['already_saved'] = [e['uuid'] for e in plan['entities'] if e['uuid'] in before]
        print('Resuming verified recovery: '+plan['resume_origin'], flush=True)
    print('Recovery source: '+plan['source'], flush=True)
    print(f"Restore {plan['main_items']} main items / {len(plan['entities'])} total records; existing dropdowns stay untouched.", flush=True)
    if not args.apply:
        print('Inspection only. Add --apply to restore. No existing records will be overwritten.')
        return
    os.umask(0o077)
    # Check runtime availability before backing up or making any changes.
    with tempfile.TemporaryDirectory(prefix='btt-media-check-') as directory:
        path = Path(directory)/'plan.json'
        path.write_text(json.dumps(plan), encoding='utf-8')
        worker('preflight', path)
    backup = backup_configuration(database, plan, prefix='btt-media-restore-backup-', filename='plan.json')
    print('Verified pre-restore configuration backup: '+str(backup.parent), flush=True)
    if preflight(database, plan, resume=args.resume)[0] != before:
        raise RuntimeError('Configuration changed during backup; no changes made.')
    try:
        print(worker('root', backup), flush=True)
        wait_saved(database, plan, preset_pk, plan['entities'][:1])
        print(worker('children', backup), flush=True)
        wait_saved(database, plan, preset_pk)
        verify_unrelated(database, before, plan)
        print(worker('enable', backup), flush=True)
        wait_saved(database, plan, preset_pk, enabled=True)
    except (RuntimeError, subprocess.SubprocessError) as error:
        raise RuntimeError(str(error)+'\nBackup: '+str(backup.parent)+
                           '\nNo automatic deletion or full-database rollback was attempted.') from error
    print('Restored and verified saved Media tree. Hold Ctrl+Option+Command to display it.')
    print('No actions executed, other menus replaced, or BTT restart performed.')


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, OSError, ValueError, KeyError, sqlite3.Error, subprocess.SubprocessError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
