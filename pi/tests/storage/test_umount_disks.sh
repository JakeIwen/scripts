#!/bin/bash

set -u

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)
script="$repo_root/pi/scripts/umount_disks.sh"
abort_script="$repo_root/pi/scripts/backup/abort_backup.sh"

# shellcheck source=../lib.sh
source "$repo_root/pi/tests/lib.sh"

export UMOUNT_DISKS_LIBRARY_ONLY=1
# shellcheck source=../../scripts/umount_disks.sh
source "$script"
hold_backup_disks_exclusive() { :; } # Never inspect the live lock registry.

ud_findmnt_source_targets() { return 1; }
reconcile_output=$(ud_reconcile_unmount_result hdd1tb /dev/sdz1 124 2>&1)
[[ $? == 0 ]] ||
  fail "a timed-out unmount was not accepted after exact-device findmnt reported it absent"

ud_findmnt_source_targets() { printf '%s\n' /mnt/hdd1tb; return 0; }
ud_reconcile_unmount_result hdd1tb /dev/sdz1 124 >/dev/null 2>&1 &&
  fail "a timed-out unmount was accepted while the exact device remained mounted"

ud_findmnt_source_targets() { echo "synthetic discovery error" >&2; return 2; }
ud_reconcile_unmount_result hdd1tb /dev/sdz1 124 >/dev/null 2>&1 &&
  fail "a timed-out unmount was accepted after findmnt failed"

# Restore the production helper for any later integration assertions.
ud_findmnt_source_targets() { /usr/bin/findmnt -rn -S "$1" -o TARGET; }

# Only --all may accept a classified vanished udev mapping, and then only when
# both the managed target and vanished source are absent from the mount table.
DISK_POLICY_RESOLVE_REASON=vanished-udev-mapping
DISK_POLICY_RESOLVE_ERROR="synthetic vanished udev mapping"
ud_findmnt_exact_source() { return 1; }
ud_findmnt_source_targets() { return 1; }
ud_accept_vanished_label_for_all mbp2tbkup /dev/vanished-test-device \
  >/dev/null 2>&1 || fail "fully unmounted vanished mapping was rejected"

ud_findmnt_exact_source() { printf '%s\n' /dev/vanished-test-device; return 0; }
if ud_accept_vanished_label_for_all mbp2tbkup /dev/vanished-test-device \
    >/dev/null 2>&1; then
  fail "vanished mapping was accepted while its managed target remained mounted"
fi

ud_findmnt_exact_source() { return 1; }
ud_findmnt_source_targets() { printf '%s\n' /mnt/unexpected; return 0; }
if ud_accept_vanished_label_for_all mbp2tbkup /dev/vanished-test-device \
    >/dev/null 2>&1; then
  fail "vanished mapping was accepted while its source remained mounted elsewhere"
fi

ud_findmnt_source_targets() { echo "synthetic mount discovery error" >&2; return 2; }
if ud_accept_vanished_label_for_all mbp2tbkup /dev/vanished-test-device \
    >/dev/null 2>&1; then
  fail "vanished mapping was accepted after mount discovery failed"
fi


# Restore production helpers for later assertions.
ud_findmnt_source_targets() { /usr/bin/findmnt -rn -S "$1" -o TARGET; }
ud_findmnt_exact_source() { /usr/bin/findmnt -rn -M "$1" -o SOURCE; }

umount_disks_main --emergency >/dev/null 2>&1
[[ $? == 2 ]] ||
  fail "emergency mode was accepted without --spindown"
umount_disks_main --spindown --emergency movingparts >/dev/null 2>&1
[[ $? == 2 ]] ||
  fail "ignition emergency mode accepted a single-label target"

(
  observed_drain=
  lifecycle_test_drain() { observed_drain="$*"; }
  backup_lifecycle_claim lifecycle_test_drain 1 movingparts || exit 1
  assert_eq '--emergency movingparts' "$observed_drain" 'emergency disk drain arguments'
  observed_drain=
  if backup_lifecycle_claim lifecycle_test_drain 0 movingparts unknown >/dev/null 2>&1; then exit 1; fi
  assert_eq '' "$observed_drain" 'invalid target set must fail before draining any disk'
) || fail 'shared lifecycle ownership did not validate and forward emergency targets'
# Retained ownership is exercised by the real-process spindown fence below.

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

