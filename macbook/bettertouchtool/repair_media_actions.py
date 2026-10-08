#!/usr/bin/env python3
"""Restore missing Media action records from exports, preserving current items."""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import closing
from copy import deepcopy
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
from typing import Any, Optional, TypedDict
from uuid import NAMESPACE_URL, uuid5

from btt_common import ROOT, MEDIA, backup_configuration, connect, current_database
from repair_script_paths import replace_path, rewrite_command
from restore_media import descendants, entity_error, flatten, restore_records
from stabilize_media import restart_btt

WORKER = Path(__file__).with_suffix('.js')


class ActionEntity(TypedDict):
    uuid: str
    parent: Optional[str]
    node: dict[str, Any]  # BTT's external trigger JSON schema.


class ActionRepairPlan(TypedDict):
    """Canonical JSON wire contract consumed by repair_media_actions.js."""
    version: int
    media: str
    preset_pk: int
    preset_uuid: str
    entities: list[ActionEntity]
    buttons: list[dict[str, Any]]
    dependencies: list[dict[str, str]]
    dependency_sources: list[str]
    snapshot: dict[str, Any]


def export_items(node):
    yield node
    for child in node.get('BTTMenuItems', []):
        yield from export_items(child)


def archives(source=None):
    paths = [source] if source else sorted((ROOT/'tmp').glob('btt-*backup-*/backup.json'),
                                          key=lambda p: p.stat().st_mtime, reverse=True)
    for path in paths:
        data = json.loads(Path(path).read_text(encoding='utf-8'))
        root = data.get('media') or data.get('mediaExport')
        if isinstance(root, dict) and root.get('BTTUUID') == MEDIA:
            yield str(Path(path).resolve()), {n['BTTUUID']: n for n in export_items(root)}


def missing_buttons(saved):
    if MEDIA not in saved:
        raise RuntimeError('Media is missing; action repair cannot recreate the menu.')
    children = Counter(row['parent'] for row in saved.values())
    return sorted(uid for uid in descendants(saved, MEDIA)
                  if saved[uid]['ZGESTURETYPE'] == 773 and saved[uid]['ZENABLEDNEW'] != 0
                  and saved[uid]['ZACTION'] in (None, -1, 366) and not children[uid])


def restore_button_actions(uid, exported, preset_name):
    entities = []
    for key in ('BTTMenuItemActions', 'BTTAdditionalActions'):
        for action in exported.get(key, []):
            for entity in flatten(action, uid, action=True):
                node = entity['node']
                if node.get('BTTPredefinedActionType') not in (206, 248, 480):
                    raise RuntimeError('Unreviewed recovery action type for '+uid)
                node.setdefault('BTTEnabled', 1)
                node.setdefault('BTTEnabled2', 1)
                node.setdefault('BTTActionCategory', 0)
                node['BTTTriggerBelongsToPreset'] = preset_name
                if 'BTTShellTaskActionScript' in node:
                    node['BTTShellTaskActionScript'], _ = rewrite_command(node['BTTShellTaskActionScript'])
                    config = node.get('BTTShellTaskActionConfig', '')
                    node['BTTShellTaskActionConfig'], _ = replace_path(
                        config, str(ROOT/'automation'), str(ROOT/'shared/python'))
                entities.append(entity)
    # Exported orders can be global/sparse (e.g.119 for the only action). New
    # child collections need dense indices without moving the button itself.
    indices = Counter()
    for entity in entities:
        key = (entity['parent'], entity['node']['BTTActionCategory'])
        entity['node']['BTTOrder'] = indices[key]
        indices[key] += 1
    return entities


def named_records(database):
    with closing(connect(database)) as connection:
        return [dict(row) for row in connection.execute('''SELECT t.ZUNIQUEIDENTIFIER,
            t.ZGESTURECONFIG,t.ZENABLEDNEW,t.ZACTION,t.ZLAUNCHPATH,t.ZADDITIONALACTIONSTRING,
            p.ZACTIVATED,t.ZBELONGSTOPRESET2 FROM ZBTTBASEENTITY t
            JOIN ZBTTBASEENTITY p ON t.ZBELONGSTOPRESET2=p.Z_PK WHERE t.ZGESTURETYPE=643''')]


