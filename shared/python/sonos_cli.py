"""Shared sns argument handling; help and validation never discover speakers."""

import argparse
import importlib
import inspect
import json
import sys

from sonos_cli_commands import COMMANDS


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        prog='sns', description='Sonos tasks (local by default on Mac; --pi selects SSH).',
        epilog='Commands: ' + ', '.join(COMMANDS),
    )
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--check', action='store_true', help='validate without contacting speakers')
    modes.add_argument('--doctor', action='store_true', help='show Python and SoCo; no speaker access')
    modes.add_argument('--devices', action='store_true', help='discover and list speakers; read only')
    parser.add_argument('task', nargs='?')
    parser.add_argument('arguments', nargs='*')
    args = parser.parse_args(argv)
    if args.doctor or args.devices:
        if args.task:
            parser.error('diagnostics do not take a task')
        return parser, args
    if args.task not in COMMANDS:
        parser.error('choose a command from --help (for example: pink_noise)')
    try:
        args.arguments = COMMANDS[args.task].parse(args.arguments)
    except ValueError as error:
        parser.error(f'{args.task}: {error}')
    return parser, args


def main(argv=None):
    parser, args = parse_args(argv)
    try:
        tasks = importlib.import_module('sonos_tasks')
        if args.doctor:
            import soco
            print(json.dumps({'python': sys.executable, 'version': sys.version.split()[0],
                              'soco': soco.__version__, 'tasks': tasks.__file__}))
            return 0
        if args.devices:
            devices = tasks.get_all_devices()
            print(json.dumps(sorted(
                ({'name': device.player_name, 'uid': device.uid,
                  'visible': device.is_visible} for device in devices),
                key=lambda device: device['uid'],
            )))
            return 0 if devices else 1
        task = getattr(tasks, args.task)
        try:
            inspect.signature(task).bind(*args.arguments)
        except TypeError as error:
            parser.error(f'{args.task}{inspect.signature(task)}: {error}')
        if args.check:
            print(json.dumps({'task': args.task, 'arguments': args.arguments,
                              'python': sys.executable, 'tasks': tasks.__file__}))
            return 0
        result = task(*args.arguments)
        if isinstance(result, (int, str)) and not isinstance(result, bool):
            print(result)
        return 0
    except Exception as error:
        print(f'sns: {type(error).__name__}: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
