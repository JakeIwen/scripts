#!/bin/bash
# Exercise CLI paths with command doubles only; no real mount or sudo calls.
set -u
repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)
source "$repo_root/pi/tests/lib.sh"
export MOUNT_DISKS_LIBRARY_ONLY=1 UMOUNT_DISKS_LIBRARY_ONLY=1
source "$repo_root/pi/scripts/mount_disks.sh"
source "$repo_root/pi/scripts/umount_disks.sh"
test_root=$(mktemp -d)
trap 'rm -rf "$test_root"' EXIT
calls="$test_root/calls"
mounted="$test_root/mounted"
fake_device="$test_root/device"
touch "$fake_device" "$calls"
# The only hardware predicate replaced is the final block-device test. A
# regular fixture stands in for that device; all command execution is doubled.
body=$(declare -f mntdsk)
body=${body//\! -b/\! -f}
eval "$body"
md_fix_hfs_mounts() { :; }
md_print_mounts() { :; }
md_parent_disk() { [[ "$1" == /root ]] && echo /rootdisk || echo /usbdisk; }
md_check_existing_mount() { return 1; }
md_require_empty_mount_dir() { return 0; }
md_clear_samba_drain() { echo samba >> "$calls"; }
md_resolve_label() {
  echo "resolve:$1:$2" >> "$calls"
  [[ "$1" != absent ]] || return 1
  MD_DEVICE=$fake_device
  MD_LABEL_KEY=LABEL
}
function /usr/bin/findmnt {
  case "$*" in
    *'-T / -o SOURCE'*) echo /root ;;
    *'-S '*'-o TARGET'*) [[ -f "$mounted" ]] && echo /mnt/xf-500 || return 1 ;;
    *) fail "unexpected findmnt: $*" ;;
  esac
}
function /usr/bin/lsblk { echo 0; }
function /usr/bin/id { echo 1000; }
function /usr/bin/sudo {
  echo "sudo:$*" >> "$calls"
  case "$*" in
    *'blkid -s TYPE'*) echo exfat ;;
    *'blkid -s LABEL'*) echo xf-500 ;;
    *'/usr/bin/mount -t exfat '*) touch "$mounted" ;;
    *'/usr/bin/install '*|*'/usr/sbin/modprobe '*|*'/usr/bin/rmdir '*) : ;;
    *) fail "unexpected sudo: $*" ;;
  esac
}
mount_disks_main xf-500 >"$test_root/output" 2>&1 || { cat "$test_root/output"; fail "explicit ad-hoc mount failed"; }
[[ -f "$mounted" ]] || fail "ad-hoc path never mounted"
grep -q 'resolve:xf-500:label-only' "$calls" || fail "ad-hoc mount used PARTLABEL"
grep -q 'uid=1000,gid=1000' "$calls" || fail "mount lost pi ownership options"

for label in '' . .. a..b ../bad a/b -bad $'bad\nlabel' "$(printf '%065d' 0)"; do
  : > "$calls"
  mount_disks_main "$label" >"$test_root/output" 2>&1 && fail "accepted invalid label"
  grep -q 'unsafe disk label\|usage:' "$test_root/output" || fail "missing invalid-label diagnostic"
  [[ ! -s "$calls" ]] || fail "invalid label reached device commands"
done
mntdsk xf-500 >"$test_root/output" 2>&1 && fail "automatic path accepted unlisted label"
grep -q 'not in disk_policy.sh.*MANUAL_MOUNT_LABELS.*s mount_disks xf-500' "$test_root/output" || fail "missing managed refusal advice"
mount_disks_main absent >"$test_root/output" 2>&1 && fail "missing explicit label succeeded"
grep -q 'no exact LABEL or PARTLABEL match for: absent' "$test_root/output" || fail "missing discovery diagnostic"
md_resolve_label() { return 1; }
mntdsk movingparts >/dev/null 2>&1 || fail "missing automatic optional disk failed"

