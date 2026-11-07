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
            not valid_priority(marker.get('priority')) or
            not isinstance(marker.get('created'), str) or not marker['created']):
        raise RuntimeError('Invalid offsite marker')


def valid_priority(value):
    """A repository's priority: a plain int (never a bool) from 0 to 2147483647."""
    return type(value) is int and 0 <= value <= 2147483647


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

# Lock policy (pi/docs/backup/CONCURRENCY.md, OFFSITE.md "Locks").
JOB_KIND = 'offsite'
# Jacob, Nov 3: every command but prepare-disk keeps iCloud uploads out, as the
# old exclusive legacy lock did. Releasing it for local seeds is a deferred choice.
HOLD_CLOUD_SLOT = True
# OOM fallback, off by default: job kinds a run also holds exclusively so they
# cannot overlap it, e.g. ('borg', 'exfat') if nightly Borg plus a seed exhausts RAM.
EXCLUDED_LOCAL_JOBS: tuple[str, ...] = ()
BORG_SOURCE = 'borg'
BORG_REPOSITORY_LOCK = 'borg-repo'
# A busy Borg repository defers the borg source to the end of the run, then waits.
BORG_WAIT_SECONDS = 3 * 60 * 60

# Frozen code snapshot (offsite_snapshot.py). The launcher copies the run's code
# closure into <state>/code/<run id>/ and execs the copy, so installers may
# publish new code during a days-long seed.
LIVE_ROOT = Path('/home/pi/scripts')
CODE_DIRECTORY = 'code'
RUN_ID_PATTERN = r'[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}'
MANIFEST = 'manifest.json'
SNAPSHOT_RESTIC = 'bin/restic'
# The restic pin (version, binary SHA-256, install target) is declared once, in
# this installer; the snapshot reads it from the live tree.
RESTIC_INSTALLER = 'backup/install_restic.sh'
METERED_CLI = 'vanpi-metered'
# Every file the run imports, runs or sources, relative to the scripts root.
# test_offsite_snapshot_closure.py rescans the sources and fails on any miss.
FROZEN_IMPLEMENTATION = (
    'backup/abort_backup.sh', 'backup/backup_admission.py', 'backup/backup_conf.sh',
    'backup/backup_deploy_state.sh',
    'backup/backup_lifecycle.sh', 'backup/backup_lock_io.sh', 'backup/backup_lock_registry.sh',
    'backup/backup_locks.py', 'backup/backup_locks.sh', 'backup/backup_priority.py',
    'backup/backup_priority_control.py', 'backup/backup_setting_fields.py', 'backup/backup_settings.py',
    'backup/backup_settings_control.py', 'backup/cloud_backup_control.py',
    'backup/cloud_backup_requests.py', 'backup/hotspare_rotation.py', 'backup/icloud_progress.py',
    'backup/icloud_status.py', 'backup/icloud_uplink.py', 'backup/mac_capture_control.py',
    'backup/offsite_availability.py', 'backup/offsite_backup.py', 'backup/offsite_config.py',
    'backup/offsite_control.py', 'backup/offsite_dashboard.py',
    'backup/offsite_locks.py', 'backup/offsite_mounts.sh', 'backup/offsite_prepare.py',
    'backup/offsite_receipts.py', 'backup/offsite_remote.py', 'backup/offsite_repository.py',
    'backup/offsite_run.py', 'backup/offsite_samba.py', 'backup/offsite_snapshot.py',
    'backup/protected_json.py', 'backup/stale_mount_recovery.sh', 'backup/time_machine_store.py',
    'backup/upload_speed_log.py', 'connectivity_status.py', 'disk_policy.sh', 'fix_hfs_fs.sh',
    'mount_disks.sh', 'ntfy_send.sh', 'samba_share_control.sh',
)
# Live scripts-tree paths that frozen code names on purpose and that stay live.
LIVE_BOUNDARIES = {
    METERED_CLI: 'fresh metering admission CLI; it imports the current package release by design',
    'backup/offsite_backup.sh': 'explicit remote resume starts the stable launcher to take a fresh code snapshot',
    'backup/pi_backup.sh': 'identity abort_backup.sh --user matches in a live command line; never run',
    'backup/exfat_snapshot.sh': 'identity abort_backup.sh --user matches in a live command line; never run',
    'backup/icloud_backup.sh': 'display text: the iCloud login hint a person runs',
}
# State, secrets, hooks and inputs frozen code reads or writes by absolute path; never copied.
LIVE_DATA = (
    '/home/pi/secrets/', '/home/pi/hooks/', '/home/pi/backups/', '/home/pi/.ssh/',
    # Borg exclusion patterns and inputs named by backup_conf.sh.
    '/home/pi/.codex', '/home/pi/build/', '/home/pi/dev/obd-things/tmp', '/home/pi/rsync-exclude-media.txt',
    '/etc/', '/var/lib/', '/run/', '/root/', '/mnt/',
)
INSTALLED_RESTIC = '/usr/local/bin/restic'


