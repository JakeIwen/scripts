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

# shellcheck source=../lib.sh
source "$repo_root/pi/tests/lib.sh"

for required in /usr/bin/flock /usr/bin/stat /usr/bin/id; do
  [[ -x "$required" ]] || {
    echo "SKIP: backup lock test requires Linux flock, stat, and id"
    exit 0
  }
done

lock="$test_root/job.lock"
registry="$test_root/registry"
offsite_lock="$registry/job-offsite.lock"
priority_tool="$test_root/priority.py"
printf '%s\n' 'raise SystemExit(0)' > "$priority_tool"
owner=$(/usr/bin/id -un)
group=$(/usr/bin/id -gn)
mkdir -m 0770 "$registry"
: > "$offsite_lock"
chmod 0660 "$offsite_lock"

run_with_lock_config() {
  VANPI_BACKUP_JOB_LOCK="$lock" \
    VANPI_BACKUP_JOB_LOCK_OWNER="$owner" \
    VANPI_BACKUP_JOB_LOCK_GROUP="$group" \
    VANPI_BACKUP_JOB_LOCK_MODE=660 \
    VANPI_BACKUP_LOCK_DIR="$registry" \
    VANPI_BACKUP_LOCK_DIR_MODE=770 \
    VANPI_BACKUP_PRIORITY_TOOL="$priority_tool" \
    /bin/bash "$@"
}

# A refused caller must give back what it took while it is still running.
refused_and_released() { # <description>: acquire_job_lock refuses and both locks are free in-process
  run_with_lock_config -c '
    source "$1"
    free() { local fd rc; exec {fd}<"$1"; /usr/bin/flock -n "$fd"; rc=$?; exec {fd}<&-; return "$rc"; }
    if acquire_job_lock; then exit 10; fi
    free "$VANPI_BACKUP_JOB_LOCK" || exit 11
    [[ ! -f "$2" ]] || [[ -n ${3:-} ]] || free "$2" || exit 12
    exit 1
  ' test "$config" "$offsite_lock" "${2:-}" 2>> "$test_root/refusals.err"
  case $? in
    1) ;;
    10) fail "$1: acquire_job_lock was admitted" ;;
    11) fail "$1: refusal kept the legacy lock" ;;
    12) fail "$1: refusal kept the offsite exclusion" ;;
    *) fail "$1: unexpected status" ;;
  esac
}

: > "$lock"
chmod 0660 "$lock"
run_with_lock_config -c 'source "$1"; acquire_job_lock' test "$config" ||
  fail "valid pre-created lock was rejected"
[[ $(< "$lock") =~ ^[1-9][0-9]*$ ]] ||
  fail "lock holder PID was not recorded"

saved_pid=$(< "$lock")
printf '%s\n' 'raise SystemExit(1)' > "$priority_tool"
if run_with_lock_config -c 'source "$1"; acquire_job_lock' test "$config"; then
  fail "lower-priority backup was admitted"
fi
[[ $(< "$lock") == "$saved_pid" ]] || fail "denied admission changed the lock owner record"
refused_and_released "denied priority admission"
printf '%s\n' 'raise SystemExit(0)' > "$priority_tool"
run_with_lock_config -c 'source "$1"; acquire_job_lock' test "$config" ||
  fail "denied priority admission did not release its lock"

# Offsite runs hold job-offsite instead of the legacy lock, so exclusive
# maintenance must refuse while one runs and give the legacy lock back.
exec {offsite_fd}<"$offsite_lock"
/usr/bin/flock -n -s "$offsite_fd" || fail "cannot hold the fixture offsite identity"
saved_pid=$(< "$lock")
: > "$test_root/refusals.err"
refused_and_released "running offsite identity" held-elsewhere
grep -Fqx 'an offsite run is active' "$test_root/refusals.err" || fail "offsite refusal did not say why"
[[ $(< "$lock") == "$saved_pid" ]] || fail "offsite refusal changed the legacy owner record"
exec {offsite_fd}<&-
run_with_lock_config -c '
  source "$1"
  acquire_job_lock || exit 1
  lock_free() { local fd rc; exec {fd}<"$1" || return 9; /usr/bin/flock -n -s "$fd"; rc=$?; exec {fd}<&-; return "$rc"; }
  lock_free "$2"
  (( $? == 1 )) || exit 2
  [[ $(< "$2") != "$$" ]] || exit 3
' test "$config" "$offsite_lock" || fail "exclusive maintenance did not hold the offsite identity without a PID record"

# A missing registry or inode cannot have admitted an offsite run; unsafe
# metadata still fails closed, and nothing is ever created.
chmod 0600 "$offsite_lock"
refused_and_released "unsafe offsite lock metadata"
rm "$offsite_lock"
run_with_lock_config -c 'source "$1"; acquire_job_lock' test "$config" || fail "missing offsite inode blocked maintenance"
[[ ! -e "$offsite_lock" ]] || fail "maintenance created the offsite lock"
mv "$registry" "$test_root/saved-registry"
ln -s "$test_root/saved-registry" "$registry"
refused_and_released "symlinked registry"
rm "$registry"
run_with_lock_config -c 'source "$1"; acquire_job_lock' test "$config" || fail "missing registry blocked maintenance"
[[ ! -e "$registry" ]] || fail "maintenance created the registry"
mv "$test_root/saved-registry" "$registry"
: > "$offsite_lock"
chmod 0660 "$offsite_lock"

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
  VANPI_BACKUP_LOCK_DIR="$registry" \
  VANPI_BACKUP_LOCK_DIR_MODE=770 \
  VANPI_BACKUP_PRIORITY_TOOL="$priority_tool" \
  /bin/bash -c '
  source "$1"
  acquire_job_lock || exit 1
  : > "$2"
  trap "exit 0" TERM INT
  while :; do /bin/sleep 0.1; done
' test "$config" "$ready" &
holder_pid=$!
# Sourcing the configuration starts Python helpers, which take seconds on a busy Pi.
for _ in {1..500}; do
  [[ -e "$ready" ]] && break
  kill -0 "$holder_pid" 2>/dev/null || break
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

echo "PASS: pre-created legacy exclusive backup lock safeguards, including the offsite exclusion"
