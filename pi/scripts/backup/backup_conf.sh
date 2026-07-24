#!/bin/bash
# vanpi backup configuration — sourced by pi_backup.sh,
# /home/pi/scripts/backup/clone_to_sd.sh, backup_watchdog.sh,
# restore_from_borg.sh, ntfy_send.sh

[ -f /home/pi/secrets/.bash_variables ] && . /home/pi/secrets/.bash_variables

# destinations
BACKUP_DISK_LABEL=bigboi
BACKUP_MNT=/mnt/bigboi
export BORG_REPO="$BACKUP_MNT/borg/vanpi-encrypted"
BORG_PASSFILE=/root/.config/borg/vanpi-encrypted.passphrase
# Backups are unattended, so root reads the repository passphrase from a
# root-only file. Keep an independent copy in the password manager: the copy
# inside a Borg archive cannot unlock that archive.
unset BORG_PASSPHRASE
export BORG_PASSCOMMAND="/usr/bin/cat $BORG_PASSFILE"

# borg retention
KEEP_DAILY=14
KEEP_WEEKLY=8
KEEP_MONTHLY=12
BORG_CHECK_DOM=1  # day of month for the integrity check (borg check)

# bootable clone targets: "<ext4-label>:<interval-days>", staggered so hotspare-b
# always holds an older known-good generation. Cards are found by ext4 label at
# runtime; init new ones with:
# /home/pi/scripts/backup/clone_to_sd.sh --init <label> sdX
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

# OpenWrt snapshot pulled from dendelion before each Borg archive. The router
# key is forced-command-only; the last valid local bundle is retained on error.
OPENWRT_SNAPSHOT_DIR="$SNAP_DIR/openwrt"
OPENWRT_SNAPSHOT_FILE="$OPENWRT_SNAPSHOT_DIR/dendelion-latest.tar.gz"
OPENWRT_BACKUP_STAMP="$STAMP_DIR/openwrt_ok"
OPENWRT_BACKUP_STALE_HOURS=72
OPENWRT_BACKUP_HOST=root@192.168.6.1
OPENWRT_BACKUP_KEY=/home/pi/.ssh/openwrt-backup
OPENWRT_BACKUP_KNOWN_HOSTS=/home/pi/.ssh/known_hosts

# Complete airOS /etc/persistent snapshot pulled from the primary NanoStation
# before each Borg archive. This is independent of the repo's interactive
# push/pull workflow, and the last valid local snapshot is retained on error.
UBNT_SNAPSHOT_DIR="$SNAP_DIR/ubnt"
UBNT_SNAPSHOT_FILE="$UBNT_SNAPSHOT_DIR/ubnt-persistent-latest.tar.gz"
UBNT_BACKUP_STAMP="$STAMP_DIR/ubnt_ok"
UBNT_BACKUP_STALE_HOURS=72
UBNT_BACKUP_HOST=ubnt@192.168.8.20
UBNT_BACKUP_KEY=/home/pi/.ssh/id_rsa
UBNT_BACKUP_KNOWN_HOSTS=/home/pi/.ssh/known_hosts
UBNT_BACKUP_MAX_BYTES=$((16 * 1024 * 1024))
# backup alerts go to NTFY_BACKUP_URL (set in secrets, e.g. https://ntfy.sh/<topic>);
# if unset, ntfy_send.sh's own fallbacks apply (NTFY_MESSAGE_URL, then local server).
# exported because ntfy_send.sh runs as a child process, not sourced
export NTFY_URL="${NTFY_BACKUP_URL:-}"
NTFY_ON_SUCCESS=1      # 0 = only notify on failures/watchdog findings
BORG_STALE_HOURS=48    # watchdog alerts past this
CLONE_STALE_FACTOR=2   # watchdog alerts when clone age > factor * interval
MIN_FREE_GB=100        # watchdog alerts when bigboi free space drops below this
ROOT_USED_MAX_GB=26    # watchdog alerts before the system outgrows a 32GB clone card
UNMOUNT_AFTER=1        # when pi_backup mounted bigboi itself, unmount it afterward;
                       # preserve a mount that was already present when the job began

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
