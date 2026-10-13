#!/bin/bash
# Targeted deployment; retains existing local settings and Apple credentials.
set -euo pipefail
repo=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
target=${1:-pi@vanpi.lan}
[[ $# -le 1 ]] || exit 2
. "$repo/pi/backup_local_deploy.sh"
local_previous=$(local_backup_previous_hashes "$repo")
tmpfiles_hash=$(local_backup_previous_tmpfiles_hash "$repo")
PYTHONPATH="$repo:$repo/pi/scripts/backup" python3 -m unittest pi.tests.backup.test_backup_locks pi.tests.backup.test_protected_json pi.tests.backup.test_local_backup_deploy
python3 "$repo/pi/tests/backup/test_icloud_backup.py"
python3 "$repo/pi/tests/backup/test_icloud_status.py"
bash -n "$repo/pi/scripts/backup/icloud_backup.sh"
conf_hash=$(local_backup_previous_conf_hash "$repo")
stage=$(ssh -o BatchMode=yes -o ConnectTimeout=8 "$target" /usr/bin/mktemp -d /tmp/vanpi-icloud.XXXXXX)
[[ "$stage" =~ ^/tmp/vanpi-icloud\.[[:alnum:]]{6}$ ]] || exit 1
scp -q "$repo/pi/scripts/backup/icloud_backup.py" \
  "$repo/pi/backup_local_deploy.sh" \
  "${LOCAL_BACKUP_FILES[@]/#/$repo/pi/scripts/backup/}" \
  "$repo/pi/scripts/backup/backup_locks.py" \
  "$repo/pi/scripts/backup/backup_priority.py" \
  "$repo/pi/scripts/backup/protected_json.py" \
  "$repo/pi/scripts/backup/backup_priority_control.py" \
  "$repo/pi/scripts/backup/mac_capture_control.py" \
  "$repo/pi/scripts/backup/time_machine_store.py" \
  "$repo/pi/scripts/backup/time_machine_staging.py" \
  "$repo/pi/scripts/backup/time_machine_icloud.py" \
  "$repo/pi/scripts/backup/time_machine_icloud.sh" \
  "$repo/pi/scripts/backup/time_machine_icloud_control.py" \
  "$repo/pi/scripts/backup/time_machine_icloud_status.py" \
  "$repo/pi/scripts/backup/time_machine_pipeline.py" \
  "$repo/pi/scripts/backup/time_machine_verification.py" \
  "$repo/pi/scripts/backup/time_machine_downloads.py" \
  "$repo/pi/scripts/backup/TIME_MACHINE_ICLOUD_RESTORE.txt" \
  "$repo/pi/scripts/backup/backup_conf.sh" \
  "$repo/pi/scripts/backup/icloud_progress.py" \
  "$repo/pi/scripts/backup/icloud_inventory.py" \
  "$repo/pi/scripts/backup/icloud_status.py" \
  "$repo/pi/scripts/backup/cloud_backup_control.py" \
  "$repo/pi/scripts/backup/icloud_backup_control.py" \
  "$repo/pi/scripts/backup/icloud_uplink.py" \
  "$repo/pi/scripts/backup/icloud_backup.sh" \
  "$repo/pi/scripts/backup/ICLOUD_RESTORE.txt" \
  "$repo/pi/configs/icloud-backup.json" \
  "$repo/pi/tmpfiles.d/vanpi-backup.conf" \
  "$repo/pi/services/vanpi-icloud-backup.service" \
  "$repo/pi/services/vanpi-icloud-backup.timer" \
  "$repo/pi/services/vanpi-icloud-backup-resume.service" \
  "$repo/pi/services/vanpi-icloud-backup-resume.timer" "$target:$stage/"
ssh -o BatchMode=yes -o ConnectTimeout=8 "$target" /bin/bash -s -- "$stage" "$conf_hash" "$tmpfiles_hash" "$local_previous" <<'REMOTE'
set -euo pipefail
stage=$1
[[ "$stage" =~ ^/tmp/vanpi-icloud\.[[:alnum:]]{6}$ ]] || exit 1
[[ -x /usr/local/bin/rclone ]]
. "$stage/backup_local_deploy.sh"
/usr/bin/python3 -m py_compile \
  "$stage/backup_locks.py" "$stage/icloud_backup.py" "$stage/icloud_uplink.py" \
  "$stage/icloud_progress.py" "$stage/icloud_status.py" "$stage/cloud_backup_control.py" \
  "$stage/icloud_backup_control.py" "$stage/time_machine_store.py" \
  "$stage/time_machine_staging.py" "$stage/time_machine_icloud.py" \
  "$stage/time_machine_icloud_control.py" "$stage/time_machine_icloud_status.py" \
  "$stage/time_machine_pipeline.py" "$stage/time_machine_verification.py" \
  "$stage/time_machine_downloads.py"
/bin/bash -n "$stage/icloud_backup.sh" "$stage/time_machine_icloud.sh"
/usr/bin/python3 -m json.tool "$stage/icloud-backup.json" >/dev/null
/usr/bin/systemd-analyze verify "$stage/vanpi-icloud-backup.service" "$stage/vanpi-icloud-backup.timer" "$stage/vanpi-icloud-backup-resume.service" "$stage/vanpi-icloud-backup-resume.timer"
# A running job must finish normally before updating its executable code. The
# exclusive legacy-lock fence below also requires every cloud worker to stop.
for unit in vanpi-icloud-backup.service vanpi-time-machine-icloud.service; do
  service_state=$(/usr/bin/systemctl show "$unit" --property=ActiveState --value)
  case "$service_state" in
    inactive|failed) ;;
    *) echo "Refusing to update active worker: $unit; the legacy-lock deployment fence requires both cloud workers to be inactive." >&2; exit 1 ;;
  esac
