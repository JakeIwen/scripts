"""Read-only, point-in-time BTT configuration snapshots."""
from __future__ import annotations

from collections import Counter, deque
from contextlib import closing
import json
from pathlib import Path
import re
import sqlite3
import time
from typing import Any

from ..btt_common import MEDIA, connect, current_database
from .model import ConfigSnapshot, Finding, FindingKind, RecordState, SCHEMA_VERSION

REQUIRED_COLUMNS = {
    'Z_PK', 'Z_ENT', 'ZUNIQUEIDENTIFIER', 'ZBUNDLEIDENTIFIER',
    'ZPARENT', 'ZBELONGSTOPRESET2',
    'ZGESTURETYPE', 'ZENABLEDNEW', 'ZORDER', 'ZACTION', 'ZACTIONCATEGORY',
    'ZICONDATA3', 'ZACTIONDATA', 'ZLAUNCHPATH', 'ZADDITIONALACTIONSTRING',
    'ZSHORTCUT', 'ZGESTURECONFIG', 'ZACTIVATED',
}
SELECT_COLUMNS = tuple(sorted(REQUIRED_COLUMNS))

# These change whether a menu or scripted item operates, rather than how it
# looks in the editor. Labels, fonts, colors, previews and runtime results are
# deliberately excluded from the checkpoint contract.
OPERATIONAL_CONFIG_KEYS = {
    'BTTMenuAnchorMenu', 'BTTMenuAnchorRelation', 'BTTMenuAvailability',
    'BTTMenuCloseAfterAction', 'BTTMenuCloseOnOutsideClick',
    'BTTMenuDisableDrag', 'BTTMenuItemCloseOnClick',
    'BTTMenuItemScriptActive', 'BTTMenuItemScriptRunWhileMenuIsHidden',
    'BTTMenuItemVisibleWhileActive', 'BTTMenuItemVisibleWhileInactive',
    'BTTMenuItemsUseModifierModes', 'BTTMenuMergeGlobalMenuItems',
    'BTTMenuModifierKeys', 'BTTMenuModifierMode', 'BTTMenuOffsetX',
    'BTTMenuOffsetXUnit', 'BTTMenuOffsetY', 'BTTMenuOffsetYUnit',
    'BTTMenuPositionPreventOffscreen', 'BTTMenuPositionRelativeTo',
    'BTTMenuPositioningType', 'BTTMenuScriptAlwaysRunOnAppear',
    'BTTMenuScriptAlwaysRunOnFirstLoad', 'BTTMenuScriptRunOnItemHover',
    'BTTMenuScriptRunOnMenuHover', 'BTTMenuScriptSettings',
    'BTTMenuScriptUpdateInterval', 'BTTMenuShowIfWindowLevelEqualsEnabled',
    'BTTMenuVisibility', 'BTTMenuWindowLevel',
}
NO_ACTION_TYPES = {-1, 366}


def _json_blob(value: Any, field: str, uuid: str) -> Any:
    if value is None:
        return None
    raw = bytes(value) if isinstance(value, (bytes, bytearray, memoryview)) else str(value).encode()
    for candidate in (raw, raw[1:]):
        try:
            return json.loads(candidate)
        except (json.JSONDecodeError, UnicodeDecodeError):
            pass
    raise ValueError(f'Invalid stored {field} JSON for BTT record {uuid}.')


def _config(row: sqlite3.Row) -> dict[str, Any]:
    decoded = _json_blob(row['ZICONDATA3'], 'configuration', row['ZUNIQUEIDENTIFIER'])
    if decoded is None:
        return {}
    if not isinstance(decoded, dict):
        raise ValueError(f'Invalid stored configuration shape for BTT record {row["ZUNIQUEIDENTIFIER"]}.')
    return {key: decoded[key] for key in sorted(OPERATIONAL_CONFIG_KEYS & decoded.keys())}


