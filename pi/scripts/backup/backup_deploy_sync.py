#!/usr/bin/env python3
"""Non-backup broad-sync scope and the existing bootstrap/legacy-fence boundary.

Staged with the bootstrap inspection closure, never imported from mutable live
backup code. Scope comes only from both profiles of backup_deploy_plan.sh.
Mac scope generation is Python 3.9+; host inspection/fencing requires Linux.
"""
import argparse
import os
from pathlib import Path, PurePosixPath
import pwd
import shutil
import stat
import subprocess
import sys

import backup_bootstrap_files as files
import backup_bootstrap_host as bootstrap
import backup_bootstrap_plan as plan
import backup_locks as locks

ROOTS = {'scripts': '/home/pi/scripts', 'services': '/etc/systemd/system',
         'tmpfiles.d': '/etc/tmpfiles.d'}
LIVE_ENV = {'scripts': 'UPDATE_SERVICES_LIVE_SCRIPTS', 'services': 'UPDATE_SERVICES_LIVE_SERVICES',
            'tmpfiles.d': 'UPDATE_SERVICES_LIVE_TMPFILES'}
BOUNDARY = '.backup-sync'


def destinations():
    result = subprocess.run(['/bin/bash', '-c', 'set -euo pipefail; . "$1"; local_backup_plan pi; local_backup_plan tm',
                             'scope', str(Path(__file__).with_name('backup_deploy_plan.sh'))],
                            capture_output=True, text=True, check=True, timeout=10)
    records = {}
    for line in result.stdout.splitlines():
        fields = line.split('|')
        if len(fields) != 6:
            raise ValueError('Malformed canonical backup deployment record')
        destination = PurePosixPath(fields[1])
        if not destination.is_absolute() or '..' in destination.parts or str(destination) != fields[1]:
            raise ValueError('Unsafe canonical backup destination')
        if destination in records and records[destination] != fields:
            raise ValueError('Conflicting canonical backup destination')
        records[destination] = fields
    if not records:
        raise ValueError('Empty canonical backup plan')
    return set(records)


def scoped(kind, protected):
    root = PurePosixPath(ROOTS[kind])
    return {path.relative_to(root) for path in protected if root in path.parents}


def inspect_ready():
    host = bootstrap.Bootstrap(os.environ.get('VANPI_BOOTSTRAP_ROOT', '/'))
    files.directory(host.state.parent, 0o755, create=False)
    try:
        host.state.lstat()
    except FileNotFoundError:
        # Absence is allowed only before bootstrap (or after safe abandonment).
        # lstat distinguishes genuine absence from inaccessible/private state.
        for path in (host.stable, host.cron):
            try:
                path.lstat()
            except FileNotFoundError:
                continue
            raise ValueError('Incomplete bootstrap without its state directory')
        return host
    host.verify()
    return host


def entries(root):
    def fail(error):
        raise error
    for directory, dirs, names in os.walk(root, followlinks=False, onerror=fail):
        for name in dirs + names:
            path = Path(directory) / name
            yield path, PurePosixPath(path.relative_to(root).as_posix()), path.lstat()


def validate_stage(stage, removals=()):
    protected = destinations()
    for removal in removals:
        kind, relative = removal.split('/', 1)
        path = PurePosixPath(ROOTS[kind]) / relative
        if '..' in path.parts or any(path == owned or path in owned.parents or owned in path.parents for owned in protected):
            raise ValueError('Backup-owned cleanup overlap: ' + removal)
    if stage.is_symlink() or not stage.is_dir():
        raise ValueError('Unsafe broad-sync stage')
    if set(path.name for path in stage.iterdir()) - (set(ROOTS) | {BOUNDARY}):
        raise ValueError('Unsupported broad-sync stage entry')
    for kind, destination in ROOTS.items():
        root = stage / kind
        if root.is_symlink() or not root.is_dir():
            raise ValueError('Incomplete broad-sync stage: ' + kind)
        live = Path(os.environ.get(LIVE_ENV[kind], destination))
        try:
            live_info = live.lstat()
        except FileNotFoundError:
            live_info = None
        if live_info is not None and not stat.S_ISDIR(live_info.st_mode):
            raise ValueError('Unsafe live root: ' + str(live))
        owned = scoped(kind, protected)
        for path, relative, info in entries(root):
            if any(relative == item or item in relative.parents or
                   (relative in item.parents and not stat.S_ISDIR(info.st_mode)) for item in owned):
                raise ValueError('Backup-owned staged overlap: ' + str(relative))
            if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode)):
                raise ValueError('Unsupported staged file type: ' + str(path))
            # A live symlinked parent could redirect an otherwise unrelated file
            # into protected code. Validate every parent before the first write.
            parents = [live] + [live / str(parent) for parent in relative.parents if str(parent) != '.']
            if stat.S_ISDIR(info.st_mode):
                parents.append(live / str(relative))
            for parent in parents:
                try:
                    metadata = parent.lstat()
                except FileNotFoundError:
                    continue
                if not stat.S_ISDIR(metadata.st_mode):
                    raise ValueError('Unsafe live directory: ' + str(parent))


