#!/bin/bash
# Targeted offsite replication deployment; no repository-wide synchronization.
set -euo pipefail
repo=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
check_only=0
target=pi@vanpi.lan
target_set=0
for argument in "$@"; do
  case "$argument" in
    --check) [[ "$check_only" == 0 ]] || exit 2; check_only=1 ;;
    -*) exit 2 ;;
    *) [[ "$target_set" == 0 && -n "$argument" && "$argument" != *[[:space:]]* ]] || exit 2
       target=$argument; target_set=1 ;;
  esac
done
python="$repo/.local/test-venv/bin/python"
[[ -x "$python" ]] || { echo 'Missing .local/test-venv/bin/python; prepare the repository test environment.' >&2; exit 1; }
cd "$repo"
"$python" run_tests.py
export PYTHONPATH="$repo:$repo/pi/scripts/backup"
"$python" -m pi.generate_backup_lock_registry --check
. "$repo/pi/backup_local_deploy.sh"
local_previous=$(local_backup_previous_hashes "$repo" tm)
payload_sources=$("$python" pi/time_machine_deploy_checks.py --files)
payload=()
while IFS= read -r source; do payload+=("$source"); done <<< "$payload_sources"
bash -n pi/scripts/backup/time_machine_icloud.sh
stage=$(ssh -o BatchMode=yes -o ConnectTimeout=8 "$target" /usr/bin/mktemp -d /tmp/vanpi-tm-icloud.XXXXXX)
[[ "$stage" =~ ^/tmp/vanpi-tm-icloud\.[[:alnum:]]{6}$ ]] || exit 1
gate_hash=$(shasum -a 256 pi/scripts/samba_require_mount.sh | awk '{print $1}')
COPYFILE_DISABLE=1 /usr/bin/tar --no-xattrs -cf - "${payload[@]}" \
  | ssh -o BatchMode=yes "$target" /bin/tar -xf - -C "$stage"
ssh -o BatchMode=yes "$target" /bin/bash -s -- "$stage" "$gate_hash" "$local_previous" "$check_only" <<'REMOTE'
set -euo pipefail
export PYTHONDONTWRITEBYTECODE=1
stage=$1
[[ "$stage" =~ ^/tmp/vanpi-tm-icloud\.[[:alnum:]]{6}$ ]] || exit 1
cd "$stage"
[[ -x /home/pi/scripts/vanpi-metered ]] || { echo 'Install the metering package and CLI before cloud workers.' >&2; exit 1; }
/home/pi/scripts/vanpi-metered config --json >/dev/null
printf '{}\n' | /home/pi/scripts/vanpi-metered admit pi.time_machine_cloud >/dev/null
export PYTHONPATH="$stage:/home/pi/scripts/python-packages/current:$stage/pi/scripts/backup"
/usr/bin/python3 -m pi.generate_backup_lock_registry --check
. pi/backup_local_deploy.sh
/usr/bin/python3 -B pi/time_machine_deploy_checks.py --run
[[ $(sha256sum /home/pi/scripts/samba_require_mount.sh | awk '{print $1}') == "$2" ]] || { echo 'Samba mount gate differs; inspect before deploying.' >&2; exit 1; }
# icloud_uplink.py imports the flat connectivity helper that only the broad sync
# publishes; a stale live copy breaks every cloud attempt's route check.
[[ $(sha256sum /home/pi/scripts/connectivity_status.py | awk '{print $1}') == $(sha256sum pi/scripts/connectivity_status.py | awk '{print $1}') ]] || { echo 'connectivity_status.py on the Pi differs from the checkout; run the non-backup pi/sync_scripts.sh first so icloud_uplink.py and its helper match.' >&2; exit 1; }
gate=$(sudo -n /usr/bin/testparm -s --section-name=mbp2tbkup --parameter-name=preexec 2>/dev/null)
[[ "$gate" == '/home/pi/scripts/samba_require_mount.sh mbp2tbkup' ]] || exit 1
close=$(sudo -n /usr/bin/testparm -s --section-name=mbp2tbkup --parameter-name='preexec close' 2>/dev/null)
[[ "$close" == Yes || "$close" == yes ]] || exit 1
/usr/bin/systemd-analyze verify pi/services/vanpi-*icloud*.service pi/services/vanpi-*icloud*.timer
sudo -n /usr/bin/python3 pi/scripts/backup/backup_schedule.py --check
if [[ "$4" == 1 ]]; then
  local_backup_check_bootstrap "$stage"
  printf 'Target checks passed; no publication or activation. Stage: %s\n' "$stage"
  exit 0
fi
local_backup_begin "$stage" tm "$3"
local_backup_publish
sudo -n /usr/bin/systemd-tmpfiles --create /etc/tmpfiles.d/vanpi-backup.conf /etc/tmpfiles.d/vanpi-samba-drain.conf
[[ -d /run/lock/vanpi-backup && ! -L /run/lock/vanpi-backup &&
   $(/usr/bin/stat -c '%U %G %a' /run/lock/vanpi-backup) == 'root pi 770' ]] || exit 1
sudo -n /usr/bin/systemctl daemon-reload
local_backup_commit
sudo -n /usr/bin/python3 /home/pi/scripts/backup/backup_schedule.py --apply
sudo -n /usr/bin/python3 -B /home/pi/scripts/backup/time_machine_icloud.py --capture-request
sudo -n /usr/bin/systemctl enable --now vanpi-time-machine-icloud.timer
sudo -n /usr/bin/systemctl enable --now vanpi-time-machine-icloud-resume.timer vanpi-icloud-backup-resume.timer
REMOTE
