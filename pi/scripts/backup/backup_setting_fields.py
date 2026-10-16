"""Canonical editable backup preferences and their presentation metadata."""
from dataclasses import asdict, dataclass
import math


@dataclass(frozen=True)
class Setting:
    id: str
    group: str
    section: str
    label: str
    unit: str
    minimum: int | float
    maximum: int | float
    default: int | bool
    source: str
    key: str
    help: str
    scale: int = 1
    kind: str = 'number'

    def public(self):
        return {key: value for key, value in asdict(self).items()
                if key not in ('source', 'key', 'scale', 'default')}


GROUPS = (
    ('pi-cloud', 'Pi · iCloud'), ('mac-cloud', 'Mac · iCloud'),
    ('disk', 'Disk backups'), ('hotspares', 'Hotspares'), ('alerts', 'Alerts'),
)


def cloud_fields(group, source, keep, source_age):
    prefix = 'pi' if source == 'pi' else 'mac'
    return [
        Setting(prefix+'.interval', group, 'Schedule & retention', 'Every', 'days', 1, 31, 7, source, 'interval_days',
                'Time between successful cloud verifications. Back up now can request an earlier copy.'),
        Setting(prefix+'.keep', group, 'Schedule & retention', 'Keep', 'copies', 1 if source == 'pi' else 2, 52 if source == 'pi' else 8,
                keep, source, 'keep_generations', 'Older verified cloud copies are retired only after a new copy passes verification. Lowering this affects future retention.'),
        Setting(prefix+'.bandwidth', group, 'Transfers', 'Transfer limit' if source == 'pi' else 'Upload limit',
                'MiB/s', .125, 100, 8, source, 'bandwidth_limit', 'Maximum transfer rate. Network and provider limits may make the actual rate lower. Pi uploads and verification share this limit.', kind='bandwidth'),
        Setting(prefix+'.source_age', group, 'Source & storage' if source == 'pi' else 'Capture', 'Maximum backup age', 'hours', 1, 168, source_age,
                source, 'max_source_age_hours', 'The newest completed disk backup must be this recent when a new cloud copy is captured. Saved unfinished copies can still resume.'),
        Setting(prefix+'.reserve', group, 'Source & storage' if source == 'pi' else 'Storage', 'Keep disk space free', 'GiB', 20 if source == 'mac' else 1,
                500 if source == 'mac' else 1000, 100, source, 'minimum_free_gib', 'Free-space reserve required before creating the staging copy. Existing backups are not deleted to make room.'),
        Setting(prefix+'.timeout', group, 'Transfers', 'Retry after no progress', 'minutes', 1, 120, 15,
                source, 'no_progress_timeout_seconds', 'Stop a stalled transfer after this long without progress. Completed objects and verification checkpoints are retained.', scale=60),
    ]


