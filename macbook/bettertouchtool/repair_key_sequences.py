#!/usr/bin/env python3
"""Restore Master key sequences that an older BTT store has and the current one lost.

BTT keeps one Core Data store per installed version and migrates the previous one
on upgrade, so older stores stay on disk. Inspect (the default) lists each
top-level key-sequence trigger whose typed keys no current trigger has. --apply
backs up the current configuration, adds each one through BTT's Apple Events API
(reads use its local socket) and waits until BTT has saved it. No live SQLite
writes, restart or action execution.

Triggers linked to BTT's Trash as well as an app scope are reported as at risk:
emptying the Trash deletes them outright, which is how the 6.826-era sequences
were lost.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import tempfile
import time

from btt_common import backup_configuration, connect, current_database

BTTCLI = Path('/Applications/BetterTouchTool.app/Contents/SharedSupport/bin/bttcli')
STORE_NAME = re.compile(r'^btt_data_store\.version_(\d+)_(\d+)_build_(\d+)$')
KEY_SEQUENCE = 624
PRESET = 'Master'
GLOBAL = 'BT.G'
TRASH = 'BT.D'
SAVE_TIMEOUT = 60
KEY_LISTS = {'down': 'BTTKeySequenceDownKeys', 'up': 'BTTKeySequenceUpKeys', 'mixed': 'BTTKeySequenceMixedKeys'}
# Saved key-sequence columns and the export keys BTT reads them from.
SEQUENCE_FIELDS = {'ZBETWEENTIME': 'BTTPauseBetween', 'ZDELETEAFTERWARDS': 'BTTCharactersToDeleteAfterwards',
                   'ZKEYCOUNT': 'BTTKeyCount', 'ZKEYBOARDTYPE': 'BTTKeyboardType',
                   'ZALLOWRETRIGGER': 'BTTAllowRetrigger', 'ZUNDOAFTERWARDS': 'BTTUndoAfterwards'}
KEY_FIELDS = {'ZCHARACTER': 'BTTKEYCharacter', 'ZDOWN': 'BTTKEYDown', 'ZORDERRELEVANT': 'BTTKEYOrderRelevant',
              'ZREQUIRED': 'BTTKEYRequired', 'ZTIME': 'BTTKEYTime'}
# Columns with no known export key: a restore that needs them is left for review.
UNMAPPED_SEQUENCE = ('ZAFTERTIME', 'ZBEFORETIME', 'ZMINIMUMDOWNTIME', 'ZKEYBOARDSCOPE',
                     'ZALLOWCONTINUEDTIME', 'ZDELETEAFTERWARDSDELAY')
UNMAPPED_KEY = ('ZDOWNTIMEREQUIRED', 'ZDOWNTIME', 'ZALLOWCONTINUEDTIME', 'ZDELETEAFTERWARDSDELAY')
ADD_SCRIPT = '''ObjC.import("Foundation");
function run(argv) {
    const text = $.NSString.stringWithContentsOfFileEncodingError(argv[0], $.NSUTF8StringEncoding, null);
    if (!text) throw new Error("Cannot read the trigger definition.");
    Application("BetterTouchTool").add_new_trigger(ObjC.unwrap(text));
}'''


def store_version(path):
    match = STORE_NAME.match(Path(path).name)
    return tuple(int(part) for part in match.groups()) if match else None


def older_stores(current):
    """Earlier per-version stores, newest first."""
    limit = store_version(current)
    found = [path for path in Path(current).parent.iterdir()
             if path.is_file() and store_version(path) is not None and store_version(path) < limit]
    return sorted(found, key=store_version, reverse=True)


def plain(value):
    """Core Data stores integral times as REAL; BTT exports them as integers."""
    return int(value) if isinstance(value, float) and value.is_integer() else value


def read_store(path):
    """Key-sequence triggers in the Master preset, plus every UUID and the at-risk set."""
    with closing(connect(path)) as connection:
        connection.execute('BEGIN')
        entities = dict(connection.execute('SELECT Z_NAME,Z_ENT FROM Z_PRIMARYKEY'))
        app, gesture = entities['APP'], entities['Gesture']
        rows = {row['Z_PK']: row for row in connection.execute('SELECT * FROM ZBTTBASEENTITY')}
        links = connection.execute(f'SELECT Z_{app}GESTURES,Z_{gesture}APPS_GESTURES FROM Z_{app}APPS_GESTURES').fetchall()
    scopes = {}
    for app_pk, gesture_pk in links:
        scopes.setdefault(gesture_pk, set()).add(rows[app_pk]['ZBUNDLEIDENTIFIER'])
    children = {}
    keys = {name: {} for name in KEY_LISTS}
    for row in rows.values():
        if row['ZPARENT'] is not None:
            children[row['ZPARENT']] = children.get(row['ZPARENT'], 0) + 1
        if row['Z_ENT'] == entities['Key']:
            for name in KEY_LISTS:
                owner = row['ZBELONGSTOKEYSEQUENCE' + name.upper()]
                if owner is not None:
                    keys[name].setdefault(owner, []).append(row)
    presets = {pk: row['ZNAME3'] for pk, row in rows.items() if row['Z_ENT'] == entities['Preset']}
    triggers = []
    for pk, row in rows.items():
        if row['Z_ENT'] != gesture or row['ZGESTURETYPE'] != KEY_SEQUENCE or row['ZPARENT'] is not None \
                or presets.get(row['ZBELONGSTOPRESET2']) != PRESET or row['ZKEYSEQUENCE'] not in rows:
            continue
        sequence = rows[row['ZKEYSEQUENCE']]
        lists = {name: sorted(keys[name].get(sequence['Z_PK'], []),
                              key=lambda key, name=name: key['Z_FOK_BELONGSTOKEYSEQUENCE' + name.upper()])
                 for name in KEY_LISTS}
        triggers.append({'uuid': row['ZUNIQUEIDENTIFIER'], 'row': row, 'sequence': sequence, 'keys': lists,
                         'scopes': sorted(scopes.get(pk, ())), 'children': children.get(pk, 0),
                         # Re-recorded copies interleave key-ups differently; the typed keys identify it.
                         'signature': tuple(key['ZCODE'] for key in lists['down'])})
    at_risk = sorted(row['ZUNIQUEIDENTIFIER'] for pk, row in rows.items()
                     if TRASH in scopes.get(pk, ()) and scopes[pk] - {TRASH})
    return {'path': Path(path), 'triggers': triggers, 'at_risk': at_risk,
            'uuids': {row['ZUNIQUEIDENTIFIER'] for row in rows.values() if row['ZUNIQUEIDENTIFIER']}}


def typed(trigger):
    return '-'.join((key['ZCHARACTER'] or '?').strip() or 'Space' for key in trigger['keys']['down'])


def review_reason(trigger):
    row, sequence = trigger['row'], trigger['sequence']
    if set(trigger['scopes']) - {TRASH} != {GLOBAL}:
        return 'only in BTT Trash' if trigger['scopes'] == [TRASH] else 'app-specific scope'
    if row['ZENABLEDNEW'] != 1:
        return 'was disabled'
    if trigger['children'] or row['ZACTION'] in (None, -1, 366):
        return 'multi-action trigger'
    if row['ZCONDITIONS']:
        return 'has activation conditions'
    required = sum(1 for key in trigger['keys']['mixed'] if key['ZREQUIRED'])
    if any(sequence[column] for column in UNMAPPED_SEQUENCE) or sequence['ZNUMBEROFREQUIRED'] not in (None, 0, required) \
            or any(key[column] for keys in trigger['keys'].values() for key in keys for column in UNMAPPED_KEY):
        return 'uses key-sequence settings this restore cannot map'
    if not trigger['keys']['mixed']:
        return 'has no recorded keys'
    return None


def definition(trigger):
    row, sequence = trigger['row'], trigger['sequence']
    keys = {}
    for name, export in KEY_LISTS.items():
        keys[export] = [{'BTTKEYCode': key['ZCODE'],
                         **{field: plain(key[column]) for column, field in KEY_FIELDS.items() if key[column]}}
                        for key in trigger['keys'][name]]
    node = {'BTTTriggerType': KEY_SEQUENCE, 'BTTTriggerClass': 'BTTTriggerTypeKeySequence',
            'BTTUUID': trigger['uuid'], 'BTTTriggerBelongsToPreset': PRESET, 'BTTAppBundleIdentifier': GLOBAL,
            'BTTEnabled': 1, 'BTTEnabled2': 1, 'BTTOrder': row['ZORDER'] or 0,
            'BTTPredefinedActionType': row['ZACTION'],
            'BTTKeySequence': {**{field: plain(sequence[column]) for column, field in SEQUENCE_FIELDS.items()
                                  if sequence[column] is not None}, **keys}}
    if row['ZACTIONDATA']:
        node['BTTAdditionalActionData'] = json.loads(row['ZACTIONDATA'])
    if row['ZADDITIONALINTEGER'] is not None:
        node['BTTMoveCursorLeftBy'] = row['ZADDITIONALINTEGER']
    return node


def restoration_plan(current, sources):
    """One trigger per lost key sequence; the most recently used copy wins."""
    present = {trigger['signature'] for trigger in current['triggers']}
    candidates, seen = {}, set()
    for source in sources:
        for trigger in source['triggers']:
            if trigger['uuid'] in current['uuids'] or trigger['uuid'] in seen or trigger['signature'] in present:
                continue
            seen.add(trigger['uuid'])
            candidates.setdefault(trigger['signature'], []).append(dict(trigger, source=source['path'].name))
    restore, review, duplicates = [], [], []
    for group in candidates.values():
        group.sort(key=lambda t: (t['row']['ZLASTUSED'] or 0, t['row']['ZUPDATEDAT'] or 0), reverse=True)
        eligible = [t for t in group if review_reason(t) is None]
        if eligible:
            restore.append(eligible[0])
            duplicates.extend(t for t in group if t is not eligible[0])
        else:
            review.extend(group)
    order = lambda t: typed(t)
    return {'restore': sorted(restore, key=order), 'review': sorted(review, key=order),
            'duplicates': sorted(duplicates, key=order)}


def summary(plan, current, sources):
    def item(trigger, reason=None):
        result = {'keys': typed(trigger), 'uuid': trigger['uuid'], 'action': trigger['row']['ZACTION'],
                  'source': trigger['source']}
        return dict(result, reason=reason) if reason else result
    return {'current_store': current['path'].name, 'sources': [source['path'].name for source in sources],
            'restore': [item(t) for t in plan['restore']],
            'needs_review': [item(t, review_reason(t)) for t in plan['review']],
            'skipped_duplicates': [item(t) for t in plan['duplicates']],
            'at_risk_trash_linked': current['at_risk'],
            'transport': 'Apple Events adds, local-socket reads', 'restarts_btt': False}


def bttcli(command, **parameters):
    result = subprocess.run([str(BTTCLI), command, *(f'{key}={value}' for key, value in parameters.items())],
                            capture_output=True, text=True, timeout=30)
    if result.returncode:
        # Trigger payloads can hold private text; never echo the response.
        raise RuntimeError(f'BTT socket command {command} failed; no success confirmed.')
    return result.stdout


def add_trigger(definition):
    """BTT's socket drops large requests (a 94 KB paste exits on SIGPIPE), so adds use Apple Events."""
    with tempfile.TemporaryDirectory(prefix='btt-key-sequence-') as directory:
        path = Path(directory)/'definition.json'
        path.write_text(json.dumps(definition, ensure_ascii=False), encoding='utf-8')
        result = subprocess.run(['/usr/bin/osascript', '-l', 'JavaScript', '-e', ADD_SCRIPT, str(path)],
                                capture_output=True, text=True, timeout=60)
    if result.returncode:
        raise RuntimeError('BTT Apple Events add failed; run --apply from your own Terminal. No success confirmed.')


