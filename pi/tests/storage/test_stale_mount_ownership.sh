#!/bin/bash
set -u

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)
source "$repo_root/pi/tests/lib.sh"
[[ -x /usr/bin/flock && -d /proc/self/fd ]] || { echo 'SKIP: lifecycle ownership needs Linux'; exit 0; }
test_root=$(mktemp -d)
pids=()
cleanup() {
  local pid
  [[ ! -f "$test_root/job-pid" ]] || kill -TERM "$(< "$test_root/job-pid")" 2>/dev/null || true
  for pid in "${pids[@]}"; do kill -TERM "$pid" 2>/dev/null || true; wait "$pid" 2>/dev/null || true; done
  find "$test_root" -depth -delete
}
trap cleanup EXIT

export TEST_ROOT="$test_root" TEST_CONF="$repo_root/pi/scripts/backup/backup_conf.sh"
export VANPI_BACKUP_LOCK_DIR="$test_root/registry" VANPI_BACKUP_JOB_LOCK="$test_root/legacy.lock"
export VANPI_BACKUP_JOB_LOCK_OWNER=$(/usr/bin/id -un) VANPI_BACKUP_JOB_LOCK_GROUP=$(/usr/bin/id -gn)
export VANPI_BACKUP_JOB_LOCK_MODE=660 VANPI_BACKUP_LOCK_DIR_MODE=770
export VANPI_BACKUP_PRIORITY_TOOL="$test_root/no-priority"
mkdir -m 0770 "$VANPI_BACKUP_LOCK_DIR"
: > "$VANPI_BACKUP_JOB_LOCK"
chmod 0660 "$VANPI_BACKUP_JOB_LOCK"
source "$TEST_CONF"
for kind in "${BACKUP_JOB_KINDS[@]}"; do : > "$(backup_lock_path job "$kind")"; done
for label in "${BACKUP_DISK_LABELS[@]}"; do : > "$(backup_lock_path disk "$label")"; done
chmod 0660 "$VANPI_BACKUP_LOCK_DIR"/*.lock

cat > "$test_root/job" <<'JOB'
#!/bin/bash
source "$TEST_CONF"
trap 'exit 0' TERM
acquire_backup_job exfat EXFAT512 || exit 2
echo "$BASHPID" > "$TEST_ROOT/job-pid"
: > "$TEST_ROOT/job-ready"
while :; do /bin/sleep 0.05; done
JOB
cat > "$test_root/abort" <<'ABORT'
#!/bin/bash
[[ "$*" == EXFAT512 ]] || exit 2
echo drain >> "$TEST_ROOT/trace"
if [[ "$TEST_PHASE" == between ]]; then
  "$TEST_ROOT/job" &
  for ((i=0; i<200; i++)); do
    [[ -e "$TEST_ROOT/job-ready" ]] && exit 0
    /bin/sleep 0.02
  done
  exit 3
fi
[[ "$TEST_PHASE" != drain-fails ]]
ABORT
cat > "$test_root/samba" <<'SAMBA'
#!/bin/bash
[[ "$*" == 'close EXFAT512' ]] || exit 2
echo consumers >> "$TEST_ROOT/trace"
if [[ "$TEST_PHASE" == after ]]; then
  : > "$TEST_ROOT/ready"
  for ((i=0; i<500; i++)); do
    [[ -e "$TEST_ROOT/go" ]] && exit 0
    /bin/sleep 0.02
  done
  exit 3
fi
[[ "$TEST_PHASE" != consumers-fail ]]
SAMBA
cat > "$test_root/mount" <<'MOUNT'
#!/bin/bash
records=$'EXFAT512\t/mnt/EXFAT512\t/dev/vanished-test'
case "$1" in
  --list-stale) printf '%s\n' "$records" ;;
  --recover-stale)
    [[ $# == 2 && "$2" == "$records" ]] || exit 2
    # This independent job must still be excluded at the actual mutation seam.
    /bin/bash -c 'source "$TEST_CONF"; acquire_backup_job exfat EXFAT512' && exit 3
    echo mutation >> "$TEST_ROOT/trace"
    [[ "$TEST_PHASE" != recovery-fails ]]
    ;;
  *) exit 4 ;;
esac
MOUNT
chmod 0700 "$test_root/job" "$test_root/abort" "$test_root/samba" "$test_root/mount"
export ISW_MOUNT_DISKS="$test_root/mount" ISW_ABORT_BACKUP="$test_root/abort"
export ISW_SAMBA_SHARE_CONTROL="$test_root/samba"
policy="$repo_root/pi/scripts/internet_switches.sh"

recover() {
  /bin/bash -uc 'source "$1"; recover_stale_mounts_if_needed' test "$policy" > "$test_root/recovery.log" 2>&1
}
job_can_start() {
  /bin/bash -uc 'source "$TEST_CONF"; acquire_backup_job exfat EXFAT512' >/dev/null 2>&1
}

# A starter wins the drain-to-claim race: the lifecycle must fail before mutation.
export TEST_PHASE=between
if recover; then fail 'recovery ignored a job that started after drain'; fi
[[ -e "$test_root/trace" ]] || { cat "$test_root/recovery.log"; fail "recovery never reached drain"; }
assert_eq drain "$(< "$test_root/trace")" 'claim failure reached consumer shutdown or mutation'
kill -TERM "$(< "$test_root/job-pid")"
for ((i=0; i<200; i++)); do job_can_start && break; /bin/sleep 0.02; done
job_can_start || fail 'competing job failed to release its claim'

# Hold recovery after drain/claim and start a competing job before mutation.
export TEST_PHASE=after
: > "$test_root/trace"
recover & recovery_pid=$!
pids+=("$recovery_pid")
for ((i=0; i<500; i++)); do
  [[ -e "$test_root/ready" ]] && break
  /bin/sleep 0.02
done
[[ -e "$test_root/ready" ]] || { cat "$test_root/recovery.log"; fail 'recovery did not reach claimed phase'; }
job_can_start && fail 'job entered before recovery mutation'
/bin/bash -uc 'source "$TEST_CONF"; acquire_backup_job borg bigboi' || fail 'unrelated disk was excluded'
: > "$test_root/go"
wait "$recovery_pid" || { cat "$test_root/recovery.log"; fail 'claimed recovery failed'; }
assert_eq $'drain\nconsumers\nmutation' "$(< "$test_root/trace")" 'recovery ownership order'
job_can_start || fail 'successful recovery retained claims after exit'

for TEST_PHASE in drain-fails consumers-fail recovery-fails; do
  export TEST_PHASE
  : > "$test_root/trace"
  if recover; then fail "$TEST_PHASE was reported successful"; fi
  job_can_start || fail "$TEST_PHASE leaked a lifecycle claim"
done
echo 'PASS: stale recovery retains exact disk ownership through mutation and releases on failure'
