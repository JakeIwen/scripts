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

# bootable clone targets: "<ext4-label>:<interval-days>", staggered so hotspare-b
# always holds an older known-good generation. Cards are found by ext4 label at
# runtime; init new ones with: clone_to_sd.sh --init <label> sdX
CLONE_TARGETS=(hotspare-a:7 hotspare-b:14)
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
# backup alerts go to NTFY_BACKUP_URL (set in secrets, e.g. https://ntfy.sh/<topic>);
# if unset, ntfy_send.sh's own fallbacks apply (NTFY_MESSAGE_URL, then local server).
# exported because ntfy_send.sh runs as a child process, not sourced
export NTFY_URL="${NTFY_BACKUP_URL:-}"
NTFY_ON_SUCCESS=1      # 0 = only notify on failures/watchdog findings
BORG_STALE_HOURS=48    # watchdog alerts past this
CLONE_STALE_FACTOR=2   # watchdog alerts when clone age > factor * interval
MIN_FREE_GB=100        # watchdog alerts when bigboi free space drops below this
ROOT_USED_MAX_GB=26    # watchdog alerts before the system outgrows a 32GB clone card
UNMOUNT_AFTER=1        # unmount bigboi when the backup finishes (bigboi is not auto-mounted;
                       # pi_backup mounts it by label at the start of each run)

# present while the van runs (ignition_monitor.sh); drives are unmounted then
IGNITION_FLAG=/home/pi/hooks/ignition_is_on

# serialize writes to a disk across clone/restore scripts. kernel drops the lock
# the moment the holding process exits or dies — cannot go stale like a flag file
lock_disk() { # lock_disk sda — held for the remaining life of the calling script
  exec 8>"/run/lock/vanpi_disk_$1.lock"
  flock -n 8 || { echo "another backup/restore is writing /dev/$1, aborting"; return 1; }
}

# one bigboi/borg operation at a time. holder PID is recorded in the lockfile so
# abort_backup.sh (called by umount_disks.sh before pulling mounts) can TERM us
JOB_LOCK=/run/lock/vanpi_backup.lock
acquire_job_lock() {
  exec 9>>"$JOB_LOCK"   # append-open: must not truncate a current holder's PID record
  flock -n 9 || return 1
  echo $$ > "$JOB_LOCK"
}