def named_dependency(name, candidates, saved, preset):
    active = [r for r in candidates if r['ZGESTURECONFIG'] == name and r['ZACTIVATED']
              and r['ZENABLEDNEW'] != 0]
    if len(active) == 1:
        return {'uuid': active[0]['ZUNIQUEIDENTIFIER'], 'name': name}, None, None
    if active:
        raise RuntimeError('Ambiguous active named trigger: '+name)
    # Only recover an unambiguous simple shell trigger. Never activate an entire
    # legacy preset or copy a complex action chain without a reviewed export.
    inactive = [r for r in candidates if r['ZGESTURECONFIG'] == name and not r['ZACTIVATED']
                and r['ZENABLEDNEW'] != 0 and r['ZACTION'] == 206]
    if len(inactive) != 1:
        raise RuntimeError('No unambiguous recoverable named trigger: '+name)
    original = inactive[0]
    source = original['ZUNIQUEIDENTIFIER']
    if any(r['parent'] == source for r in saved.values()):
        raise RuntimeError('Named dependency has additional actions: '+name)
    uid = str(uuid5(NAMESPACE_URL, 'btt-media-recovery:'+preset['ZUNIQUEIDENTIFIER']+':'+source)).upper()
    if uid in saved:
        raise RuntimeError('Named dependency UUID already exists outside active resolution: '+name)
    script, _ = rewrite_command(original['ZLAUNCHPATH'])
    script = script.replace('ssh pi@vanpi.local ', 'ssh pi@vanpi.lan ')
    order = 1+max((r['ZORDER'] or 0 for r in saved.values()
                   if r['ZBELONGSTOPRESET2'] == preset['Z_PK'] and r['parent'] is None), default=-1)
    node = {'BTTUUID': uid, 'BTTTriggerType': 643, 'BTTTriggerClass': 'BTTTriggerTypeOtherTriggers',
            'BTTTriggerName': name, 'BTTAppBundleIdentifier': 'BT.G', 'BTTEnabled': 1, 'BTTEnabled2': 1,
            'BTTTriggerBelongsToPreset': preset['ZNAME3'], 'BTTOrder': order, 'BTTPredefinedActionType': 206,
            'BTTShellTaskActionScript': script, 'BTTShellTaskActionConfig': original['ZADDITIONALACTIONSTRING']}
    return {'uuid': uid, 'name': name}, {'uuid': uid, 'parent': None, 'node': node}, source


def media_preset(database, saved):
    if MEDIA not in saved:
        raise RuntimeError('Media is missing; action repair cannot recreate the menu.')
    with closing(connect(database)) as connection:
        preset = connection.execute(
            'SELECT Z_PK,ZUNIQUEIDENTIFIER,ZNAME3,ZACTIVATED FROM ZBTTBASEENTITY WHERE Z_PK=?',
            (saved[MEDIA]['ZBELONGSTOPRESET2'],)).fetchone()
    if not preset or preset['ZACTIVATED'] != 2 or saved[MEDIA]['ZENABLEDNEW'] == 0:
        raise RuntimeError('Media must be enabled in the selected active preset.')
    return preset


def empty_plan(preset) -> ActionRepairPlan:
    return dict(version=1, media=MEDIA, preset_pk=preset['Z_PK'],
                preset_uuid=preset['ZUNIQUEIDENTIFIER'], entities=[], buttons=[],
                dependencies=[], dependency_sources=[], snapshot={})


def effectively_enabled(saved, root):
    active = {root} if saved[root]['ZENABLEDNEW'] != 0 else set()
    while True:
        expanded = active | {uid for uid, row in saved.items()
                             if row['parent'] in active and row['ZENABLEDNEW'] != 0}
        if expanded == active:
            return active
        active = expanded


