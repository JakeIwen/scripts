"""Admission and lock ownership for one offsite command (CONCURRENCY.md, OFFSITE.md "Locks").

offsite_backup.sh takes job-offsite (or, for prepare-disk, the exclusive legacy
lock) and execs the worker. This module proves that inherited hold, then takes
the run's own resource locks on existing registry inodes, non-blocking except
the bounded Borg repository wait. Release is by close, never unlock: an
orphaned child keeps its inherited hold, so the emergency drain still finds it.
"""
import os
from pathlib import Path
import time

import backup_admission
import backup_locks as locks
from backup_locks import LockClass
from offsite_config import EXCLUDED_LOCAL_JOBS, HOLD_CLOUD_SLOT, JOB_KIND, MANIFEST
from offsite_snapshot import validate_run_root

# OFFSITE_ADMISSION values; offsite_mounts.sh validates the same two modes.
JOB = 'job'
PREPARE = 'prepare'
POLL_SECONDS = 1
BUSY_CAUSES = {
    LockClass.DISK: '{name} is being unmounted, drained or prepared (its disk lock is held exclusively); retry later',
    LockClass.CLOUD: 'The iCloud upload slot is busy; retry after the cloud backup finishes',
    LockClass.JOB: 'The {name} backup job is running; retry after it finishes',
}


class OffsiteLocks:
    """The admitted descriptor plus every resource lock the run took, in order."""

    def __init__(self, mode, admitted):
        self.mode, self.admitted = mode, admitted
        self.resources = {}

    def take(self, lock_class, name, *, exclusive, wait=None, allowed=None):
        """Lock an existing registry inode; LockBusy once `wait` seconds pass (None: at once)."""
        if self.mode != JOB:
            raise RuntimeError('prepare-disk runs under the exclusive legacy lock and takes no resource locks')
        key = (LockClass(lock_class), name)
        if key in self.resources:
            raise RuntimeError(f'offsite run already holds {key[0].value}-{name}')
        path = locks.lock_path(*key)
        fd = locks.open_lock(path)
        try:
            deadline = None if wait is None else time.monotonic() + wait
            while True:
                try:
                    locks.try_flock(fd, exclusive)
                    break
                except locks.LockBusy:
                    if deadline is None or time.monotonic() >= deadline:
                        cause = BUSY_CAUSES.get(key[0], '{path} is busy')
                        raise locks.LockBusy(cause.format(name=name, path=path.name)) from None
                # try_flock's own timed wait cannot see TERM or ignition; this one must.
                if allowed:
                    allowed()
                time.sleep(min(POLL_SECONDS, max(0.0, deadline - time.monotonic())))
            if allowed:
                allowed()
            info = os.fstat(fd)
        except BaseException:
            os.close(fd)
            raise
        self.resources[key] = backup_admission.HeldDescriptor(fd, path, info.st_dev, info.st_ino)
        return fd

    def close(self, lock_class, name):
        held = self.resources.pop((LockClass(lock_class), name), None)
        if held is not None:
            os.close(held.fd)

    def close_resources(self):
        for lock_class, name in reversed(list(self.resources)):
            self.close(lock_class, name)

    def hold_policy_locks(self):
        """Whole-command holds that are policy, not a resource the command touches."""
        if HOLD_CLOUD_SLOT:
            self.take(LockClass.CLOUD, locks.CLOUD_SLOT, exclusive=True)
        for kind in EXCLUDED_LOCAL_JOBS:
            self.take(LockClass.JOB, kind, exclusive=True)

    def fds(self, resources=True):
        """Descriptors a child inherits; resources=False: only the admitted one."""
        held = (self.admitted, *(self.resources.values() if resources else ()))
        for record in held:
            record.check_identity()
        return tuple(sorted(record.fd for record in held))

    def child_environment(self, environment, resources=True):
        """Admission metadata a child (offsite_mounts.sh above all) validates."""
        environment = {key: value for key, value in environment.items() if key not in (
            'VANPI_BACKUP_JOB_LOCK_FD', 'VANPI_BACKUP_DISK_LOCK_FDS', 'VANPI_BACKUP_CLOUD_SLOT_FD')}
        environment['OFFSITE_ADMISSION'] = self.mode
        if self.mode == JOB:
            held = self.resources if resources else {}
            environment['VANPI_BACKUP_JOB_LOCK_FD'] = str(self.admitted.fd)
            environment['VANPI_BACKUP_DISK_LOCK_FDS'] = ','.join(
                f'{name}={record.fd}' for (lock_class, name), record in held.items()
                if lock_class == LockClass.DISK)
            slot = held.get((LockClass.CLOUD, locks.CLOUD_SLOT))
            if slot is not None:
                environment['VANPI_BACKUP_CLOUD_SLOT_FD'] = str(slot.fd)
        return environment


def admit(cfg, *, prepare):
    """Prove the launcher's hold before any work; prepare-disk keeps the legacy route."""
    if os.geteuid() != cfg.owner:
        raise RuntimeError('Run offsite_backup.sh as root')
    if prepare:
        return OffsiteLocks(PREPARE, backup_admission.validated_descriptor(
            cfg.lock_fd, locks.legacy_lock(), exclusive=True))
    environment = dict(os.environ)
    if backup_admission.required_value(environment, 'VANPI_BACKUP_JOB_KIND') != JOB_KIND:
        raise RuntimeError('offsite worker admission has the wrong job kind')
    if not Path(backup_admission.required_value(environment, 'VANPI_BACKUP_LOCK_DIR')).is_absolute():
        raise RuntimeError('offsite worker admission requires an absolute registry path')
    locks.validate_registry()
    check_code_root(cfg)
    fd = backup_admission.descriptor_number(backup_admission.required_value(environment, 'VANPI_BACKUP_JOB_LOCK_FD'))
    path = locks.lock_path(LockClass.JOB, JOB_KIND)
    declared = environment.get('VANPI_BACKUP_JOB_LOCK_PATH')
    if declared is not None and Path(declared) != path:
        raise RuntimeError('offsite worker admission names a different job lock')
    job = backup_admission.validated_descriptor(fd, path, exclusive=True)
    # A leaked legacy descriptor would block the deploy fence for the whole run,
    # and a stray disk descriptor would make the run a holder of that disk.
    stray = [other for other in locks.inherited_lock_fds() if other != fd]
    if stray:
        raise RuntimeError(f'offsite worker inherited other backup lock descriptors {stray}; refusing')
    return OffsiteLocks(JOB, job)


def check_code_root(cfg):
    if cfg.code_root is None:
        raise RuntimeError('offsite run must execute from its code snapshot (OFFSITE_CODE_ROOT)')
    root = validate_run_root(cfg.state, cfg.code_root)
    if not (root / MANIFEST).is_file():
        raise RuntimeError(f'offsite code snapshot has no manifest: {root}')
    if not Path(__file__).resolve().is_relative_to(root):
        raise RuntimeError('offsite worker is not running from its code snapshot')
