#!/usr/bin/env python3
"""Fixed Pi recovery cloud pause/resume entry point."""
from cloud_backup_control import run_cli
from icloud_status import STATE_DIR

SERVICE = 'vanpi-icloud-backup.service'


if __name__ == '__main__':
    run_cli(STATE_DIR, SERVICE)