def runtime_trigger(uuid):
    value = json.loads(bttcli('get_trigger', uuid=uuid) or '{}')
    if not isinstance(value, dict):
        raise RuntimeError('Invalid BTT runtime response; no success confirmed.')
    return value or None


def runtime_matches(actual, wanted):
    if not actual:
        return False
    if any(actual.get(key) != wanted[key] for key in ('BTTTriggerType', 'BTTPredefinedActionType')):
        return False
    if (actual.get('BTTAdditionalActionData') or None) != wanted.get('BTTAdditionalActionData'):
        return False
    # Exports omit zero values, including key code 0 (A).
    codes = lambda node, name: [(key.get('BTTKEYCode', 0), key.get('BTTKEYDown', 0))
                                for key in node.get('BTTKeySequence', {}).get(name, [])]
    return all(codes(actual, name) == codes(wanted, name) for name in KEY_LISTS.values())


def saved(database, wanted, before):
    """True once BTT's store has every restored trigger and still has everything else."""
    state = read_store(database)
    if not before <= state['uuids']:
        raise RuntimeError('Saved BTT records disappeared during the restore; inspect the backup.')
    by_uuid = {trigger['uuid']: trigger for trigger in state['triggers']}
    for trigger in wanted:
        stored = by_uuid.get(trigger['uuid'])
        if stored is None or stored['signature'] != trigger['signature'] or stored['scopes'] != [GLOBAL] \
                or stored['row']['ZACTION'] != trigger['row']['ZACTION'] or stored['row']['ZENABLEDNEW'] != 1 \
                or json.loads(stored['row']['ZACTIONDATA'] or 'null') != json.loads(trigger['row']['ZACTIONDATA'] or 'null'):
            return False
    return True


