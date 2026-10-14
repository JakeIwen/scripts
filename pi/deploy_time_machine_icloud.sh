#!/bin/bash
# Targeted offsite replication deployment; no repository-wide synchronization.
set -euo pipefail
repo=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
target=${1:-pi@vanpi.lan}
[[ $# -le 1 && "$target" != -* && "$target" != *[[:space:]]* ]] || exit 2
cd "$repo"
. "$repo/pi/backup_local_deploy.sh"
local_previous=$(local_backup_previous_hashes "$repo" tm)
payload=()
# The remote contract tests validate both fixed profiles; only tm is published.
while IFS= read -r source; do payload+=("$source"); done < <(
  { local_backup_sources tm; local_backup_sources pi; } | /usr/bin/sort -u
)
python3 pi/tests/backup/test_time_machine_icloud.py
python3 -m unittest pi.tests.backup.test_time_machine_staging
python3 pi/tests/backup/test_icloud_status.py
python3 pi/tests/backup/test_icloud_backup.py
PYTHONPATH=pi/scripts/backup python3 -m unittest pi.tests.backup.test_backup_locks pi.tests.backup.test_backup_admission pi.tests.backup.test_backup_priority pi.tests.backup.test_protected_json pi.tests.backup.test_local_backup_deploy.DeploymentPlanTests pi.tests.backup.test_local_backup_deploy.DeploymentPreflightTests pi.tests.backup.test_openwrt_schedule
PYTHONPATH=pi/scripts/backup python3 -m unittest pi.tests.backup.test_time_machine_pipeline
PYTHONPATH=pi/scripts/backup python3 -m unittest pi.tests.backup.test_time_machine_downloads pi.tests.backup.test_icloud_uplink
bash pi/tests/storage/test_samba_share_control.sh
bash -n pi/scripts/backup/time_machine_icloud.sh
stage=$(ssh -o BatchMode=yes -o ConnectTimeout=8 "$target" /usr/bin/mktemp -d /tmp/vanpi-tm-icloud.XXXXXX)
[[ "$stage" =~ ^/tmp/vanpi-tm-icloud\.[[:alnum:]]{6}$ ]] || exit 1
gate_hash=$(shasum -a 256 pi/scripts/samba_require_mount.sh | awk '{print $1}')
COPYFILE_DISABLE=1 /usr/bin/tar --no-xattrs -cf - \
  pi/__init__.py pi/tests/__init__.py pi/backup_local_deploy.sh \
  pi/deploy_icloud_backup.sh pi/deploy_time_machine_icloud.sh \
  "${payload[@]}" \
  pi/tests/backup/test_backup_locks.py pi/tests/backup/test_local_backup_deploy.py pi/tests/backup/test_openwrt_schedule.py pi/tests/backup/test_protected_json.py \
  pi/tests/backup/backup_lock_fixtures.py pi/tests/backup/test_backup_admission.py \
  pi/tests/backup/priority_fixtures.py pi/tests/backup/test_backup_priority.py \
  pi/tests/storage/test_backup_job_lock.sh pi/tests/storage/test_backup_locks.sh \
  pi/tests/backup/test_time_machine_staging.py pi/tests/backup/test_time_machine_downloads.py pi/tests/backup/test_icloud_uplink.py \
  pi/tests/backup/test_time_machine_pipeline.py pi/tests/backup/test_time_machine_icloud.py \
  pi/tests/backup/test_icloud_status.py pi/tests/backup/test_icloud_backup.py \
  pi/scripts/connectivity_status.py \
  pi/scripts/disk_policy.sh pi/scripts/remount.sh \
  pi/tests/lib.sh pi/tests/storage/test_samba_share_control.sh \
  macbook/scripts/time_machine_offsite_coordinator.py \
  | ssh -o BatchMode=yes "$target" /bin/tar -xf - -C "$stage"
ssh -o BatchMode=yes "$target" /bin/bash -s -- "$stage" "$gate_hash" "$local_previous" <<'REMOTE'
set -euo pipefail
export PYTHONDONTWRITEBYTECODE=1
stage=$1
[[ "$stage" =~ ^/tmp/vanpi-tm-icloud\.[[:alnum:]]{6}$ ]] || exit 1
cd "$stage"
. pi/backup_local_deploy.sh
/usr/bin/python3 pi/tests/backup/test_time_machine_icloud.py
/usr/bin/python3 -m unittest pi.tests.backup.test_time_machine_staging
/usr/bin/python3 pi/tests/backup/test_icloud_status.py
/usr/bin/python3 pi/tests/backup/test_icloud_backup.py
# Full fixture/failure coverage runs before deployment; repeat only the remote
# transfer and complete-success smokes here to keep the upload pause bounded.
PYTHONPATH=pi/scripts/backup /usr/bin/python3 -m unittest pi.tests.backup.test_backup_locks pi.tests.backup.test_backup_admission pi.tests.backup.test_backup_priority pi.tests.backup.test_protected_json pi.tests.backup.test_local_backup_deploy.DeploymentPlanTests.test_single_allowlist_drives_transfer_and_dependency_order pi.tests.backup.test_local_backup_deploy.DeploymentPreflightTests.test_both_profiles_publish_complete_private_backup_and_preserve_settings pi.tests.backup.test_openwrt_schedule
PYTHONPATH=pi/scripts/backup /usr/bin/python3 -m unittest pi.tests.backup.test_time_machine_pipeline
PYTHONPATH=pi/scripts/backup /usr/bin/python3 -m unittest pi.tests.backup.test_time_machine_downloads pi.tests.backup.test_icloud_uplink
bash pi/tests/storage/test_backup_job_lock.sh
bash pi/tests/storage/test_backup_locks.sh
bash pi/tests/storage/test_samba_share_control.sh
[[ $(sha256sum /home/pi/scripts/samba_require_mount.sh | awk '{print $1}') == "$2" ]] || { echo 'Samba mount gate differs; inspect before deploying.' >&2; exit 1; }
gate=$(sudo -n /usr/bin/testparm -s --section-name=mbp2tbkup --parameter-name=preexec 2>/dev/null)
[[ "$gate" == '/home/pi/scripts/samba_require_mount.sh mbp2tbkup' ]] || exit 1
close=$(sudo -n /usr/bin/testparm -s --section-name=mbp2tbkup --parameter-name='preexec close' 2>/dev/null)
[[ "$close" == Yes || "$close" == yes ]] || exit 1
/usr/bin/systemd-analyze verify pi/services/vanpi-*icloud*.service pi/services/vanpi-*icloud*.timer
local_backup_begin "$stage" tm "$3"
local_backup_publish
sudo -n /usr/bin/systemd-tmpfiles --create /etc/tmpfiles.d/vanpi-backup.conf
[[ -d /run/lock/vanpi-backup && ! -L /run/lock/vanpi-backup &&
   $(/usr/bin/stat -c '%U %G %a' /run/lock/vanpi-backup) == 'root pi 770' ]] || exit 1
sudo -n /usr/bin/systemctl daemon-reload
local_backup_commit
sudo -n /usr/bin/python3 -B /home/pi/scripts/backup/time_machine_icloud.py --capture-request
sudo -n /usr/bin/systemctl enable --now vanpi-time-machine-icloud.timer
sudo -n /usr/bin/systemctl enable --now vanpi-time-machine-icloud-resume.timer vanpi-icloud-backup-resume.timer
REMOTE
