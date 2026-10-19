"""Narrow checkpoint acceptance after an installer's verified, UUID-scoped edits.

Standalone installers use before_install/after_install. Their subprocess boundary
keeps Guard's package imports intact without changing Python's import path. The
engine runs under the same guard.lock as manual checkpoint and monitor commands.
"""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

from .model import (AuditStatus, ExportUnavailableError, FindingKind, InstallerBaseline,
                    InstallerCheckpointOperation as Operation, InstallerCheckpointRequest,
                    InstallerCheckpointReason as Reason, InstallerCheckpointResult,
                    InstallerCheckpointStatus as Status, InstallerEditReceipt)
from .storage import state_lock

MANUAL_COMMAND = ('/usr/bin/python3 -B ~/dev/scripts/macbook/bettertouchtool/btt.py '
                  'guard checkpoint --accept-current --replace')
REASON_TEXT = {
    Reason.OPT_OUT: 'disabled by --no-checkpoint',
    Reason.NO_WRITES: 'no records were written',
    Reason.NO_CHECKPOINT: 'Guard has no active checkpoint',
    Reason.BASELINE_DRIFT: 'Guard had drift before this run',
    Reason.BASELINE_UNAVAILABLE: 'Guard was unavailable before this run',
    Reason.BASELINE_FAILED: 'the baseline audit could not be completed',
    Reason.CHECKPOINT_CHANGED: 'the active checkpoint changed during this run',
    Reason.FOREIGN_FINDINGS: "findings outside this installer's edits",
    Reason.AUDIT_UNAVAILABLE: 'Guard is unavailable after this run',
    Reason.AUDIT_FAILED: 'the final audit could not be completed',
    Reason.INVALID_EDITS: 'the verified edit UUIDs are missing or invalid',
    Reason.SAVED_UNVERIFIED: 'BTT has not yet saved all installer edits',
    Reason.CAPTURE_FAILED: 'checkpoint capture failed; the prior checkpoint is unchanged',
    Reason.EXPORT_UNAVAILABLE: 'BTT export needs your Terminal (Apple Events)',
    Reason.HELPER_FAILED: 'the checkpoint helper could not complete',
}


def _result(status, reason=None, checkpoint_id=None) -> InstallerCheckpointResult:
    return dict(status=status.value, reason=reason.value if reason else None,
                checkpoint_id=checkpoint_id)


def record_baseline(state_dir, *, no_checkpoint=False) -> InstallerBaseline:
    """Read-only audit before installer writes; failure never blocks installation."""
    baseline: InstallerBaseline = dict(state_dir=str(state_dir), audit=None, reason=None)
    if no_checkpoint:
        baseline['reason'] = Reason.OPT_OUT.value
        return baseline
    try:
        from .cli import audit_state
        with state_lock(state_dir):
            report = deepcopy(audit_state(state_dir))
        baseline['audit'] = report
        missing = any(finding['kind'] == FindingKind.CHECKPOINT_MISSING for finding in report['findings'])
        if not report['checkpoint_id'] and (missing or report['status'] == AuditStatus.HEALTHY):
            reason = Reason.NO_CHECKPOINT
        elif report['status'] == AuditStatus.DRIFT:
            reason = Reason.BASELINE_DRIFT
        elif report['status'] != AuditStatus.HEALTHY or report['findings']:
            reason = Reason.BASELINE_UNAVAILABLE
        else:
            return baseline
        baseline['reason'] = reason.value
    except Exception:
        baseline['reason'] = Reason.BASELINE_FAILED.value
    return baseline


def _accept_locked(baseline, touched_uuids):
    from .checkpoint import capture
    from .cli import audit_state
    try:
        report = audit_state(baseline['state_dir'])
        if report['checkpoint_id'] != baseline['audit']['checkpoint_id']:
            return _result(Status.SKIPPED, Reason.CHECKPOINT_CHANGED)
        if report['status'] not in (AuditStatus.HEALTHY, AuditStatus.DRIFT):
            return _result(Status.SKIPPED, Reason.AUDIT_UNAVAILABLE)
        if any(finding['uuid'] not in touched_uuids for finding in report['findings']):
            return _result(Status.SKIPPED, Reason.FOREIGN_FINDINGS)
    except Exception:
        return _result(Status.FAILED, Reason.AUDIT_FAILED)
    try:
        manifest = capture(baseline['state_dir'], accept_current=True, replace=True)
        return _result(Status.TAKEN, checkpoint_id=manifest['checkpoint_id'])
    except ExportUnavailableError:
        return _result(Status.FAILED, Reason.EXPORT_UNAVAILABLE)
    except Exception:
        return _result(Status.FAILED, Reason.CAPTURE_FAILED)


def accept_installer_edits(baseline, touched_uuids, installer_name, *, wrote):
    """Accept only a healthy baseline and findings confined to verified edits.

The caller must complete its saved-state verification first. A failed checkpoint
is not a failed apply: no exception from this function escapes into the installer.
"""
    try:
        if not wrote:
            return _result(Status.SKIPPED, Reason.NO_WRITES)
        if baseline['reason']:
            return _result(Status.SKIPPED, Reason(baseline['reason']))
        audit = baseline['audit']
        if not audit or not audit['checkpoint_id']:
            return _result(Status.SKIPPED, Reason.NO_CHECKPOINT)
        if audit['status'] != AuditStatus.HEALTHY or audit['findings']:
            return _result(Status.SKIPPED, Reason.BASELINE_UNAVAILABLE)
        if (not isinstance(touched_uuids, (list, tuple, set)) or not touched_uuids or
                any(not isinstance(uid, str) or not uid.strip() for uid in touched_uuids)):
            return _result(Status.SKIPPED, Reason.INVALID_EDITS)
        with state_lock(baseline['state_dir']):
            return _accept_locked(baseline, set(touched_uuids))
    except Exception:
        return _result(Status.FAILED, Reason.HELPER_FAILED)