done
[[ ! -L /run/lock/vanpi_backup.lock && -f /run/lock/vanpi_backup.lock &&
   $(/usr/bin/stat -c '%U %G %a' /run/lock/vanpi_backup.lock) == 'root pi 660' ]] || exit 1
exec 9</run/lock/vanpi_backup.lock
/usr/bin/flock -n 9 || { echo 'Another backup is active; defer deployment.' >&2; exit 1; }
verify_local_backup_payload "$stage" "$4"
verify_local_backup_tmpfiles "$stage/vanpi-backup.conf" "$3"
live_conf_hash=$(sha256sum /home/pi/scripts/backup/backup_conf.sh | awk '{print $1}')
new_conf_hash=$(sha256sum "$stage/backup_conf.sh" | awk '{print $1}')
[[ "$live_conf_hash" == "$2" || "$live_conf_hash" == "$new_conf_hash" ]] || { echo 'Live backup configuration differs; inspect before deploying.' >&2; exit 1; }
/usr/bin/install -d -m 0700 "$stage/previous"
[[ ! -e /etc/tmpfiles.d/vanpi-backup.conf ]] || /usr/bin/cp -p /etc/tmpfiles.d/vanpi-backup.conf "$stage/previous/vanpi-backup.conf"
/usr/bin/sudo -n /usr/bin/install -o root -g root -m 0644 "$stage/vanpi-backup.conf" /etc/tmpfiles.d/vanpi-backup.conf
/usr/bin/sudo -n /usr/bin/systemd-tmpfiles --create /etc/tmpfiles.d/vanpi-backup.conf
[[ -d /run/lock/vanpi-backup && ! -L /run/lock/vanpi-backup &&
   $(/usr/bin/stat -c '%U %G %a' /run/lock/vanpi-backup) == 'root pi 770' ]] || exit 1
for name in "${LOCAL_BACKUP_FILES[@]}" backup_locks.py time_machine_store.py time_machine_staging.py time_machine_icloud.py time_machine_icloud.sh time_machine_icloud_control.py time_machine_icloud_status.py time_machine_pipeline.py time_machine_verification.py time_machine_downloads.py protected_json.py backup_priority.py mac_capture_control.py icloud_status.py cloud_backup_control.py backup_priority_control.py icloud_backup_control.py icloud_inventory.py icloud_progress.py icloud_backup.py icloud_uplink.py icloud_backup.sh backup_conf.sh ICLOUD_RESTORE.txt TIME_MACHINE_ICLOUD_RESTORE.txt; do
  live=/home/pi/scripts/backup/$name
  [[ ! -e "$live" ]] || /usr/bin/cp -p "$live" "$stage/previous/$name"
  /usr/bin/sudo -n /usr/bin/install -o pi -g pi -m 0750 "$stage/$name" "$live.new"
  /usr/bin/sudo -n /usr/bin/mv -Tf "$live.new" "$live"
done
if [[ ! -e /etc/vanpi-icloud-backup.json ]]; then
  /usr/bin/sudo -n /usr/bin/install -o root -g root -m 0600 "$stage/icloud-backup.json" /etc/vanpi-icloud-backup.json
fi
# Add newly introduced defaults without overwriting any existing preference.
/usr/bin/sudo -n /usr/bin/python3 - "$stage/icloud-backup.json" <<'PY'
import json,os,stat,sys,tempfile
from pathlib import Path
p=Path('/etc/vanpi-icloud-backup.json');s=p.lstat()
assert stat.S_ISREG(s.st_mode) and s.st_uid==0 and not s.st_mode&0o022
old=p.read_bytes();current=json.loads(old);defaults=json.loads(Path(sys.argv[1]).read_text())
updated=dict(defaults,**current)
if updated != current:
    fd,tmp=tempfile.mkstemp(prefix='.icloud-config-',dir=p.parent)
    try:
        with os.fdopen(fd,'w') as f:
            json.dump(updated,f,indent=2);f.write('\n');f.flush();os.fsync(f.fileno())
        assert p.read_bytes()==old,'Settings changed concurrently'
        os.replace(tmp,p)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)
PY
for name in vanpi-icloud-backup.service vanpi-icloud-backup.timer vanpi-icloud-backup-resume.service vanpi-icloud-backup-resume.timer; do
  [[ ! -e /etc/systemd/system/$name ]] || /usr/bin/cp -p /etc/systemd/system/"$name" "$stage/previous/$name"
  /usr/bin/sudo -n /usr/bin/install -o root -g root -m 0644 "$stage/$name" /etc/systemd/system/"$name"
done
/usr/bin/sudo -n /usr/bin/systemctl daemon-reload
/usr/bin/sudo -n /bin/bash /home/pi/scripts/backup/icloud_backup.sh --preflight
/usr/bin/sudo -n /usr/bin/systemctl enable --now vanpi-icloud-backup.timer vanpi-icloud-backup-resume.timer
/usr/bin/systemctl list-timers --all vanpi-icloud-backup.timer --no-pager
echo "Rollback: restore every file from $stage/previous with the install/mv primitives under the exclusive legacy lock; the retired fairness record /var/lib/vanpi-backup-priority/local-fairness.json is ignored by the new code and remains valid for the old code."
REMOTE
