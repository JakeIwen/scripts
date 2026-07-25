#!/bin/bash

set -u

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)
script="$repo_root/pi/scripts/umount_disks.sh"
abort_script="$repo_root/pi/scripts/backup/abort_backup.sh"

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

export UMOUNT_DISKS_LIBRARY_ONLY=1
# shellcheck source=../../scripts/umount_disks.sh
source "$script"

umount_disks_main --emergency >/dev/null 2>&1
[[ $? == 2 ]] ||
  fail "emergency mode was accepted without --spindown"
umount_disks_main --spindown --emergency movingparts >/dev/null 2>&1
[[ $? == 2 ]] ||
  fail "ignition emergency mode accepted a single-label target"

grep -Fq '"$samba_share_control" drain "${mounted_labels[@]}"' "$script" ||
  fail "disk shutdown does not block Samba reconnects before closure"
grep -Fq 'abort_backup.sh "${abort_args[@]}"' "$script" ||
  fail "disk shutdown does not pass emergency mode to backup termination"
grep -Fq 'ud_kill_torrent_client "$emergency"' "$script" ||
  fail "disk shutdown does not escalate the exact qBittorrent process"
grep -Fq 'ud_emergency_stop_samba' "$script" ||
  fail "disk shutdown has no emergency global Samba fallback"
grep -Fq 'ud_emergency_evict_mount_holders "$expected_mount"' "$script" ||
  fail "disk shutdown has no exact mount-holder eviction"
grep -Fq 'ud_sync_mount "$expected_mount"' "$script" ||
  fail "disk shutdown does not sync before normal unmount"
grep -Fq 'ud_normal_unmount "$expected_mount"' "$script" ||
  fail "disk shutdown bypasses the bounded normal-unmount helper"
grep -Fq '/usr/bin/fuser -k -KILL "$job_lock"' "$abort_script" ||
  fail "emergency backup shutdown does not target exact lock holders"

if grep -Eq '/usr/bin/umount.*(--force|[[:space:]]-f|--lazy|[[:space:]]-l)' "$script"; then
  fail "disk shutdown reintroduced force or lazy unmount"
fi

echo "PASS: emergency disk shutdown escalates consumers without force/lazy unmount"