# These public managed-only commands must refuse before any sudo/device work.
for command in diskctl samba_share_control.sh; do
  action=mount
  [[ "$command" == samba_share_control.sh ]] && action=drain
  /bin/bash "$repo_root/pi/scripts/$command" "$action" xf-500 >"$test_root/output" 2>&1
  [[ $? == 2 ]] || fail "$command accepted an unlisted label"
  grep -q 'not in disk_policy.sh.*s mount_disks' "$test_root/output" || fail "$command lacked shared refusal advice"
done
disk_eject_hold_set xf-500 60 >"$test_root/output" 2>&1 && fail "unlisted eject hold succeeded"
grep -q 'not in disk_policy.sh.*s mount_disks' "$test_root/output" || fail "eject hold lacked refusal advice"
stale_mount_parse_records $'xf-500\t/mnt/xf-500\t/dev/sdc1' >"$test_root/output" 2>&1 && fail "unlisted stale recovery succeeded"
grep -q 'not in disk_policy.sh.*s mount_disks' "$test_root/output" || fail "stale recovery lacked refusal advice"

# CLI preflight discovery is isolated, but its production orchestration and
# ad-hoc unmount phase run unchanged. Managed phases must never be entered.
ud_resolve_label() { UD_DEVICE=$fake_device; UD_PARENT=/usbdisk; UD_MOUNTS=/mnt/xf-500; }
ud_findmnt_exact_source() { echo "$fake_device"; }
ud_sync_mount() { echo sync >> "$calls"; }
ud_normal_unmount() { echo "umount:$1" >> "$calls"; return "${unmount_status:-0}"; }
ud_findmnt_source_targets() { return 1; }
ud_unmount_all() { fail "ad-hoc path entered managed Samba/fence phases"; }
ud_spindown() { fail "ad-hoc path entered spindown"; }
adhoc_unmount() {
  if (( BASH_VERSINFO[0] >= 4 )); then
    umount_disks_main xf-500
  else
    # The Linux CLI uses associative arrays; test its portable phase here.
    # The Linux lane exercises CLI preflight and phase selection as well.
    local dry_run=0
    ud_unmount_adhoc xf-500 "$fake_device"
  fi
}
: > "$calls"
adhoc_unmount >"$test_root/output" 2>&1 || fail "ad-hoc unmount failed"
grep -q 'umount:/mnt/xf-500' "$calls" || fail "ordinary unmount not called"
unmount_status=32
adhoc_unmount >"$test_root/output" 2>&1 && fail "busy ad-hoc unmount succeeded"
grep -q 'possibly busy' "$test_root/output" || fail "busy refusal lacked diagnostic"
[[ $(grep -c '^umount:' "$calls") == 2 ]] || fail "busy unmount retried or escalated"
if (( BASH_VERSINFO[0] >= 4 )); then
  umount_disks_main --spindown xf-500 >"$test_root/output" 2>&1 && fail "unlisted spindown succeeded"
  grep -q 'not in disk_policy.sh.*s mount_disks' "$test_root/output" || fail "spindown missing hand-mount advice"
else
  echo 'SKIP: ad-hoc CLI orchestration and spindown need Linux Bash 4+; portable phase tested'
fi

# The shutdown check reports advice only after verifying the filesystem LABEL.
function /usr/bin/sudo { [[ "$*" == *'blkid -s LABEL'* ]] || fail "unexpected shutdown command"; echo xf-500; }
output=$(ud_report_remaining_mounts $'/dev/sdc1 /mnt/xf-500\n/dev/sdd1 /elsewhere' 2>&1)
[[ "$output" == *'xf-500 (/dev/sdc1) is still mounted at /mnt/xf-500; unmount it first: s umount_disks xf-500'* ]] || fail "missing exact shutdown fix"
[[ "$output" == *'/dev/sdd1 is still mounted at /elsewhere; unmount it first'* ]] || fail "missing generic shutdown fix"
echo 'PASS: ad-hoc mount, ordinary unmount, and shutdown refusal diagnostics'
