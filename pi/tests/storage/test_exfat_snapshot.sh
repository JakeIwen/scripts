#!/bin/bash

set -u

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)
snapshot_script="$repo_root/pi/scripts/backup/exfat_snapshot.sh"
window_script="$repo_root/pi/scripts/backup/backup_window.sh"
test_root=$(mktemp -d)
trap 'rm -rf "$test_root"' EXIT

# shellcheck source=../lib.sh
source "$repo_root/pi/tests/lib.sh"

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
EXFAT_SNAPSHOT_TELEMETRY_SECONDS=1
acquire_backup_job() {
  exec 9>'$test_root/job.lock'
  /usr/bin/flock -n 9
}
release_backup_disk() { # idempotent like the real helper: cleanup releases twice
  { true >&9; } 2>/dev/null || return 0
  exec 9>&-
}
EOF

export EXFAT_SNAPSHOT_LIBRARY_ONLY=1
export EXFAT_SNAPSHOT_CONF="$fake_conf"
export EXFAT_SNAPSHOT_POLICYCTL=/usr/bin/true
export EXFAT_SNAPSHOT_DISKCTL=/usr/bin/true
export EXFAT_SNAPSHOT_UMOUNT_DISKS=/usr/bin/true
export EXFAT_SNAPSHOT_NOTIFY=/usr/bin/true
export EXFAT_SNAPSHOT_SMARTCTL=/usr/bin/true
export EXFAT_SNAPSHOT_PROGRESS_HELPER="$repo_root/pi/scripts/backup/progress.sh"
fake_block_root="$test_root/sys/class/block"
mkdir -p "$fake_block_root/fakeblock"
printf '%s\n' '100 0 200 300 400 0 500 600 0 700 800 0 0 0 0 0 0' \
  > "$fake_block_root/fakeblock/stat"
export EXFAT_SNAPSHOT_BLOCK_STAT_ROOT="$fake_block_root"
# shellcheck source=../../scripts/backup/exfat_snapshot.sh
source "$snapshot_script"

# Telemetry must sample block deltas while the command runs, then terminate its
# sampler promptly instead of leaving a five-minute sleep behind.
(
  /usr/bin/sleep 0.4
  printf '%s\n' '110 0 2248 1300 420 0 4596 2600 0 1700 2800 0 0 0 0 0 0' \
    > "$fake_block_root/fakeblock/stat"
) &
telemetry_fixture_pid=$!
telemetry_output=$(run_snapshot_rsync fakeblock /usr/bin/sleep 2.2)
telemetry_status=$?
wait "$telemetry_fixture_pid"
[[ $telemetry_status == 0 ]] || fail "telemetry wrapper changed command success"
[[ "$telemetry_output" == *"telemetry target=fakeblock"* &&
   "$telemetry_output" == *"read_ops=10 read_mib=1 read_avg_ms=100"* &&
   "$telemetry_output" == *"write_ops=20 write_mib=2 write_avg_ms=100"* &&
   "$telemetry_output" == *"telemetry finished: target=fakeblock"* ]] ||
  fail "telemetry wrapper did not report the expected block-I/O deltas"

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

# The real entry point uses the isolated mount fixture and actual rsync binary.
mkdir -p "$source_mnt/van-tv/.cache" "$source_mnt/keep"
printf 'video buffer\n' > "$source_mnt/van-tv/.cache/segment"
printf 'retain this\n' > "$source_mnt/keep/file.txt"
resolve_label() {
  case "$1" in
    EXFAT512) printf '%s\n' "$source_mnt" ;;
    hdd1tb) printf '%s\n' "$target_mnt" ;;
    *) return 1 ;;
  esac
}
verify_exact_mount() { return 0; }
ensure_backup_mount() { backup_disk_active=1; return 0; }
snapshot_parent_device() { printf '%s\n' /dev/fakeblock; }
log_snapshot_smart() { :; }
stop_backup_disk() { backup_disk_active=0; return 0; }
findmnt_command="$test_root/findmnt"
cat > "$findmnt_command" <<EOF
#!/bin/bash
printf '%s\\n' '$source_mnt'
EOF
chmod +x "$findmnt_command"
main || fail "snapshot main entry point failed"
mapfile -t main_snapshots < <(list_completed_snapshots)
[[ ${#main_snapshots[@]} == 1 ]] || fail "snapshot main did not publish one completed tree"
main_snapshot="$snapshot_root/${main_snapshots[0]}"
[[ ! -e "$main_snapshot/van-tv" ]] ||
  fail "snapshot copied the excluded Van TV tree"
[[ -f "$main_snapshot/keep/file.txt" ]] ||
  fail "snapshot exclusion dropped unrelated source content"

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
for ((attempt=0; attempt<100; attempt++)); do
  if [[ -f "$TEST_WINDOW_CALLS.exfat" ]]; then
    : > "$TEST_WINDOW_CALLS.concurrent"
    exit 7
  fi
  /bin/sleep 0.02
done
exit 8
EOF
cat > "$window_root/exfat_snapshot.sh" <<'EOF'
#!/bin/bash
printf 'exfat:%s\n' "$*" >> "$TEST_WINDOW_CALLS"
: > "$TEST_WINDOW_CALLS.exfat"
EOF
chmod +x "$window_root/"*.sh
window_calls="$test_root/window.calls"
TEST_WINDOW_CALLS="$window_calls" "$window_root/backup_window.sh" --force >/dev/null 2>&1
window_status=$?
[[ $window_status == 1 ]] || fail "window runner hid a child backup failure"
[[ $(sort "$window_calls") == $'exfat:--force\npi:--force' ]] ||
  fail "window runner did not run and force both independently stamped jobs"
[[ -f "$window_calls.concurrent" ]] || fail "window jobs did not run concurrently"
rm "$window_calls" "$window_calls.concurrent" "$window_calls.exfat"
TEST_WINDOW_CALLS="$window_calls" "$window_root/backup_window.sh" >/dev/null 2>&1
[[ $? == 1 ]] || fail "ordinary window hid a child failure"
[[ $(sort "$window_calls") == $'exfat:\npi:' ]] || fail "ordinary window changed child arguments"
[[ -f "$window_calls.concurrent" ]] || fail "ordinary window jobs did not overlap"

echo "PASS: EXFAT van-tv exclusion, hard-link snapshot retention and backup-window isolation"
