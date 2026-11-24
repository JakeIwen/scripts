"""Rejecting and rebuilding an unreadable or corrupt unpublished Borg stage."""
from contextlib import contextmanager
import errno
import hashlib
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
CHECK_WAIT_MESSAGE = 'a staged copy check waits for the next local Borg backup after a disk I/O error'
CHECK_RETRY = 'stage_check_retry'
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


def borg_check_failure(exc):
    return (getattr(exc, 'command', None) == BORG_CHECK
            and getattr(exc, 'returncode', None) in BORG_CHECK_FAILURES)


def tag_borg_check(failure, stderr):
    """Keep only the evidence needed for the rejection decision, not the raw diagnostics."""
    if borg_check_failure(failure):
        failure.borg_integrity = BORG_INTEGRITY.search(stderr) is not None


def rejection(exc):
    """Only Borg's own integrity diagnostics are direct proof of a bad copy."""
    if borg_check_failure(exc) and getattr(exc, 'borg_integrity', False):
        return StageRejected('staged repository copy failed its Borg check', BackupFailure.STAGE_CHECK_FAILED,
                             getattr(exc, 'detail', ''))
    return None


def confirm_read_error(exc, repository, check):
    # Borg can report a bare EIO from a segment fd or a named EIO from a write
    # (e.g. save_config's temp file). Neither proves a staged read failed.
    if not borg_check_failure(exc) or not disk_io_error(exc):
        return False
    file_manifest(repository, check, hash_files=False)
    return True


def stage_read_error(repository, exc):
    if exc.errno == errno.EIO:
        raise StageReadError(repository) from exc
    raise exc


def staged_read(repository, read, *args):
    try:
        return read(*args)
    except OSError as exc:
        stage_read_error(repository, exc)


def staged_files(root, check):
    if check is not None:
        check()
    for directory, dirs, files in os.walk(root, onerror=lambda exc: stage_read_error(root, exc)):
        for name in dirs + files:
            if check is not None:
                check()
            path = Path(directory) / name
            mode = staged_read(root, path.lstat).st_mode
            if stat.S_ISLNK(mode):
                raise RuntimeError('repository copy contains a symlink')
        for name in sorted(files):
            path = Path(directory) / name
            if not stat.S_ISREG(staged_read(root, path.stat).st_mode):
                raise RuntimeError('repository copy contains a non-file')
            yield path


def file_manifest(root, check=None, *, hash_files=True):
    """Read the staged repository; the EIO confirmation needs no hashes or manifest."""
    result = {}
    for path in staged_files(root, check):
        digest = hashlib.sha256() if hash_files else None
        with staged_read(root, path.open, 'rb') as stream:
            for data in iter(lambda: staged_read(root, stream.read, 4 * 1024 * 1024), b''):
                if check is not None:
                    check()
                if digest is not None:
                    digest.update(data)
        size = staged_read(root, path.stat).st_size
        if digest is not None:
            result[str(path.relative_to(root))] = {'bytes': size, 'sha256': digest.hexdigest()}
    return result


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


def record_clean_sweep(state, save, now=None):
    """The stage was readable; keep it, but do not repeat an expensive check at the same stamp."""
    state[CHECK_RETRY] = {
        'generation': state['pending'],
        'borg_stamp_mtime': state.get('pending_borg_stamp_mtime'),
        'checked_at': time.time() if now is None else now,
    }
    save(state)


def check_retry(state):
    record = state.get(CHECK_RETRY)
    if isinstance(record, dict) and state.get('pending') and record.get('generation') == state['pending']:
        return record
    return None


def clear_stale_check_retry(state):
    if check_retry(state) is None:
        clear_check_retry(state)


def clear_check_retry(state):
    state.pop(CHECK_RETRY, None)


def check_must_wait(state, read_borg_stamp):
    record = check_retry(state)
    if record is None:
        return False
    stamp = number(record.get('borg_stamp_mtime'))
    if stamp is None:
        stamp = number(record.get('checked_at'))
    return stamp_not_advanced(stamp, read_borg_stamp)


def rejected_stage(state):
    record = state.get('stage_rejected')
    return record if isinstance(record, dict) else {}


def record(state, generation, failure_code, now=None):
    """Journal the rejection before any deletion; use the stamp at the start of rsync."""
    clear_check_retry(state)
    count = rejected_stage(state).get('count')
    state['stage_rejected'] = {
        'state': DISCARDING, 'generation': generation, 'failure_code': failure_code,
        'rejected_at': time.time() if now is None else now,
        'borg_stamp_mtime': state.get('pending_borg_stamp_mtime'),
        'count': (count if type(count) is int and count > 0 else 0) + 1}


def cleanup_pending(state):
    return rejected_stage(state).get('state') == DISCARDING


def cleanup_failure(state):
    code = rejected_stage(state).get('failure_code')
    if code not in (BackupFailure.STAGE_UNREADABLE, BackupFailure.STAGE_CHECK_FAILED):
        raise RuntimeError('missing or invalid staged copy rejection code')
    return StageRejected('finished interrupted cleanup of rejected staged copy', BackupFailure(code))


def finish_discard(state, mount, generation, verify, save, report):
    record = rejected_stage(state)
    if state.get('pending') != generation.name or record.get('generation') != generation.name:
        raise RuntimeError('pending generation changed before its staged copy could be discarded')
    save()
    discard(mount, generation, verify)
    # A restart must still owe this report until deletion and reporting have both run.
    report()
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
    return stamp_not_advanced(stamp, read_borg_stamp)


def stamp_not_advanced(stamp, read_borg_stamp):
    current = number(read_borg_stamp())
    return stamp is None or current is None or current <= stamp


def clear(state):
    clear_check_retry(state)
    state.pop('stage_rejected', None)
    state.pop('pending_borg_stamp_mtime', None)