FIELDS = cloud_fields('pi-cloud', 'pi', 8, 48) + cloud_fields('mac-cloud', 'mac', 2, 72) + [
    Setting('mac.download', 'mac-cloud', 'Transfers', 'Verification limit', 'MiB/s', .125, 100, 8, 'mac',
            'verification_bandwidth_limit', 'Maximum download rate while checking uploaded chunks against their SHA-256 hashes.', kind='bandwidth'),
    Setting('mac.overlap', 'mac-cloud', 'Transfers', 'Verify while uploading', '', 0, 1, True, 'mac',
            'overlap_verification', 'Verify completed chunks while remaining chunks upload. Turning this off performs verification after the upload finishes.', kind='toggle'),
    Setting('mac.budget', 'mac-cloud', 'Storage', 'Cloud storage budget', 'GiB', 100, 1500, 900, 'mac',
            'max_cloud_store_gib', 'Maximum size of the Mac recovery store in iCloud. This is a backup limit, not a reading of free iCloud account space.'),
    Setting('mac.wait', 'mac-cloud', 'Capture', 'Wait for Time Machine', 'minutes', 2, 120, 30, 'mac',
            'capture_wait_seconds', 'How long capture may wait for clean exclusive access to the Mac backup image. It never force-closes an active image.', scale=60),
    Setting('disk.start', 'disk', 'Daily window', 'Start', 'hour', 0, 23, 3, 'local', 'BACKUP_WINDOW_START_HOUR',
            'Pi local time. Borg, EXFAT512 and router export retry hourly in this window, stopping after their daily success.', kind='hour'),
    Setting('disk.end', 'disk', 'Daily window', 'End', 'hour', 0, 23, 9, 'local', 'BACKUP_WINDOW_END_HOUR',
            'End is exclusive, in Pi local time. A window can cross midnight. This does not interrupt an already-running backup.', kind='hour'),
    Setting('borg.daily', 'disk', 'Borg retention', 'Daily', 'copies', 1, 365, 14, 'local', 'KEEP_DAILY',
            'Keep this many daily Borg recovery points. Weekly and monthly retention are additional tiers.'),
    Setting('borg.weekly', 'disk', 'Borg retention', 'Weekly', 'copies', 1, 104, 8, 'local', 'KEEP_WEEKLY', 'Keep this many weekly Borg recovery points.'),
    Setting('borg.monthly', 'disk', 'Borg retention', 'Monthly', 'copies', 1, 120, 12, 'local', 'KEEP_MONTHLY', 'Keep this many monthly Borg recovery points.'),
    Setting('borg.check', 'disk', 'Borg maintenance', 'Integrity check day', 'of month', 1, 28, 1, 'local', 'BORG_CHECK_DOM', 'Run Borg’s repository check when a backup runs on this day of the month.'),
    Setting('disk.reserve', 'disk', 'Borg maintenance', 'Low disk-space warning', 'GiB', 1, 1000, 100, 'local', 'MIN_FREE_GB', 'Warn when bigboi has less free space than this. This is an alert threshold.'),
    Setting('exfat.daily', 'disk', 'EXFAT512 retention', 'Daily through', 'days', 1, 365, 30, 'local', 'EXFAT_SNAPSHOT_KEEP_DAILY_DAYS', 'Keep daily snapshots through this age, then thin them to the weekly tier.'),
    Setting('exfat.weekly', 'disk', 'EXFAT512 retention', 'Weekly through', 'days', 2, 730, 84, 'local', 'EXFAT_SNAPSHOT_KEEP_WEEKLY_DAYS', 'Keep weekly snapshots through this age, then thin them to the monthly tier.'),
    Setting('exfat.monthly', 'disk', 'EXFAT512 retention', 'Monthly through', 'days', 3, 3650, 365, 'local', 'EXFAT_SNAPSHOT_KEEP_MONTHLY_DAYS', 'Oldest age retained for EXFAT512 snapshots. Changes apply during the next successful snapshot’s cleanup.'),
    Setting('exfat.reserve', 'disk', 'EXFAT512 storage', 'Low disk-space warning', 'GiB', 1, 1000, 100, 'local', 'EXFAT_SNAPSHOT_MIN_FREE_GB', 'Warn when the EXFAT512 snapshot disk has less free space than this.'),
    Setting('spare.a', 'hotspares', 'Refresh cadence', 'Hotspare A', 'days', 1, 365, 7, 'local', 'hotspare-a', 'Refresh this bootable spare when due during a successful Borg run. Existing disk-identity and capacity checks remain.'),
    Setting('spare.b', 'hotspares', 'Refresh cadence', 'Hotspare B', 'days', 2, 730, 14, 'local', 'hotspare-b', 'Keep this spare on a longer cadence—at least twice A’s—to retain an older recovery generation.'),
    Setting('alerts.success', 'alerts', 'Notifications', 'Successful disk backups', '', 0, 1, True, 'local', 'NTFY_ON_SUCCESS', 'Send success notifications for local disk backups as well as failure and watchdog alerts. Cloud verification confirmations remain enabled.', kind='toggle'),
    Setting('alerts.borg', 'alerts', 'Warn when older than', 'Pi Borg', 'hours', 12, 720, 48, 'local', 'BORG_STALE_HOURS', 'Age of the latest successful Borg backup before the watchdog warns.'),
    Setting('alerts.exfat', 'alerts', 'Warn when older than', 'EXFAT512', 'hours', 12, 720, 48, 'local', 'EXFAT_SNAPSHOT_STALE_HOURS', 'Age of the latest successful EXFAT512 snapshot before the watchdog warns.'),
    Setting('alerts.router', 'alerts', 'Warn when older than', 'OpenWrt export', 'hours', 12, 720, 72, 'local', 'OPENWRT_BACKUP_STALE_HOURS', 'Age of the latest verified router export before the watchdog warns.'),
    Setting('alerts.antenna', 'alerts', 'Warn when older than', 'Antenna export', 'hours', 12, 720, 72, 'local', 'UBNT_BACKUP_STALE_HOURS', 'Age of the latest antenna configuration export before the watchdog warns. It refreshes with Borg.'),
    Setting('alerts.spare', 'alerts', 'Hotspares', 'Warn after', '× cadence', 2, 10, 2, 'local', 'CLONE_STALE_FACTOR', 'Warn when a spare’s age exceeds its refresh cadence multiplied by this value.'),
]
BY_ID = {field.id: field for field in FIELDS}


def validate(values):
    if not isinstance(values, dict) or set(values) - BY_ID.keys():
        raise ValueError('Unknown backup setting.')
    for key, value in values.items():
        field = BY_ID[key]
        if field.kind == 'toggle':
            valid = type(value) is bool
        elif field.kind == 'bandwidth':
            valid = (type(value) in (int, float) and math.isfinite(value)
                     and field.minimum <= value <= field.maximum and value * 1024 == int(value * 1024))
        else:
            valid = type(value) is int and field.minimum <= value <= field.maximum
        if not valid:
            raise ValueError(f'{field.label} must be {field.minimum}–{field.maximum} {field.unit}.')
    if all(key in values for key in ('exfat.daily', 'exfat.weekly', 'exfat.monthly')):
        if not values['exfat.daily'] < values['exfat.weekly'] < values['exfat.monthly']:
            raise ValueError('EXFAT retention must increase from daily to weekly to monthly.')
    if 'spare.a' in values and 'spare.b' in values and values['spare.b'] < 2 * values['spare.a']:
        raise ValueError('Hotspare B must keep a cadence of at least twice Hotspare A.')
    if 'disk.start' in values and values.get('disk.end') == values['disk.start']:
        raise ValueError('Choose different start and end hours for the daily window.')
