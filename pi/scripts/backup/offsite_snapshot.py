#!/usr/bin/env python3
"""Frozen per-run copy of the offsite code closure; see OFFSITE.md "Code snapshot".

offsite_backup.sh runs `create` while it holds job-offsite exclusively and the
legacy lock shared, so no targeted installer publishes during the copy. The run
then executes only from the copy, which lets installers ship new code during a
days-long seed. Deletion walks by descriptor, never follows a symlink and never
crosses a mount. `create --remote` marks a command that reaches a remote target;
only such a run of a kind smb transport also freezes rclone.
"""
import hashlib
import os
from pathlib import Path
import re
import secrets
import stat
import sys
import time

import backup_admission
import backup_locks as locks
from offsite_config import (CODE_DIRECTORY, FROZEN_IMPLEMENTATION, INSTALLED_RCLONE, JOB_KIND, MANIFEST,
                            RCLONE_VERSION, REMOTE_CONFIG, RESTIC_INSTALLER, RUN_ID_PATTERN, SCHEMA,
                            SNAPSHOT_RCLONE, SNAPSHOT_RESTIC, TransportKind, state_root)
from offsite_remote import transport_config
from offsite_repository import durable_json, private_directory, sync_directory
from offsite_smb import check_rclone_version

CHUNK = 1024 * 1024
PIN_FIELDS = {'version': r'[0-9]+\.[0-9]+\.[0-9]+', 'binary_sha': r'[0-9a-f]{64}', 'target': r'/[A-Za-z0-9._/-]+'}


def source_root():
    """The live scripts tree this module was started from (/home/pi/scripts on the Pi)."""
    return Path(__file__).resolve().parent.parent


def restic_pin(root):
    """install_restic.sh's version, binary SHA-256 and target; each assigned exactly once."""
    text = (root / RESTIC_INSTALLER).read_text()
    pin = {}
    for name, pattern in PIN_FIELDS.items():
        values = re.findall(rf'^{name}=({pattern})$', text, re.MULTILINE)
        if len(values) != 1:
            raise RuntimeError(f'Cannot read the pinned restic {name} from {RESTIC_INSTALLER}')
        pin[name] = values[0]
    return pin


def open_regular(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        raise RuntimeError(f'Snapshot source is not a regular file: {path}')
    return fd


def file_hash(path):
    digest = hashlib.sha256()
    fd = open_regular(path)
    try:
        while chunk := os.read(fd, CHUNK):
            digest.update(chunk)
    finally:
        os.close(fd)
    return digest.hexdigest()


def copy_file(source, destination):
    """Copy one regular file; returns the SHA-256 of the bytes written."""
    digest = hashlib.sha256()
    fd = open_regular(source)
    try:
        mode = 0o700 if os.fstat(fd).st_mode & 0o111 else 0o600
        out = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, mode)
        try:
            os.fchmod(out, mode)
            while chunk := os.read(fd, CHUNK):
                digest.update(chunk)
                view = memoryview(chunk)
                while view:
                    view = view[os.write(out, view):]
            os.fsync(out)
        finally:
            os.close(out)
    finally:
        os.close(fd)
    return digest.hexdigest()


def private_subdirectory(path):
    path.mkdir(mode=0o700, exist_ok=True)
    path.chmod(0o700)


def mount_points():
    """Canonical mount points from mountinfo; bind mounts share st_dev, so check both."""
    try:
        lines = Path('/proc/self/mountinfo').read_text().splitlines()
    except FileNotFoundError:
        if sys.platform.startswith('linux'):
            raise
        return set()  # Non-Linux test hosts: the st_dev check still applies.
    return {unescape_mount(line.split()[4]) for line in lines if len(line.split()) > 4}


def unescape_mount(text):
    # mountinfo writes space, tab, newline and backslash as three-digit octal escapes.
    return re.sub(r'\\([0-7]{3})', lambda match: chr(int(match.group(1), 8)), text)


def remove_entry(parent_fd, name, device, path, mounts):
    """Delete one entry beneath an open directory without following symlinks or mounts."""
    info = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    if not stat.S_ISDIR(info.st_mode):
        os.unlink(name, dir_fd=parent_fd)  # Removes a symlink itself, never its target.
        return
    if info.st_dev != device or str(path) in mounts:
        raise RuntimeError(f'Refusing to delete a mount point or another filesystem: {path}')
    fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent_fd)
    try:
        actual = os.fstat(fd)
        if (actual.st_dev, actual.st_ino) != (info.st_dev, info.st_ino):
            raise RuntimeError(f'Snapshot directory changed during deletion: {path}')
        for child in os.listdir(fd):
            remove_entry(fd, child, device, path / child, mounts)
    finally:
        os.close(fd)
    os.rmdir(name, dir_fd=parent_fd)


def code_directory(state):
    if state.resolve() != state:
        raise RuntimeError(f'Offsite state root must be a canonical path: {state}')
    private_directory(state, os.geteuid())
    code = state / CODE_DIRECTORY
    private_directory(code, os.geteuid())
    if code.lstat().st_dev != state.lstat().st_dev or str(code) in mount_points():
        raise RuntimeError(f'Snapshot directory must be on the state filesystem: {code}')
    return code


