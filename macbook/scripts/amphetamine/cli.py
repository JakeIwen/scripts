"""CLI boundary; caffeinate holds idle sleep only for the duration of a change."""
import argparse
import json
from pathlib import Path
import subprocess
import sys

from .control import configure, read_status
from .model import Conflict, ControlError, validate_change
from .native import NativeApp


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(description='Read or configure Amphetamine automatic screen locking.')
    parser.add_argument('--awake-guard', action='store_true', help=argparse.SUPPRESS)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('status')
    change = commands.add_parser('configure')
    change.add_argument('--enabled', choices=('on', 'off'), required=True)
    change.add_argument('--minutes', type=int, required=True)
    change.add_argument('--revision', required=True)
    args = parser.parse_args(argv)
    if args.command == 'configure':
        try:
            validate_change(args.enabled == 'on', args.minutes, args.revision)
        except ControlError as exc:
            parser.error(str(exc))
    try:
        if args.command == 'configure':
            if not args.awake_guard:
                entry = Path(__file__).resolve().parents[1] / 'amphetamine_control.py'
                return subprocess.call(('/usr/bin/caffeinate', '-i', sys.executable, '-B',
                                        str(entry), '--awake-guard', *argv))
            value = configure(NativeApp(), args.enabled == 'on', args.minutes, args.revision)
        else:
            value = read_status(NativeApp())
        print(json.dumps(value))
        return 0
    except (ControlError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 3 if isinstance(exc, Conflict) else 1
