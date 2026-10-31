"""mac-metered: cached admission, local permissions and authenticated Pi sync."""
import argparse
import json
import re

from shared.metered.records import Host, MAX_OVERRIDE_SECONDS, PolicyChange, PolicyOperation
from shared.metered.registry import consumer
from shared.metered.store import RevisionConflict
from shared.python.tool_settings import SettingsConflict
from .controller import Controller
from .policy_requests import apply_policy


def duration(text):
    match = re.fullmatch(r'([1-9][0-9]*)([smh])', text)
    if not match:
        raise argparse.ArgumentTypeError('duration needs a positive number and s, m or h')
    seconds = int(match[1]) * {'s': 1, 'm': 60, 'h': 3600}[match[2]]
    if seconds > MAX_OVERRIDE_SECONDS:
        raise argparse.ArgumentTypeError('duration cannot exceed 24h')
    return seconds


def parser():
    root = argparse.ArgumentParser(description=__doc__)
    commands = root.add_subparsers(dest='command', required=True)
    for name in ('status', 'config'):
        commands.add_parser(name).add_argument('--json', action='store_true')
    commands.add_parser('is-metered').add_argument('-v', action='store_true')
    allows = commands.add_parser('allows')
    allows.add_argument('consumer'); allows.add_argument('-v', action='store_true')
    commands.add_parser('sync'); commands.add_parser('tick')
    ssids = commands.add_parser('ssid')
    ssids.add_argument('op', choices=('add', 'remove')); ssids.add_argument('ssid')
    ssids.add_argument('--revision', type=int)
    mode = commands.add_parser('mode')
    mode.add_argument('value', choices=('auto', 'metered', 'unmetered'))
    mode.add_argument('--for', dest='seconds', type=duration)
    mode.add_argument('--revision', type=int)
    approve = commands.add_parser('approve')
    approve.add_argument('consumer'); approve.add_argument('duration')
    approve.add_argument('--revision', type=int)
    revoke = commands.add_parser('revoke'); revoke.add_argument('consumer')
    revoke.add_argument('--revision', type=int)
    exempt = commands.add_parser('exempt'); exempt.add_argument('consumer')
    exempt.add_argument('enabled', choices=('on', 'off')); exempt.add_argument('--revision', type=int)
    low = commands.add_parser('low-data-mode')
    low.add_argument('value', choices=('on', 'off', 'restore'))
    low.add_argument('--revision'); low.add_argument('--confirm', action='store_true')
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


def admission(args, controller):
    state = controller.current()
    if args.command == 'is-metered':
        if args.v:
            print(state.verdict.reason)
        return 0 if state.effective.metered else 1
    consumer(args.consumer, Host.MAC)
    result = state.consumers.get(args.consumer)
    if args.v:
        print(result.reason if result else 'fresh consumer state unavailable')
    return 0 if result and result.permitted else 1


def low_data(args, controller):
    if args.value == 'restore':
        if not args.confirm:
            raise ValueError('restoring an owned flag requires --confirm')
        return controller.tick(enforce=True, restore=True).as_dict()
    if args.value == 'on' and not args.confirm:
        raise ValueError('pass --confirm only after the LAN Time Machine test succeeds')
    return controller.configure_low_data(args.value == 'on', args.revision)


def dispatch(args, controller):
    reads = {'status': controller.status, 'config': lambda: controller.cache.overlay().as_dict(),
             'tick': lambda: controller.tick().as_dict(),
             'sync': lambda: sync(controller)}
    if args.command in reads:
        return reads[args.command]()
    if args.command == 'ssid':
        actions = {'add': lambda: controller.add_ssid(args.ssid),
                   'remove': lambda: controller.remove_ssid(args.ssid, args.revision)}
        return actions[args.op]()
    if args.command == 'low-data-mode':
        return low_data(args, controller)
    if args.command in {item.value for item in PolicyOperation}:
        return apply_policy(controller, policy_request(args, controller))
    return admission(args, controller)


def sync(controller):
    controller.tick(force_sync=True, enforce=False)
    result = controller.status()
    if result['sync']['error']:
        raise RuntimeError(result['sync']['error'])
    return result


def main(argv=None, controller=None):
    args = parser().parse_args(argv)
    try:
        result = dispatch(args, controller or Controller())
        if type(result) is int:
            return result
        print(json.dumps(result, allow_nan=False))
        return 0
    except (OSError, ValueError, TypeError, RuntimeError, argparse.ArgumentTypeError) as error:
        if args.command in ('allows', 'is-metered'):
            if args.v:
                print(str(error))
            return 0 if args.command == 'is-metered' else 1
        print(json.dumps({'error': str(error)}))
        return 75 if isinstance(error, (RevisionConflict, SettingsConflict)) else 2
