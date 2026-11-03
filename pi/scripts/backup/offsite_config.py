"""Local offsite configuration and wire vocabulary (Pi Python 3.11+)."""
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TypedDict
import os


MARKER = '.vanpi-offsite'
REPO_PATH = 'vanpi-restic'
SCHEMA = 1
HOST = 'vanpi'
INCOMPLETE_TAG = 'offsite-incomplete'
LABEL_PATTERN = r'[A-Za-z0-9][A-Za-z0-9._-]{0,15}'
REPO_ID_PATTERN = r'[0-9a-f]{64}'
PRIORITY_EXAMPLES = {'3tb': 10, '14tb': 20}
GUARD_SECONDS = 5
STOP_SECONDS = 15
# Restic patterns are absolute, unlike rsync's source-root-anchored patterns.
EXFAT_EXCLUDES = ('van-tv', 'System Volume Information', 'vanpi-network')
COMMANDS = {
    'restic': '/usr/local/bin/restic', 'lsblk': '/usr/bin/lsblk',
    'findmnt': '/usr/bin/findmnt', 'wipefs': '/usr/sbin/wipefs',
    'sfdisk': '/usr/sbin/sfdisk', 'mkfs': '/usr/sbin/mkfs.ext4',
    'tune2fs': '/usr/sbin/tune2fs', 'udevadm': '/usr/bin/udevadm',
    'sync': '/usr/bin/sync', 'umount': '/usr/bin/umount', 'smbstatus': '/usr/bin/smbstatus',
    'samba': '/home/pi/scripts/samba_share_control.sh',
    'mounts': '/home/pi/scripts/backup/offsite_mounts.sh',
    'notify': '/home/pi/scripts/ntfy_send.sh',
}


class Result(str, Enum):
    RUNNING = 'running'
    OK = 'ok'
    FAILED = 'failed'
    INTERRUPTED = 'interrupted'
    NOT_RUN = 'not-run'


class MarkerRecord(TypedDict):
    schema: int
    label: str
    repo_id: str
    repo_path: str
    priority: int
    created: str


class RepositoryRecords(TypedDict):
    schema: int
    repos: dict[str, MarkerRecord]


class SourceOutcome(TypedDict, total=False):
    result: Result
    cause: str
    snapshot: str


class StatusRecord(TypedDict, total=False):
    schema: int
    pid: int
    label: str
    result: Result
    source: str | None
    bytes_done: int
    bytes_total: int | None
    percent: float | None
    rate: float | None
    eta: float | None
    started: float
    updated: float
    finished: float
    last_result: SourceOutcome | None
    sources: dict[str, SourceOutcome]
    cause: str
    cleanup_errors: list[str]
    notification_error: str


@dataclass(frozen=True)
class Source:
    label: str
    paths: tuple[Path, ...]
    excludes: tuple[str, ...] = ()
    compression: str = 'auto'


@dataclass(frozen=True)
class Config:
    borg: Path
    ignition: Path
    state: Path = Path('/var/lib/vanpi-offsite')
    password: Path = Path('/root/.config/restic/vanpi-offsite.password')
    mount_root: Path = Path('/mnt')
    by_id: Path = Path('/dev/disk/by-id')
    drain: Path = Path('/run/lock/vanpi-samba-drain/mbp2tbkup')
    owner: int = 0
    block_devices: bool = True
    lock_fd: int = 9
    commands: dict[str, str] = field(default_factory=lambda: dict(COMMANDS))

    @classmethod
    def from_environment(cls):
        # These two values belong to backup_conf.sh, not a second policy file.
        return cls(borg=Path(os.environ['BORG_REPO']),
                   ignition=Path(os.environ['OFFSITE_IGNITION_FLAG']))

    def sources(self):
        tm = self.mount_root / 'mbp2tbkup' / 'm4mac0.sparsebundle'
        exfat = self.mount_root / 'EXFAT512'
        media = self.mount_root / 'movingparts'
        return {
            'time-machine': Source('mbp2tbkup', (tm,), (str(tm / 'lock'),), 'off'),
            'exfat512': Source('EXFAT512', (exfat,),
                              tuple(str(exfat / name) for name in EXFAT_EXCLUDES)),
            'movingparts': Source('movingparts', tuple(media / name for name in
                                  ('Jake', 'music', 'guitar_tab_archive'))),
            'borg': Source('bigboi', (self.borg,)),
        }
