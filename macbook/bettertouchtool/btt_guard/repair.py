"""Pure, conflict-first planning for BTT Guard repair."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from .model import (ConfigSnapshot, Finding, FindingKind, RepairOperation,
                    RepairPlan, SCHEMA_VERSION)


def _finding(kind: FindingKind, uuid: str, detail: str) -> Finding:
    return Finding(kind=kind.value, uuid=uuid, detail=detail)


def _changed_keys(expected: dict[str, Any], current: dict[str, Any], prefix: str) -> list[str]:
    return [prefix+key for key in sorted(expected.keys() | current.keys())
            if expected.get(key) != current.get(key)]


def _state_conflicts(
    expected: dict[str, Any],
    current: dict[str, Any],
    parent_changed: bool,
) -> list[Finding]:
    uuid = expected['uuid']
    conflicts: list[Finding] = []
    if expected['preset'] != current['preset']:
        conflicts.append(_finding(FindingKind.PRESET_CHANGED, uuid, 'field: preset'))
    if expected['trigger_type'] != current['trigger_type']:
        conflicts.append(_finding(FindingKind.TYPE_CHANGED, uuid, 'field: trigger_type'))
    if expected['enabled'] != current['enabled']:
        kind = FindingKind.DISABLED if expected['enabled'] else FindingKind.ENABLED_CHANGED
        conflicts.append(_finding(kind, uuid, 'field: enabled'))
    action_fields = [key for key in ('action_type', 'action_category')
                     if expected[key] != current[key]]
    action_fields.extend(_changed_keys(expected['payload'], current['payload'], 'payload.'))
    if action_fields:
        conflicts.append(_finding(FindingKind.ACTION_CHANGED, uuid,
                                  'fields: '+', '.join(action_fields)))
    if (expected['trigger_type'] == -1 and not parent_changed
            and expected['order'] != current['order']):
        conflicts.append(_finding(FindingKind.ORDER_CHANGED, uuid, 'field: order'))
    config_fields = _changed_keys(expected['config'], current['config'], 'config.')
    if expected['app_scope'] != current['app_scope']:
        config_fields.append('app_scope')
    if config_fields:
        conflicts.append(_finding(FindingKind.CONFIG_CHANGED, uuid,
                                  'fields: '+', '.join(config_fields)))
    return conflicts


def _definition(
    uuid: str,
    expected: dict[str, Any],
    definitions: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any] | None, Finding | None]:
    source = definitions.get(uuid)
    if not isinstance(source, dict) or source.get('BTTUUID') != uuid:
        return None, _finding(FindingKind.CHECKPOINT_INVALID, uuid,
                              'trusted runtime definition missing')
    node = deepcopy(source)
    if int(node.get('BTTTriggerType', -999)) != expected['trigger_type']:
        return None, _finding(FindingKind.CHECKPOINT_INVALID, uuid,
                              'definition field mismatch: trigger_type')
    parent = expected['parent']
    if parent is None:
        node.pop('BTTTriggerParentUUID', None)
        if not isinstance(node.get('BTTTriggerBelongsToPreset'), str):
            return None, _finding(FindingKind.CHECKPOINT_INVALID, uuid,
                                  'definition field missing: preset name')
    else:
        node['BTTTriggerParentUUID'] = parent
    if expected['trigger_type'] == -1:
        node['BTTTriggerType'] = -1
        inherited = node.get('BTTTriggerClass')
        if not inherited and isinstance(definitions.get(parent), dict):
            inherited = definitions[parent].get('BTTTriggerClass')
        if not inherited:
            return None, _finding(FindingKind.CHECKPOINT_INVALID, uuid,
                                  'definition field missing: trigger class')
        node['BTTTriggerClass'] = inherited
    return node, None


def _preset_conflicts(expected: ConfigSnapshot, current: ConfigSnapshot) -> list[Finding]:
    conflicts = []
    for uuid, active in expected['presets'].items():
        if uuid not in current['presets']:
            conflicts.append(_finding(FindingKind.MISSING, uuid, 'expected preset missing'))
        elif active != current['presets'][uuid]:
            kind = FindingKind.DISABLED if active else FindingKind.ENABLED_CHANGED
            conflicts.append(_finding(kind, uuid, 'field: preset activation'))
    return conflicts


def _finding_conflicts(
    expected: ConfigSnapshot,
    current: ConfigSnapshot,
    creates: set[str],
    definitions: dict[str, dict[str, Any]],
    restores: set[str],
) -> list[Finding]:
    conflicts = [_finding(FindingKind.CHECKPOINT_INVALID, item['uuid'],
                          'checkpoint finding: '+item['kind'])
                 for item in expected['findings']]
    missing_by_parent = {record['parent'] for uuid, record in expected['records'].items()
                         if uuid in restores and record['parent']}
    repaired_dependencies = _repairable_dependency_sources(expected, creates, definitions)
    for item in current['findings']:
        if item['kind'] == FindingKind.DEPENDENCY_CHANGED.value:
            if 'ambiguous' in item['detail'] or item['uuid'] not in repaired_dependencies:
                conflicts.append(item)
        elif item['kind'] == FindingKind.UNASSIGNED.value and item['uuid'] not in missing_by_parent:
            conflicts.append(item)
    return conflicts


def _repairable_dependency_sources(
    expected: ConfigSnapshot,
    creates: set[str],
    definitions: dict[str, dict[str, Any]],
) -> set[str]:
    repaired = set()
    for uuid, source in expected['records'].items():
        targets = []
        if source['action_type'] == 248:
            name = source['payload'].get('launch_path')
            if isinstance(name, str):
                targets = [target_uuid for target_uuid, target in expected['records'].items()
                           if target['trigger_type'] == 643
                           and target['payload'].get('gesture_config') == name]
        action_data = source['payload'].get('action_data')
        if isinstance(action_data, dict) and ({'BTTMenuActionMenuID',
                                               'BTTMenuActionMenuName'} & action_data.keys()):
            reference = action_data.get('BTTMenuActionMenuID') or action_data.get('BTTMenuActionMenuName')
            if isinstance(reference, str):
                targets.extend(target_uuid for target_uuid, target in expected['records'].items()
                               if target['trigger_type'] == 767 and
                               (target_uuid == reference or
                                _menu_identifier(definitions.get(target_uuid)) == reference))
        if len(set(targets)) == 1 and targets[0] in creates:
            repaired.add(uuid)
    return repaired


def _menu_identifier(definition: dict[str, Any] | None) -> Any:
    if not isinstance(definition, dict):
        return None
    config = definition.get('BTTMenuConfig')
    return config.get('BTTMenuElementIdentifier') if isinstance(config, dict) else None


def _sort_operations(
    operations: list[RepairOperation],
    current_ids: set[str],
) -> tuple[list[RepairOperation], Finding | None]:
    remaining = {operation['uuid']: operation for operation in operations}
    ordered: list[RepairOperation] = []
    available = set(current_ids)
    while remaining:
        ready = [operation for operation in remaining.values()
                 if operation['parent'] is None or operation['parent'] in available]
        if not ready:
            uuid = sorted(remaining)[0]
            return [], _finding(FindingKind.CHECKPOINT_INVALID, uuid,
                                'repair parent graph is incomplete or cyclic')
        ready.sort(key=lambda operation: (int(operation['definition'].get('BTTOrder', 0)),
                                          operation['uuid']))
        for operation in ready:
            ordered.append(operation)
            available.add(operation['uuid'])
            del remaining[operation['uuid']]
    return ordered, None


def _deduplicate(findings: list[Finding]) -> list[Finding]:
    unique = {(item['kind'], item['uuid'], item['detail']): item for item in findings}
    return [unique[key] for key in sorted(unique)]


def plan_repair(
    expected: ConfigSnapshot,
    current: ConfigSnapshot,
    definitions: dict[str, dict[str, Any]],
    checkpoint_id: str,
) -> RepairPlan:
    """Plan only safe creation or orphan reattachment; all other drift conflicts."""
    conflicts = _preset_conflicts(expected, current)
    operations: list[RepairOperation] = []
    creates = {uuid for uuid in expected['records'] if uuid not in current['records']}
    for uuid, wanted in expected['records'].items():
        actual = current['records'].get(uuid)
        mode = 'create' if actual is None else None
        if mode == 'create' and wanted['trigger_type'] == 643 and not wanted.get('app_scope'):
            conflicts.append(_finding(FindingKind.CHECKPOINT_INVALID, uuid,
                'legacy named trigger has no explicit app scope; reviewed restoration required'))
            continue
        parent_changed = actual is not None and wanted['parent'] != actual['parent']
        state_conflicts = [] if actual is None else _state_conflicts(wanted, actual, parent_changed)
        if parent_changed:
            if actual['parent'] is None and wanted['parent'] is not None and not state_conflicts:
                mode = 'reattach'
            else:
                conflicts.append(_finding(FindingKind.REPARENTED, uuid, 'field: parent'))
        conflicts.extend(state_conflicts)
        if mode is None:
            continue
        definition, error = _definition(uuid, wanted, definitions)
        if error:
            conflicts.append(error)
        else:
            operations.append(RepairOperation(uuid=uuid, parent=wanted['parent'],
                                              mode=mode, definition=definition))
    conflicts.extend(_finding_conflicts(expected, current, creates, definitions,
                                       {operation['uuid'] for operation in operations}))
    operations, graph_error = _sort_operations(operations, set(current['records']))
    if graph_error:
        conflicts.append(graph_error)
    return RepairPlan(schema_version=SCHEMA_VERSION, checkpoint_id=checkpoint_id,
                      operations=operations, conflicts=_deduplicate(conflicts))