def _request(document: InstallerCheckpointRequest):
    # Installers already import this standalone module; the package engine is a
    # fixed subprocess, like Guard's existing restart bridge in api.py.
    from btt_common import ROOT
    result = subprocess.run(
        [sys.executable, '-B', '-m', 'macbook.bettertouchtool.btt_guard.installer_checkpoint'],
        cwd=ROOT, input=json.dumps(document), capture_output=True, text=True, check=True)
    return json.loads(result.stdout)


def before_install(*, no_checkpoint=False) -> InstallerBaseline:
    """Installer entry: capture the baseline before any BTT mutation."""
    if no_checkpoint:
        return dict(state_dir='', audit=None, reason=Reason.OPT_OUT.value)
    try:
        baseline = _request({'operation': Operation.BEFORE.value})
        _validate_baseline(baseline)
        return baseline
    except Exception:
        return dict(state_dir='', audit=None, reason=Reason.BASELINE_FAILED.value)


def _validate_baseline(baseline):
    if not isinstance(baseline, dict) or not isinstance(baseline.get('state_dir'), str):
        raise ValueError('Invalid installer baseline.')
    if baseline.get('reason') is not None:
        Reason(baseline['reason'])
    else:
        audit = baseline['audit']
        if (audit['status'] != AuditStatus.HEALTHY or not isinstance(audit['checkpoint_id'], str)
                or not audit['checkpoint_id'] or audit['findings'] != []):
            raise ValueError('Invalid eligible installer baseline.')


def parse_edit_receipt(output) -> InstallerEditReceipt:
    """Validate the native worker's InstallerEditReceipt before trusting its UUIDs."""
    result = json.loads(output)
    if (not isinstance(result, dict) or not isinstance(result.get('message'), str)
            or not isinstance(result.get('wrote'), bool)
            or not isinstance(result.get('touched_uuids'), list)
            or any(not isinstance(uid, str) or not uid.strip() for uid in result['touched_uuids'])
            or result['wrote'] != bool(result['touched_uuids'])):
        raise ValueError('Invalid installer edit receipt; no checkpoint requested.')
    return result


def _validate_result(result):
    status = Status(result['status'])
    if status == Status.TAKEN:
        if not isinstance(result['checkpoint_id'], str) or not result['checkpoint_id']:
            raise ValueError('Missing checkpoint identifier.')
    else:
        Reason(result['reason'])


def print_result(result, installer_name):
    """Keep payloads/native error details private, and leave a Terminal recovery command."""
    try:
        if result['status'] == Status.TAKEN:
            print(f"BTT Guard checkpoint taken ({installer_name}): {result['checkpoint_id']}")
        else:
            reason = Reason(result['reason'])
            print(f"BTT Guard checkpoint {result['status']} ({installer_name}): {REASON_TEXT[reason]}.")
            # Nothing changed, so the current checkpoint still covers the menu.
            if reason != Reason.NO_WRITES:
                print('From your Terminal, after reviewing the menu: '+MANUAL_COMMAND)
    except Exception:
        # Broken stdout must not turn an already verified apply into a failure.
        pass


def skip_checkpoint(installer_name, reason):
    """Report a nonfatal installer-specific refusal without auditing or capturing."""
    try:
        result = _result(Status.SKIPPED, Reason(reason))
    except Exception:
        result = _result(Status.FAILED, Reason.HELPER_FAILED)
    print_result(result, installer_name)
    return result


def after_install(baseline, touched_uuids, installer_name, *, wrote=True):
    """Installer entry: best-effort checkpoint and one clear result after verification."""
    try:
        if not wrote:
            result = _result(Status.SKIPPED, Reason.NO_WRITES)
        elif baseline['reason']:
            result = _result(Status.SKIPPED, Reason(baseline['reason']))
        else:
            result = _request(dict(operation=Operation.AFTER.value, baseline=baseline,
                touched_uuids=list(touched_uuids), installer_name=installer_name, wrote=wrote))
            _validate_result(result)
    except Exception:
        result = _result(Status.FAILED, Reason.HELPER_FAILED)
    print_result(result, installer_name)
    return result


def main():
    """Private stdin/stdout bridge; standalone installers never edit Guard state."""
    from .checkpoint import DEFAULT_STATE
    request = json.load(sys.stdin)
    if request['operation'] == Operation.BEFORE:
        result = record_baseline(DEFAULT_STATE)
    elif request['operation'] == Operation.AFTER:
        baseline = request['baseline']
        _validate_baseline(baseline)
        if Path(baseline['state_dir']) != DEFAULT_STATE or request['wrote'] is not True:
            raise ValueError('Invalid installer checkpoint request.')
        result = accept_installer_edits(baseline, request['touched_uuids'],
                                       request['installer_name'], wrote=True)
    else:
        raise ValueError('Unknown installer checkpoint operation.')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
