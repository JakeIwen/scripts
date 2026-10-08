"""Explicit checkpoint/repair commands and an unattended, read-only audit path."""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
import os
import stat
from pathlib import Path
import sqlite3
import time
from uuid import uuid4

from ..btt_common import ROOT, backup_configuration, current_database
from . import api
from .audit import compare
from .checkpoint import ACTIVE_FILE, DEFAULT_STATE, capture, load_checkpoint, preset_names
from .database import read_snapshot
from .definitions import build_definitions, verify_created_appearance
from .model import AuditReport, AuditStatus, FindingKind, MonitorHealth, SCHEMA_VERSION, ServiceVerb
from .monitor import INVALID_STATE, monitor_report, select_monitor_state
from .notify import GuardNotification, send_warning_notification
from .repair import plan_repair
from .service import run_service, ServiceError
from .storage import load_json, private_dir, state_lock, write_json


def current_for(expected):
    return read_snapshot(roots=expected['roots'], include_ids=list(expected['records']),
                         include_preset_ids=list(expected['presets']))


def unavailable(kind, checkpoint_id=None):
    return AuditReport(status=AuditStatus.UNAVAILABLE.value, checkpoint_id=checkpoint_id,
        checked_at=time.time(), btt_version=None,
        findings=[{'kind': kind.value, 'uuid': '', 'detail': kind.value}])


def audit_state(state_dir, checkpoint_id=None):
    try:
        manifest, expected, _ = load_checkpoint(state_dir, checkpoint_id)
    except FileNotFoundError:
        kind = FindingKind.CHECKPOINT_INVALID if (Path(state_dir)/ACTIVE_FILE).exists() else FindingKind.CHECKPOINT_MISSING
        return unavailable(kind, checkpoint_id)
    except (ValueError, OSError, TypeError, KeyError):
        return unavailable(FindingKind.CHECKPOINT_INVALID, checkpoint_id)
    try:
        current = current_for(expected)
        return compare(expected, current, manifest['checkpoint_id'])
    except (ValueError, sqlite3.Error, OSError, RuntimeError):
        return unavailable(FindingKind.DATABASE_UNAVAILABLE, manifest['checkpoint_id'])


def repair_checkpoint(state_dir, *, apply=False, checkpoint_id=None):
    manifest, expected, definitions = load_checkpoint(state_dir, checkpoint_id)
    current = current_for(expected)
    plan = plan_repair(expected, current, definitions, manifest['checkpoint_id'])
    summary = {'checkpoint_id': plan['checkpoint_id'], 'operations': [
        {'uuid': op['uuid'], 'mode': op['mode'], 'parent': op['parent']} for op in plan['operations']],
        'conflicts': plan['conflicts']}
    if not apply or not plan['operations'] or plan['conflicts']:
        return summary
    names = preset_names(current_database(), expected['presets'])
    for operation in plan['operations']:
        preset = expected['records'][operation['uuid']]['preset']
        operation['definition']['BTTTriggerBelongsToPreset'] = names[preset]
    plan['runtime_snapshot'] = json.loads(api.call_worker('worker.js', plan, 'snapshot'))
    database = current_database()
    backup = backup_configuration(database, plan, prefix='repair-', filename='plan.json',
                                 directory_root=private_dir(Path(state_dir)/'safety-backups'))
    before = current_for(expected)
    if before['records'] != current['records'] or before['presets'] != current['presets']:
        raise RuntimeError('BTT changed during repair preparation. No repair applied.')
    print('Verified safety backup: '+str(backup.parent), flush=True)
    print(api.call_worker('worker.js', plan, 'apply'), flush=True)
    print('Restarting BTT cleanly to verify saved recovery. The repair does not invoke your buttons.', flush=True)
    api.restart_btt()
    report = audit_state(state_dir, manifest['checkpoint_id'])
    write_json(Path(state_dir)/'last-audit.json', report)
    if report['status'] != AuditStatus.HEALTHY:
        raise RuntimeError('Repair is not verified after restart. Review last-audit.json and the safety backup; no rollback performed.')
    after = current_for(expected)
    exports = api.wait_for_exports(after)
    rebuilt = build_definitions(exports, after, preset_names(current_database(), after['presets']))
    verify_created_appearance(definitions, rebuilt,
                              [op['uuid'] for op in plan['operations'] if op['mode'] == 'create'])
    print(api.call_worker('worker.js', plan, 'verify'), flush=True)
    return {**summary, 'verified_after_restart': True, 'backup': str(backup.parent)}


def _read_notification_state(path):
    try:
        return load_json(path)
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        return INVALID_STATE


def _archive_recovery_state(path):
    if not path.exists() and not path.is_symlink():
        return
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
        raise ValueError('Unsafe monitor recovery state; refusing to move it.')
    directory = private_dir(path.parent/'state-history')
    path.rename(directory/('monitor-state-'+str(uuid4())+'.json'))