def _payload(row: sqlite3.Row) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    action_data = _json_blob(row['ZACTIONDATA'], 'action data', row['ZUNIQUEIDENTIFIER'])
    if action_data is not None:
        payload['action_data'] = action_data
    for column, key in (
        ('ZLAUNCHPATH', 'launch_path'),
        ('ZADDITIONALACTIONSTRING', 'additional_action_string'),
        ('ZSHORTCUT', 'shortcut'),
        ('ZGESTURECONFIG', 'gesture_config'),
    ):
        if column == 'ZGESTURECONFIG' and row['ZGESTURETYPE'] != 643:
            continue
        if row[column] is not None:
            payload[key] = row[column]
    return payload


def _record(
    row: sqlite3.Row,
    by_pk: dict[int, sqlite3.Row],
    action_ranks: dict[int, int],
    app_scopes: dict[int, list[str]],
) -> RecordState:
    parent = by_pk.get(row['ZPARENT']) if row['ZPARENT'] is not None else None
    preset = by_pk.get(row['ZBELONGSTOPRESET2']) if row['ZBELONGSTOPRESET2'] is not None else None
    if row['ZPARENT'] is not None and parent is None:
        raise ValueError(f'BTT parent is missing for record {row["ZUNIQUEIDENTIFIER"]}.')
    if row['ZBELONGSTOPRESET2'] is not None and preset is None:
        raise ValueError(f'BTT preset is missing for record {row["ZUNIQUEIDENTIFIER"]}.')
    if parent is not None and not parent['ZUNIQUEIDENTIFIER']:
        raise ValueError(f'BTT parent lacks a UUID for record {row["ZUNIQUEIDENTIFIER"]}.')
    if preset is not None and not preset['ZUNIQUEIDENTIFIER']:
        raise ValueError(f'BTT preset lacks a UUID for record {row["ZUNIQUEIDENTIFIER"]}.')
    return RecordState(
        uuid=row['ZUNIQUEIDENTIFIER'],
        parent=parent['ZUNIQUEIDENTIFIER'] if parent else None,
        preset=preset['ZUNIQUEIDENTIFIER'] if preset else None,
        trigger_type=int(row['ZGESTURETYPE'] if row['ZGESTURETYPE'] is not None else -1),
        enabled=row['ZENABLEDNEW'] != 0,
        order=action_ranks.get(row['Z_PK'], int(row['ZORDER'] or 0)),
        action_type=int(row['ZACTION'] if row['ZACTION'] is not None else -1),
        action_category=int(row['ZACTIONCATEGORY'] or 0),
        payload=_payload(row),
        config=_config(row),
        app_scope=app_scopes.get(row['Z_PK'], []),
    )


def _finding(kind: FindingKind, uuid: str, detail: str) -> Finding:
    return Finding(kind=kind.value, uuid=uuid, detail=detail)


def _named_dependency(
    source: sqlite3.Row, rows: list[sqlite3.Row], active_presets: set[int]
) -> tuple[sqlite3.Row | None, Finding | None]:
    name = source['ZLAUNCHPATH']
    matches = [row for row in rows if row['ZGESTURETYPE'] == 643
               and row['ZGESTURECONFIG'] == name and row['ZENABLEDNEW'] != 0
               and row['ZBELONGSTOPRESET2'] in active_presets]
    if len(matches) == 1:
        return matches[0], None
    detail = 'named-trigger dependency unresolved' if not matches else 'named-trigger dependency ambiguous'
    return None, _finding(FindingKind.DEPENDENCY_CHANGED, source['ZUNIQUEIDENTIFIER'], detail)


