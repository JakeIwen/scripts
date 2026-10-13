#!/usr/bin/env python3
"""Stable Time Machine cloud control entry point."""
from pathlib import Path
import subprocess

from cloud_backup_control import admit_worker, is_paused, pause_deadline, run_cli
from cloud_backup_control import control as control_backup

STATE = Path('/var/lib/vanpi-time-machine-icloud')
SERVICE = 'vanpi-time-machine-icloud.service'


def control(action, minutes=None, directory=STATE, now=None, command=subprocess.run, *,
            priority_directory=None, jobs=None):
    return control_backup(action, minutes, directory=directory, service=SERVICE,
                          now=now, command=command, priority_directory=priority_directory, jobs=jobs)


def main():
    run_cli(STATE, SERVICE)


if __name__ == '__main__':
    main()
