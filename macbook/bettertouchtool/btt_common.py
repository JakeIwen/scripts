"""Shared read-only BTT database inspection and verified private snapshots.

No Apple Events, live database writes, restarts, or action execution on import.
Snapshots contain configuration metadata; external preset assets/preferences are
not included. Keep them private and out of Git.
"""
from __future__ import annotations

from contextlib import closing
import json
from pathlib import Path
import plistlib
import sqlite3
import tempfile

ROOT = Path(__file__).resolve().parents[2]
MEDIA = "D9B0ED12-C4BE-4E74-B0DA-0CC3BE092289"


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


def backup_configuration(database, snapshot, prefix='btt-config-backup-', filename='backup.json'):
    if '/' in prefix or '\\' in prefix:
        raise ValueError('Backup prefix must not contain path separators.')
    if Path(filename).name != filename or filename in ('.', '..'):
        raise ValueError('Backup filename must be a simple filename.')
    directory = Path(tempfile.mkdtemp(prefix=prefix, dir=ROOT/'tmp'))
    path = directory/filename
    path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    if json.loads(path.read_text(encoding='utf-8')) != snapshot:
        raise RuntimeError('Export backup verification failed; no changes made.')
    path.chmod(0o600)
    source = connect(database)
    try:
        with closing(sqlite3.connect(directory/'configuration.sqlite')) as destination:
            source.backup(destination)
            if destination.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                raise RuntimeError('Configuration snapshot verification failed; no changes made.')
    finally:
        source.close()
    (directory/'configuration.sqlite').chmod(0o600)
    return path