def restore(apply=False, source=None):
    os.umask(0o077)
    database = current_database()
    current = read_store(database)
    sources = [read_store(path) for path in ([source] if source else older_stores(database))]
    plan = restoration_plan(current, sources)
    result = summary(plan, current, sources)
    if not apply or not plan['restore']:
        return dict(result, mode='apply' if apply else 'preview', changes_applied=False)
    definitions = [definition(trigger) for trigger in plan['restore']]
    backup = backup_configuration(database, {'kind': 'btt-key-sequence-restore', 'definitions': definitions},
                                  prefix='btt-key-sequences-backup-')
    if read_store(database)['uuids'] != current['uuids']:
        raise RuntimeError('BTT changed during backup; no changes made. Backup: ' + str(backup.parent))
    try:
        for wanted in definitions:
            if runtime_trigger(wanted['BTTUUID']) is not None:
                raise RuntimeError(wanted['BTTUUID'] + ' already exists in BTT; stopped without retrying.')
            add_trigger(wanted)
            if not runtime_matches(runtime_trigger(wanted['BTTUUID']), wanted):
                raise RuntimeError(wanted['BTTUUID'] + ' runtime readback failed; stopped without retrying.')
        deadline = time.monotonic() + SAVE_TIMEOUT
        while not saved(database, plan['restore'], current['uuids']):
            if time.monotonic() >= deadline:
                raise RuntimeError('BTT has not saved the restored key sequences yet; no restart or retry attempted.')
            time.sleep(.5)
    except (RuntimeError, ValueError, OSError, sqlite3.Error, subprocess.SubprocessError) as error:
        raise RuntimeError(f'{error} Backup: {backup.parent}') from error
    return dict(result, mode='apply', changes_applied=True, saved_verified=True, backup=str(backup.parent))


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--apply', action='store_true', help='back up, add through BTT socket, verify the saved store')
    mode.add_argument('--inspect', action='store_true', help='read-only preview (the default)')
    parser.add_argument('--source', type=Path, help='one older BTT store or snapshot; defaults to every older store')
    args = parser.parse_args()
    try:
        print(json.dumps(restore(apply=args.apply, source=args.source), ensure_ascii=False, indent=2))
    except (RuntimeError, ValueError, OSError, sqlite3.Error, subprocess.SubprocessError, KeyError) as error:
        parser.exit(1, str(error) + '\n')


if __name__ == '__main__':
    main()
