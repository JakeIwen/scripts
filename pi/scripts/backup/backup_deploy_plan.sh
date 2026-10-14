#!/bin/bash
# Canonical deployment records, in dependency order. Fields are:
# profile | checkout source | absolute destination | predecessor policy |
# owner:group | mode (or preserve) | first install (new/optional/required).
# "new" permits a missing live file only when the previous tree lacked it;
# even then an existing file must equal the staged source. "required" forbids
# first install. "replace" retains the installers' managed document/unit policy.
BACKUP_DEPLOY_PLAN=(
  'both|pi/scripts/backup/backup_lock_registry.sh|/home/pi/scripts/backup/backup_lock_registry.sh|revision|pi:pi|750|new'
  'both|pi/scripts/backup/backup_lock_io.sh|/home/pi/scripts/backup/backup_lock_io.sh|revision|pi:pi|750|new'
  'both|pi/scripts/backup/backup_locks.sh|/home/pi/scripts/backup/backup_locks.sh|revision|pi:pi|750|new'
  'both|pi/scripts/backup/backup_lifecycle.sh|/home/pi/scripts/backup/backup_lifecycle.sh|revision|pi:pi|750|new'
  'both|pi/scripts/backup/stale_mount_recovery.sh|/home/pi/scripts/backup/stale_mount_recovery.sh|revision|pi:pi|750|new'
  'both|pi/scripts/backup/umount_disks_fence.sh|/home/pi/scripts/backup/umount_disks_fence.sh|revision|pi:pi|750|new'
  'both|pi/scripts/backup/backup_conf.sh|/home/pi/scripts/backup/backup_conf.sh|conf|pi:pi|750|new'
  'both|pi/scripts/backup/protected_json.py|/home/pi/scripts/backup/protected_json.py|revision|pi:pi|750|new'
  'both|pi/scripts/backup/backup_locks.py|/home/pi/scripts/backup/backup_locks.py|revision|pi:pi|750|new'
  'both|pi/scripts/backup/backup_admission.py|/home/pi/scripts/backup/backup_admission.py|revision|pi:pi|750|new'
  'both|pi/scripts/backup/backup_priority.py|/home/pi/scripts/backup/backup_priority.py|revision|pi:pi|750|new'
  'both|pi/scripts/backup/cloud_backup_control.py|/home/pi/scripts/backup/cloud_backup_control.py|revision|pi:pi|750|new'
  'both|pi/scripts/backup/backup_window.sh|/home/pi/scripts/backup/backup_window.sh|revision|pi:pi|750|new'
  'both|pi/scripts/backup/pi_backup.sh|/home/pi/scripts/backup/pi_backup.sh|revision|pi:pi|750|new'
  'both|pi/scripts/backup/exfat_snapshot.sh|/home/pi/scripts/backup/exfat_snapshot.sh|revision|pi:pi|750|new'
  'both|pi/scripts/backup/openwrt_backup.sh|/home/pi/scripts/backup/openwrt_backup.sh|revision|pi:pi|750|new'
  'both|pi/scripts/backup/abort_backup.sh|/home/pi/scripts/backup/abort_backup.sh|revision|pi:pi|750|new'
  'both|pi/scripts/backup/clone_now.sh|/home/pi/scripts/backup/clone_now.sh|revision|pi:pi|750|new'
  'both|pi/scripts/backup/time_machine_icloud.py|/home/pi/scripts/backup/time_machine_icloud.py|revision|pi:pi|750|new'
  'both|pi/scripts/backup/time_machine_icloud.sh|/home/pi/scripts/backup/time_machine_icloud.sh|revision|pi:pi|750|new'
  'both|pi/scripts/backup/time_machine_pipeline.py|/home/pi/scripts/backup/time_machine_pipeline.py|revision|pi:pi|750|new'
  'both|pi/scripts/backup/time_machine_staging.py|/home/pi/scripts/backup/time_machine_staging.py|revision|pi:pi|750|new'
  'both|pi/scripts/backup/time_machine_store.py|/home/pi/scripts/backup/time_machine_store.py|revision|pi:pi|750|new'
  'both|pi/scripts/backup/time_machine_verification.py|/home/pi/scripts/backup/time_machine_verification.py|revision|pi:pi|750|new'
  'both|pi/scripts/backup/time_machine_downloads.py|/home/pi/scripts/backup/time_machine_downloads.py|revision|pi:pi|750|new'
  'both|pi/scripts/backup/time_machine_icloud_control.py|/home/pi/scripts/backup/time_machine_icloud_control.py|revision|pi:pi|750|new'
  'both|pi/scripts/backup/time_machine_icloud_status.py|/home/pi/scripts/backup/time_machine_icloud_status.py|revision|pi:pi|750|new'
  'both|pi/scripts/backup/icloud_backup.sh|/home/pi/scripts/backup/icloud_backup.sh|revision|pi:pi|750|new'
  'both|pi/scripts/backup/icloud_progress.py|/home/pi/scripts/backup/icloud_progress.py|revision|pi:pi|750|new'
  'both|pi/scripts/backup/icloud_inventory.py|/home/pi/scripts/backup/icloud_inventory.py|revision|pi:pi|750|new'
  'both|pi/scripts/backup/icloud_uplink.py|/home/pi/scripts/backup/icloud_uplink.py|revision|pi:pi|750|new'
  'both|pi/scripts/backup/icloud_backup_control.py|/home/pi/scripts/backup/icloud_backup_control.py|revision|pi:pi|750|new'
  'both|pi/scripts/backup/mac_capture_control.py|/home/pi/scripts/backup/mac_capture_control.py|revision|pi:pi|750|new'
  'both|pi/scripts/backup/backup_priority_control.py|/home/pi/scripts/backup/backup_priority_control.py|revision|pi:pi|750|new'
  'both|pi/scripts/backup/icloud_backup.py|/home/pi/scripts/backup/icloud_backup.py|cloud|pi:pi|750|new'
  'both|pi/scripts/backup/icloud_status.py|/home/pi/scripts/backup/icloud_status.py|status|pi:pi|750|new'
  'both|pi/scripts/backup/ICLOUD_RESTORE.txt|/home/pi/scripts/backup/ICLOUD_RESTORE.txt|replace|pi:pi|750|optional'
  'both|pi/scripts/backup/TIME_MACHINE_ICLOUD_RESTORE.txt|/home/pi/scripts/backup/TIME_MACHINE_ICLOUD_RESTORE.txt|replace|pi:pi|750|optional'
  'both|pi/scripts/backup/backup_deploy_plan.sh|/home/pi/scripts/backup/backup_deploy_plan.sh|revision|pi:pi|750|new'
  'both|pi/scripts/backup/backup_deploy_transaction.sh|/home/pi/scripts/backup/backup_deploy_transaction.sh|revision|pi:pi|750|new'
  'both|pi/scripts/mount_disks.sh|/home/pi/scripts/mount_disks.sh|revision|pi:pi|preserve|required'
  'both|pi/scripts/umount_disks.sh|/home/pi/scripts/umount_disks.sh|revision|pi:pi|preserve|required'
  'both|pi/scripts/internet_switches.sh|/home/pi/scripts/internet_switches.sh|revision|pi:pi|preserve|required'
  'both|pi/tmpfiles.d/vanpi-backup.conf|/etc/tmpfiles.d/vanpi-backup.conf|revision|root:root|644|optional'
  'tm|pi/scripts/samba_share_control.sh|/home/pi/scripts/samba_share_control.sh|revision|pi:pi|750|required'
  'tm|pi/configs/time-machine-icloud.json|/etc/vanpi-time-machine-icloud.json|preserve|root:root|600|optional'
  'pi|pi/configs/icloud-backup.json|/etc/vanpi-icloud-backup.json|defaults|root:root|600|optional'
  'tm|pi/services/vanpi-time-machine-icloud.service|/etc/systemd/system/vanpi-time-machine-icloud.service|replace|root:root|644|optional'
  'tm|pi/services/vanpi-time-machine-icloud.timer|/etc/systemd/system/vanpi-time-machine-icloud.timer|replace|root:root|644|optional'
  'tm|pi/services/vanpi-time-machine-icloud-resume.service|/etc/systemd/system/vanpi-time-machine-icloud-resume.service|replace|root:root|644|optional'
  'tm|pi/services/vanpi-time-machine-icloud-resume.timer|/etc/systemd/system/vanpi-time-machine-icloud-resume.timer|replace|root:root|644|optional'
  'pi|pi/services/vanpi-icloud-backup.service|/etc/systemd/system/vanpi-icloud-backup.service|replace|root:root|644|optional'
  'pi|pi/services/vanpi-icloud-backup.timer|/etc/systemd/system/vanpi-icloud-backup.timer|replace|root:root|644|optional'
  'both|pi/services/vanpi-icloud-backup-resume.service|/etc/systemd/system/vanpi-icloud-backup-resume.service|replace|root:root|644|optional'
  'both|pi/services/vanpi-icloud-backup-resume.timer|/etc/systemd/system/vanpi-icloud-backup-resume.timer|replace|root:root|644|optional'
)

local_backup_plan() { # <pi|tm>
  local profile=$1 record scope
  [[ "$profile" == pi || "$profile" == tm ]] || return 1
  for record in "${BACKUP_DEPLOY_PLAN[@]}"; do
    scope=${record%%|*}
    [[ "$scope" == both || "$scope" == "$profile" ]] || continue
    printf '%s\n' "${record#*|}"
  done
}

local_backup_sources() { # <pi|tm>: checkout-relative transfer allowlist
  local plan
  plan=$(local_backup_plan "$1") || return 1
  printf '%s\n' "$plan" | /usr/bin/cut -d '|' -f 1
}
