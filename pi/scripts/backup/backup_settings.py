"""Validated preference overrides, separate from credentials and backup state."""
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import shlex
import time

from backup_setting_fields import BY_ID, FIELDS, GROUPS, validate
from protected_json import MISSING, read_protected_json
from time_machine_store import atomic_json, read_json
import backup_priority as priority


@dataclass(frozen=True)
class SettingsPaths:
    preferences: Path = Path('/etc/vanpi-backup-settings.json')
    local: Path = Path('/home/pi/scripts/backup/backup_conf.sh')
    pi: Path = Path('/etc/vanpi-icloud-backup.json')
    mac: Path = Path('/etc/vanpi-time-machine-icloud.json')
    lock_directory: Path = priority.DIRECTORY


PATHS = SettingsPaths()


def overrides(paths=PATHS):
    row = read_protected_json(paths.preferences, 'Backup settings failed their file safety checks.')
    if row is MISSING:
        return {}
    if (not isinstance(row, dict) or type(row.get('version')) is not int or row['version'] != 1
            or set(row) != {'version', 'values'}):
        raise ValueError('Invalid backup settings file.')
    validate(row['values'])
    return row['values']


def cloud_config(cfg, source, paths=PATHS, *, native_path=None):
    result = dict(cfg)
    if native_path is not None and native_path != getattr(paths, source):
        return result
    if source == 'mac' and 'bandwidth_limit' in cfg:
        result.setdefault('verification_bandwidth_limit', cfg['bandwidth_limit'])
    for key, value in overrides(paths).items():
        field = BY_ID[key]
        if field.source == source:
            result[field.key] = ((f'{int(value)}M' if value == int(value) else f'{int(value * 1024)}k') if field.kind == 'bandwidth'
                                 else value * field.scale if type(value) is int else value)
    return result


def local_values(text):
    values = {}
    for field in FIELDS:
        if field.source != 'local':
            continue
        match = re.search(r'^\s*' + re.escape(field.key) + r'=(\d+)\s*(?:#.*)?$', text, re.M)
        if match:
            values[field.key] = int(match[1])
    targets = re.search(r'^CLONE_TARGETS=\(([^)]*)\)', text, re.M)
    if targets:
        for entry in shlex.split(targets[1]):
            label, _, interval = entry.rpartition(':')
            if label in ('hotspare-a', 'hotspare-b') and interval.isdecimal():
                values[label] = int(interval)
    return values


def effective(paths=PATHS):
    pi = read_json(paths.pi, {})
    mac = {**pi, **read_json(paths.mac, {})}
    # Mac verification inherits its transfer limit unless explicitly configured.
    mac.setdefault('verification_bandwidth_limit', mac.get('bandwidth_limit', '8M'))
    sources = {'local': local_values(paths.local.read_text()), 'pi': pi, 'mac': mac}
    values = {}
    for field in FIELDS:
        value = sources[field.source].get(field.key)
        if value is None:
            value = field.default
        elif field.kind == 'bandwidth':
            if not isinstance(value, str) or not re.fullmatch(r'[1-9][0-9]*[kM]', value):
                raise ValueError('Unsupported backup transfer limit.')
            value = int(value[:-1]) / (1024 if value[-1] == 'k' else 1)
            if value == int(value): value = int(value)
        elif field.kind == 'toggle':
            value = bool(value)
        else:
            value /= field.scale
            if int(value) == value:
                value = int(value)
        values[field.id] = value
    values.update(overrides(paths))
    validate(values)
    return values


def snapshot(paths=PATHS):
    values = effective(paths)
    revision = hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()
    return {'revision': revision, 'values': values, 'fields': [field.public() for field in FIELDS],
            'groups': [{'id': key, 'label': label} for key, label in GROUPS],
            'timezone': time.tzname[time.localtime().tm_isdst]}


def save(payload, paths=PATHS):
    if not isinstance(payload, dict) or set(payload) != {'revision', 'changes'}:
        raise ValueError('Settings require a revision and changes.')
    validate(payload['changes'])
    if not payload['changes']:
        raise ValueError('No settings were changed.')
    with priority.locked(paths.lock_directory):
        current = snapshot(paths)
        if payload['revision'] != current['revision']:
            raise ValueError('Settings changed elsewhere. Reload before saving.')
        validate({**current['values'], **payload['changes']})
        atomic_json(paths.preferences, {'version': 1, 'values': {**overrides(paths), **payload['changes']}})
    return snapshot(paths)


def shell_overrides(paths=PATHS):
    values = overrides(paths)
    rows = [f'{BY_ID[key].key}={int(value)}' for key, value in values.items()
            if BY_ID[key].source == 'local' and not key.startswith('spare.')]
    if any(key.startswith('spare.') for key in values):
        current = effective(paths)
        rows.extend([f'CLONE_INTERVAL_A={current["spare.a"]}', f'CLONE_INTERVAL_B={current["spare.b"]}'])
    return '\n'.join(rows)


def local_projection(values):
    return {'targets': [{'label': 'hotspare-a', 'interval_days': values['spare.a']},
                        {'label': 'hotspare-b', 'interval_days': values['spare.b']}],
            'borg_stale_hours': values['alerts.borg'], 'exfat_snapshot_stale_hours': values['alerts.exfat'],
            'openwrt_backup_stale_hours': values['alerts.router'], 'clone_stale_factor': values['alerts.spare']}
