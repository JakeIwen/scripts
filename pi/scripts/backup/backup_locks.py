"""Resource-specific backup lock registry for Python workers and status tools.

Defines the vocabulary used by backup_locks.sh and systemd-tmpfiles; regenerate
their declarations with python3 -m pi.generate_backup_lock_registry.
Pre-created lock files under /run/lock/vanpi-backup,
never created at runtime, validated before use. Shell entry points acquire the
job, disk and cloud-slot descriptors and exec the worker, which inherits them;
this module lets the worker assert those descriptors and lets read-only status
code enumerate live holders. See pi/docs/backup/CONCURRENCY.md.
"""
from contextlib import contextmanager
from enum import Enum
import fcntl
import grp
import os
from pathlib import Path
import pwd
import re
import stat
import time


class LockClass(str, Enum):
    JOB = 'job'
    DISK = 'disk'
    DATA = 'data'
    CLOUD = 'cloud'


PI_CLOUD = 'pi-cloud'
TIME_MACHINE = 'time-machine'
DISK_LABELS = ('bigboi', 'hdd1tb', 'mbp2tbkup', 'movingparts', 'EXFAT512')
DATA_NAMES = ('borg-repo',)
CLOUD_SLOT = 'upload'
JOB_LABELS = {'borg': 'local Pi backup', 'exfat': 'EXFAT512 snapshot', PI_CLOUD: 'Pi iCloud backup',
              TIME_MACHINE: 'Mac Time Machine iCloud backup', 'clone': 'hotspare clone'}
JOB_KINDS = tuple(JOB_LABELS)
LOCK_NAMES = {LockClass.JOB: JOB_KINDS, LockClass.DISK: DISK_LABELS,
              LockClass.DATA: DATA_NAMES, LockClass.CLOUD: (CLOUD_SLOT,)}
PID_RECORD = re.compile(r'[1-9][0-9]{0,9}')
POLL_SECONDS = 0.5
FALLBACK_FD_LIMIT = 1024


class LockBusy(BlockingIOError):
    """The requested lock is held in a conflicting mode."""


def lock_dir():
    return Path(os.environ.get('VANPI_BACKUP_LOCK_DIR', '/run/lock/vanpi-backup'))


def legacy_lock():
    return Path(os.environ.get('VANPI_BACKUP_JOB_LOCK', '/run/lock/vanpi_backup.lock'))


def job_lock_fd():
    return int(os.environ.get('VANPI_BACKUP_JOB_LOCK_FD', '10'))


def cloud_slot_fd():
    value = os.environ.get('VANPI_BACKUP_CLOUD_SLOT_FD')
    return int(value) if value is not None else None


def lock_path(lock_class, name):
    lock_class = LockClass(lock_class)
    if name not in LOCK_NAMES[lock_class]:
        raise ValueError(f'unknown backup lock: {lock_class.value}-{name}')
    return lock_dir() / f'{lock_class.value}-{name}.lock'


def expected_ownership():
    owner = os.environ.get('VANPI_BACKUP_JOB_LOCK_OWNER', 'root')
    group = os.environ.get('VANPI_BACKUP_JOB_LOCK_GROUP', 'pi')
    mode = int(os.environ.get('VANPI_BACKUP_JOB_LOCK_MODE', '660'), 8)
    return pwd.getpwnam(owner).pw_uid, grp.getgrnam(group).gr_gid, mode


def validate_lock_file(path, info=None):
    uid, gid, mode = expected_ownership()
    info = path.lstat() if info is None else info
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != uid or info.st_gid != gid
            or stat.S_IMODE(info.st_mode) != mode):
        raise RuntimeError(f'backup lock is missing or unsafe: {path}')
    return info


def validate_registry():
    directory = lock_dir()
    uid, gid, _ = expected_ownership()
    mode = int(os.environ.get('VANPI_BACKUP_LOCK_DIR_MODE', '770'), 8)
    info = directory.lstat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != uid or info.st_gid != gid
            or stat.S_IMODE(info.st_mode) != mode):
        raise RuntimeError(f'backup lock directory is missing or unsafe: {directory}')


