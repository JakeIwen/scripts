"""Allowlisted privileged settings requests; no shell evaluation or arbitrary paths."""
import json
import os
import subprocess

from .van_dashboard_common import SUDO, run_command


class BackupSettingsConflict(ValueError):
    """The privileged writer rejected a stale settings revision."""


class BackupSettingsControl:
    HELPER = '/home/pi/scripts/backup/backup_settings_control.py'

    def __init__(self, command=run_command):
        self.command = command

    def request(self, operation, payload=None):
        if operation not in ('status', 'save', 'local'):
            raise ValueError('Unsupported backup settings operation.')
        try:
            result = self.command([SUDO, '-n', '/usr/bin/python3', self.HELPER, '--' + operation],
                                  timeout=15, input_text=None if payload is None else json.dumps(payload))
            data = json.loads(result.stdout)
            if result.returncode in (2, os.EX_TEMPFAIL) and isinstance(data.get('message'), str):
                error = BackupSettingsConflict if result.returncode == os.EX_TEMPFAIL else ValueError
                raise error(data['message'][:200])
            if result.returncode or data.get('ok') is not True or not isinstance(data.get('settings'), dict):
                raise RuntimeError('Backup settings are unavailable. Refresh and retry.')
            return data['settings']
        except (OSError, subprocess.SubprocessError, json.JSONDecodeError, AttributeError) as exc:
            raise RuntimeError('Backup settings are unavailable. Refresh and retry.') from exc

    def local_configuration(self):
        return self.request('local')