def dependency_plan(database) -> ActionRepairPlan:
    """Copy only missing named-trigger dependencies used by active Media actions."""
    saved = restore_records(database)
    preset = media_preset(database, saved)
    active = effectively_enabled(saved, MEDIA)
    names = set()
    for uid in active:
        row = saved[uid]
        if row['ZACTION'] != 248:
            continue
        if not isinstance(row['ZLAUNCHPATH'], str) or not row['ZLAUNCHPATH']:
            raise RuntimeError('Enabled named-trigger action has no dependency name: '+uid)
        names.add(row['ZLAUNCHPATH'])
    plan = empty_plan(preset)
    candidates = named_records(database)
    for name in sorted(names):
        dependency, entity, origin = named_dependency(name, candidates, saved, preset)
        plan['dependencies'].append(dependency)
        if entity:
            plan['entities'].append(entity)
            plan['dependency_sources'].append(origin)
    first_order = 1+max((row['ZORDER'] or 0 for row in saved.values()
                         if row['ZBELONGSTOPRESET2'] == preset['Z_PK']
                         and row['parent'] is None), default=-1)
    for offset, entity in enumerate(plan['entities']):
        entity['node']['BTTOrder'] = first_order+offset
    if len({entity['uuid'] for entity in plan['entities']}) != len(plan['entities']):
        raise RuntimeError('Duplicate recovery UUID; no changes made.')
    return plan


def action_plan(database, source=None) -> ActionRepairPlan:
    saved = restore_records(database)
    missing = missing_buttons(saved)
    preset = media_preset(database, saved)
    available = list(archives(source)) if missing else []
    plan = empty_plan(preset)
    for uid in missing:
        match = next(((path, nodes[uid]) for path, nodes in available if uid in nodes and
                      (nodes[uid].get('BTTMenuItemActions') or nodes[uid].get('BTTAdditionalActions'))), None)
        if not match:
            raise RuntimeError('No action backup found for empty button '+uid)
        path, exported = match
        restored = restore_button_actions(uid, exported, preset['ZNAME3'])
        if any(entity['uuid'] in saved for entity in restored):
            raise RuntimeError('Recovered action UUID already exists; refusing to move or replace it: '+uid)
        plan['entities'].extend(restored)
        plan['buttons'].append({'uuid': uid, 'source': path,
                                'label': exported.get('BTTMenuName') or exported.get('BTTTriggerName') or uid,
                                'parent': saved[uid]['parent'], 'order': saved[uid]['ZORDER']})
    candidates = named_records(database)
    names = sorted({e['node']['BTTNamedTriggerToTrigger'] for e in plan['entities']
                    if e['node'].get('BTTPredefinedActionType') == 248})
    for name in names:
        dependency, entity, origin = named_dependency(name, candidates, saved, preset)
        plan['dependencies'].append(dependency)
        if entity:
            plan['entities'].insert(0, entity)
            plan['dependency_sources'].append(origin)
    if len({e['uuid'] for e in plan['entities']}) != len(plan['entities']):
        raise RuntimeError('Duplicate recovery UUID; no changes made.')
    return plan


def verify_saved(database, plan):
    saved = restore_records(database)
    errors = []
    for entity in plan['entities']:
        row = saved.get(entity['uuid'])
        error = entity_error(row, entity, plan['preset_pk']) if row else 'not saved'
        if not error and row['ZENABLEDNEW'] != int(entity['node'].get('BTTEnabled', 1)):
            error = 'incorrect enabled state'
        if error:
            errors.append(entity['uuid']+': '+error)
    candidates = named_records(database)
    for dependency in plan['dependencies']:
        ids = [r['ZUNIQUEIDENTIFIER'] for r in candidates if r['ZGESTURECONFIG'] == dependency['name']
               and r['ZACTIVATED'] and r['ZENABLEDNEW'] != 0]
        if ids != [dependency['uuid']]:
            errors.append('Named trigger resolution changed: '+dependency['name'])
    return errors


