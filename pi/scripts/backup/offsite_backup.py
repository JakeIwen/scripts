#!/usr/bin/env python3
"""Seed and verify encrypted local offsite drives. Run via offsite_backup.sh."""
import argparse
import json
import re
import sys

from offsite_config import Config, Result
from offsite_prepare import prepare
from offsite_repository import Commands, Interrupted, LocalRepository, admit, read_record
from offsite_run import Backup


def parser(cfg):
    root = argparse.ArgumentParser(description=__doc__)
    sub = root.add_subparsers(dest='command', required=True)
    disk = sub.add_parser('prepare-disk', help='erase a confirmed USB disk and initialize one repository')
    disk.add_argument('--device', required=True)
    disk.add_argument('--label', required=True)
    disk.add_argument('--priority', required=True, type=int)
    disk.add_argument('--erase-confirm', help='exact serial; omission prints the layout and refuses erasure')
    backup = sub.add_parser('backup', help='snapshot sources into a known local repository')
    backup.add_argument('--label', required=True)
    backup.add_argument('--only', action='append', choices=tuple(cfg.sources()),
                        help='repeat to select several sources; default is all four')
    check = sub.add_parser('check', help='check a known local repository')
    check.add_argument('--label', required=True)
    check.add_argument('--read-data-subset', help='restic subset such as 5%%, 1/10, or 10G')
    sub.add_parser('status', help='print the current/last run as JSON; does not take the backup lock')
    return root


def main(argv=None, *, config=None):
    try:
        cfg = config or Config.from_environment()
        args = parser(cfg).parse_args(argv)
        if args.command == 'status':
            path = cfg.state / 'status.json'
            value = read_record(path) if path.exists() or path.is_symlink() else {'result': Result.NOT_RUN}
            print(json.dumps(value, sort_keys=True))
            return 0
        admit(cfg)
        commands = Commands(cfg)
        repo = LocalRepository(cfg, commands, args.label)
        with commands.signals():
            commands.allowed()
            if args.command == 'prepare-disk':
                prepare(repo, args.device, args.erase_confirm, args.priority)
            elif args.command == 'backup':
                return Backup(repo, list(dict.fromkeys(args.only or cfg.sources()))).run()
            elif args.command == 'check':
                subset = args.read_data_subset
                if subset and not re.fullmatch(r'(?:[1-9][0-9]*/[1-9][0-9]*|(?:[0-9]+(?:\.[0-9]+)?)(?:%|[kKMGT]))', subset):
                    raise ValueError('Invalid --read-data-subset; use a percentage, n/t, or size')
                print(repo.check(subset), end='', flush=True)
        return 0
    except (OSError, ValueError, RuntimeError, KeyError) as exc:
        print(f'offsite: {exc}', file=sys.stderr, flush=True)
        return 143 if isinstance(exc, Interrupted) else 1


if __name__ == '__main__':
    raise SystemExit(main())
