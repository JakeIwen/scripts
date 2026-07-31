#!/bin/bash

set -u

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)
snapshot_script="$repo_root/pi/scripts/backup/exfat_snapshot.sh"
window_script="$repo_root/pi/scripts/backup/backup_window.sh"
test_root=$(mktemp -d)
trap 'rm -rf "$test_root"' EXIT

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

for required in /usr/bin/rsync /usr/bin/find /usr/bin/date /usr/bin/flock; do
  [[ -x "$required" ]] || {
    echo "SKIP: EXFAT snapshot test requires GNU/Linux backup tools"
    exit 0
  }
done

stamp_dir="$test_root/stamps"
source_mnt="$test_root/source"
target_mnt="$test_root/target"
snapshot_root="$target_mnt/backups"
mkdir -p "$stamp_dir" "$source_mnt" "$snapshot_root"

fake_conf="$test_root/backup_conf.sh"
cat > "$fake_conf" <<EOF
STAMP_DIR='$stamp_dir'
IGNITION_FLAG='$test_root/ignition'
NTFY_ON_SUCCESS=0
EXFAT_SNAPSHOT_SOURCE_LABEL=EXFAT512
EXFAT_SNAPSHOT_SOURCE_MNT='$source_mnt'
EXFAT_SNAPSHOT_DISK_LABEL=hdd1tb
EXFAT_SNAPSHOT_MNT='$target_mnt'
EXFAT_SNAPSHOT_ROOT='$snapshot_root'
EXFAT_SNAPSHOT_PREFIX=EXFAT512_
EXFAT_SNAPSHOT_STAMP='$stamp_dir/exfat512_ok'
EXFAT_SNAPSHOT_STALE_HOURS=48
EXFAT_SNAPSHOT_MIN_FREE_GB=1
EXFAT_SNAPSHOT_KEEP_DAILY_DAYS=30
EXFAT_SNAPSHOT_KEEP_WEEKLY_DAYS=84
EXFAT_SNAPSHOT_KEEP_MONTHLY_DAYS=365
acquire_job_lock() {
  exec 9>'$test_root/job.lock'
  /usr/bin/flock -n 9
}
EOF

export EXFAT_SNAPSHOT_LIBRARY_ONLY=1
export EXFAT_SNAPSHOT_CONF="$fake_conf"
export EXFAT_SNAPSHOT_POLICYCTL=/usr/bin/true
export EXFAT_SNAPSHOT_DISKCTL=/usr/bin/true
export EXFAT_SNAPSHOT_UMOUNT_DISKS=/usr/bin/true
export EXFAT_SNAPSHOT_NOTIFY=/usr/bin/true
# shellcheck source=../../scripts/backup/exfat_snapshot.sh
source "$snapshot_script"

# The selected rsync mode must share an unchanged file's inode, while a changed
# source file creates a new inode and leaves the earlier snapshot untouched.
mkdir "$test_root/previous" "$test_root/next" "$test_root/changed"
printf 'frozen audiobook\n' > "$source_mnt/book.m4b"
/usr/bin/rsync -rt -- "$source_mnt/" "$test_root/previous/" ||
  fail "initial rsync fixture failed"
/usr/bin/rsync -rt --link-dest="$test_root/previous" -- \
  "$source_mnt/" "$test_root/next/" || fail "hard-link rsync fixture failed"
previous_inode=$(stat -c %i "$test_root/previous/book.m4b")
next_inode=$(stat -c %i "$test_root/next/book.m4b")
[[ "$previous_inode" == "$next_inode" ]] ||
  fail "unchanged file was not hard-linked to the previous snapshot"
printf 'changed audiobook\n' > "$source_mnt/book.m4b"
/usr/bin/rsync -rt --link-dest="$test_root/next" -- \
  "$source_mnt/" "$test_root/changed/" || fail "changed rsync fixture failed"
[[ $(cat "$test_root/previous/book.m4b") == "frozen audiobook" ]] ||
  fail "changed source content modified an earlier hard-link snapshot"
[[ $(stat -c %i "$test_root/changed/book.m4b") != "$previous_inode" ]] ||
  fail "changed file reused the earlier snapshot inode"

make_snapshot() {
  local age_days=$1 offset_minutes=$2 epoch name path
  epoch=$((EXFAT_SNAPSHOT_NOW - age_days * 86400 - offset_minutes * 60))
  name="EXFAT512_$(/usr/bin/date -d "@$epoch" '+%F_%H-%M')"
  path="$snapshot_root/$name"
  mkdir "$path"
  printf 'complete\n' > "$path/.vanpi_snapshot_complete"
  printf '%s\n' "$name"
}

EXFAT_SNAPSHOT_NOW=$(/usr/bin/date -d '2026-07-31 12:00:00' +%s)
newest=$(make_snapshot 0 0)
daily=$(make_snapshot 10 0)
weekly_new=$(make_snapshot 40 0)
weekly_old=$(make_snapshot 40 60)
monthly_new=$(make_snapshot 100 0)
monthly_old=$(make_snapshot 100 60)
expired=$(make_snapshot 400 0)

prune_snapshots || fail "retention pruning reported failure"
for kept in "$newest" "$daily" "$weekly_new" "$monthly_new"; do
  [[ -d "$snapshot_root/$kept" ]] || fail "retention removed $kept"
done
for removed in "$weekly_old" "$monthly_old" "$expired"; do
  [[ ! -e "$snapshot_root/$removed" ]] || fail "retention kept expired $removed"
done

# The window runner must try the EXFAT job even if the existing Borg job fails,
# and must pass --force to both jobs for a dashboard/manual run.
window_root="$test_root/window"
mkdir "$window_root"
cp "$window_script" "$window_root/backup_window.sh"
cat > "$window_root/pi_backup.sh" <<'EOF'
#!/bin/bash
printf 'pi:%s\n' "$*" >> "$TEST_WINDOW_CALLS"
exit 7
EOF
cat > "$window_root/exfat_snapshot.sh" <<'EOF'
#!/bin/bash
printf 'exfat:%s\n' "$*" >> "$TEST_WINDOW_CALLS"
EOF
chmod +x "$window_root/"*.sh
window_calls="$test_root/window.calls"
TEST_WINDOW_CALLS="$window_calls" "$window_root/backup_window.sh" --force >/dev/null 2>&1
window_status=$?
[[ $window_status == 1 ]] || fail "window runner hid a child backup failure"
[[ $(cat "$window_calls") == $'pi:--force\nexfat:--force' ]] ||
  fail "window runner did not run and force both independently stamped jobs"

grep -Fq -- "--link-dest=\$previous_path" "$snapshot_script" ||
  fail "production snapshot script does not link against the previous snapshot"
grep -Fq -- "--spindown \"\$EXFAT_SNAPSHOT_DISK_LABEL\"" "$snapshot_script" ||
  fail "production snapshot does not explicitly stop its HDD"

echo "PASS: EXFAT hard-link snapshot retention and backup-window isolation"
