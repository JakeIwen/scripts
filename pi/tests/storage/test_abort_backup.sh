#!/bin/bash

set -u

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)
abort_script="$repo_root/pi/scripts/backup/abort_backup.sh"
test_root=$(mktemp -d)
holder_pid=

cleanup() {
  if [[ -n "$holder_pid" ]] && kill -0 "$holder_pid" 2>/dev/null; then
    kill -TERM "$holder_pid" 2>/dev/null || true
    wait "$holder_pid" 2>/dev/null || true
  fi
  find "$test_root" -depth -delete
}
trap cleanup EXIT

# shellcheck source=../lib.sh
source "$repo_root/pi/tests/lib.sh"

for required in /usr/bin/flock /usr/bin/stat /usr/bin/python3 /bin/sleep; do
  [[ -x "$required" ]] || {
    echo "SKIP: backup abort test requires Linux process and flock tools"
    exit 0
  }
done

job_lock="$test_root/job.lock"
fake_conf="$repo_root/pi/scripts/backup/backup_conf.sh"
borg_tool="$test_root/pi_backup.sh"
exfat_tool="$test_root/exfat_snapshot.sh"
restore_tool="$test_root/restore_from_borg.sh"
signal_file="$test_root/signal"

export VANPI_BACKUP_JOB_LOCK="$job_lock"
export VANPI_BACKUP_LOCK_DIR="$test_root/registry"
export VANPI_BACKUP_JOB_LOCK_OWNER=$(/usr/bin/id -un)
export VANPI_BACKUP_JOB_LOCK_GROUP=$(/usr/bin/id -gn)
export VANPI_BACKUP_JOB_LOCK_MODE=660 VANPI_BACKUP_LOCK_DIR_MODE=770
export VANPI_BACKUP_PRIORITY_TOOL="$test_root/priority.py"
printf '%s\n' 'raise SystemExit(0)' > "$VANPI_BACKUP_PRIORITY_TOOL"
mkdir -m 0770 "$VANPI_BACKUP_LOCK_DIR"
: > "$job_lock"
chmod 0660 "$job_lock"
source "$fake_conf"
for kind in "${BACKUP_JOB_KINDS[@]}"; do
  : > "$(backup_lock_path job "$kind")"
done
for label in "${BACKUP_DISK_LABELS[@]}"; do
  : > "$(backup_lock_path disk "$label")"
