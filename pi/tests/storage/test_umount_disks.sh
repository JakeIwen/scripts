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

ud_findmnt_source_targets() { return 1; }
reconcile_output=$(ud_reconcile_unmount_result hdd1tb /dev/sdz1 124 2>&1)
[[ $? == 0 ]] ||
  fail "a timed-out unmount was not accepted after exact-device findmnt reported it absent"
[[ "$reconcile_output" == *"findmnt verifies /dev/sdz1 is unmounted"* ]] ||
  fail "reconciled timed-out unmount did not explain its authoritative device check"

ud_findmnt_source_targets() { printf '%s\n' /mnt/hdd1tb; return 0; }
ud_reconcile_unmount_result hdd1tb /dev/sdz1 124 >/dev/null 2>&1 &&
  fail "a timed-out unmount was accepted while the exact device remained mounted"

ud_findmnt_source_targets() { echo "synthetic discovery error" >&2; return 2; }
ud_reconcile_unmount_result hdd1tb /dev/sdz1 124 >/dev/null 2>&1 &&
  fail "a timed-out unmount was accepted after findmnt failed"

# Restore the production helper for any later integration assertions.
ud_findmnt_source_targets() { /usr/bin/findmnt -rn -S "$1" -o TARGET; }

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
grep -Fq 'continuing with guarded unmount and exact holder eviction' "$script" ||
  fail "qBittorrent stop failure still prevents the guarded unmount attempt"
if grep -Fq 'qbittorrent-nox did not stop; refusing to unmount' "$script"; then
  fail "qBittorrent stop failure still terminates disk unmount"
fi
grep -Fq 'ud_emergency_stop_samba' "$script" ||
  fail "disk shutdown has no emergency global Samba fallback"
grep -Fq 'ud_evict_mount_holders "$expected_mount"' "$script" ||
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

# Ordinary shutdown must force an exact-name qBittorrent process down after its
# grace period instead of treating graceful-stop failure as the terminal state.
qbit_state=running
qbit_signals=
qbit_waits=0
ud_qbit_is_running() {
  [[ "$qbit_state" == running ]]
}
ud_signal_qbit() {
  qbit_signals+="${qbit_signals:+ }$1"
  [[ "$1" == KILL ]] && qbit_state=stopped
  return 0
}
ud_qbit_wait_one_second() {
  ((qbit_waits += 1))
}
UD_QBIT_GRACE_SECONDS=2
ud_kill_torrent_client 0 >/dev/null 2>&1 ||
  fail "ordinary shutdown did not recover from a stuck qBittorrent process"
[[ "$qbit_signals" == "TERM KILL" ]] ||
  fail "ordinary shutdown did not escalate qBittorrent from TERM to KILL"
[[ "$qbit_waits" == 2 ]] ||
  fail "ordinary shutdown did not honor the bounded qBittorrent grace period"

# A failed ordinary unmount must evict exact-mount holders with TERM and KILL.
holder_calls=$(mktemp)
trap 'rm -f "$holder_calls"' EXIT
ud_mount_holder_summary() {
  printf '%s\n' "pi 123 f.... vlc"
}
ud_signal_mount_holders() {
  printf '%s %s\n' "$2" "$1" >> "$holder_calls"
  return 0
}
ud_holder_wait() { :; }
ud_evict_mount_holders /mnt/movingparts >/dev/null 2>&1 ||
  fail "ordinary shutdown mount-holder eviction returned failure"
[[ $(cat "$holder_calls") == $'TERM /mnt/movingparts\nKILL /mnt/movingparts' ]] ||
  fail "ordinary shutdown did not escalate exact mount holders from TERM to KILL"

if grep -Fq 'if (( rc != 0 && emergency ))' "$script"; then
  fail "mount-holder eviction is still restricted to ignition emergency mode"
fi

echo "PASS: guarded disk shutdown escalates consumers without force/lazy unmount"