def verify_preserved(before, after):
    for uid, original in before.items():
        current = after.get(uid)
        if current is None:
            raise RuntimeError('Existing record disappeared: '+uid)
        for key, value in original.items():
            if key in ('ZICONDATA3', 'config'):
                if key == 'config':
                    stable = lambda c: {k: v for k, v in c.items() if k not in ('BTTLastUpdatedAt', 'BTTLastChangeUUID')}
                    if stable(value) != stable(current[key]):
                        raise RuntimeError('Existing menu configuration changed: '+uid)
            elif key != 'ZISENABLED' and current[key] != value:
                raise RuntimeError('Existing record changed at '+key+': '+uid)


def action_worker(mode, path):
    result = subprocess.run(['/usr/bin/osascript', '-l', 'JavaScript', str(WORKER), mode,
                             str(ROOT), str(path)], capture_output=True, text=True, timeout=60)
    if result.returncode:
        raise RuntimeError('BTT action recovery failed: '+result.stderr.strip()[-1200:])
    return result.stdout.strip()


def apply_plan(database, plan):
    os.umask(0o077)
    before = restore_records(database)
    with tempfile.TemporaryDirectory(prefix='btt-action-plan-') as directory:
        path = Path(directory)/'plan.json'
        path.write_text(json.dumps(plan), encoding='utf-8')
        plan['snapshot'] = json.loads(action_worker('snapshot', path))
    backup = backup_configuration(database, plan, prefix='btt-actions-backup-', filename='plan.json')
    print('Verified pre-repair configuration backup: '+str(backup.parent), flush=True)
    if restore_records(database) != before:
        raise RuntimeError('Configuration changed during backup; no changes made.')
    try:
        print(action_worker('apply', backup), flush=True)
        print('Restarting BTT cleanly to flush saves and verify persistence; no buttons will run.', flush=True)
        restart_btt()
        errors = verify_saved(current_database(), plan)
        if errors:
            raise RuntimeError('Repair did not survive restart:\n'+'\n'.join(errors))
        verify_preserved(before, restore_records(current_database()))
        print(action_worker('verify', backup), flush=True)
    except (RuntimeError, subprocess.SubprocessError) as error:
        raise RuntimeError(str(error)+'\nSafety backup: '+str(backup.parent)+
                           '\nNo automatic rollback or deletion was attempted.') from error
    print('Verified all restored assignments after restart. Existing menu items and layout preserved.')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--inspect', action='store_true')
    mode.add_argument('--apply', action='store_true')
    parser.add_argument('--source', type=Path, help='specific prior menu export')
    parser.add_argument('--database', type=Path, help='offline SQLite inspection only')
    parser.add_argument('--dependencies-only', action='store_true',
                        help='repair only missing named triggers referenced by active Media actions')
    args = parser.parse_args(argv)
    if args.database and args.apply:
        parser.error('--database is read-only; cannot combine with --apply')
    if args.dependencies_only and args.source:
        parser.error('--source is not used with --dependencies-only')
    database = args.database or current_database()
    plan = dependency_plan(database) if args.dependencies_only else action_plan(database, args.source)
    summary = {'missing_buttons': len(plan['buttons']), 'action_records': sum(bool(e['parent']) for e in plan['entities']),
                      'named_triggers_to_restore': [e['node']['BTTTriggerName'] for e in plan['entities'] if not e['parent']],
                      'named_trigger_orders': [e['node']['BTTOrder'] for e in plan['entities'] if not e['parent']],
                      'buttons': plan['buttons']}
    if args.apply and plan['entities']:
        print('Recovering '+str(summary['action_records'])+' action assignments and '+
              str(len(summary['named_triggers_to_restore']))+' named trigger(s).', flush=True)
        apply_plan(database, plan)
    elif not plan['entities']:
        if args.dependencies_only:
            print('All enabled Media named-trigger dependencies resolve. No changes made.')
        else:
            print('No enabled Media buttons without saved actions were found. No changes made.')
    else:
        print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, OSError, ValueError, KeyError, sqlite3.Error, subprocess.SubprocessError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
