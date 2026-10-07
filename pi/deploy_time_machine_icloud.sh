#!/bin/bash
# Targeted offsite replication deployment; no repository-wide synchronization.
set -euo pipefail
repo=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
target=${1:-pi@vanpi.lan}
[[ $# -le 1 && "$target" != -* && "$target" != *[[:space:]]* ]] || exit 2
cd "$repo"
python3 pi/tests/backup/test_time_machine_icloud.py
python3 pi/tests/backup/test_icloud_status.py
python3 pi/tests/backup/test_icloud_backup.py
bash pi/tests/storage/test_samba_share_control.sh
bash -n pi/scripts/backup/time_machine_icloud.sh
stage=$(ssh -o BatchMode=yes -o ConnectTimeout=8 "$target" /usr/bin/mktemp -d /tmp/vanpi-tm-icloud.XXXXXX)
[[ "$stage" =~ ^/tmp/vanpi-tm-icloud\.[[:alnum:]]{6}$ ]] || exit 1
shared_hash=$(git show HEAD:pi/scripts/backup/icloud_backup.py | shasum -a 256 | awk '{print $1}')
progress_hash=$(git show HEAD:pi/scripts/backup/icloud_progress.py | shasum -a 256 | awk '{print $1}')
share_hash=$(git show HEAD:pi/scripts/samba_share_control.sh | shasum -a 256 | awk '{print $1}')
gate_hash=$(shasum -a 256 pi/scripts/samba_require_mount.sh | awk '{print $1}')
# For a second, still-uncommitted cutover, supply only the reviewed hash from
# the previous deployment's staged source. Never blindly trust a live hash.
old_status_hash=${VANPI_TM_PREVIOUS_STATUS_SHA256:-$(git show HEAD:pi/scripts/backup/icloud_status.py | shasum -a 256 | awk '{print $1}')}
[[ "$old_status_hash" =~ ^[0-9a-f]{64}$ ]] || exit 1
COPYFILE_DISABLE=1 /usr/bin/tar --no-xattrs -cf - \
  pi/scripts/backup/time_machine_icloud.py pi/scripts/backup/time_machine_icloud.sh \
  pi/scripts/backup/time_machine_store.py pi/scripts/backup/TIME_MACHINE_ICLOUD_RESTORE.txt \
  pi/scripts/backup/time_machine_icloud_status.py \
  pi/scripts/backup/time_machine_icloud_control.py \
  pi/scripts/backup/icloud_backup.py pi/scripts/backup/icloud_progress.py \
  pi/scripts/backup/icloud_status.py pi/scripts/backup/icloud_uplink.py \
  pi/scripts/backup/ICLOUD_RESTORE.txt \
  pi/scripts/connectivity_status.py pi/configs/icloud-backup.json \
  pi/configs/time-machine-icloud.json pi/services/vanpi-time-machine-icloud.service \
  pi/services/vanpi-time-machine-icloud.timer pi/tests/backup/test_time_machine_icloud.py \
  pi/services/vanpi-time-machine-icloud-resume.service pi/services/vanpi-time-machine-icloud-resume.timer \
  pi/scripts/samba_share_control.sh pi/scripts/disk_policy.sh pi/scripts/umount_disks.sh pi/scripts/remount.sh \
  pi/tests/lib.sh pi/tests/storage/test_samba_share_control.sh \
  pi/tests/backup/test_icloud_status.py pi/tests/backup/test_icloud_backup.py macbook/scripts/time_machine_offsite_coordinator.py \
  | ssh -o BatchMode=yes "$target" /bin/tar -xf - -C "$stage"
ssh -o BatchMode=yes "$target" /bin/bash -s -- "$stage" "$shared_hash" "$gate_hash" "$old_status_hash" "$progress_hash" "$share_hash" <<'REMOTE'
set -euo pipefail
stage=$1
[[ "$stage" =~ ^/tmp/vanpi-tm-icloud\.[[:alnum:]]{6}$ ]] || exit 1
cd "$stage"
# Fence hourly starts and Mac-heartbeat starts while publishing the worker set.
[[ ! -L /run/lock/vanpi_backup.lock && -f /run/lock/vanpi_backup.lock &&
   $(/usr/bin/stat -c '%U %G %a' /run/lock/vanpi_backup.lock) == 'root pi 660' ]] || exit 1
exec 9</run/lock/vanpi_backup.lock
/usr/bin/flock -n 9 || { echo 'Backup lock became busy; retry deployment later.' >&2; exit 1; }
/usr/bin/python3 pi/tests/backup/test_time_machine_icloud.py
/usr/bin/python3 pi/tests/backup/test_icloud_status.py
/usr/bin/python3 pi/tests/backup/test_icloud_backup.py
bash pi/tests/storage/test_samba_share_control.sh
verify_previous() {
  local source=$1 live=$2 previous=$3 actual expected
  actual=$(sha256sum "$live" | awk '{print $1}')
  expected=$(sha256sum "$source" | awk '{print $1}')
  [[ "$actual" == "$previous" || "$actual" == "$expected" ]] || { echo "Live code differs: $live; inspect before deploying." >&2; exit 1; }
}
verify_previous pi/scripts/backup/icloud_backup.py /home/pi/scripts/backup/icloud_backup.py "$2"
verify_previous pi/scripts/backup/icloud_progress.py /home/pi/scripts/backup/icloud_progress.py "$5"
verify_previous pi/scripts/samba_share_control.sh /home/pi/scripts/samba_share_control.sh "$6"
[[ $(sha256sum /home/pi/scripts/samba_require_mount.sh | awk '{print $1}') == "$3" ]] || { echo 'Samba mount gate differs; inspect before deploying.' >&2; exit 1; }
live_status_hash=$(sha256sum /home/pi/scripts/backup/icloud_status.py | awk '{print $1}')
new_status_hash=$(sha256sum pi/scripts/backup/icloud_status.py | awk '{print $1}')
[[ "$live_status_hash" == "$4" || "$live_status_hash" == "$new_status_hash" ]] || { echo 'Shared status helper differs; inspect before deploying.' >&2; exit 1; }
gate=$(sudo -n /usr/bin/testparm -s --section-name=mbp2tbkup --parameter-name=preexec 2>/dev/null)
[[ "$gate" == '/home/pi/scripts/samba_require_mount.sh mbp2tbkup' ]] || exit 1
close=$(sudo -n /usr/bin/testparm -s --section-name=mbp2tbkup --parameter-name='preexec close' 2>/dev/null)
[[ "$close" == Yes || "$close" == yes ]] || exit 1
/usr/bin/systemd-analyze verify pi/services/vanpi-time-machine-icloud*.service pi/services/vanpi-time-machine-icloud*.timer
pi_timer_was_active=$(/usr/bin/systemctl is-active vanpi-icloud-backup.timer || true)
tm_timer_was_active=$(/usr/bin/systemctl is-active vanpi-time-machine-icloud.timer || true)
resume_timer_was_active=$(/usr/bin/systemctl is-active vanpi-time-machine-icloud-resume.timer || true)
restore_timers() {
  [[ "$pi_timer_was_active" != active ]] || sudo -n /usr/bin/systemctl start vanpi-icloud-backup.timer
  [[ "$tm_timer_was_active" != active ]] || sudo -n /usr/bin/systemctl start vanpi-time-machine-icloud.timer
  [[ "$resume_timer_was_active" != active ]] || sudo -n /usr/bin/systemctl start vanpi-time-machine-icloud-resume.timer
}
trap restore_timers EXIT
sudo -n /usr/bin/systemctl stop vanpi-icloud-backup.timer
if [[ "$tm_timer_was_active" == active ]]; then sudo -n /usr/bin/systemctl stop vanpi-time-machine-icloud.timer; fi
if [[ "$resume_timer_was_active" == active ]]; then sudo -n /usr/bin/systemctl stop vanpi-time-machine-icloud-resume.timer; fi
for unit in vanpi-icloud-backup.service vanpi-time-machine-icloud.service; do
  state=$(/usr/bin/systemctl show "$unit" -p ActiveState --value)
  case "$state" in inactive|failed) ;; *) echo "Refusing to update active worker: $unit" >&2; exit 1;; esac
