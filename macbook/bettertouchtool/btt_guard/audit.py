"""Pure comparison of a trusted BTT checkpoint with current saved state."""
from __future__ import annotations

import time
from typing import Any

from .model import AuditReport, AuditStatus, ConfigSnapshot, Finding, FindingKind, RecordState


def _finding(kind: FindingKind, uuid: str, detail: str) -> Finding:
    return Finding(kind=kind.value, uuid=uuid, detail=detail)


def _changed_keys(expected: dict[str, Any], current: dict[str, Any], prefix: str) -> list[str]:
    return [prefix+key for key in sorted(expected.keys() | current.keys())
            if expected.get(key) != current.get(key)]


def _record_findings(expected: RecordState, current: RecordState) -> list[Finding]:
    uuid = expected['uuid']
    findings: list[Finding] = []
    parent_changed = expected['parent'] != current['parent']
    if parent_changed:
        findings.append(_finding(FindingKind.REPARENTED, uuid, 'field: parent'))
    if expected['preset'] != current['preset']:
        findings.append(_finding(FindingKind.PRESET_CHANGED, uuid, 'field: preset'))
    if expected['enabled'] != current['enabled']:
        kind = FindingKind.DISABLED if expected['enabled'] and not current['enabled'] else FindingKind.ENABLED_CHANGED
        findings.append(_finding(kind, uuid, 'field: enabled'))
    if expected['trigger_type'] != current['trigger_type']:
        findings.append(_finding(FindingKind.TYPE_CHANGED, uuid, 'field: trigger_type'))
    if (expected['trigger_type'] == -1 and not parent_changed
            and expected['order'] != current['order']):
        findings.append(_finding(FindingKind.ORDER_CHANGED, uuid, 'field: order'))
    action_fields = []
    for field in ('action_type', 'action_category'):
        if expected[field] != current[field]:
            action_fields.append(field)
    action_fields.extend(_changed_keys(expected['payload'], current['payload'], 'payload.'))
    if action_fields:
        findings.append(_finding(FindingKind.ACTION_CHANGED, uuid,
                                 'fields: '+', '.join(action_fields)))
    config_fields = _changed_keys(expected['config'], current['config'], 'config.')
    if expected['app_scope'] != current['app_scope']:
        config_fields.append('app_scope')
    if config_fields:
        findings.append(_finding(FindingKind.CONFIG_CHANGED, uuid,
                                 'fields: '+', '.join(config_fields)))
    return findings


def _preset_findings(expected: ConfigSnapshot, current: ConfigSnapshot) -> list[Finding]:
    findings = []
    for uuid, active in expected['presets'].items():
        if uuid not in current['presets']:
            findings.append(_finding(FindingKind.MISSING, uuid, 'expected preset missing'))
        elif active and not current['presets'][uuid]:
            findings.append(_finding(FindingKind.DISABLED, uuid, 'field: preset activation'))
    return findings


def _missing_detail(expected: ConfigSnapshot, uuid: str, record: RecordState) -> str:
    if uuid in expected['roots'] or record['trigger_type'] == 767:
        return 'expected root missing'
    if record['trigger_type'] == -1:
        return 'expected action missing'
    return 'expected item missing'


def compare(
    expected: ConfigSnapshot,
    current: ConfigSnapshot,
    checkpoint_id: str,
) -> AuditReport:
    """Report expected-record drift without treating unrelated additions as damage."""
    findings: list[Finding] = [item for item in current['findings']
                               if not (item['kind'] == FindingKind.MISSING.value
                                       and item['uuid'] in expected['records'])]
    findings.extend(_preset_findings(expected, current))
    for uuid, expected_record in expected['records'].items():
        current_record = current['records'].get(uuid)
        if current_record is None:
            findings.append(_finding(FindingKind.MISSING, uuid,
                                     _missing_detail(expected, uuid, expected_record)))
        else:
            findings.extend(_record_findings(expected_record, current_record))
    findings.sort(key=lambda item: (item['kind'], item['uuid'], item['detail']))
    status = AuditStatus.DRIFT if findings else AuditStatus.HEALTHY
    return AuditReport(
        status=status.value,
        checkpoint_id=checkpoint_id,
        checked_at=time.time(),
        btt_version=current['btt_version'],
        findings=findings,
    )
