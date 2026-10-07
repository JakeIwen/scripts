"""Fixed privileged entry point for Time Machine cloud pause/resume."""
import subprocess

from .van_dashboard_common import SUDO, run_command


class TimeMachineCloudControl:
    def __init__(self, command=run_command):
        self.command = command

    def request(self, action, minutes=None):
        if action == 'pause':
            if (not isinstance(minutes, str) or not minutes.isascii() or not minutes.isdecimal()
                    or not 1 <= int(minutes) <= 10080):
                raise ValueError('pause duration must be between 1 minute and 7 days')
            args = ['--pause', str(int(minutes))]
        elif action == 'resume' and minutes is None:
            args = ['--resume']
        else:
            raise ValueError('unsupported Time Machine control')
        try:
            result = self.command([SUDO, '-n', '/usr/bin/python3',
                '/home/pi/scripts/backup/time_machine_icloud_control.py', *args], timeout=15)
            if result.returncode:
                raise RuntimeError('Time Machine control was not accepted; refresh status before retrying')
        except (OSError, subprocess.SubprocessError) as exc:
            raise RuntimeError('Time Machine control is unavailable; refresh status before retrying') from exc
