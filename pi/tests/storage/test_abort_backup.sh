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

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

for required in /usr/bin/flock /usr/bin/readlink /bin/sleep; do
  [[ -x "$required" ]] || {
    echo "SKIP: backup abort test requires Linux process and flock tools"
    exit 0
  }
done

job_lock="$test_root/job.lock"
fake_conf="$test_root/backup_conf.sh"
borg_tool="$test_root/pi_backup.sh"
exfat_tool="$test_root/exfat_snapshot.sh"
restore_tool="$test_root/restore_from_borg.sh"
signal_file="$test_root/signal"

printf 'JOB_LOCK=%q\n' "$job_lock" > "$fake_conf"
cat > "$borg_tool" <<'HOLDER'
#!/bin/bash
set -u
exec 9>>"$TEST_JOB_LOCK"
/usr/bin/flock -n 9 || exit 4
echo $$ > "$TEST_JOB_LOCK"
trap 'printf "USR1\n" > "$TEST_SIGNAL_FILE"; exit 143' USR1
trap 'printf "TERM\n" > "$TEST_SIGNAL_FILE"; exit 143' TERM INT
while :; do /bin/sleep 0.1; done
HOLDER
cp "$borg_tool" "$exfat_tool"
cp "$borg_tool" "$restore_tool"
chmod +x "$borg_tool" "$exfat_tool" "$restore_tool"

start_holder() {
  local tool=$1 attempts
  : > "$signal_file"
  TEST_JOB_LOCK="$job_lock" TEST_SIGNAL_FILE="$signal_file" "$tool" &
  holder_pid=$!
  for ((attempts = 0; attempts < 50; attempts++)); do
    if [[ -s "$job_lock" ]] && ! /usr/bin/flock -n "$job_lock" true 2>/dev/null; then
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
/usr/bin/flock -n "$job_lock" true || fail "Borg stop did not release the job lock"

start_holder "$restore_tool" || fail "could not start fake restore lock holder"
if run_user_stop borg > "$test_root/refusal.out" 2>&1; then
  fail "Borg stop accepted a restore lock holder"
fi
kill -0 "$holder_pid" 2>/dev/null || fail "refused stop still signaled the restore"
grep -Fq "shared lock holder is not the active borg backup" "$test_root/refusal.out" ||
  fail "refused stop did not explain the exact-holder mismatch"
kill -TERM "$holder_pid" 2>/dev/null || true
wait "$holder_pid" 2>/dev/null || true
holder_pid=

if run_user_stop exfat > "$test_root/idle.out" 2>&1; then
  fail "user stop succeeded while no backup held the lock"
fi
grep -Fq "no active exfat backup" "$test_root/idle.out" ||
  fail "idle stop did not explain that no EXFAT backup was active"

echo "PASS: guarded backup stop tests"