def command_paths(code_root=None):
    """Fixed child commands; helpers and restic come from the run's snapshot when it has one."""
    root = LIVE_ROOT if code_root is None else Path(code_root)
    return {
        'restic': INSTALLED_RESTIC if code_root is None else str(root / SNAPSHOT_RESTIC),
        'lsblk': '/usr/bin/lsblk',
        'findmnt': '/usr/bin/findmnt', 'wipefs': '/usr/sbin/wipefs',
        'sfdisk': '/usr/sbin/sfdisk', 'mkfs': '/usr/sbin/mkfs.ext4',
        'tune2fs': '/usr/sbin/tune2fs', 'udevadm': '/usr/bin/udevadm',
        'sync': '/usr/bin/sync', 'smbstatus': '/usr/bin/smbstatus',
        'samba': str(root / 'samba_share_control.sh'),
        'mounts': str(root / 'backup' / 'offsite_mounts.sh'),
        'notify': str(root / 'ntfy_send.sh'),
    }


COMMANDS = command_paths()
DEFAULT_STATE = Path('/var/lib/vanpi-offsite')


def state_root():
    # Tests point both the snapshot CLI and the run at a disposable state root.
    return Path(os.environ.get('OFFSITE_STATE_ROOT', str(DEFAULT_STATE)))


class Result(str, Enum):
    RUNNING = 'running'
    OK = 'ok'
    FAILED = 'failed'
    INTERRUPTED = 'interrupted'
    NOT_RUN = 'not-run'


class TargetKind(str, Enum):
    LOCAL = 'local'
    REMOTE = 'remote'


@dataclass(frozen=True)
class RemoteControlGuard:
    label: str
    revision: str
    expected_paused: bool = False


class RemoteControlRecord(TypedDict):
    schema: int
    label: str
    revision: str
    repo_id: str
    sources: list[str]
    paused: bool


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
    # Additive since granular locks: disks left mounted for another holder or
    # already unmounted by a drain. Readers ignore unknown fields.
    cleanup_notes: list[str]
    notification_error: str
    duration_seconds: float
    target_kind: TargetKind
    progress_measure: str
    history_error: str
    control_revision: str


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
    state: Path = DEFAULT_STATE
    password: Path = Path('/root/.config/restic/vanpi-offsite.password')
    mount_root: Path = Path('/mnt')
    by_id: Path = Path('/dev/disk/by-id')
    drain: Path = Path('/run/lock/vanpi-samba-drain/mbp2tbkup')
    owner: int = 0
    block_devices: bool = True
    # prepare-disk's exclusive legacy lock; every other command holds job-offsite.
    lock_fd: int = 9
    commands: dict[str, str] = field(default_factory=lambda: dict(COMMANDS))
    # The run's frozen snapshot root; None for read-only commands and prepare-disk.
    code_root: Path | None = None
    borg_wait: float = BORG_WAIT_SECONDS

    @classmethod
    def from_environment(cls):
        # BORG_REPO and the ignition flag belong to backup_conf.sh, not a second
        # policy file; the launcher exports OFFSITE_CODE_ROOT for snapshot runs.
        code = os.environ.get('OFFSITE_CODE_ROOT')
        code_root = Path(code) if code else None
        return cls(borg=Path(os.environ['BORG_REPO']),
                   ignition=Path(os.environ['OFFSITE_IGNITION_FLAG']),
                   state=state_root(), code_root=code_root, commands=command_paths(code_root))

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