def open_lock(path, *, legacy=False):
    """Open an existing lock file without creating it; returns a close-on-exec fd."""
    if not legacy and path != legacy_lock():
        validate_registry()
    expected = validate_lock_file(path)
    fd = os.open(path, os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        actual = os.fstat(fd)
        if (actual.st_dev, actual.st_ino) != (expected.st_dev, expected.st_ino):
            raise RuntimeError(f'backup lock changed while opening: {path}')
    except BaseException:
        os.close(fd)
        raise
    return fd


def try_flock(fd, exclusive, timeout=None):
    operation = (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB
    deadline = None if timeout is None else time.monotonic() + timeout
    while True:
        try:
            fcntl.flock(fd, operation)
            return
        except BlockingIOError:
            if deadline is None or time.monotonic() >= deadline:
                raise LockBusy('backup lock is busy')
            time.sleep(min(POLL_SECONDS, max(0.0, deadline - time.monotonic())))


@contextmanager
def held(lock_class, name, *, exclusive=True, timeout=None):
    """Hold one registry lock for the block; raises LockBusy when unavailable."""
    path = lock_path(lock_class, name)
    fd = open_lock(path)
    try:
        try:
            try_flock(fd, exclusive, timeout)
        except LockBusy as exc:
            raise LockBusy(f'{path.name} is busy') from exc
        yield fd
    finally:
        # Close without unlocking: an explicit LOCK_UN would also strip a child
        # that inherited this descriptor, while the close ends the lock only
        # with the last descriptor (the orphan guarantee in CONCURRENCY.md).
        os.close(fd)


def require_held(fd, path, *, exclusive=True):
    """Assert this descriptor already holds the required flock; never acquire it.

    Linux fdinfo reports locks attached to this open file description, including
    inherited holds. A probe on another fd could mistake somebody else's hold
    for ours, and re-flocking this fd would silently admit an unlocked caller.
    """
    actual, expected = os.fstat(fd), path.lstat()
    if (not stat.S_ISREG(expected.st_mode) or not stat.S_ISREG(actual.st_mode)
            or (actual.st_dev, actual.st_ino) != (expected.st_dev, expected.st_ino)):
        raise RuntimeError(f'backup worker requires its inherited lock descriptor for {path.name}')
    mode = 'WRITE' if exclusive else 'READ'
    try:
        records = Path(f'/proc/self/fdinfo/{fd}').read_text().splitlines()
    except OSError as exc:
        raise RuntimeError('backup lock ownership requires readable Linux fdinfo') from exc
    for record in records:
        fields = record.split()
        if len(fields) != 9 or fields[:1] != ['lock:'] or fields[2:5] != ['FLOCK', 'ADVISORY', mode]:
            continue
        try:
            major, minor, inode = fields[6].split(':')
            identity = (int(major, 16), int(minor, 16), int(inode))
        except ValueError:
            continue
        if identity == (os.major(actual.st_dev), os.minor(actual.st_dev), actual.st_ino):
            return
    raise LockBusy(f'backup descriptor {fd} does not hold {path.name} in {mode} mode')


def require_job_lock(kind, fd=None):
    require_held(job_lock_fd() if fd is None else fd, lock_path(LockClass.JOB, kind))


def require_cloud_slot():
    """Assert the exported upload slot, refusing absent admission metadata."""
    fd = cloud_slot_fd()
    if fd is None:
        raise RuntimeError('backup worker requires its inherited cloud slot descriptor')
    require_held(fd, lock_path(LockClass.CLOUD, CLOUD_SLOT))


def _registered_lock_inodes():
    paths = [legacy_lock()]
    try:
        paths.extend(lock_dir().iterdir())
    except OSError:
        pass
    inodes = set()
    for path in paths:
        try:
            info = path.lstat()
        except OSError:
            continue
        if stat.S_ISREG(info.st_mode):
            inodes.add((info.st_dev, info.st_ino))
    return inodes


def _open_fd_numbers():
    try:
        return [int(entry.name) for entry in (Path('/proc/self/fd')).iterdir()]
    except (OSError, ValueError):
        return range(FALLBACK_FD_LIMIT)


def inherited_lock_fds():
    """Return open descriptors for the legacy lock and registry lock files."""
    inodes = _registered_lock_inodes()
    inherited = []
    for fd in _open_fd_numbers():
        try:
            info = os.fstat(fd)
        except OSError:
            continue
        if (info.st_dev, info.st_ino) in inodes:
            inherited.append(fd)
    return sorted(inherited)


def record_pid(path):
    try:
        text = path.read_text().strip()
    except OSError:
        return None
    return int(text) if PID_RECORD.fullmatch(text) else None


def process_holds(pid, path, process_root=Path('/proc')):
    """True when the live process has any descriptor open on the lock inode."""
    try:
        expected = path.lstat()
        entries = list((process_root / str(pid) / 'fd').iterdir())
    except OSError:
        return False
    for entry in entries:
        try:
            info = entry.stat()
        except OSError:
            continue
        if (info.st_dev, info.st_ino) == (expected.st_dev, expected.st_ino):
            return True
    return False


def release_inherited(path, process_root=Path('/proc')):
    """Close this process's descriptors opened on path; return the count closed."""
    expected = path.lstat()
    entries = list((process_root / 'self' / 'fd').iterdir())
    closed = 0
    for entry in entries:
        try:
            info = entry.stat()
            fd = int(entry.name)
        except (OSError, ValueError):
            continue
        if (info.st_dev, info.st_ino) != (expected.st_dev, expected.st_ino):
            continue
        try:
            os.close(fd)
        except OSError:
            continue
        closed += 1
    return closed


def job_holder(kind, process_root=Path('/proc')):
    """The verified live parent PID of a job kind, or None."""
    path = lock_path(LockClass.JOB, kind)
    pid = record_pid(path)
    if pid is None or not process_holds(pid, path, process_root):
        return None
    return pid


def active_jobs(process_root=Path('/proc')):
    jobs = {}
    for kind in JOB_KINDS:
        try:
            pid = job_holder(kind, process_root)
        except (OSError, ValueError):
            pid = None
        if pid is not None:
            jobs[kind] = pid
    return jobs


def legacy_exclusive_holder(process_root=Path('/proc'), path=None):
    """PID of a verified exclusive legacy holder (restore, hotspare init), else None."""
    path = legacy_lock() if path is None else Path(path)
    try:
        fd = open_lock(path, legacy=True)
    except (OSError, RuntimeError, KeyError):
        return None
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError:
            pid = record_pid(path)
            return pid if pid is not None and process_holds(pid, path, process_root) else None
        fcntl.flock(fd, fcntl.LOCK_UN)
        return None
    finally:
        os.close(fd)
