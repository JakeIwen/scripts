#!/bin/bash
# vanpi backup configuration — sourced by pi_backup.sh, clone_to_sd.sh,
# backup_watchdog.sh, restore_from_borg.sh, ntfy_send.sh

[ -f /home/pi/secrets/.bash_variables ] && . /home/pi/secrets/.bash_variables

# destinations
BACKUP_DISK_LABEL=bigboi
BACKUP_MNT=/mnt/bigboi
export BORG_REPO="$BACKUP_MNT/borg/vanpi"
export BORG_PASSPHRASE=  # repo is encryption=none: same exposure as the raw images it replaces

# borg retention
KEEP_DAILY=14
KEEP_WEEKLY=8
KEEP_MONTHLY=12
BORG_CHECK_DOM=1  # day of month for the integrity check (borg check)

# bootable clone targets: "<ext4-label>:<interval-days>", staggered by giving
# each card a different interval, e.g. (hotspare-a:7 hotspare-b:14).
# Empty until the first card is initialized with: clone_to_sd.sh --init hotspare-a sdX
CLONE_TARGETS=()
CLONE_MAX_DISK_GB=500  # refuse to clone onto anything bigger (TB-drive footgun guard)

# media mirror (movingparts -> bigboi), carried over from rsync_schedule.sh
MEDIA_SRC=/mnt/movingparts
MEDIA_DST="$BACKUP_MNT/mp_backup"
MEDIA_EXCLUDES=/home/pi/rsync-exclude-media.txt

# home assistant (venv install; sqlite must be snapshotted, not live-copied)
HA_DB=/home/homeassistant/.homeassistant/home-assistant_v2.db

# state + notifications
STAMP_DIR=/home/pi/backups/stamps
SNAP_DIR=/home/pi/backups/snapshots
# set NTFY_BACKUP_URL in /home/pi/secrets/.bash_variables (e.g. https://ntfy.sh/<topic>)
# for phone delivery off-LAN; falls back to the Pi's local ntfy server
NTFY_URL="${NTFY_BACKUP_URL:-http://127.0.0.1/vanpi-backup}"
NTFY_ON_SUCCESS=1      # 0 = only notify on failures/watchdog findings
BORG_STALE_HOURS=48    # watchdog alerts past this
CLONE_STALE_FACTOR=2   # watchdog alerts when clone age > factor * interval
MIN_FREE_GB=100        # watchdog alerts when bigboi free space drops below this
UNMOUNT_AFTER=0        # 1 = unmount bigboi when the backup finishes (old rsync_schedule behavior)
