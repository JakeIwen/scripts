"""Fixed privileged requests for backup priority and Mac capture permission."""
import json
import subprocess

from .van_dashboard_common import SUDO, run_command


class BackupPriorityControl:
    MODES = ('normal', 'pi', 'time-machine')
    HELPER = '/home/pi/scripts/backup/backup_priority_control.py'

    def __init__(self, command=run_command):
        self.command = command

    def _run(self, args):
        try:
            result = self.command([SUDO, '-n', '/usr/bin/python3', self.HELPER, *args], timeout=15)
            data = json.loads(result.stdout)
            if not isinstance(data, dict):
                raise RuntimeError('Backup priority returned invalid status.')
            if result.returncode or data.get('ok') is False:
                message = data.get('message')
                if result.returncode == 2 and isinstance(message, str) and len(message) <= 200:
                    raise ValueError(message)
                raise RuntimeError('Backup priority is unavailable; refresh and retry.')
            return data
        except (OSError, json.JSONDecodeError, subprocess.SubprocessError) as exc:
            raise RuntimeError('Backup priority is unavailable; refresh and retry.') from exc

    def status(self):
        return self._run(['--status'])

    def select(self, mode):
        if mode not in self.MODES:
            raise ValueError('Choose a supported backup priority.')
        return self._run(['--set', mode])

    def capture(self, cancel=False):
        return self._run(['--cancel-capture' if cancel else '--request-capture'])
