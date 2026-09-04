#!/bin/bash

set -u

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)
config="$repo_root/pi/scripts/backup/backup_conf.sh"
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

for required in /usr/bin/flock /usr/bin/stat /usr/bin/id; do
  [[ -x "$required" ]] || {
    echo "SKIP: backup lock test requires Linux flock, stat, and id"
    exit 0
  }
done

lock="$test_root/job.lock"
owner=$(/usr/bin/id -un)
group=$(/usr/bin/id -gn)

run_with_lock_config() {
  VANPI_BACKUP_JOB_LOCK="$lock" \
    VANPI_BACKUP_JOB_LOCK_OWNER="$owner" \
    VANPI_BACKUP_JOB_LOCK_GROUP="$group" \
    VANPI_BACKUP_JOB_LOCK_MODE=660 \
    /bin/bash "$@"
}

: > "$lock"
chmod 0660 "$lock"
run_with_lock_config -c 'source "$1"; acquire_job_lock' test "$config" ||
  fail "valid pre-created lock was rejected"
[[ $(< "$lock") =~ ^[1-9][0-9]*$ ]] ||
  fail "lock holder PID was not recorded"

chmod 0644 "$lock"
if run_with_lock_config -c 'source "$1"; acquire_job_lock' test "$config" \
    >/dev/null 2>&1; then
  fail "unsafe lock mode was accepted"
fi

rm -f "$lock"
ln -s "$test_root/elsewhere" "$lock"
if run_with_lock_config -c 'source "$1"; acquire_job_lock' test "$config" \
    >/dev/null 2>&1; then
  fail "symlinked lock was accepted"
fi

rm -f "$lock"
if run_with_lock_config -c 'source "$1"; acquire_job_lock' test "$config" \
    >/dev/null 2>&1; then
  fail "missing tmpfiles-managed lock was accepted"
fi
[[ ! -e "$lock" ]] || fail "failed acquisition created the missing lock"

: > "$lock"
chmod 0660 "$lock"
ready="$test_root/ready"
VANPI_BACKUP_JOB_LOCK="$lock" \
  VANPI_BACKUP_JOB_LOCK_OWNER="$owner" \
  VANPI_BACKUP_JOB_LOCK_GROUP="$group" \
  VANPI_BACKUP_JOB_LOCK_MODE=660 \
  /bin/bash -c '
  source "$1"
  acquire_job_lock || exit 1
  : > "$2"
  trap "exit 0" TERM INT
  while :; do /bin/sleep 0.1; done
' test "$config" "$ready" &
holder_pid=$!
for _ in {1..50}; do
  [[ -e "$ready" ]] && break
  /bin/sleep 0.02
done
[[ -e "$ready" ]] || fail "lock holder did not start"
if run_with_lock_config -c 'source "$1"; acquire_job_lock' test "$config" \
    >/dev/null 2>&1; then
  fail "second caller acquired an active lock"
fi
kill -TERM "$holder_pid"
wait "$holder_pid" 2>/dev/null || true
holder_pid=

echo "PASS: pre-created shared backup lock safeguards"
