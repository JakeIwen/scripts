#!/usr/bin/env python3
"""Stage a minimal predecessor bootstrap, or explicitly apply/verify/release it over LAN.

Staging is read-only to the host. Apply requires the separate operator maintenance
boundary documented in pi/docs/backup/CONCURRENCY.md; it never releases holds.
"""
import argparse
from pathlib import Path
import re
import subprocess

from pi.scripts.backup.backup_bootstrap_plan import BOOTSTRAP_DIR, build


def remote(args):
    if args.target.startswith('-') or re.search(r'\s', args.target):
        raise ValueError('Invalid SSH target')
    ssh = ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=8', args.target]
    command = ['/usr/bin/sudo', '-n', '/usr/bin/python3', '-B', BOOTSTRAP_DIR + '/backup_bootstrap_host.py']
    if args.action == 'apply':
        if not args.maintenance_confirmed:
            raise ValueError('Apply requires --maintenance-confirmed; read the maintenance runbook first')
        stage = subprocess.check_output(ssh + ['/usr/bin/mktemp', '-d', '/tmp/vanpi-backup-bootstrap.XXXXXX'], text=True).strip()
        if not re.fullmatch(r'/tmp/vanpi-backup-bootstrap\.[A-Za-z0-9]{6}', stage):
            raise ValueError('Unexpected remote stage path')
        subprocess.run(['scp', '-rq', str(args.bundle) + '/.', args.target + ':' + stage + '/'], check=True)
        command[4] = stage + '/runner/backup_bootstrap_host.py'
        command += ['--maintenance-confirmed', 'apply', stage]
    elif args.action == 'release':
        if not args.maintenance_confirmed:
            raise ValueError('Release requires --maintenance-confirmed')
        command += ['--maintenance-confirmed', 'release']
    else:
        command += ['verify', '--held']
    return subprocess.run(ssh + command, check=True).returncode


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='action', required=True)
    stage = commands.add_parser('stage')
    stage.add_argument('revision', help='reviewed ACTUAL deployed predecessor, not a hardcoded historical cohort')
    stage.add_argument('output', type=Path)
    for action in ('apply', 'verify', 'release'):
        command = commands.add_parser(action)
        command.add_argument('--target', default='pi@vanpi.lan')
        if action in ('apply', 'release'):
            command.add_argument('--maintenance-confirmed', action='store_true')
        if action == 'apply':
            command.add_argument('bundle', type=Path)
    args = parser.parse_args(argv)
    try:
        if args.action == 'stage':
            plan = build(Path(__file__).resolve().parent.parent, args.revision, args.output)
            print('Staged reviewed predecessor ' + plan['revision'] + ' at ' + str(args.output.resolve()))
            return 0
        return remote(args)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        parser.exit(1, str(error) + '\n')


if __name__ == '__main__':
    raise SystemExit(main())
