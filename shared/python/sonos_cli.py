"""Shared sns argument handling; help and validation never discover speakers."""

import argparse
import importlib
import inspect
import json
import sys


COMMANDS = (
    'brown_noise', 'pink_noise', 'discover_weekly', 'random_album', 'random_radio',
    'group_vol_set', 'group_vol_up', 'group_vol_down', 'vol_up', 'vol_down',
    'back_15', 'back_30', 'fwd_15', 'fwd_30', 'next_track', 'prev_track',
    'rear_movie', 'rear_movie_resume', 'ensure_rear_movie', 'rear_normal',
    'rear_inverted', 'play_soundbyte', 'make_stereo_pair', 'vol_eql_all',
    'adjust_volume', 'play', 'pause', 'stop', 'mute', 'start_noise',
    'play_from_faves', 'audio_source', 'scrub', 'add_to_main_group',
    'remove_from_group', 'partymode', 'unjoin_all', 'standby_grouped',
    'direct', 'crossfade_on', 'crossfade_off', 'num_devices',
)
PHRASES = {('pink', 'noise'): 'pink_noise', ('brown', 'noise'): 'brown_noise'}


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
    if args.arguments and (args.task, args.arguments[0]) in PHRASES:
        args.task = PHRASES[(args.task, args.arguments.pop(0))]
    if args.task not in COMMANDS:
        parser.error('choose a command from --help (for example: pink_noise or pink noise)')
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