def run_monitor(state_dir, *, notify=True, managed=False):
    state = private_dir(state_dir)
    with state_lock(state):
        report = audit_state(state)
        primary, fallback = state/'monitor-state.json', state/'monitor-recovery-state.json'
        selection = select_monitor_state(_read_notification_state(primary), _read_notification_state(fallback))
        if selection.reason:
            report['status'] = AuditStatus.UNAVAILABLE.value
            report['findings'].append({'kind': FindingKind.MONITOR_STATE_INVALID.value,
                                       'uuid': '', 'detail': 'notification state needs review'})
        write_json(state/'last-audit.json', report)
        sender = send_warning_notification if notify else lambda _: False
        result = monitor_report(report, state=selection.state, sender=sender, now=time.time())
        if notify:
            destination = primary if selection.write_target == 'primary' else fallback
            if destination == fallback and selection.reason in ('both-invalid', 'recovery-invalid'):
                _archive_recovery_state(fallback)
            write_json(destination, result.state)
            if selection.adopt_recovery:
                _archive_recovery_state(fallback)
        health: MonitorHealth = dict(schema_version=SCHEMA_VERSION, run_id=str(uuid4()), pid=os.getpid(),
            uid=os.getuid(), root=str(ROOT), completed_at=time.time(), audit_status=report['status'])
        write_json(state/('monitor-health.json' if managed else 'manual-audit-health.json'), health)
        return {'audit_status': report['status'], 'findings': len(report['findings']),
                'notification': result.action if notify else 'suppressed', 'run_id': health['run_id']}


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument('--state-dir', type=Path, default=DEFAULT_STATE)
    commands = result.add_subparsers(dest='command', required=True)
    commands.add_parser('inspect', help='read-only current graph checks before creating a checkpoint')
    checkpoint = commands.add_parser('checkpoint', help='explicitly promote a verified current configuration')
    checkpoint.add_argument('--accept-current', action='store_true')
    checkpoint.add_argument('--replace', action='store_true')
    checkpoint.add_argument('--list', action='store_true')
    audit = commands.add_parser('audit', help='read-only saved-state check')
    audit.add_argument('--checkpoint-id')
    repair = commands.add_parser('repair', help='preview missing/detached-record repair; --apply requires owner Terminal')
    repair.add_argument('--checkpoint-id')
    repair.add_argument('--apply', action='store_true')
    monitor = commands.add_parser('monitor', help='one scheduled read-only audit, with debounced warnings')
    monitor.add_argument('--no-notify', action='store_true')
    monitor.add_argument('--managed', action='store_true', help=argparse.SUPPRESS)
    notify = commands.add_parser('notify-test', help='send one clearly marked setup test only with --send')
    notify.add_argument('--send', action='store_true')
    service = commands.add_parser('service', help='manage the finite launchd audit job')
    service.add_argument('verb', choices=[verb.value for verb in ServiceVerb])
    return result


def _checkpoint_command(args):
    if args.list:
        directory = args.state_dir/'checkpoints'
        return [{'checkpoint_id': p.name, 'active': (args.state_dir/ACTIVE_FILE).exists() and
                 load_json(args.state_dir/ACTIVE_FILE).get('checkpoint_id') == p.name}
                for p in sorted(directory.glob('cp-*')) if (p/'manifest.json').is_file()]
    with state_lock(args.state_dir):
        return capture(args.state_dir, accept_current=args.accept_current, replace=args.replace)


def main(argv=None):
    args = parser().parse_args(argv)
    os.umask(0o077)
    code = 0
    if args.command == 'checkpoint':
        output = _checkpoint_command(args)
    elif args.command == 'inspect':
        snapshot = read_snapshot()
        output = {'btt_version': snapshot['btt_version'], 'records': len(snapshot['records']),
                  'findings': snapshot['findings']}
        code = 2 if snapshot['findings'] else 0
    elif args.command == 'audit':
        output = audit_state(args.state_dir, args.checkpoint_id)
        code = 0 if output['status'] == AuditStatus.HEALTHY else 2
    elif args.command == 'repair':
        with state_lock(args.state_dir):
            output = repair_checkpoint(args.state_dir, apply=args.apply, checkpoint_id=args.checkpoint_id)
        code = 2 if output['conflicts'] else 0
    elif args.command == 'monitor':
        output = run_monitor(args.state_dir, notify=not args.no_notify, managed=args.managed)
    elif args.command == 'notify-test':
        if not args.send:
            output = {'sent': False, 'instruction': 'Use --send to send one generic BTT Guard test warning.'}
        else:
            sent = send_warning_notification(GuardNotification('BetterTouchTool guard: setup test',
                'Test notification only. No BetterTouchTool settings were changed.', 'default'))
            output, code = {'sent': sent}, 0 if sent else 2
    else:
        output = run_service(args.verb, args.state_dir, ROOT)
        if args.verb == 'status' and not output['supervised']:
            code = 2
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return code
