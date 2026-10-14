"""One validated shell admission for a mutating cloud run and its clients.

The ContextVar follows synchronous worker helpers without mixing descriptors
into configuration. Process boundaries receive the bundle explicitly; any
future thread launching clients must likewise receive and bind it explicitly.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace
import os
from pathlib import Path
import re

import backup_locks as locks


CLOUD_DISKS = {locks.PI_CLOUD: ('bigboi',), locks.TIME_MACHINE: ('mbp2tbkup',)}
FD_NUMBER = re.compile(r'[0-9]+\Z')


@dataclass(frozen=True)
class HeldDescriptor:
    fd: int
    path: Path
    device: int
    inode: int

    def check_identity(self):
        actual = os.fstat(self.fd)
        if (actual.st_dev, actual.st_ino) != (self.device, self.inode):
            raise RuntimeError('admitted backup descriptor was replaced')


@dataclass(frozen=True)
class WorkerAdmission:
    kind: str
    registry: Path
    legacy: HeldDescriptor
    job: HeldDescriptor
    cloud: HeldDescriptor
    disks: tuple[tuple[str, HeldDescriptor], ...]

    def client_fds(self):
        if {label for label, _ in self.disks} != set(CLOUD_DISKS[self.kind]):
            raise RuntimeError('backup worker released its disk admission before launching a client')
        held = (self.legacy, self.job, self.cloud, *(record for _, record in self.disks))
        for record in held:
            record.check_identity()
        return tuple(sorted(record.fd for record in held))


_ACTIVE: ContextVar[WorkerAdmission | None] = ContextVar('backup_worker_admission', default=None)


def required_value(environment, name):
    value = environment.get(name)
    if not isinstance(value, str) or not value:
        raise RuntimeError(f'backup worker admission is missing {name}')
    return value


def descriptor_number(value):
    if not FD_NUMBER.fullmatch(value) or int(value) < 3:
        raise RuntimeError('backup worker admission has an invalid descriptor number')
    return int(value)


def validated_descriptor(fd, path, *, exclusive):
    expected = locks.validate_lock_file(path)
    locks.require_held(fd, path, exclusive=exclusive)
    actual = os.fstat(fd)
    if (actual.st_dev, actual.st_ino) != (expected.st_dev, expected.st_ino):
        raise RuntimeError('backup lock changed during worker admission')
    return HeldDescriptor(fd, path, actual.st_dev, actual.st_ino)


def from_environment(kind):
    """Read metadata once, then prove exact inode ownership and held modes."""
    environment = dict(os.environ)
    if kind not in CLOUD_DISKS or required_value(environment, 'VANPI_BACKUP_JOB_KIND') != kind:
        raise RuntimeError('backup worker admission has the wrong job kind')
    registry = Path(required_value(environment, 'VANPI_BACKUP_LOCK_DIR'))
    if not registry.is_absolute():
        raise RuntimeError('backup worker admission requires an absolute registry path')
    locks.validate_registry()
    legacy_fd, job_fd, cloud_fd = (descriptor_number(required_value(environment, name)) for name in (
        'VANPI_BACKUP_LEGACY_LOCK_FD', 'VANPI_BACKUP_JOB_LOCK_FD', 'VANPI_BACKUP_CLOUD_SLOT_FD'))
    disk_fds = {}
    for record in required_value(environment, 'VANPI_BACKUP_DISK_LOCK_FDS').split(','):
        fields = record.split('=')
        if len(fields) != 2 or fields[0] in disk_fds:
            raise RuntimeError('backup worker admission has invalid disk descriptors')
        disk_fds[fields[0]] = descriptor_number(fields[1])
    if set(disk_fds) != set(CLOUD_DISKS[kind]):
        raise RuntimeError('backup worker admission does not cover its exact disk set')
    all_fds = (legacy_fd, job_fd, cloud_fd, *disk_fds.values())
    if len(set(all_fds)) != len(all_fds):
        raise RuntimeError('backup worker admission repeats a descriptor')
    return WorkerAdmission(kind, registry,
        validated_descriptor(legacy_fd, locks.legacy_lock(), exclusive=False),
        validated_descriptor(job_fd, registry / f'job-{kind}.lock', exclusive=True),
        validated_descriptor(cloud_fd, registry / f'cloud-{locks.CLOUD_SLOT}.lock', exclusive=True),
        tuple((label, validated_descriptor(fd, registry / f'disk-{label}.lock', exclusive=False))
              for label, fd in disk_fds.items()))


@contextmanager
def use(admission):
    if not isinstance(admission, WorkerAdmission):
        raise RuntimeError('backup worker requires a validated admission bundle')
    token = _ACTIVE.set(admission)
    try:
        yield admission
    finally:
        _ACTIVE.reset(token)


@contextmanager
def admitted_run(kind):
    with use(from_environment(kind)) as admission:
        yield admission


def current():
    admission = _ACTIVE.get()
    if admission is None:
        raise RuntimeError('backup worker requires its admission context')
    return admission


def client_fds(*, account=False, repository_fd=None):
    admission = _ACTIVE.get()
    if admission is None and account and repository_fd is None:
        return ()
    admission = current()
    descriptors = admission.client_fds()
    if repository_fd is not None:
        extra = validated_descriptor(repository_fd, admission.registry / 'data-borg-repo.lock', exclusive=True)
        descriptors = tuple(sorted(set((*descriptors, extra.fd))))
    return descriptors


def release_disk(label):
    admission = current()
    held = dict(admission.disks).get(label)
    if held is None:
        raise RuntimeError('backup worker has no admitted hold for the disk')
    held.check_identity()
    os.close(held.fd)  # Close only: inherited children retain their own holds.
    _ACTIVE.set(replace(admission, disks=tuple(pair for pair in admission.disks if pair[0] != label)))


def staging_job_fd():
    # Standalone staging GC still asserts the shell's job hold at each storage
    # boundary. A cloud run uses its already captured descriptor identity.
    admission = _ACTIVE.get()
    return admission.job.fd if admission is not None else locks.job_lock_fd()