done
install_atomic() {
  local source=$1 target=$2 mode=$3 temporary
  temporary="$target.deploy.$$"
  sudo -n /usr/bin/install -o pi -g pi -m "$mode" "$source" "$temporary"
  sudo -n /usr/bin/mv -Tf "$temporary" "$target"
}
/usr/bin/install -d -m 0700 previous
for name in icloud_status.py icloud_progress.py icloud_backup.py time_machine_icloud_control.py time_machine_store.py time_machine_icloud.py time_machine_icloud.sh time_machine_icloud_status.py TIME_MACHINE_ICLOUD_RESTORE.txt; do
  live=/home/pi/scripts/backup/$name
  [[ ! -e "$live" ]] || /usr/bin/cp -p "$live" previous/"$name"
  install_atomic pi/scripts/backup/"$name" "$live" 0750
done
/usr/bin/cp -p /home/pi/scripts/samba_share_control.sh previous/samba_share_control.sh
install_atomic pi/scripts/samba_share_control.sh /home/pi/scripts/samba_share_control.sh 0750
if [[ ! -e /etc/vanpi-time-machine-icloud.json ]]; then
  sudo -n /usr/bin/install -o root -g root -m 0600 pi/configs/time-machine-icloud.json /etc/vanpi-time-machine-icloud.json
fi
for name in vanpi-time-machine-icloud.service vanpi-time-machine-icloud.timer vanpi-time-machine-icloud-resume.service vanpi-time-machine-icloud-resume.timer; do
  [[ ! -e /etc/systemd/system/$name ]] || /usr/bin/cp -p /etc/systemd/system/"$name" previous/"$name"
  sudo -n /usr/bin/install -o root -g root -m 0644 pi/services/"$name" /etc/systemd/system/"$name"
done
sudo -n /usr/bin/systemctl daemon-reload
sudo -n /usr/bin/python3 /home/pi/scripts/backup/time_machine_icloud.py --capture-request
sudo -n /usr/bin/systemctl enable --now vanpi-time-machine-icloud.timer
sudo -n /usr/bin/systemctl enable --now vanpi-time-machine-icloud-resume.timer
echo "Time Machine replication installed. Rollback files: $stage/previous"
REMOTE