def _menu_dependency(
    source: sqlite3.Row,
    rows: list[sqlite3.Row],
    by_uuid: dict[str, sqlite3.Row],
    active_presets: set[int],
) -> tuple[sqlite3.Row | None, Finding | None]:
    data = _json_blob(source['ZACTIONDATA'], 'action data', source['ZUNIQUEIDENTIFIER'])
    if not isinstance(data, dict) or not ({'BTTMenuActionMenuID', 'BTTMenuActionMenuName'} & data.keys()):
        return None, None
    menu_id = data.get('BTTMenuActionMenuID')
    if isinstance(menu_id, str):
        target = by_uuid.get(menu_id)
        if target is not None and target['ZGESTURETYPE'] == 767 and target['ZPARENT'] is None:
            if target['ZENABLEDNEW'] != 0 and target['ZBELONGSTOPRESET2'] in active_presets:
                return target, None
            return target, _finding(FindingKind.DEPENDENCY_CHANGED,
                                    source['ZUNIQUEIDENTIFIER'],
                                    'floating-menu dependency disabled')
        if target is not None:
            return None, _finding(FindingKind.DEPENDENCY_CHANGED, source['ZUNIQUEIDENTIFIER'],
                                  'floating-menu dependency unresolved')
    reference = menu_id if isinstance(menu_id, str) else data.get('BTTMenuActionMenuName')
    matches = [row for row in rows if row['ZGESTURETYPE'] == 767
               and row['ZPARENT'] is None and _menu_identifier(row) == reference]
    if len(matches) == 1:
        target = matches[0]
        if target['ZENABLEDNEW'] != 0 and target['ZBELONGSTOPRESET2'] in active_presets:
            return target, None
        return target, _finding(FindingKind.DEPENDENCY_CHANGED,
                                source['ZUNIQUEIDENTIFIER'],
                                'floating-menu dependency disabled')
    detail = 'floating-menu dependency unresolved' if not matches else 'floating-menu dependency ambiguous'
    return None, _finding(FindingKind.DEPENDENCY_CHANGED, source['ZUNIQUEIDENTIFIER'], detail)


def _menu_identifier(row: sqlite3.Row) -> Any:
    decoded = _json_blob(row['ZICONDATA3'], 'configuration', row['ZUNIQUEIDENTIFIER'])
    return decoded.get('BTTMenuElementIdentifier') if isinstance(decoded, dict) else None


def _select_graph(
    roots: list[str], rows: list[sqlite3.Row], by_uuid: dict[str, sqlite3.Row]
) -> tuple[set[int], set[int], list[Finding]]:
    children: dict[int, list[sqlite3.Row]] = {}
    for row in rows:
        children.setdefault(row['ZPARENT'], []).append(row)
    active_presets = {row['Z_PK'] for row in rows if bool(row['ZACTIVATED'])}
    queue: deque[tuple[sqlite3.Row, bool]] = deque()
    findings: list[Finding] = []
    for uuid in roots:
        if uuid in by_uuid:
            queue.append((by_uuid[uuid], True))
        else:
            findings.append(_finding(FindingKind.MISSING, uuid, 'configured root missing'))
    selected: set[int] = set()
    effective_ids: set[int] = set()
    processed: dict[int, bool] = {}
    while queue:
        row, parent_enabled = queue.popleft()
        effective = (parent_enabled and row['ZENABLEDNEW'] != 0
                     and (row['ZBELONGSTOPRESET2'] is None
                          or row['ZBELONGSTOPRESET2'] in active_presets))
        if row['Z_PK'] in processed and (processed[row['Z_PK']] or not effective):
            continue
        processed[row['Z_PK']] = effective
        selected.add(row['Z_PK'])
        if effective:
            effective_ids.add(row['Z_PK'])
        queue.extend((child, effective) for child in children.get(row['Z_PK'], ()))
        if not effective:
            continue
        dependency, finding = (None, None)
        if row['ZACTION'] == 248:
            dependency, finding = _named_dependency(row, rows, active_presets)
        elif row['ZACTIONDATA'] is not None:
            dependency, finding = _menu_dependency(row, rows, by_uuid, active_presets)
        if dependency is not None:
            queue.append((dependency, True))
        if finding is not None:
            findings.append(finding)
    return selected, effective_ids, findings


def _unassigned_findings(effective_ids: set[int], rows: list[sqlite3.Row]) -> list[Finding]:
    runnable_children = Counter(row['ZPARENT'] for row in rows
                                if row['Z_PK'] in effective_ids
                                and row['ZGESTURETYPE'] == -1
                                and _runnable_action(row))
    findings = []
    for row in rows:
        if row['Z_PK'] not in effective_ids or row['ZGESTURETYPE'] != 773:
            continue
        action = int(row['ZACTION'] if row['ZACTION'] is not None else -1)
        config = _config(row)
        scripted = bool(config.get('BTTMenuItemScriptActive'))
        direct = action == -1 and any(value not in (None, '', '-1', -1)
                                      for value in (row['ZSHORTCUT'], row['ZLAUNCHPATH']))
        if action in NO_ACTION_TYPES and not runnable_children[row['Z_PK']] and not scripted and not direct:
            findings.append(_finding(FindingKind.UNASSIGNED, row['ZUNIQUEIDENTIFIER'],
                                     'enabled action button has no action'))
    return findings


