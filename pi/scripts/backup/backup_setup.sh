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

if [ ! -e "$BORG_REPO" ]; then
  [ -s "$BORG_PASSFILE" ] || {
    echo "missing Borg passphrase file: $BORG_PASSFILE" >&2
    echo "create it root-owned and mode 0600 before running setup" >&2
    exit 1
  }
  mkdir -p "$(dirname "$BORG_REPO")"
  borg init --encryption=repokey-blake2
  echo "created encrypted borg repo at $BORG_REPO"
elif ! borg info >/dev/null 2>&1; then
  echo "borg repo exists but could not be opened: $BORG_REPO" >&2
  echo "check the passphrase file before making any changes" >&2
  exit 1
fi

echo "setup complete. next steps:"
echo "  1. run /home/pi/scripts/backup/pi_backup.sh once manually and read the output"
echo "  2. swap the crontab entries (rsync_schedule.sh -> pi_backup.sh + backup_watchdog.sh)"
echo "  3. when a spare card is attached: clone_to_sd.sh --init hotspare-a sdX,"
echo "     then add 'hotspare-a:7' to CLONE_TARGETS in backup_conf.sh"
