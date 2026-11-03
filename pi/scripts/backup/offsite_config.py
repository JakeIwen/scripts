"""Offsite sources and shared protocol vocabulary (NAS 3.9+, Pi runtime 3.11+)."""
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TypedDict
import os
import re
import ipaddress


def validate_transport(value, extra_fields=()):
    if (not isinstance(value, dict) or set(value) != {'schema', 'address', 'port', 'transport_password', *extra_fields}
            or type(value.get('schema')) is not int or value['schema'] != SCHEMA):
        raise ValueError('Missing or invalid offsite transport configuration')
    if not isinstance(value['address'], str):
        raise ValueError('Offsite endpoint must be a literal Tailscale IPv4 address')
    address = ipaddress.IPv4Address(value['address'])
    if address not in ipaddress.IPv4Network('100.64.0.0/10'):
        raise ValueError('Offsite endpoint must be a literal Tailscale IPv4 address')
    if type(value['port']) is not int or not 1024 <= value['port'] <= 65535:
        raise ValueError('Invalid offsite receiver port')
    if not isinstance(value['transport_password'], str) or not re.fullmatch(r'[A-Za-z0-9_-]{43,128}', value['transport_password']):
        raise ValueError('Transport password must be a separate random URL-safe secret (at least 32 random bytes)')
    return value


def validate_marker(marker, label):
    if (not isinstance(marker, dict) or type(marker.get('schema')) is not int or marker['schema'] != SCHEMA or
            not isinstance(label, str) or marker.get('label') != label or not re.fullmatch(LABEL_PATTERN, label) or
            marker.get('repo_path') != REPO_PATH or not isinstance(marker.get('repo_id'), str) or
            not re.fullmatch(REPO_ID_PATTERN, marker['repo_id']) or
            type(marker.get('priority')) is not int or not 0 <= marker['priority'] <= 2147483647 or
            not isinstance(marker.get('created'), str) or not marker['created']):
        raise RuntimeError('Invalid offsite marker')


MARKER = '.vanpi-offsite'
REPO_PATH = 'vanpi-restic'
SCHEMA = 1
HOST = 'vanpi'
INCOMPLETE_TAG = 'offsite-incomplete'
RECEIPT_TAG = 'offsite-receipt-v1'
RECEIPT_FILE = 'offsite-completion.json'
REMOTE_CONSUMER = 'pi.offsite_backup'
REMOTE_CONFIG = 'remote.json'
LABEL_PATTERN = r'[A-Za-z0-9][A-Za-z0-9._-]{0,15}'
REPO_ID_PATTERN = r'[0-9a-f]{64}'
PRIORITY_EXAMPLES = {'5tb': 10, '14tb': 20}
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


class TargetKind(str, Enum):
    LOCAL = 'local'
    REMOTE = 'remote'


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
    receipt: str
    logical_bytes_processed: int
    newly_added_packed_bytes: int | None
    duration_seconds: float


class HistoryEntry(SourceOutcome):
    finished: float
    target_kind: TargetKind


class SourceHistory(TypedDict):
    latest: HistoryEntry
    last_ok: HistoryEntry | None


HistoryRecord = dict[str, dict[str, SourceHistory]]


class CompletionReceipt(TypedDict):
    schema: int
    repo_id: str
    source: str
    snapshot: str
    completed: float
    logical_bytes_processed: int | None
    newly_added_packed_bytes: int | None


class TransportConfig(TypedDict):
    schema: int
    address: str
    port: int
    transport_password: str


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
    duration_seconds: float
    target_kind: TargetKind
    progress_measure: str
    history_error: str


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