def _runnable_action(row: sqlite3.Row) -> bool:
    action = int(row['ZACTION'] if row['ZACTION'] is not None else -1)
    if action not in NO_ACTION_TYPES:
        return True
    return action == -1 and any(value not in (None, '', '-1', -1)
                                for value in (row['ZSHORTCUT'], row['ZLAUNCHPATH']))


def _database_version(path: Path) -> str:
    match = re.search(r'version_([0-9_]+)_build_', path.name)
    return match.group(1).replace('_', '.') if match else 'unknown'


def _action_ranks(rows: list[sqlite3.Row]) -> dict[int, int]:
    groups: dict[tuple[int | None, int], list[sqlite3.Row]] = {}
    for row in rows:
        if row['ZGESTURETYPE'] == -1:
            key = (row['ZPARENT'], int(row['ZACTIONCATEGORY'] or 0))
            groups.setdefault(key, []).append(row)
    ranks = {}
    for siblings in groups.values():
        # Some legacy exports reuse sparse or tied indices. UUID is the stable
        # tie-breaker, so database primary keys never enter the checkpoint.
        ordered = sorted(siblings, key=lambda row: (int(row['ZORDER'] or 0),
                                                     row['ZUNIQUEIDENTIFIER'] or ''))
        ranks.update({row['Z_PK']: rank for rank, row in enumerate(ordered)})
    return ranks


def _quoted_identifier(value: str) -> str:
    if not re.fullmatch(r'[A-Z0-9_]+', value):
        raise ValueError('Unsupported BTT relationship schema identifier.')
    return '"'+value+'"'


def _relationship_entity(column: str) -> int | None:
    match = re.match(r'^Z_(\d+)', column)
    return int(match.group(1)) if match else None


def _app_relationship(connection: sqlite3.Connection) -> tuple[str, str, str]:
    metadata_columns = {row['name'] for row in connection.execute('PRAGMA table_info(Z_PRIMARYKEY)')}
    if not {'Z_ENT', 'Z_NAME'} <= metadata_columns:
        raise ValueError('Unsupported BTT app-scope metadata schema.')
    entities = {str(row['Z_NAME']).upper(): int(row['Z_ENT'])
                for row in connection.execute('SELECT Z_ENT,Z_NAME FROM Z_PRIMARYKEY')}
    if 'APP' not in entities or 'GESTURE' not in entities:
        raise ValueError('Unsupported BTT app/gesture entity metadata.')
    candidates = []
    for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'"):
        table = row['name']
        if 'APPS' not in table or 'GESTURES' not in table:
            continue
        columns = [item['name'] for item in
                   connection.execute('PRAGMA table_info('+_quoted_identifier(table)+')')]
        app = [name for name in columns if _relationship_entity(name) == entities['APP']]
        gesture = [name for name in columns if _relationship_entity(name) == entities['GESTURE']]
        if len(columns) == 2 and len(app) == 1 and len(gesture) == 1:
            candidates.append((table, app[0], gesture[0]))
    if len(candidates) != 1:
        raise ValueError('Unsupported or ambiguous BTT app-scope relationship schema.')
    return candidates[0]