# A failed ordinary unmount must evict exact-mount holders with TERM and KILL.
holder_calls=$(mktemp)
state_test_root=$(mktemp -d)
trap 'rm -f "$holder_calls"; rm -rf "$state_test_root"' EXIT
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


# Root backup cleanup and the pi-run ignition service must share the same
# spindown directory and marker files. The root path establishes a setgid pi
# directory; markers remain group-readable/writable regardless of caller UID.
state_calls="$state_test_root/calls"
state_dir="$state_test_root/state"
fake_install="$state_test_root/install"
fake_mkdir="$state_test_root/mkdir"
fake_stat="$state_test_root/stat"
cat > "$fake_install" <<'EOF'
#!/bin/bash
printf 'install:%s\n' "$*" >> "$TEST_STATE_CALLS"
target=${!#}
/bin/mkdir -p -- "$target"
/bin/chmod 770 "$target"
EOF
cat > "$fake_mkdir" <<'EOF'
#!/bin/bash
printf 'mkdir:%s\n' "$*" >> "$TEST_STATE_CALLS"
target=${!#}
/bin/mkdir -m 770 -- "$target"
EOF
cat > "$fake_stat" <<'EOF'
#!/bin/bash
target=${!#}
if [[ ${TEST_STATE_BAD_METADATA:-0} == 1 ]]; then
  printf 'root 700\n'
elif [[ "$target" == "$TEST_STATE_DIR" ]]; then
  printf 'pi 2770\n'
else
  printf 'pi 660\n'
fi
EOF
chmod +x "$fake_install" "$fake_mkdir" "$fake_stat"
export TEST_STATE_CALLS="$state_calls" TEST_STATE_DIR="$state_dir"
UD_STATE_DIR=$state_dir
UD_STATE_GROUP=pi
UD_STATE_INSTALL=$fake_install
UD_STATE_MKDIR=$fake_mkdir
UD_STATE_MV=/bin/mv
UD_STATE_RM=/bin/rm
UD_STATE_STAT=$fake_stat

: > "$state_calls"
ud_running_as_root() { return 0; }
ud_prepare_spindown_state_dir >/dev/null 2>&1 ||
  fail "root caller could not prepare shared spindown state"
grep -Fq "install:-d -o root -g pi -m 2770 -- $state_dir" "$state_calls" ||
  fail "root caller did not establish the setgid pi state directory"

ud_fast_identity() {
  UD_FAST_DEVICE=/dev/sdz1
  UD_FAST_PARENT=/dev/sdz
  UD_FAST_DISKSEQ=123
}
ud_write_spindown_state movingparts /dev/sdz1 /dev/sdz >/dev/null 2>&1 ||
  fail "root caller could not write shared spindown state"
[[ $(cat "$state_dir/movingparts") == "/dev/sdz1 /dev/sdz 123" ]] ||
  fail "spindown marker did not retain the exact device identity"

call_count=$(wc -l < "$state_calls" | tr -d ' ')
ud_running_as_root() { return 1; }
ud_prepare_spindown_state_dir >/dev/null 2>&1 ||
  fail "pi caller could not reuse root-created spindown state"
[[ $(wc -l < "$state_calls" | tr -d ' ') == "$call_count" ]] ||
  fail "pi caller tried to recreate an existing shared state directory"
ud_findmnt_source_targets() { return 1; }
ud_spindown_state_is_current movingparts >/dev/null 2>&1 ||
  fail "pi caller could not recognize a root-created current marker"
ud_remove_spindown_state movingparts ||
  fail "pi caller could not remove a root-created marker"
[[ ! -e "$state_dir/movingparts" ]] ||
  fail "spindown marker removal used the wrong label path"
ud_write_spindown_state movingparts /dev/sdz1 /dev/sdz >/dev/null 2>&1 ||
  fail "pi caller could not replace shared spindown state"

TEST_STATE_BAD_METADATA=1
export TEST_STATE_BAD_METADATA
if ud_prepare_spindown_state_dir >/dev/null 2>&1; then
  fail "pi caller accepted unsafe root-only spindown state metadata"
fi
unset TEST_STATE_BAD_METADATA

/bin/rm -f -- "$state_dir/movingparts"
/bin/rmdir -- "$state_dir"
: > "$state_calls"
ud_prepare_spindown_state_dir >/dev/null 2>&1 ||
  fail "pi caller could not create shared spindown state first"
grep -Fq "mkdir:-m 2770 -- $state_dir" "$state_calls" ||
  fail "pi caller did not create a setgid shared state directory"

# With --spindown, hd-idle stops every attached HDD, so a job that holds only a
# disk lock (past its ignition check, still mounting) must be stopped and the
# disk claimed before the physical spindown. Without --spindown an unmounted
# disk is left untouched, so its job keeps running. Real processes: the
# repository's abort_backup.sh and lock library against a private registry.
if [[ -x /usr/bin/flock && -x /usr/sbin/hd-idle && -d /proc/self/fd ]]; then
  lock_root=$(mktemp -d)
  fence_job_pid=
  fence_cleanup() {
    if [[ -n "$fence_job_pid" ]] && kill -0 "$fence_job_pid" 2>/dev/null; then
      kill -KILL "$fence_job_pid" 2>/dev/null || true
      wait "$fence_job_pid" 2>/dev/null || true
    fi
    rm -f "$holder_calls"
    rm -rf "$state_test_root" "$lock_root"
  }
  trap fence_cleanup EXIT

  export VANPI_BACKUP_LOCK_DIR="$lock_root/registry"
  export VANPI_BACKUP_JOB_LOCK="$lock_root/legacy.lock"
  export VANPI_BACKUP_JOB_LOCK_OWNER=$(/usr/bin/id -un)
  export VANPI_BACKUP_JOB_LOCK_GROUP=$(/usr/bin/id -gn)
  export VANPI_BACKUP_JOB_LOCK_MODE=660 VANPI_BACKUP_LOCK_DIR_MODE=770
  export VANPI_BACKUP_PRIORITY_TOOL="$lock_root/priority.py"
  printf '%s\n' 'raise SystemExit(0)' > "$VANPI_BACKUP_PRIORITY_TOOL"
  mkdir -m 0770 "$VANPI_BACKUP_LOCK_DIR"
  : > "$VANPI_BACKUP_JOB_LOCK"
  chmod 0660 "$VANPI_BACKUP_JOB_LOCK"
  # Replace the earlier stub and the live registry defaults with the real
  # claim against the private registry.
  unset JOB_LOCK JOB_LOCK_OWNER JOB_LOCK_GROUP JOB_LOCK_MODE
  source "$repo_root/pi/scripts/backup/backup_locks.sh"
  for kind in "${BACKUP_JOB_KINDS[@]}"; do
    : > "$(backup_lock_path job "$kind")"
  done
  for label in "${BACKUP_DISK_LABELS[@]}"; do
    : > "$(backup_lock_path disk "$label")"
  done
  chmod 0660 "$BACKUP_LOCK_DIR"/*.lock

  backup_conf="$repo_root/pi/scripts/backup/backup_conf.sh"
  fence_job="$lock_root/pi_backup.sh"
  fence_signal="$lock_root/signal"
  fence_ready="$lock_root/ready"
  spindown_calls="$lock_root/spindown"
  cat > "$fence_job" <<'HOLDER'
#!/bin/bash
set -u
source "$TEST_CONF"
# Trap first: a drain may land between the lock record and the sleep loop.
trap 'printf "TERM\n" > "$TEST_SIGNAL_FILE"; exit 143' TERM INT
acquire_backup_job borg hdd1tb || exit 4
: > "$TEST_READY_FILE"
while :; do /bin/sleep 0.1; done
HOLDER
  chmod +x "$fence_job"
  start_fence_job() {
    local attempts
    : > "$fence_signal"
    : > "$spindown_calls"
    rm -f "$fence_ready"
    TEST_CONF="$backup_conf" TEST_SIGNAL_FILE="$fence_signal" \
      TEST_READY_FILE="$fence_ready" "$fence_job" &
    fence_job_pid=$!
    for ((attempts = 0; attempts < 50; attempts++)); do
      if [[ -e "$fence_ready" ]] &&
         ! /usr/bin/flock -n "$BACKUP_LOCK_DIR/disk-hdd1tb.lock" true 2>/dev/null; then
        return 0
      fi
      /bin/sleep 0.02
    done
    return 1
  }
  disk_lock_is_shareable() {
    /usr/bin/flock -n -s "$BACKUP_LOCK_DIR/disk-hdd1tb.lock" true
  }

  # Never alert, probe udev or call hd-idle from a regression test. hdd1tb is
  # attached and unmounted in both runs; only the fence decision differs.
  ud_notify_failures() { :; }
  ud_notify_recovery() { :; }
  ud_prepare_spindown_state_dir() { return 0; }
  ud_spindown_state_is_current() { return 1; }
  ud_resolve_label() {
    UD_DEVICE=/dev/sdz1
    UD_PARENT=/dev/sdz
    UD_MOUNTS=
    DISK_POLICY_RESOLVE_REASON=
    return 0
  }
  ud_spindown() {
    # Ownership must already exist here: hd-idle runs next.
    local state=claimed
    (( spindown )) || return 0
    disk_lock_is_shareable && state=shareable
    printf '%s %s\n' "${attached_labels[*]}" "$state" > "$spindown_calls"
  }
  ud_abort_backup_jobs() {
    ABORT_BACKUP_CONF="$backup_conf" "$abort_script" "$@"
  }

  start_fence_job || fail "could not start a Borg job holding the unmounted hdd1tb lock"
  (
    umount_disks_main --spindown hdd1tb > "$lock_root/spindown.out" 2>&1 || exit 1
    # The claim must outlive the drain: hdd1tb stays exclusive until this
    # lifecycle shell exits, so a retrying job cannot slip in before hd-idle.
    if disk_lock_is_shareable; then
      echo shareable > "$lock_root/claim"
    else
      echo claimed > "$lock_root/claim"
    fi
  ) || fail "spindown of an unmounted, job-held disk failed: $(cat "$lock_root/spindown.out")"
  # Check the fence before waiting on the job: a missing fence must fail
  # here, not hang on a job nothing stopped.
  assert_eq "hdd1tb claimed" "$(< "$spindown_calls")" "hd-idle was reached without owning the disk"
  assert_eq claimed "$(< "$lock_root/claim")" "spindown did not keep the disk claimed after the drain"
  for ((attempts = 0; attempts < 100; attempts++)); do
    kill -0 "$fence_job_pid" 2>/dev/null || break
    /bin/sleep 0.1
  done
  kill -0 "$fence_job_pid" 2>/dev/null && fail "the drained job is still running after spindown"
  wait "$fence_job_pid" 2>/dev/null || true
  fence_job_pid=
  assert_eq TERM "$(< "$fence_signal")" "spindown did not stop the job holding the unmounted disk"
  disk_lock_is_shareable || fail "the lifecycle claim outlived its shell"

  start_fence_job || fail "could not restart the Borg job for the unmount-only check"
  ( umount_disks_main hdd1tb > "$lock_root/unmount.out" 2>&1 ) ||
    fail "unmount of an idle, job-held disk failed: $(cat "$lock_root/unmount.out")"
  kill -0 "$fence_job_pid" 2>/dev/null ||
    fail "an unmount without --spindown stopped the job holding an unmounted disk"
  [[ ! -s "$fence_signal" ]] || fail "an unmount without --spindown signaled the job"
  [[ ! -s "$spindown_calls" ]] || fail "hd-idle was reached without --spindown"
  kill -TERM "$fence_job_pid" 2>/dev/null || true
  wait "$fence_job_pid" 2>/dev/null || true
  fence_job_pid=
else
  echo "SKIP: spindown fence process tests need Linux flock, /proc and hd-idle"
fi

echo "PASS: guarded disk shutdown escalates consumers without force/lazy unmount"
