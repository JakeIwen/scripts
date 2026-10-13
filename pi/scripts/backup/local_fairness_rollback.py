#!/usr/bin/env python3
"""Check rollback quiescence or archive fairness state; never copy code or stop jobs."""
import argparse
from contextlib import contextmanager
import datetime as dt
import grp
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
from typing import TypedDict

import backup_priority as priority
import local_fairness as fairness
from local_fairness_window import SCRIPTS, cloud_stopped
from time_machine_staging import JOB_LOCK, require_lock

PROCESS_NAMES = frozenset((*SCRIPTS.values(), 'backup_window.sh', 'local_fairness_window.py',
    'local_fairness_window', 'borg', 'borg.bin', 'time_machine_icloud.py',
    'time_machine_icloud.sh', 'icloud_backup.py', 'icloud_backup.sh'))


class ArchiveResult(TypedDict):
    archive: str | None
    checked: bool


@contextmanager
def backup_lock(path, owner, group):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) != (owner, group, 0o660)):
            raise ValueError('Unsafe shared backup lock; archival refused.')
        require_lock(fd, path)
        yield
    finally:
        os.close(fd)


def require_quiescent(proc_root):
    if not proc_root.is_dir():
        raise RuntimeError('Cannot inspect backup processes; archival refused.')
    for entry in proc_root.iterdir():
        if not entry.name.isascii() or not entry.name.isdecimal():
            continue
        try:
            with (entry / 'cmdline').open('rb') as source:
                raw = source.read(65537)
        except FileNotFoundError:
            if entry.exists():
                raise RuntimeError('Cannot inspect a live process; archival refused.') from None
            continue
        if len(raw) > 65536:
            raise RuntimeError('Cannot inspect a complete process command; archival refused.')
        names = {arg.rsplit(b'/', 1)[-1].decode(errors='replace') for arg in raw.split(b'\0')}
        if names & PROCESS_NAMES:
            raise RuntimeError('A backup or lifecycle process is still running; archival refused.')


def sync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def archive_record(directory):
    if fairness.read_record(directory) is None:
        return None
    stamp = dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    archive = Path(tempfile.mkdtemp(prefix='local-fairness-rollback-' + stamp + '-', dir=directory))
    destination = archive / 'local-fairness.json'
    try:
        (directory / 'local-fairness.json').rename(destination)
    except OSError:
        archive.rmdir()  # Only the still-empty directory created by this call.
        raise
    sync_directory(archive)
    sync_directory(directory)
    return str(destination)


def prepare_rollback(*, check_only=False, directory=None, lock_path=JOB_LOCK,
                     proc_root=Path('/proc'), lock_owner=0, lock_group=None,
                     command=subprocess.run) -> ArchiveResult:
    directory = directory or priority.DIRECTORY
    lock_group = grp.getgrnam('pi').gr_gid if lock_group is None else lock_group
    with fairness.supervisor_slot(directory), backup_lock(lock_path, lock_owner, lock_group), priority.locked(directory):
        for job in priority.JOBS.values():
            if not cloud_stopped(job.service, command):
                raise RuntimeError('Cloud teardown is not complete; archival refused.')
        require_quiescent(proc_root)
        return {'checked': True, 'archive': None if check_only else archive_record(directory)}


def main(args=None):
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument('--check', action='store_true')
    action.add_argument('--archive', action='store_true')
    options = parser.parse_args(args)
    if os.geteuid() != 0:
        raise ValueError('Run the guarded state check or archival as root.')
    os.umask(0o077)
    print(json.dumps(prepare_rollback(check_only=options.check), allow_nan=False))
    if options.check:
        print('Next command: exec 9</run/lock/vanpi_backup.lock', file=sys.stderr)
        print('Then: /usr/bin/flock -n 9', file=sys.stderr)
        print('Restore the reviewed old code using the documented install/mv procedure.', file=sys.stderr)
    else:
        print('Next commands:', file=sys.stderr)
        print('L=/home/pi/scripts/backup', file=sys.stderr)
        print('sudo /usr/bin/python3 -B "$L/backup_priority_control.py" --status', file=sys.stderr)
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print('Fairness state operation did not complete: ' + type(exc).__name__, file=sys.stderr)
        sys.exit(1)
