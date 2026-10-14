"""Private pre-created registries and real admission descriptors for Linux tests."""
from contextlib import contextmanager
from dataclasses import dataclass
import grp
import os
from pathlib import Path
import pwd
import tempfile
from unittest import mock

import backup_admission
import backup_locks


@dataclass
class LockRegistry:
    root: Path
    registry: Path
    legacy: Path
    environment: dict[str, str]

    @contextmanager
    def admitted(self, kind):
        held = []
        def take(path, exclusive):
            fd = backup_locks.open_lock(path)
            info = os.fstat(fd)
            held.append((fd, info.st_dev, info.st_ino))
            backup_locks.try_flock(fd, exclusive)
            return fd
        try:
            legacy = take(self.legacy, False)
            job = take(backup_locks.lock_path(backup_locks.LockClass.JOB, kind), True)
            cloud = take(backup_locks.lock_path(backup_locks.LockClass.CLOUD, backup_locks.CLOUD_SLOT), True)
            disks = {label: take(backup_locks.lock_path(backup_locks.LockClass.DISK, label), False)
                     for label in backup_admission.CLOUD_DISKS[kind]}
            metadata = {'VANPI_BACKUP_JOB_KIND': kind, 'VANPI_BACKUP_LEGACY_LOCK_FD': str(legacy),
                        'VANPI_BACKUP_JOB_LOCK_FD': str(job), 'VANPI_BACKUP_CLOUD_SLOT_FD': str(cloud),
                        'VANPI_BACKUP_DISK_LOCK_FDS': ','.join(f'{label}={fd}' for label, fd in disks.items())}
            with mock.patch.dict(os.environ, metadata):
                yield backup_admission.from_environment(kind)
        finally:
            # Tests deliberately close parent holds to prove inherited children
            # keep them alive. Never close a descriptor number subsequently reused.
            for fd, device, inode in reversed(held):
                try:
                    info = os.fstat(fd)
                except OSError:
                    continue
                if (info.st_dev, info.st_ino) == (device, inode):
                    os.close(fd)


@contextmanager
def temporary_registry():
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        registry = root / 'registry'
        registry.mkdir(mode=0o770); registry.chmod(0o770)
        legacy = root / 'legacy.lock'
        legacy.touch(mode=0o660); legacy.chmod(0o660)
        for lock_class, names in (
                (backup_locks.LockClass.JOB, backup_locks.JOB_KINDS),
                (backup_locks.LockClass.DISK, backup_locks.DISK_LABELS),
                (backup_locks.LockClass.DATA, backup_locks.DATA_NAMES),
                (backup_locks.LockClass.CLOUD, (backup_locks.CLOUD_SLOT,))):
            for name in names:
                path = registry / f'{lock_class.value}-{name}.lock'
                path.touch(mode=0o660); path.chmod(0o660)
        environment = {'VANPI_BACKUP_LOCK_DIR': str(registry), 'VANPI_BACKUP_JOB_LOCK': str(legacy),
                       'VANPI_BACKUP_JOB_LOCK_OWNER': pwd.getpwuid(os.getuid()).pw_name,
                       'VANPI_BACKUP_JOB_LOCK_GROUP': grp.getgrgid(os.getgid()).gr_name,
                       'VANPI_BACKUP_JOB_LOCK_MODE': '660', 'VANPI_BACKUP_LOCK_DIR_MODE': '770'}
        with mock.patch.dict(os.environ, environment):
            yield LockRegistry(root, registry, legacy, environment)
