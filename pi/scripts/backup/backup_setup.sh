#!/bin/bash
# one-time (idempotent) setup for the new backup scheme — run as root on the Pi
set -eu
. /home/pi/scripts/backup/backup_conf.sh

apt-get install -y borgbackup sqlite3 jq

if ! command -v rpi-clone >/dev/null; then
  rm -rf /tmp/rpi-clone
  git clone --depth 1 https://github.com/geerlingguy/rpi-clone /tmp/rpi-clone
  install -m 755 /tmp/rpi-clone/rpi-clone /usr/local/sbin/
  rm -rf /tmp/rpi-clone
fi

mkdir -p "$STAMP_DIR" "$SNAP_DIR"
chown -R pi:pi /home/pi/backups

if ! borg info >/dev/null 2>&1; then
  mkdir -p "$(dirname "$BORG_REPO")"
  borg init --encryption=none
  echo "created borg repo at $BORG_REPO"
fi

echo "setup complete. next steps:"
echo "  1. run /home/pi/scripts/backup/pi_backup.sh once manually and read the output"
echo "  2. swap the crontab entries (rsync_schedule.sh -> pi_backup.sh + backup_watchdog.sh)"
echo "  3. when a spare card is attached: clone_to_sd.sh --init hotspare-a sdX,"
echo "     then add 'hotspare-a:7' to CLONE_TARGETS in backup_conf.sh"
