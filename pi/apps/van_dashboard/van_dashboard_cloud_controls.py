"""Fixed privileged entry points for cloud backup pause/resume."""
import subprocess

from .van_dashboard_common import SUDO, run_command


class CloudBackupControl:
    HELPERS = {'pi': 'icloud_backup_control.py', 'time-machine': 'time_machine_icloud_control.py'}

    def __init__(self, kind='time-machine', command=run_command):
        self.helper = '/home/pi/scripts/backup/' + self.HELPERS[kind]
        self.command = command

    def request(self, action, minutes=None):
        if action == 'pause' and minutes == 'indefinite':
            args = ['--pause', minutes]
        elif action == 'pause':
            if (not isinstance(minutes, str) or not minutes.isascii() or not minutes.isdecimal()
                    or not 1 <= int(minutes) <= 10080):
                raise ValueError('pause duration must be between 1 minute and 7 days')
            args = ['--pause', str(int(minutes))]
        elif action in ('resume', 'take-turn') and minutes is None:
            args = ['--' + action]
        else:
            raise ValueError('unsupported cloud backup control')
        try:
            result = self.command([SUDO, '-n', '/usr/bin/python3',
                self.helper, *args], timeout=30 if action == 'take-turn' else 15)
            if result.returncode:
                raise RuntimeError('Cloud backup control was not accepted; refresh status before retrying')
        except (OSError, subprocess.SubprocessError) as exc:
            raise RuntimeError('Cloud backup control is unavailable; refresh status before retrying') from exc