def validate_run_root(state, root):
    """A run root is a real directory directly under <state>/code on the state filesystem."""
    root = Path(root)
    code = state / CODE_DIRECTORY
    if not root.is_absolute() or root.parent != code or not re.fullmatch(RUN_ID_PATTERN, root.name):
        raise RuntimeError(f'Not an offsite code snapshot: {root}')
    info = root.lstat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_dev != state.lstat().st_dev
            or root.resolve() != root or str(root) in mount_points()):
        raise RuntimeError(f'Unsafe offsite code snapshot: {root}')
    return root


def remove_children(directory, names=None):
    mounts = mount_points()
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        device = os.fstat(fd).st_dev
        for name in os.listdir(fd) if names is None else names:
            remove_entry(fd, name, device, directory / name, mounts)
        os.fsync(fd)
    finally:
        os.close(fd)


def remove(state, root):
    """Delete one run's snapshot (the run's last cleanup step)."""
    root = validate_run_root(state, root)
    remove_children(root.parent, [root.name])


def needs_rclone(state, remote):
    """True only for a remote command (the launcher's --remote) while remote.json is kind smb.

    Local commands, seeds above all, never run rclone even when the record is smb,
    nor do REST runs; a missing or unusable record needs none either, since an smb
    run then fails at configure (its frozen rclone is absent).
    """
    if not remote:
        return False
    try:
        return transport_config(state / REMOTE_CONFIG).get('kind') == TransportKind.SMB
    except (OSError, ValueError):
        return False


def copy_rclone(run_root):
    """Freeze the installed rclone for a shared-folder run, as restic is frozen for every run."""
    source, target = Path(INSTALLED_RCLONE), run_root / SNAPSHOT_RCLONE
    private_subdirectory(target.parent)
    digest = copy_file(source, target)
    if file_hash(target) != digest:
        raise RuntimeError('The code snapshot\'s rclone differs from the bytes copied')
    check_rclone_version(str(target))
    return {'path': str(source), 'sha256': digest, 'version': RCLONE_VERSION}


def copy_closure(source, run_root, rclone=False):
    files = {}
    for relative in FROZEN_IMPLEMENTATION:
        parent = run_root
        for part in Path(relative).parts[:-1]:
            parent = parent / part
            private_subdirectory(parent)
        files[relative] = copy_file(source / relative, run_root / relative)
    pin = restic_pin(source)
    private_subdirectory((run_root / SNAPSHOT_RESTIC).parent)
    restic = copy_file(Path(pin['target']), run_root / SNAPSHOT_RESTIC)
    if restic != pin['binary_sha']:
        raise RuntimeError(f"Installed restic does not match {RESTIC_INSTALLER}'s pinned SHA-256; rerun it")
    # A sync or deployment that slipped past the fence shows up as a changed source.
    for relative, digest in files.items():
        if file_hash(source / relative) != digest:
            raise RuntimeError(f'{relative} changed while the code snapshot was taken; retry after the sync')
    if file_hash(Path(pin['target'])) != restic:
        raise RuntimeError('Installed restic changed while the code snapshot was taken')
    frozen = copy_rclone(run_root) if rclone else None
    if frozen and file_hash(Path(frozen['path'])) != frozen['sha256']:
        raise RuntimeError('Installed rclone changed while the code snapshot was taken')
    return files, {'path': pin['target'], 'sha256': restic, 'version': pin['version']}, frozen


def create(job_fd, state=None, source=None, remote=False):
    """Garbage-collect stale snapshots, copy and verify the closure; returns the run root.

    remote: the run reaches a remote target, so a kind smb transport freezes rclone too.
    """
    backup_admission.validated_descriptor(job_fd, locks.lock_path(locks.LockClass.JOB, JOB_KIND), exclusive=True)
    state = state_root() if state is None else state
    source = source_root() if source is None else source
    code = code_directory(state)
    # job-offsite is a singleton held exclusively here, so no other run is live:
    # every existing snapshot is stale.
    remove_children(code)
    run_id = time.strftime('%Y%m%dT%H%M%SZ', time.gmtime()) + '-' + secrets.token_hex(4)
    run_root = code / run_id
    run_root.mkdir(mode=0o700)
    try:
        run_root.chmod(0o700)
        files, restic, rclone = copy_closure(source, run_root, rclone=needs_rclone(state, remote))
        # The backup cohort records no deployed revision on the Pi yet.
        durable_json(run_root / MANIFEST, {'schema': SCHEMA, 'run_id': run_id, 'created': time.time(),
                                           'source_root': str(source), 'files': files, 'restic': restic,
                                           'rclone': rclone, 'deployed_revision': None})
        for directory in sorted({(run_root / name).parent for name in (*files, SNAPSHOT_RESTIC)}, reverse=True):
            sync_directory(directory)
        sync_directory(code)
    except BaseException:
        try:
            remove(state, run_root)
        except (OSError, RuntimeError) as exc:
            print(f'offsite snapshot: left a partial snapshot for the next run to collect: {exc}', file=sys.stderr)
        raise
    return run_root


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    if args not in (['create'], ['create', '--remote']):
        print('usage: offsite_snapshot.py create [--remote]', file=sys.stderr)
        return 2
    try:
        environment = dict(os.environ)
        job_fd = backup_admission.descriptor_number(
            backup_admission.required_value(environment, 'VANPI_BACKUP_JOB_LOCK_FD'))
        print(create(job_fd, remote=args[1:] == ['--remote']), flush=True)
        return 0
    except (OSError, ValueError, RuntimeError) as exc:
        print(f'offsite snapshot: {exc}', file=sys.stderr, flush=True)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