done
chmod 0660 "$BACKUP_LOCK_DIR"/*.lock
cat > "$borg_tool" <<'HOLDER'
#!/bin/bash
set -u
source "$TEST_CONF"
acquire_backup_job "$TEST_KIND" || exit 4
trap 'printf "USR1\n" > "$TEST_SIGNAL_FILE"; exit 143' USR1
trap 'printf "TERM\n" > "$TEST_SIGNAL_FILE"; exit 143' TERM INT
while :; do /bin/sleep 0.1; done
HOLDER
cp "$borg_tool" "$exfat_tool"
cp "$borg_tool" "$restore_tool"
chmod +x "$borg_tool" "$exfat_tool" "$restore_tool"

start_holder() {
  local tool=$1 kind=${2:-borg} attempts
  : > "$signal_file"
  TEST_CONF="$fake_conf" TEST_KIND="$kind" TEST_SIGNAL_FILE="$signal_file" "$tool" &
  holder_pid=$!
  for ((attempts = 0; attempts < 50; attempts++)); do
    if [[ $(cat "$BACKUP_LOCK_DIR/job-$kind.lock") == "$holder_pid" ]] &&
       ! /usr/bin/flock -n "$BACKUP_LOCK_DIR/job-$kind.lock" true 2>/dev/null; then
      return 0
    fi
    /bin/sleep 0.02
  done
  return 1
}

run_user_stop() {
  ABORT_BACKUP_CONF="$fake_conf" \
    ABORT_BACKUP_BORG_TOOL="$borg_tool" \
    ABORT_BACKUP_EXFAT_TOOL="$exfat_tool" \
    ABORT_BACKUP_USER_GRACE_SECONDS=5 \
    "$abort_script" --user "$1"
}

start_holder "$borg_tool" || fail "could not start fake Borg lock holder"
run_user_stop borg >/dev/null 2>&1 || fail "exact Borg holder was not stopped"
wait "$holder_pid" 2>/dev/null || true
holder_pid=
[[ $(< "$signal_file") == USR1 ]] || fail "dashboard stop did not use USR1"
/usr/bin/flock -n "$BACKUP_LOCK_DIR/job-borg.lock" true || fail "Borg stop did not release the job lock"

start_holder "$restore_tool" || fail "could not start fake restore lock holder"
if run_user_stop borg > "$test_root/refusal.out" 2>&1; then
  fail "Borg stop accepted a restore lock holder"
fi
kill -0 "$holder_pid" 2>/dev/null || fail "refused stop still signaled the restore"
kill -TERM "$holder_pid" 2>/dev/null || true
wait "$holder_pid" 2>/dev/null || true
holder_pid=

if run_user_stop exfat > "$test_root/idle.out" 2>&1; then
  fail "user stop succeeded while no backup held the lock"
fi

# An idle check must open the existing lock read-only and must not create a
# missing lock file merely to decide that no job is active.
chmod 0444 "$job_lock"
ABORT_BACKUP_CONF="$fake_conf" "$abort_script" >/dev/null 2>&1 ||
  fail "idle abort check could not use a read-only lock file"
rm -f "$job_lock"
ABORT_BACKUP_CONF="$fake_conf" "$abort_script" >/dev/null 2>&1 ||
  fail "idle abort check rejected an absent lock file"
[[ ! -e "$job_lock" ]] ||
  fail "idle abort check created the missing lock file"

: > "$job_lock"
chmod 0660 "$job_lock"

# A PID record naming a live process is not enough: it must hold this inode.
start_holder "$borg_tool" || fail "could not start holder for PID-record validation"
printf '%s\n' "$$" > "$BACKUP_LOCK_DIR/job-borg.lock"
if run_user_stop borg > "$test_root/stale-pid.out" 2>&1; then
  fail "user stop trusted a PID that did not hold the job lock"
fi
kill -0 "$holder_pid" || fail "invalid PID record signaled the real job"
kill -TERM "$holder_pid"
wait "$holder_pid" 2>/dev/null || true
holder_pid=

for arguments in 'unknown' '--disk' '--user clone' '--user borg bigboi' '--emergency --user borg' '--unknown' 'bigboi --emergency'; do
  ABORT_BACKUP_CONF="$fake_conf" "$abort_script" $arguments > "$test_root/usage.out" 2>&1
  [[ $? == 2 ]] || fail "invalid arguments did not return usage: $arguments"
done
ABORT_BACKUP_CONF="$fake_conf" ABORT_BACKUP_USER_GRACE_SECONDS=181 \
  "$abort_script" > "$test_root/timeout.out" 2>&1
[[ $? == 2 ]] || fail "invalid stop timeout was accepted"

rm "$BACKUP_LOCK_DIR/disk-hdd1tb.lock"
ABORT_BACKUP_CONF="$fake_conf" "$abort_script" hdd1tb ||
  fail "absent disk lock did not skip idle disk drain"
[[ ! -e "$BACKUP_LOCK_DIR/disk-hdd1tb.lock" ]] || fail "abort created missing disk lock"
mv "$BACKUP_LOCK_DIR" "$test_root/saved-registry"
ABORT_BACKUP_CONF="$fake_conf" "$abort_script" || fail "absent registry was not idle"
[[ ! -e "$BACKUP_LOCK_DIR" ]] || fail "abort created a missing registry"
ln -s "$test_root/saved-registry" "$BACKUP_LOCK_DIR"
if ABORT_BACKUP_CONF="$fake_conf" "$abort_script" > "$test_root/unsafe.out" 2>&1; then
  fail "symlinked lock registry was accepted"
fi

echo "PASS: guarded backup stop tests"