def copy_scripts(source, target):
    # cp -a of a parent also overwrites its metadata, even when every protected
    # child was excluded. Existing directories are never replaced or chmodded.
    target.mkdir(exist_ok=True)
    if target.is_symlink():
        raise ValueError('Symlinked scripts directory')
    for child in source.iterdir():
        destination = target / child.name
        if child.is_dir() and not child.is_symlink():
            copy_scripts(child, destination)
        else:
            # Do not follow a destination symlink or modify a hardlinked inode.
            if os.path.lexists(destination):
                destination.unlink()
            shutil.copy2(child, destination, follow_symlinks=False)


def run_receiver(host, stage, command):
    with host.fence(shared=True) as fd:
        inspect_ready()
        validate_stage(stage)
        # The root coordinator retains its hold; sudo inside the old receiver
        # need not preserve descriptors. The non-root receiver inherits fd 9.
        if fd != 9:
            os.dup2(fd, 9, inheritable=True)
        try:
            uid = int(os.environ.get('SUDO_UID', os.getuid()))
            gid = int(os.environ.get('SUDO_GID', os.getgid()))
            identity = {}
            if os.geteuid() == 0:
                identity = dict(user=uid, group=gid, extra_groups=os.getgrouplist(pwd.getpwuid(uid).pw_name, gid))
            return subprocess.run(command, pass_fds=(9,), check=False, **identity).returncode
        finally:
            if fd != 9:
                os.close(9)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    sub.add_parser('files')
    sub.add_parser('root').add_argument('kind', choices=ROOTS)
    sub.add_parser('excludes').add_argument('kind', choices=ROOTS)
    sub.add_parser('check')
    sub.add_parser('hold')
    sub.add_parser('validate').add_argument('stage', type=Path)
    check = sub.add_parser('check-stage')
    check.add_argument('stage', type=Path)
    check.add_argument('--remove', action='append', default=[])
    copy = sub.add_parser('copy-scripts')
    copy.add_argument('source', type=Path)
    copy.add_argument('target', type=Path)
    run = sub.add_parser('run')
    run.add_argument('stage', type=Path)
    run.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    try:
        if args.action == 'files':
            print('\n'.join(plan.SYNC_FILES))
        elif args.action == 'root':
            print(ROOTS[args.kind])
        elif args.action == 'excludes':
            for path in sorted(scoped(args.kind, destinations())):
                print('/' + str(path))
        elif args.action == 'validate':
            validate_stage(args.stage)
        elif args.action == 'copy-scripts':
            locks.require_legacy_hold()
            copy_scripts(args.source, args.target)
        elif args.action == 'check-stage':
            inspect_ready()
            validate_stage(args.stage, args.remove)
        else:
            host = inspect_ready()
            if args.action == 'hold':
                with host.fence(shared=True):
                    inspect_ready()
                    print(plan.SYNC_READY, flush=True)
                    line = sys.stdin.readline()
                    if line not in ('', plan.SYNC_RELEASE + '\n'):
                        raise ValueError('Invalid sync hold release')
            elif args.action == 'run':
                return run_receiver(host, args.stage, args.command)
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, subprocess.SubprocessError) as error:
        parser.exit(1, 'Backup sync refused: ' + str(error) + '\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
