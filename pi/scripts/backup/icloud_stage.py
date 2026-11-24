"""Rejecting and rebuilding an unreadable or corrupt unpublished Borg stage."""
from contextlib import contextmanager
import errno
import os
from pathlib import Path
import re
import stat
import sys
import time

import backup_priority
from icloud_status import BackupFailure, generation as valid_generation, number

# From the second consecutive rejection, copy again only after a newer Borg stamp.
IMMEDIATE_RESTAGES = 1
WAIT_MESSAGE = 'a discarded staged copy waits for the next local Borg backup before copying again'
DISCARDING = 'discarding'
DISCARDED = 'discarded'

# Borg 1.x do_check returns EXIT_WARNING for failed repository/archive checks;
# IntegrityError and an unhandled OSError exit with EXIT_ERROR instead.
BORG_CHECK = ('borg', 'check')
BORG_CHECK_FAILURES = frozenset({1, 2})
BORG_INTEGRITY = re.compile(
    r'^(?:Finished (?:full|partial) repository check, errors found\.$'
    r'|Archive consistency check complete, problems found\.$'
    r'|(?:borg\.helpers\.errors\.IntegrityError: )?Data integrity error: .+'
    r'|Segment entry checksum mismatch \[segment \d+, offset \d+\])', re.MULTILINE)
BORG_EIO_PATH = re.compile(r"^OSError: \[Errno 5\] Input/output error: (['\"])(/.+?)\1\s*$", re.MULTILINE)


class StageRejected(RuntimeError):
    """The unpublished copy is unusable; weekly() must discard it before re-copying."""

    def __init__(self, message, failure_code, detail=''):
        super().__init__(message)
        self.failure_code = failure_code
        self.detail = detail


class StageReadError(StageRejected):
    def __init__(self, repository):
        super().__init__('staged repository copy could not be read', BackupFailure.STAGE_UNREADABLE)
        self.repository = repository


def disk_io_error(exc):
    return (isinstance(exc, OSError) and exc.errno == errno.EIO) or getattr(exc, 'disk_io', False) is True


def tag_borg_check(failure, stderr):
    """Keep only the evidence needed for the rejection decision, not the raw diagnostics."""
    if failure.command == BORG_CHECK and failure.returncode in BORG_CHECK_FAILURES:
        failure.borg_integrity = BORG_INTEGRITY.search(stderr) is not None
        failure.borg_eio_paths = tuple(Path(match[2]) for match in BORG_EIO_PATH.finditer(stderr))


def rejection(exc, repository):
    """Only a completed check with positive evidence condemns the copy."""
    if (getattr(exc, 'command', None) != BORG_CHECK
            or getattr(exc, 'returncode', None) not in BORG_CHECK_FAILURES):
        return None
    detail = getattr(exc, 'detail', '')
    if any((path == repository or repository in path.parents) and '..' not in path.parts
           for path in getattr(exc, 'borg_eio_paths', ())):
        return StageRejected('staged repository copy could not be read', BackupFailure.STAGE_UNREADABLE, detail)
    if getattr(exc, 'borg_integrity', False):
        return StageRejected('staged repository copy failed its Borg check', BackupFailure.STAGE_CHECK_FAILED, detail)
    return None


def stage_read_error(repository, exc):
    if exc.errno == errno.EIO:
        raise StageReadError(repository) from exc
    raise exc


def staged_read(repository, read, *args):
    try:
        return read(*args)
    except OSError as exc:
        stage_read_error(repository, exc)


DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC


@contextmanager
def open_fd(name, flags=DIR_FLAGS, *, parent=None):
    fd = os.open(name, flags, dir_fd=parent)
    try:
        yield fd
    finally:
        os.close(fd)


def mount_id(fd):
    if sys.platform != 'linux':
        return None  # Non-Linux unit tests still compare st_dev.
    with open(f'/proc/self/fdinfo/{fd}', encoding='ascii') as handle:
        for line in handle:
            if line.startswith('mnt_id:'):
                return int(line.split()[1])
    raise RuntimeError('refusing to discard staged copy: cannot verify mount identity')


def descriptor_identity(fd):
    info = os.fstat(fd)
    return (info.st_dev, mount_id(fd)), info.st_ino


def require_boundary(boundary, fd):
    actual, _ = descriptor_identity(fd)
    if actual != boundary:
        raise RuntimeError('refusing to discard staged copy across a mount boundary')


@contextmanager
def verified_mount(mount, verify):
    # Pin first, then prove that the mount verified by pathname is still the one held open.
    with open_fd(mount) as fd:
        expected = descriptor_identity(fd)
        verify(expected[0][1])
        with open_fd(mount) as current:
            if descriptor_identity(current) != expected:
                raise RuntimeError('refusing to discard staged copy: backup mount changed during verification')
        yield fd, expected[0]


