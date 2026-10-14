#!/bin/bash
set -u

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)
source "$repo_root/pi/tests/lib.sh"
for required in /usr/bin/flock /usr/bin/stat /usr/bin/id /usr/bin/fuser /usr/bin/python3; do
  [[ -x "$required" && -d /proc/self/fd ]] || {
    echo "SKIP: resource backup locks require Linux flock and process tools"
    exit 0
  }
done

test_root=$(mktemp -d)
config="$repo_root/pi/scripts/backup/backup_conf.sh"
abort_script="$repo_root/pi/scripts/backup/abort_backup.sh"
pids=()
cleanup() {
  local pid file
  for pid in "${pids[@]}"; do
    kill -KILL "$pid" 2>/dev/null || true
    wait "$pid" 2>/dev/null || true
  done
  for file in "$test_root"/*.orphan; do
    [[ -f "$file" ]] || continue
    kill -KILL "$(< "$file")" 2>/dev/null || true
  done
  find "$test_root" -depth -delete
}
trap cleanup EXIT

export VANPI_BACKUP_LOCK_DIR="$test_root/registry"
export VANPI_BACKUP_JOB_LOCK="$test_root/legacy.lock"
export VANPI_BACKUP_JOB_LOCK_OWNER=$(/usr/bin/id -un)
export VANPI_BACKUP_JOB_LOCK_GROUP=$(/usr/bin/id -gn)
export VANPI_BACKUP_JOB_LOCK_MODE=660 VANPI_BACKUP_LOCK_DIR_MODE=770
export VANPI_BACKUP_PRIORITY_TOOL="$test_root/priority.py"
printf '%s\n' 'raise SystemExit(0)' > "$VANPI_BACKUP_PRIORITY_TOOL"
mkdir -m 0770 "$VANPI_BACKUP_LOCK_DIR"
: > "$VANPI_BACKUP_JOB_LOCK"
chmod 0660 "$VANPI_BACKUP_JOB_LOCK"
source "$config"
for kind in "${BACKUP_JOB_KINDS[@]}"; do
  : > "$(backup_lock_path job "$kind")"
done
for label in "${BACKUP_DISK_LABELS[@]}"; do
  : > "$(backup_lock_path disk "$label")"
done
: > "$(backup_lock_path data borg-repo)"
: > "$(backup_lock_path cloud upload)"
chmod 0660 "$BACKUP_LOCK_DIR"/*.lock

export TEST_LOCK_LIBRARY="$repo_root/pi/scripts/backup/backup_locks.sh"
export ABORT_BACKUP_CONF="$config"
export ABORT_BACKUP_BORG_TOOL="$test_root/pi_backup.sh"
export ABORT_BACKUP_EXFAT_TOOL="$test_root/exfat_snapshot.sh"
export ABORT_BACKUP_USER_GRACE_SECONDS=3
export ABORT_BACKUP_EMERGENCY_GRACE_SECONDS=1
export ABORT_BACKUP_EMERGENCY_KILL_WAIT_SECONDS=2
cat > "$ABORT_BACKUP_BORG_TOOL" <<'HOLDER'
#!/bin/bash
set -u
kind=$1
shift
if [[ "$kind" == lifecycle ]]; then
  source "$TEST_LOCK_LIBRARY"
  hold_backup_disks_exclusive "$@" || exit 4
else
  source "$ABORT_BACKUP_CONF"
  if [[ "$kind" == legacy ]]; then
    acquire_job_lock || exit 4
  else
    acquire_backup_job "$kind" "$@" || exit 4
  fi
fi
child=
stop_holder() {
  printf '%s\n' "$1" >> "$TEST_READY.signal"
  [[ -z "$child" ]] || { kill -TERM "$child" 2>/dev/null; wait "$child" 2>/dev/null; }
  exit 0
}
trap 'stop_holder TERM' TERM INT
trap 'stop_holder USR1' USR1
if [[ ${TEST_MODE:-normal} == repository ]]; then
  acquire_backup_lock data borg-repo || exit 5
elif [[ ${TEST_MODE:-normal} == wait ]]; then
  : > "$TEST_READY"
  acquire_backup_lock data borg-repo 60 || exit 5
  exit 6
fi
if [[ ${TEST_MODE:-normal} == stubborn ]]; then
  trap 'printf "TERM\n" >> "$TEST_READY.signal"' TERM
  # The intermediate parent exits so the sleep is already orphaned at drain.
  /bin/bash -c 'trap "" TERM; /bin/sleep 30 & echo "$!" > "$TEST_READY.orphan"'
fi
: > "$TEST_READY"
# Stop without a child so SIGKILL can prove immediate descriptor recovery.
[[ ${TEST_MODE:-normal} != crash ]] || kill -STOP "$$"
while :; do
  /bin/sleep 0.05 & child=$!
  wait "$child" || true
  child=
done
HOLDER
cp "$ABORT_BACKUP_BORG_TOOL" "$ABORT_BACKUP_EXFAT_TOOL"
cp "$ABORT_BACKUP_BORG_TOOL" "$test_root/not-the-backup.sh"

start_holder() { # <name> <tool> <kind> [disk ...]
  local name=$1 tool=$2 attempt
  shift 2
  TEST_READY="$test_root/$name" /bin/bash "$tool" "$@" > "$test_root/$name.log" 2>&1 &
  started_pid=$!
  pids+=("$started_pid")
  for ((attempt=0; attempt<300; attempt++)); do
    [[ -e "$test_root/$name" ]] && return 0
    kill -0 "$started_pid" 2>/dev/null || break
    /bin/sleep 0.05
  done
  cat "$test_root/$name.log" >&2
  fail "holder $name did not become ready"
}

stop_holder() {
  kill -TERM "$1" || fail "could not stop holder $1"
  wait "$1" || fail "holder $1 did not exit cleanly"
}

lock_is_free() { # Probe a pre-existing inode without creating it.
  local fd rc
  exec {fd}<"$1" || return 1
  /usr/bin/flock -n "$fd"
  rc=$?
  exec {fd}<&-
  return "$rc"
}

run_abort() {
  /bin/bash "$abort_script" "$@" > "$test_root/abort.out" 2>&1 || {
    cat "$test_root/abort.out" >&2
    fail "abort $* failed"
  }
}

# Cloud selection never gates local work, even if admission is denied.
printf '%s\n' 'raise SystemExit(1)' > "$VANPI_BACKUP_PRIORITY_TOOL"
for kind in borg exfat clone; do
  /bin/bash -c 'source "$1"; acquire_backup_job "$2" bigboi' test "$config" "$kind" ||
    fail "cloud priority denied local $kind admission"
done
for kind in pi-cloud time-machine; do
  if /bin/bash -c 'source "$1"; acquire_backup_job "$2" bigboi' test "$config" "$kind"; then
    fail "cloud priority admitted blocked $kind"
  fi
done
printf '%s\n' 'raise SystemExit(0)' > "$VANPI_BACKUP_PRIORITY_TOOL"
for kind in borg exfat clone; do
  VANPI_BACKUP_PRIORITY_TOOL="$test_root/missing-priority.py" /bin/bash -c '
    source "$1"; acquire_backup_job "$2" bigboi
  ' test "$config" "$kind" || fail "missing cloud helper blocked local $kind"
done

# Timed repository waits must reach either signal trap well before their deadline.
TEST_MODE=repository start_holder repository "$test_root/not-the-backup.sh" pi-cloud bigboi
repository_pid=$started_pid
for signal in USR1 TERM; do
  TEST_MODE=wait start_holder "wait-$signal" "$ABORT_BACKUP_BORG_TOOL" borg bigboi
  waiter_pid=$started_pid
  /bin/sleep 0.2
  kill -"$signal" "$waiter_pid" || fail "repository waiter exited before $signal"
  deadline=$((SECONDS + 5))
  while kill -0 "$waiter_pid" 2>/dev/null && (( SECONDS < deadline )); do
    /bin/sleep 0.05
  done
  kill -0 "$waiter_pid" 2>/dev/null && fail "$signal did not interrupt repository wait within 5s"
  wait "$waiter_pid" || fail "repository waiter did not exit through its $signal trap"
  assert_eq "$signal" "$(< "$test_root/wait-$signal.signal")" "repository wait signal"
  lock_is_free "$(backup_lock_path job borg)" || fail "repository waiter retained its job lock"
  kill -0 "$repository_pid" || fail "repository waiter stopped its peer"
done
stop_holder "$repository_pid"
lock_is_free "$(backup_lock_path data borg-repo)" || fail "repository wait left an orphan holder"

# Standalone lifecycle callers retain exclusivity across the drain/unmount gap.
start_holder lifecycle "$test_root/not-the-backup.sh" lifecycle bigboi hdd1tb
lifecycle_pid=$started_pid
if /bin/bash -c 'source "$1"; acquire_backup_job borg bigboi' test "$config" 2>/dev/null; then
  fail "Borg was admitted during bigboi lifecycle ownership"
fi
if /bin/bash -c 'source "$1"; acquire_backup_job exfat hdd1tb EXFAT512' test "$config" 2>/dev/null; then
  fail "EXFAT was admitted during hdd1tb lifecycle ownership"
fi
/bin/bash -c 'source "$1"; acquire_backup_job time-machine mbp2tbkup' test "$config" ||
  fail "lifecycle ownership blocked an unrelated disk"
stop_holder "$lifecycle_pid"
for label in bigboi hdd1tb; do
  lock_is_free "$(backup_lock_path disk "$label")" || fail "lifecycle exit retained $label"
done

# A failed multi-disk claim releases earlier claims before the caller exits.
start_holder claim-blocker "$ABORT_BACKUP_BORG_TOOL" borg bigboi
claim_blocker=$started_pid
/bin/bash -uc '
  source "$1"
  if hold_backup_disks_exclusive hdd1tb bigboi; then exit 1; fi
  /usr/bin/flock -n "$BACKUP_LOCK_DIR/disk-hdd1tb.lock" true || exit 2
  ! /usr/bin/flock -n "$BACKUP_LOCK_DIR/disk-bigboi.lock" true || exit 3
' test "$TEST_LOCK_LIBRARY" > "$test_root/claim.out" 2>&1 ||
  fail "failed lifecycle claim retained an earlier disk or disturbed its peer"
grep -Fq 'bigboi is in use by a backup job; refusing to unmount' "$test_root/claim.out" ||
  fail "lifecycle refusal did not identify its busy disk"
stop_holder "$claim_blocker"

# Missing registry entries cannot admit jobs; lifecycle must not create them.
mv "$BACKUP_LOCK_DIR/disk-bigboi.lock" "$test_root/saved-disk.lock"
/bin/bash -uc '
  source "$1"
  hold_backup_disks_exclusive bigboi hdd1tb || exit 1
  [[ ! -e "$BACKUP_LOCK_DIR/disk-bigboi.lock" ]] || exit 2
  ! /usr/bin/flock -n "$BACKUP_LOCK_DIR/disk-hdd1tb.lock" true || exit 3
' test "$TEST_LOCK_LIBRARY" || fail "lifecycle did not safely skip a missing disk lock"
mv "$test_root/saved-disk.lock" "$BACKUP_LOCK_DIR/disk-bigboi.lock"
chmod 0600 "$BACKUP_LOCK_DIR/disk-bigboi.lock"
/bin/bash -uc '
  source "$1"
  if hold_backup_disks_exclusive hdd1tb bigboi; then exit 1; fi
  /usr/bin/flock -n "$BACKUP_LOCK_DIR/disk-hdd1tb.lock" true
' test "$TEST_LOCK_LIBRARY" > "$test_root/claim.out" 2>&1 ||
  fail "unsafe disk lock metadata was accepted or retained an earlier disk"
chmod 0660 "$BACKUP_LOCK_DIR/disk-bigboi.lock"

# Release helpers must use their argument rather than an outer shell variable.
/bin/bash -c '
  source "$1"
  acquire_backup_job borg bigboi movingparts || exit 1
  label=unknown
  release_backup_disk movingparts
  /usr/bin/flock -n "$BACKUP_LOCK_DIR/disk-movingparts.lock" true || exit 2
  acquire_backup_lock data borg-repo || exit 3
  key=unknown
  release_backup_lock data borg-repo
  /usr/bin/flock -n "$BACKUP_LOCK_DIR/data-borg-repo.lock" true || exit 4
  release_backup_job
  /usr/bin/flock -n "$BACKUP_LOCK_DIR/job-borg.lock" true || exit 5
  /usr/bin/flock -n "$VANPI_BACKUP_JOB_LOCK" true
' test "$config" || fail "argument-scoped resource release failed"

# EXFAT releases only its destination before asking lifecycle to spin it down.
/bin/bash -c '
  source "$1"
  acquire_backup_job exfat hdd1tb EXFAT512 || exit 1
  release_backup_disk hdd1tb
  /usr/bin/flock -n "$BACKUP_LOCK_DIR/disk-hdd1tb.lock" true || exit 2
  ! /usr/bin/flock -n "$BACKUP_LOCK_DIR/disk-EXFAT512.lock" true || exit 3
  ! /usr/bin/flock -n "$BACKUP_LOCK_DIR/job-exfat.lock" true || exit 4
  ! /usr/bin/flock -n "$VANPI_BACKUP_JOB_LOCK" true || exit 5
  /bin/bash "$2" hdd1tb
' test "$config" "$abort_script" || fail "EXFAT destination cleanup lost its remaining holds"

# Independent disks overlap, while identities and the legacy fence exclude.
start_holder borg "$ABORT_BACKUP_BORG_TOOL" borg bigboi movingparts
borg_pid=$started_pid
start_holder exfat "$ABORT_BACKUP_EXFAT_TOOL" exfat hdd1tb EXFAT512
exfat_pid=$started_pid
kill -0 "$borg_pid" "$exfat_pid" || fail "independent jobs did not overlap"
if /bin/bash -c 'source "$1"; acquire_backup_job borg bigboi' test "$config" 2>/dev/null; then
  fail "a second Borg was admitted"
fi
if /bin/bash -c 'source "$1"; acquire_job_lock' test "$config" 2>/dev/null; then
  fail "legacy exclusive lock overlapped migrated jobs"
fi

# A mount owner cannot unmount while a peer still holds that disk.
/bin/bash -c '
  source "$1"
  acquire_backup_job pi-cloud bigboi || exit 1
  ! release_backup_disk_if_unused bigboi
' test "$config" || fail "shared bigboi was declared unused"

# A parent's release never strips a child that inherited the descriptor: the
# disk stays owned (and is not "unused") until that child exits.
/bin/bash -c '
  source "$1"
  acquire_backup_job time-machine mbp2tbkup || exit 1
  /bin/sleep 30 & heir=$!
  trap "kill -TERM $heir 2>/dev/null" EXIT
  ! release_backup_disk_if_unused mbp2tbkup || exit 2
  release_backup_disk mbp2tbkup
  ! /usr/bin/flock -n "$BACKUP_LOCK_DIR/disk-mbp2tbkup.lock" true || exit 3
  kill -TERM "$heir"; wait "$heir" 2>/dev/null
  /usr/bin/flock -n "$BACKUP_LOCK_DIR/disk-mbp2tbkup.lock" true || exit 4
' test "$config" || fail "a live child's inherited disk hold was dropped by the parent release"

# Disk-scoped drain must never TERM the unrelated Borg parent.
run_abort hdd1tb
wait "$exfat_pid" || fail "EXFAT disk stop failed"
assert_eq TERM "$(< "$test_root/exfat.signal")" "EXFAT disk drain signal"
kill -0 "$borg_pid" || fail "hdd1tb drain stopped Borg"
lock_is_free "$(backup_lock_path disk bigboi)" && fail "Borg lost its bigboi hold"
stop_holder "$borg_pid"
/bin/bash -c '
  source "$1"
  acquire_backup_job pi-cloud bigboi || exit 1
  release_backup_disk_if_unused bigboi || exit 2
  ! /usr/bin/flock -n -s "$BACKUP_LOCK_DIR/disk-bigboi.lock" true || exit 3
  release_backup_disk bigboi
' test "$config" || fail "unused disk was not held exclusively until release"

/bin/bash -uc '
  source "$1"
  hold_backup_disks_exclusive bigboi || exit 4
  : > "$2"
  trap "exit 0" TERM
  while :; do /bin/sleep 0.1; done
' test "$TEST_LOCK_LIBRARY" "$test_root/claim.ready" &
claim_pid=$!
pids+=("$claim_pid")
for ((attempt=0; attempt<300; attempt++)); do
  [[ -e "$test_root/claim.ready" ]] && break
  /bin/sleep 0.05
done
[[ -e "$test_root/claim.ready" ]] || fail "lifecycle claim holder did not start"
if /bin/bash -c 'source "$1"; acquire_job_lock' test "$config" 2>/dev/null; then
  fail "legacy exclusive lock was admitted during a lifecycle claim"
fi
stop_holder "$claim_pid"

start_holder legacy "$test_root/not-the-backup.sh" legacy
legacy_pid=$started_pid
# A legacy exclusive holder (restore, hotspare init) blocks the lifecycle claim,
# and an active lifecycle claim blocks a new legacy exclusive holder.
if /bin/bash -uc 'source "$1"; hold_backup_disks_exclusive bigboi' test "$TEST_LOCK_LIBRARY" \
    > "$test_root/claim.out" 2>&1; then
  fail "lifecycle claim ignored a legacy exclusive holder"
fi
grep -Fq 'restore or maintenance job holds the backup lock' "$test_root/claim.out" ||
  fail "lifecycle refusal did not name the legacy holder"
if /bin/bash -c 'source "$1"; acquire_backup_job borg bigboi' test "$config" 2>/dev/null; then
  fail "migrated job overlapped legacy exclusive holder"
fi
if /bin/bash "$abort_script" --user borg > "$test_root/refusal.out" 2>&1; then
  fail "dashboard stop accepted a legacy holder"
fi
kill -0 "$legacy_pid" || fail "dashboard stop signaled legacy holder"
run_abort
wait "$legacy_pid" || fail "legacy lifecycle stop failed"
assert_eq TERM "$(< "$test_root/legacy.signal")" "legacy lifecycle signal"

# A missing registry must not hide an older exclusive consumer.
mv "$BACKUP_LOCK_DIR" "$test_root/saved-registry"
/bin/bash -uc 'source "$1"; hold_backup_disks_exclusive bigboi hdd1tb' test "$TEST_LOCK_LIBRARY" ||
  fail "lifecycle refused a missing registry"
[[ ! -e "$BACKUP_LOCK_DIR" ]] || fail "lifecycle recreated a missing registry"
# Without a registry the claim and a legacy exclusive holder still exclude each
# other, so a restore cannot slip in between the drain and the unmount.
/bin/bash -uc '
  source "$1"
  hold_backup_disks_exclusive bigboi || exit 4
  : > "$2"
  trap "exit 0" TERM
  while :; do /bin/sleep 0.1; done
' test "$TEST_LOCK_LIBRARY" "$test_root/claim-no-registry.ready" &
claim_pid=$!
pids+=("$claim_pid")
for ((attempt=0; attempt<300; attempt++)); do
  [[ -e "$test_root/claim-no-registry.ready" ]] && break
  /bin/sleep 0.05
done
[[ -e "$test_root/claim-no-registry.ready" ]] || fail "lifecycle claim without a registry did not start"
if /bin/bash -c 'source "$1"; acquire_job_lock' test "$config" 2>/dev/null; then
  fail "legacy exclusive lock was admitted during a lifecycle claim without a registry"
fi
stop_holder "$claim_pid"
start_holder legacy-no-registry "$test_root/not-the-backup.sh" legacy
legacy_pid=$started_pid
if /bin/bash -uc 'source "$1"; hold_backup_disks_exclusive bigboi hdd1tb' test "$TEST_LOCK_LIBRARY" 2>/dev/null; then
  fail "lifecycle claim without a registry ignored a legacy exclusive holder"
fi
run_abort
wait "$legacy_pid" || fail "missing registry hid a legacy exclusive holder"
assert_eq TERM "$(< "$test_root/legacy-no-registry.signal")" "legacy stop without registry"
mv "$test_root/saved-registry" "$BACKUP_LOCK_DIR"

# User cancellation selects an exact tool and job identity, not the global lock.
start_holder user-borg "$ABORT_BACKUP_BORG_TOOL" borg bigboi
borg_pid=$started_pid
start_holder user-exfat "$ABORT_BACKUP_EXFAT_TOOL" exfat hdd1tb EXFAT512
exfat_pid=$started_pid
run_abort --user borg
wait "$borg_pid" || fail "Borg user stop failed"
assert_eq USR1 "$(< "$test_root/user-borg.signal")" "Borg user stop signal"
kill -0 "$exfat_pid" || fail "Borg user stop affected EXFAT"
lock_is_free "$(backup_lock_path disk hdd1tb)" && fail "EXFAT lost its disk hold"
start_holder wrong-tool "$test_root/not-the-backup.sh" borg bigboi
wrong_pid=$started_pid
if /bin/bash "$abort_script" --user borg > "$test_root/refusal.out" 2>&1; then
  fail "dashboard stop accepted a different tool"
fi
kill -0 "$wrong_pid" || fail "refused dashboard stop signaled the different tool"
grep -Fq 'not the active borg backup' "$test_root/refusal.out" || fail "missing exact-tool refusal"
stop_holder "$wrong_pid"
stop_holder "$exfat_pid"

# Emergency eviction targets the disk inode, including orphaned descendants.
TEST_MODE=stubborn start_holder stubborn "$ABORT_BACKUP_BORG_TOOL" borg bigboi
stubborn_pid=$started_pid
start_holder emergency-peer "$ABORT_BACKUP_EXFAT_TOOL" exfat hdd1tb EXFAT512
peer_pid=$started_pid
run_abort --emergency bigboi
wait "$stubborn_pid" 2>/dev/null || true
lock_is_free "$(backup_lock_path disk bigboi)" || fail "emergency left an inherited disk lock"
grep -Fq TERM "$test_root/stubborn.signal" || fail "emergency did not try TERM first"
kill -0 "$peer_pid" || fail "emergency killed the other disk's holder"
stop_holder "$peer_pid"

# Kernel lifetime, not the stale PID record, controls crash recovery.
TEST_MODE=crash start_holder crash "$ABORT_BACKUP_BORG_TOOL" borg bigboi
crash_pid=$started_pid
kill -KILL "$crash_pid"
wait "$crash_pid" 2>/dev/null || true
start_holder recovered "$ABORT_BACKUP_BORG_TOOL" borg bigboi
assert_eq "$started_pid" "$(< "$BACKUP_LOCK_DIR/job-borg.lock")" "recovered job PID record"
[[ "$started_pid" != "$crash_pid" ]] || fail "crash fixture reused the process"
stop_holder "$started_pid"

# Unscoped lifecycle callers drain every registered disk, once per parent.
start_holder all-borg "$ABORT_BACKUP_BORG_TOOL" borg bigboi movingparts
all_borg=$started_pid
start_holder all-exfat "$ABORT_BACKUP_EXFAT_TOOL" exfat hdd1tb EXFAT512
all_exfat=$started_pid
start_holder all-mac "$test_root/not-the-backup.sh" time-machine mbp2tbkup
all_mac=$started_pid
run_abort
for pid in "$all_borg" "$all_exfat" "$all_mac"; do
  wait "$pid" || fail "unscoped drain failed"
done
for name in all-borg all-exfat all-mac; do
  assert_eq TERM "$(< "$test_root/$name.signal")" "unscoped drain signal count"
done
for label in "${BACKUP_DISK_LABELS[@]}"; do
  lock_is_free "$(backup_lock_path disk "$label")" || fail "unscoped drain left $label held"
done

echo "PASS: concurrent backup identities, disk ownership, cancellation and crash recovery"
