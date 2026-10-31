"""The stable vanpi-metered command surface; stdout is JSON for data commands."""
import argparse
import json
import re
import sys

from shared.metered.records import (
    MAX_OVERRIDE_SECONDS, PolicyChange, PolicyOperation, SSIDChange, SyncRequest,
)
from shared.metered.registry import consumer
from shared.metered.store import MAX_RECORD_BYTES, RevisionConflict
from .controller import Controller
from .policy_requests import apply_policy


def read_input(record_type, stream):
    text = stream.read(MAX_RECORD_BYTES + 1)
    if len(text.encode('utf-8')) > MAX_RECORD_BYTES:
        raise ValueError('request is too large')
    value = json.loads(text)
    if not isinstance(value, dict):
        raise ValueError('request must be an object')
    return record_type.from_dict(value) if record_type is not None else value


def duration(text):
    match = re.fullmatch(r'([1-9][0-9]*)([smh])', text)
    if not match:
        raise argparse.ArgumentTypeError('duration must be a positive number followed by s, m or h')
    seconds = int(match[1]) * {'s': 1, 'm': 60, 'h': 3600}[match[2]]
    if seconds > MAX_OVERRIDE_SECONDS:
        raise argparse.ArgumentTypeError('duration cannot exceed 24 hours')
    return seconds


def parser():
    root = argparse.ArgumentParser(description=__doc__)
    commands = root.add_subparsers(dest='command', required=True)
    status = commands.add_parser('status'); status.add_argument('--json', action='store_true')
    config = commands.add_parser('config'); config.add_argument('--json', action='store_true')
    metered = commands.add_parser('is-metered'); metered.add_argument('-v', action='store_true')
    allows = commands.add_parser('allows'); allows.add_argument('consumer')
    allows.add_argument('-v', action='store_true')
    admit = commands.add_parser('admit'); admit.add_argument('consumer')
    for name in ('sync', 'edit-ssids', 'edit-policy', 'run', 'tick'):
        commands.add_parser(name)
    ssids = commands.add_parser('ssid')
    ssids.add_argument('op', choices=('add', 'remove')); ssids.add_argument('ssid')
    ssids.add_argument('--revision', type=int)
    mode = commands.add_parser('mode')
    mode.add_argument('value', choices=('auto', 'metered', 'unmetered'))
    mode.add_argument('--for', dest='seconds', type=duration); mode.add_argument('--revision', type=int)
    approve = commands.add_parser('approve')
    approve.add_argument('consumer'); approve.add_argument('duration')
    approve.add_argument('--revision', type=int)
    revoke = commands.add_parser('revoke'); revoke.add_argument('consumer')
    revoke.add_argument('--revision', type=int)
    exempt = commands.add_parser('exempt'); exempt.add_argument('consumer')
    exempt.add_argument('enabled', choices=('on', 'off')); exempt.add_argument('--revision', type=int)
    return root


def policy_request(args, controller):
    revision = args.revision if args.revision is not None else controller.policy.load().revision
    values = {'op': args.command, 'revision': revision}
    if args.command == PolicyOperation.MODE:
        values.update(value=args.value, seconds=args.seconds)
    else:
        values['consumer'] = args.consumer
    if args.command == PolicyOperation.APPROVE:
        episode = args.duration == 'episode'
        values.update(value='episode' if episode else 'until',
                      seconds=None if episode else duration(args.duration))
    if args.command == PolicyOperation.EXEMPT:
        values['enabled'] = args.enabled == 'on'
    return PolicyChange.from_dict(values)


def dispatch(args, controller, stream):
    if args.command in ('run', 'tick'):
        from .runtime import run
        return run(controller, once=args.command == 'tick')
    if args.command == 'status':
        return controller.status()
    if args.command == 'config':
        return controller.config.load().as_dict()
    if args.command == 'admit':
        from .admission import decide
        allowed, reason = decide(read_input(None, stream), consumer_id=args.consumer)
        return {'allowed': allowed, 'reason': reason}
    if args.command == 'sync':
        return controller.sync(read_input(SyncRequest, stream)).as_dict()
    if args.command == 'edit-ssids':
        change = read_input(SSIDChange, stream)
        return controller.config.edit_ssid(change.op.value, change.ssid, change.revision).as_dict()
    if args.command == 'ssid':
        revision = args.revision if args.revision is not None else controller.config.load().revision
        return controller.config.edit_ssid(args.op, args.ssid, revision).as_dict()
    if args.command == 'edit-policy':
        return apply_policy(controller, read_input(PolicyChange, stream), 'dashboard').as_dict()
    if args.command in {item.value for item in PolicyOperation}:
        return apply_policy(controller, policy_request(args, controller)).as_dict()
    state = controller.current()
    if args.command == 'is-metered':
        if args.v:
            print(state.verdict.reason)
        return 0 if state.effective.metered else 1
    consumer(args.consumer, 'pi')
    result = state.consumers.get(args.consumer)
    if args.v:
        print(result.reason if result else 'consumer state unavailable')
    return 0 if result and result.permitted else 1


def main(argv=None, controller=None, stream=None):
    args = parser().parse_args(argv)
    try:
        result = dispatch(args, controller or Controller(), stream or sys.stdin)
        if type(result) is int:
            return result
        print(json.dumps(result, allow_nan=False))
        return 0
    except (OSError, ValueError, TypeError, RuntimeError, argparse.ArgumentTypeError) as error:
        print(json.dumps({'error': str(error)}))
        if args.command == 'is-metered':
            return 0
        if args.command == 'allows':
            return 1
        return 75 if isinstance(error, RevisionConflict) else 2