def walk_entry(parent, name, boundary, *, remove):
    # offsite_snapshot's removal helper pulls in the offsite transport and checks paths,
    # not the mount IDs of opened descriptors; the staged copy needs its own fd walk.
    info = os.stat(name, dir_fd=parent, follow_symlinks=False)
    if sys.platform == 'linux':
        # O_PATH checks even a file bind mount, without reading files or following links.
        with open_fd(name, os.O_PATH | os.O_NOFOLLOW | os.O_CLOEXEC, parent=parent) as fd:
            require_boundary(boundary, fd)
            opened = os.fstat(fd)
            if (info.st_dev, info.st_ino) != (opened.st_dev, opened.st_ino):
                raise RuntimeError('staged copy entry changed during deletion')
            if stat.S_ISDIR(opened.st_mode):
                with open_fd('.', parent=fd) as directory:
                    walk_directory(parent, name, directory, info, boundary, remove=remove)
                return
    elif stat.S_ISDIR(info.st_mode):
        with open_fd(name, parent=parent) as directory:
            walk_directory(parent, name, directory, info, boundary, remove=remove)
        return
    if remove:
        os.unlink(name, dir_fd=parent)


def walk_directory(parent, name, directory, info, boundary, *, remove):
    require_boundary(boundary, directory)
    if os.fstat(directory).st_ino != info.st_ino:
        raise RuntimeError('staged copy directory changed during deletion')
    for child in os.listdir(directory):
        walk_entry(directory, child, boundary, remove=remove)
    if remove:
        remove_directory(parent, name, directory)


def remove_directory(parent, name, directory):
    with open_fd(name, parent=parent) as current:
        if descriptor_identity(current) != descriptor_identity(directory):
            raise RuntimeError('staged copy directory changed during deletion')
    os.rmdir(name, dir_fd=parent)


def present(parent, name):
    try:
        os.stat(name, dir_fd=parent, follow_symlinks=False)
    except FileNotFoundError:
        return False
    return True


def discard_generation(root, name, boundary):
    if not present(root, name):
        return  # Restart after deletion but before clearing pending.
    with open_fd(name, parent=root) as generation:
        require_boundary(boundary, generation)
        if present(generation, '_COMPLETE.json'):
            raise RuntimeError('refusing to discard a staged copy that was published for upload')
        if present(generation, 'payload'):
            with open_fd('payload', parent=generation) as payload:
                require_boundary(boundary, payload)
                if present(payload, 'manifest.json'):
                    raise RuntimeError('refusing to discard a staged copy that was published for upload')
        # Refuse already-mounted children before removing anything; recheck each
        # opened entry during deletion in case a mount appeared after this pass.
        for remove in (False, True):
            for child in os.listdir(generation):
                walk_entry(generation, child, boundary, remove=remove)
        remove_directory(root, name, generation)


def discard(mount, generation, verify):
    """Delete only the unpublished generation under the pinned, verified mount."""
    if generation.parent.parent != mount or not valid_generation(generation.name):
        raise RuntimeError('refusing to discard a staged copy outside the backup mount')
    with verified_mount(mount, verify) as (mount_fd, boundary):
        with open_fd(generation.parent.name, parent=mount_fd) as root:
            require_boundary(boundary, root)
            info = os.fstat(root)
            if info.st_uid != os.geteuid() or info.st_mode & 0o077:
                raise RuntimeError('refusing to discard staged copy: staging root is not private')
            discard_generation(root, generation.name, boundary)
            os.fsync(root)


def rejected_stage(state):
    record = state.get('stage_rejected')
    return record if isinstance(record, dict) else {}


def record(state, generation, failure_code, now=None):
    """Journal the rejection before any deletion; use the stamp at the start of rsync."""
    count = rejected_stage(state).get('count')
    state['stage_rejected'] = {
        'state': DISCARDING, 'generation': generation, 'failure_code': failure_code,
        'rejected_at': time.time() if now is None else now,
        'borg_stamp_mtime': state.get('pending_borg_stamp_mtime'),
        'count': (count if type(count) is int and count > 0 else 0) + 1}


def cleanup_pending(state):
    return rejected_stage(state).get('state') == DISCARDING


def finish_discard(state, mount, generation, verify, save):
    record = rejected_stage(state)
    if state.get('pending') != generation.name or record.get('generation') != generation.name:
        raise RuntimeError('pending generation changed before its staged copy could be discarded')
    save()
    discard(mount, generation, verify)
    record['state'] = DISCARDED
    state.update(pending=None, progress={})
    state.pop('pending_borg_stamp_mtime', None)
    save()


def carry_priority(state, replacement, save):
    rejected = rejected_stage(state).get('generation')
    if valid_generation(rejected) and rejected != replacement:
        # Claim the replacement durably first so either side of a restart can
        # retry this compare-and-swap without pinning a name the worker lost.
        save()
        backup_priority.replace_rejected_pi_generation(rejected, replacement)


def must_wait(state, read_borg_stamp):
    """Wait for a readable newer stamp after repeated rejection, or when the baseline was unknown."""
    record = rejected_stage(state)
    count, stamp = record.get('count'), number(record.get('borg_stamp_mtime'))
    if type(count) is not int or count <= 0:
        return False
    if stamp is None:
        stamp = number(record.get('rejected_at'))
    elif count <= IMMEDIATE_RESTAGES:
        return False
    current = number(read_borg_stamp())
    return stamp is None or current is None or current <= stamp


def clear(state):
    state.pop('stage_rejected', None)
    state.pop('pending_borg_stamp_mtime', None)
