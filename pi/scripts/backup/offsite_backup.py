#!/usr/bin/env python3
"""Seed and verify encrypted local offsite drives. Run via offsite_backup.sh."""
import argparse
import json
import os
import re
import sys

from offsite_config import Config
from offsite_dashboard import dashboard, rates, status
from offsite_locks import admit
from offsite_prepare import prepare
from offsite_repository import Commands, Interrupted, LocalRepository
from offsite_run import Backup
import offsite_snapshot


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
    for command in (backup, check):
        command.add_argument('--remote', action='store_true', help='use the registered NAS REST target over Tailscale')
    receipts = sub.add_parser('receipts', help='list validated encrypted remote completion receipts')
    receipts.add_argument('--label', required=True)
    restore = sub.add_parser('restore-test', help='restore one remote path using a completion receipt and verify bytes')
    restore.add_argument('--label', required=True)
    restore.add_argument('--receipt', required=True)
    restore.add_argument('--path', required=True)
    restore.add_argument('--target', required=True, help='absolute path to a separate empty directory')
    sub.add_parser('status', help='print the current/last run as JSON; does not take the backup lock')
    sub.add_parser('dashboard', help='print status, per-source history and verification evidence; read-only')
    rate = sub.add_parser('rates', help='print per-minute logical processing rates; read-only')
    rate.add_argument('source', choices=tuple(cfg.sources()))
    for name in ('pause', 'resume', 'resume-run'):
        control = sub.add_parser(name, help='manual remote backup control (resume-run is the admitted worker)')
        control.add_argument('--remote', action='store_true', required=True)
        control.add_argument('--label', required=True)
        control.add_argument('--revision', required=True)
    return root


def mutate(cfg, args, commands):
    if getattr(args, 'remote', False) or args.command in ('receipts', 'restore-test'):
        from offsite_remote import RemoteRepository
        repo = RemoteRepository(cfg, commands, args.label)
    else:
        repo = LocalRepository(cfg, commands, args.label)
    with commands.signals():
        commands.allowed()
        if args.command == 'prepare-disk':
            prepare(repo, args.device, args.erase_confirm, args.priority)
            return 0
        commands.locks.hold_policy_locks()
        if args.command in ('backup', 'resume-run'):
            from offsite_control import admit_backup, resume_backup
            if args.command == 'resume-run':
                selected = resume_backup(repo, args.revision)
            else:
                selected = list(dict.fromkeys(args.only or cfg.sources()))
                if getattr(repo, 'remote', False):
                    admit_backup(repo, selected)
            return Backup(repo, selected).run()
        elif args.command == 'check':
            subset = args.read_data_subset
            if subset and not re.fullmatch(r'(?:[1-9][0-9]*/[1-9][0-9]*|(?:[0-9]+(?:\.[0-9]+)?)(?:%|[kKMGT]))', subset):
                raise ValueError('Invalid --read-data-subset; use a percentage, n/t, or size')
            print(repo.check(subset), end='', flush=True)
        elif args.command == 'receipts':
            from offsite_receipts import list_receipts
            print(json.dumps(list_receipts(repo), sort_keys=True))
        elif args.command == 'restore-test':
            from offsite_receipts import restore_test
            print(restore_test(repo, args.receipt, args.path, args.target), end='', flush=True)
    return 0


def discard_snapshot(cfg):
    # The run's last step; nothing imports from the snapshot after it. The next
    # run's garbage collection retries a snapshot left behind here.
    try:
        offsite_snapshot.remove(cfg.state, cfg.code_root)
    except (OSError, RuntimeError) as exc:
        print(f'offsite: could not remove code snapshot {cfg.code_root}: {exc}', file=sys.stderr, flush=True)


def main(argv=None, *, config=None):
    try:
        cfg = config or Config.from_environment()
        args = parser(cfg).parse_args(argv)
        if args.command in ('status', 'dashboard', 'rates'):
            if args.command != 'status' and os.geteuid() != cfg.owner:
                raise RuntimeError('Run offsite_backup.sh as root')
            value = (rates(cfg, args.source) if args.command == 'rates' else
                     dashboard(cfg) if args.command == 'dashboard' else status(cfg))
            print(json.dumps(value, sort_keys=True))
            return 0
        if args.command in ('pause', 'resume'):
            from offsite_control import request_control
            print(json.dumps(request_control(cfg, args.command, args.label, args.revision), sort_keys=True))
            return 0
        locks = admit(cfg, prepare=args.command == 'prepare-disk')
        try:
            return mutate(cfg, args, Commands(cfg, locks))
        finally:
            locks.close_resources()
            if cfg.code_root is not None:
                discard_snapshot(cfg)
    except (OSError, ValueError, RuntimeError, KeyError) as exc:
        print(f'offsite: {exc}', file=sys.stderr, flush=True)
        return 143 if isinstance(exc, Interrupted) else 1


if __name__ == '__main__':
    raise SystemExit(main())