def _app_scopes(connection: sqlite3.Connection) -> dict[int, list[str]]:
    table, app_column, trigger_column = _app_relationship(connection)
    table_q, app_q, trigger_q = map(_quoted_identifier, (table, app_column, trigger_column))
    invalid = connection.execute(f'''SELECT COUNT(*) FROM {table_q} relationship
        LEFT JOIN ZBTTBASEENTITY app ON app.Z_PK=relationship.{app_q}
        LEFT JOIN ZBTTBASEENTITY trigger ON trigger.Z_PK=relationship.{trigger_q}
        WHERE app.Z_PK IS NULL OR trigger.Z_PK IS NULL OR app.Z_ENT<>(
            SELECT Z_ENT FROM Z_PRIMARYKEY WHERE UPPER(Z_NAME)='APP')
        OR trigger.Z_ENT<>(SELECT Z_ENT FROM Z_PRIMARYKEY WHERE UPPER(Z_NAME)='GESTURE')
        OR app.ZBUNDLEIDENTIFIER IS NULL OR app.ZBUNDLEIDENTIFIER='' ''').fetchone()[0]
    if invalid:
        raise ValueError('Invalid BTT app-scope relationship data.')
    scopes: dict[int, set[str]] = {}
    for row in connection.execute(f'''SELECT relationship.{trigger_q} trigger_pk,
            app.ZBUNDLEIDENTIFIER bundle FROM {table_q} relationship
            JOIN ZBTTBASEENTITY app ON app.Z_PK=relationship.{app_q}'''):
        scopes.setdefault(int(row['trigger_pk']), set()).add(row['bundle'])
    return {pk: sorted(bundles) for pk, bundles in scopes.items()}


def _read_rows(connection: sqlite3.Connection) -> list[sqlite3.Row]:
    columns = {row['name'] for row in connection.execute('PRAGMA table_info(ZBTTBASEENTITY)')}
    missing = REQUIRED_COLUMNS - columns
    if missing:
        raise ValueError('Unsupported BTT database schema; missing columns: '+', '.join(sorted(missing)))
    rows = list(connection.execute('SELECT '+','.join(SELECT_COLUMNS)+' FROM ZBTTBASEENTITY'))
    uuids = [row['ZUNIQUEIDENTIFIER'] for row in rows if row['ZUNIQUEIDENTIFIER']]
    duplicates = sorted(uuid for uuid, count in Counter(uuids).items() if count > 1)
    if duplicates:
        raise ValueError('Duplicate stored BTT UUID; refusing snapshot: '+duplicates[0])
    primary_keys = [row['Z_PK'] for row in rows]
    if len(set(primary_keys)) != len(primary_keys):
        raise ValueError('Duplicate BTT primary key; refusing snapshot.')
    return rows


def read_snapshot(
    database: Path | None = None,
    roots: list[str] | None = None,
    btt_version: str | None = None,
    include_ids: list[str] | None = None,
    include_preset_ids: list[str] | None = None,
) -> ConfigSnapshot:
    """Capture graph roots plus exact expected IDs in one read-only transaction."""
    path = Path(database) if database is not None else current_database()
    requested_roots = list(roots) if roots is not None else [MEDIA]
    requested_ids = set(include_ids or ())
    requested_presets = set(include_preset_ids or ())
    if len(set(requested_roots)) != len(requested_roots):
        raise ValueError('Duplicate configured BTT Guard root.')
    with closing(connect(path)) as connection:
        connection.execute('BEGIN')
        try:
            rows = _read_rows(connection)
            by_uuid = {row['ZUNIQUEIDENTIFIER']: row for row in rows if row['ZUNIQUEIDENTIFIER']}
            by_pk = {row['Z_PK']: row for row in rows}
            action_ranks = _action_ranks(rows)
            app_scopes = _app_scopes(connection)
            selected, effective_ids, findings = _select_graph(requested_roots, rows, by_uuid)
            findings.extend(_unassigned_findings(effective_ids, rows))
            selected.update(by_uuid[uuid]['Z_PK'] for uuid in requested_ids if uuid in by_uuid)
            records = {row['ZUNIQUEIDENTIFIER']: _record(row, by_pk, action_ranks, app_scopes) for row in rows
                       if row['Z_PK'] in selected}
            preset_ids = ({record['preset'] for record in records.values() if record['preset']}
                          | requested_presets)
            presets = {uuid: bool(by_uuid[uuid]['ZACTIVATED']) for uuid in sorted(preset_ids)
                       if uuid in by_uuid}
        finally:
            connection.rollback()
    return ConfigSnapshot(
        schema_version=SCHEMA_VERSION,
        captured_at=time.time(),
        btt_version=btt_version or _database_version(path),
        database_name=path.name,
        roots=requested_roots,
        presets=presets,
        records=dict(sorted(records.items())),
        findings=sorted(findings, key=lambda item: (item['kind'], item['uuid'], item['detail'])),
    )
