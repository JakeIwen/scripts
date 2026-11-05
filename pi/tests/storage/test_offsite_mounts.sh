#!/bin/bash
# offsite_mounts.sh admission modes and release outcomes with real flock,
# fdinfo, findmnt and tmpfs mounts. The test re-executes itself in a private
# user + mount namespace, so its mounts over /mnt never reach the host.
set -u

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)
source "$repo_root/pi/tests/lib.sh"
for required in /usr/bin/flock /usr/bin/unshare /usr/bin/findmnt /usr/bin/mount /usr/bin/umount /usr/bin/python3; do
  [[ -x "$required" && -d /proc/self/fdinfo ]] || {
    echo "SKIP: offsite mount helper needs Linux flock, fdinfo, findmnt and unshare"
    exit 0
  }
done
if [[ ${OFFSITE_MOUNTS_TEST_NAMESPACE:-} != 1 ]]; then
  /usr/bin/unshare --user --map-root-user /bin/true 2>/dev/null || {
    echo "SKIP: offsite mount helper test needs unprivileged user namespaces"
    exit 0
  }
  OFFSITE_MOUNTS_TEST_NAMESPACE=1 exec /usr/bin/unshare --user --map-root-user --mount \
    --propagation private /bin/bash "${BASH_SOURCE[0]}" "$@"
fi
# Refuse to mount anything unless every mount here is private to this namespace.
/usr/bin/awk '{ for (i = 7; $i != "-"; i++) if ($i ~ /^shared:/) bad = 1 } END { exit bad }' \
  /proc/self/mountinfo || fail "mount namespace is not private; refusing fixture mounts"

test_root=$(mktemp -d)
holder_pid=
cleanup() {
  [[ -z "$holder_pid" ]] || { kill -TERM "$holder_pid" 2>/dev/null; wait "$holder_pid" 2>/dev/null; }
  find "$test_root" -depth -delete
}
trap cleanup EXIT
/usr/bin/mount -t tmpfs offsite-test-root /mnt || fail "cannot mount the private /mnt fixture"

tree="$test_root/scripts"
mkdir -p "$tree/backup"
for name in offsite_mounts.sh backup_conf.sh backup_deploy_state.sh backup_locks.sh backup_lock_io.sh \
    backup_lock_registry.sh backup_locks.py backup_admission.py; do
  cp "$repo_root/pi/scripts/backup/$name" "$tree/backup/$name"
done
: > "$tree/backup/backup_settings_control.py"
secrets=/home/pi/secrets/.bash_variables
[[ $(grep -c -F "$secrets" "$tree/backup/backup_conf.sh") == 1 ]] || fail "backup_conf.sh secrets line moved"
sed -i "s|$secrets|$test_root/no-secrets|g" "$tree/backup/backup_conf.sh"
# A fixture-only label: the copied mount table still lists the host's real
# /mnt/<label> mounts beneath the private /mnt, so never touch a real label.
label=offsite-test
grep -q '^BACKUP_DISK_LABELS=(' "$tree/backup/backup_lock_registry.sh" &&
  grep -q "^DISK_LABELS = (" "$tree/backup/backup_locks.py" || fail "registry vocabulary moved"
sed -i "s|^BACKUP_DISK_LABELS=(|BACKUP_DISK_LABELS=($label |" "$tree/backup/backup_lock_registry.sh"
sed -i "s|^DISK_LABELS = (|DISK_LABELS = ('$label', |" "$tree/backup/backup_locks.py"
/usr/bin/findmnt -rn -M "/mnt/$label" >/dev/null && fail "/mnt/$label is already a mount point"
# The real mount library, with only its disk-touching entry points stubbed.
cat > "$tree/mount_disks.sh" <<'STUB'
. "$REAL_MOUNT_DISKS" || return 1
disk_policy_resolve_exact_label() {
  [[ -z ${TEST_RESOLVE_FAIL:-} ]] || return 1
  DISK_POLICY_RESOLVED_DEVICE=${TEST_RESOLVED_DEVICE:-offsite-test-device}
}
mntdsk() {
  printf 'mntdsk %s\n' "$1" >> "$TEST_EVENTS"
  /usr/bin/mkdir -p "/mnt/$1" && /usr/bin/mount -t tmpfs offsite-test-device "/mnt/$1"
}
STUB

export REAL_MOUNT_DISKS="$repo_root/pi/scripts/mount_disks.sh" TEST_EVENTS="$test_root/events"
export VANPI_BACKUP_LOCK_DIR="$test_root/registry" VANPI_BACKUP_JOB_LOCK="$test_root/legacy.lock"
export VANPI_BACKUP_DEPLOY_STATE_DIR="$test_root/deploy-state"
export VANPI_BACKUP_JOB_LOCK_OWNER=$(/usr/bin/id -un) VANPI_BACKUP_JOB_LOCK_GROUP=$(/usr/bin/id -gn)
export VANPI_BACKUP_JOB_LOCK_MODE=660 VANPI_BACKUP_LOCK_DIR_MODE=770
export OFFSITE_IGNITION_FLAG="$test_root/ignition"
unset OFFSITE_ADMISSION VANPI_BACKUP_JOB_LOCK_FD VANPI_BACKUP_DISK_LOCK_FDS
mkdir -m 0770 "$VANPI_BACKUP_LOCK_DIR"
: > "$VANPI_BACKUP_JOB_LOCK"
: > "$TEST_EVENTS"
(
  . "$tree/backup/backup_lock_registry.sh"
  for name in "${BACKUP_JOB_KINDS[@]/#/job-}" "${BACKUP_DISK_LABELS[@]/#/disk-}" \
      "${BACKUP_DATA_NAMES[@]/#/data-}" "cloud-$BACKUP_CLOUD_SLOT"; do
    : > "$VANPI_BACKUP_LOCK_DIR/$name.lock"
  done
)
chmod 0660 "$VANPI_BACKUP_JOB_LOCK" "$VANPI_BACKUP_LOCK_DIR"/*.lock

disk_lock="$VANPI_BACKUP_LOCK_DIR/disk-$label.lock"
helper="$tree/backup/offsite_mounts.sh"
chmod 0700 "$helper"
mounted() { /usr/bin/findmnt -rn -M "/mnt/$1" >/dev/null; }
mount_count() { /usr/bin/grep -c "^mntdsk " "$TEST_EVENTS"; }
expect() { # <status> <description> <command ...>
  local expected=$1 description=$2 status
  shift 2
  "$@" 2>>"$test_root/helper.err"
  status=$?
  assert_eq "$expected" "$status" "$description"
}
start_disk_holder() { # another job holding the disk shared
  /bin/bash -c 'exec {fd}<"$1"; /usr/bin/flock -n -s "$fd" || exit 1; : > "$2"; exec /bin/sleep 30' \
    holder "$disk_lock" "$test_root/holder.ready" &
  holder_pid=$!
  for _ in {1..100}; do [[ -e "$test_root/holder.ready" ]] && return 0; /bin/sleep 0.05; done
  fail "disk holder did not start"
}
stop_disk_holder() {
  kill -TERM "$holder_pid"; wait "$holder_pid" 2>/dev/null; holder_pid=
  rm -f "$test_root/holder.ready"
}

# The run's identity (fd 10) and its shared disk hold, as offsite_run.py passes them.
exec 10<"$VANPI_BACKUP_LOCK_DIR/job-offsite.lock"
/usr/bin/flock -n 10 || fail "cannot hold the fixture job lock"
exec 12<"$VANPI_BACKUP_LOCK_DIR/job-offsite.lock"   # open but never locked
exec 11<"$disk_lock"
/usr/bin/flock -n -s 11 || fail "cannot hold the fixture disk lock"

# Admission is explicit: no mode, an unknown mode, or a descriptor that does not
# hold the right lock in the right mode never reaches the mounter.
expect 1 "missing admission mode" env VANPI_BACKUP_JOB_LOCK_FD=10 VANPI_BACKUP_DISK_LOCK_FDS="$label=11" "$helper" mount "$label"
expect 1 "unknown admission mode" env OFFSITE_ADMISSION=either VANPI_BACKUP_JOB_LOCK_FD=10 \
  VANPI_BACKUP_DISK_LOCK_FDS="$label=11" "$helper" mount "$label"
export OFFSITE_ADMISSION=job
expect 1 "missing job descriptor" env VANPI_BACKUP_DISK_LOCK_FDS="$label=11" "$helper" mount "$label"
expect 1 "unlocked job descriptor" env VANPI_BACKUP_JOB_LOCK_FD=12 VANPI_BACKUP_DISK_LOCK_FDS="$label=11" \
  "$helper" mount "$label"
expect 1 "job descriptor on the disk lock" env VANPI_BACKUP_JOB_LOCK_FD=11 VANPI_BACKUP_DISK_LOCK_FDS="$label=11" \
  "$helper" mount "$label"
export VANPI_BACKUP_JOB_LOCK_FD=10
expect 1 "missing disk descriptors" "$helper" mount "$label"
expect 1 "another label's descriptor" env VANPI_BACKUP_DISK_LOCK_FDS="bigboi=11" "$helper" mount "$label"
expect 1 "repeated label" env VANPI_BACKUP_DISK_LOCK_FDS="$label=11,$label=11" "$helper" mount "$label"
expect 1 "malformed descriptor" env VANPI_BACKUP_DISK_LOCK_FDS="$label=x" "$helper" mount "$label"
expect 1 "unregistered label" env VANPI_BACKUP_DISK_LOCK_FDS="offsite-3tb=11" "$helper" mount offsite-3tb
exec 11<&-
exec 13<"$disk_lock"
/usr/bin/flock -n 13 || fail "cannot hold the fixture disk lock exclusively"
expect 1 "exclusive instead of shared disk hold" env VANPI_BACKUP_DISK_LOCK_FDS="$label=13" "$helper" mount "$label"
exec 13<&-
exec 11<"$disk_lock"
/usr/bin/flock -n -s 11 || fail "cannot re-hold the fixture disk lock"
: > "$OFFSITE_IGNITION_FLAG"
expect 1 "ignition on" env VANPI_BACKUP_DISK_LOCK_FDS="$label=11" "$helper" mount "$label"
rm "$OFFSITE_IGNITION_FLAG"
assert_eq 0 "$(mount_count)" "refused admissions reached the mounter"
mounted "$label" && fail "a refused admission mounted the disk"

expect 0 "admitted job mount" env VANPI_BACKUP_DISK_LOCK_FDS="$label=11" "$helper" mount "$label"
mounted "$label" || fail "admitted mount left the disk unmounted"
assert_eq 1 "$(mount_count)" "admitted mount count"

# Release: the run closes its own disk descriptor first. Another holder keeps
# the disk mounted (3); a disk already unmounted (by a drain) is 4; unsafe or
# undeterminable state is an error (1) that never unmounts.
expect 3 "release while this run still holds its descriptor" "$helper" release "$label"
exec 11<&-
start_disk_holder
expect 3 "release while another job holds the disk" "$helper" release "$label"
mounted "$label" || fail "busy release unmounted the disk"
stop_disk_holder
chmod 0600 "$disk_lock"
expect 1 "release with unsafe lock metadata" "$helper" release "$label"
chmod 0660 "$disk_lock"
mv "$disk_lock" "$test_root/saved.lock"
expect 1 "release with a missing lock" "$helper" release "$label"
[[ ! -e "$disk_lock" ]] || fail "release created a missing lock"
mv "$test_root/saved.lock" "$disk_lock"
expect 1 "release whose source no longer matches the label" env TEST_RESOLVED_DEVICE=elsewhere "$helper" release "$label"
/usr/bin/mount -t tmpfs offsite-test-device "/mnt/$label"
expect 1 "release of a stacked mount point" "$helper" release "$label"
/usr/bin/umount "/mnt/$label"
expect 1 "release without the job identity" env VANPI_BACKUP_JOB_LOCK_FD=12 "$helper" release "$label"
# Only flock's contention status (1) means another holder; any other failure
# (usage, bad descriptor, EX_OSERR) is a cleanup error, not a harmless "busy".
flock_error="$test_root/flock-error"
printf '#!/bin/bash\nexit "$TEST_FLOCK_STATUS"\n' > "$flock_error"
chmod 0700 "$flock_error"
for code in 64 71; do
  expect 1 "release whose flock fails with $code" env VANPI_BACKUP_JOB_LOCK_FLOCK="$flock_error" \
    TEST_FLOCK_STATUS="$code" "$helper" release "$label"
done
expect 3 "release whose flock reports contention" env VANPI_BACKUP_JOB_LOCK_FLOCK="$flock_error" \
  TEST_FLOCK_STATUS=1 "$helper" release "$label"
mounted "$label" || fail "a refused release unmounted the disk"
expect 0 "release of an unused disk" "$helper" release "$label"
mounted "$label" && fail "release left the disk mounted"
# Probe the existing file through a descriptor: path-form flock would create it.
[[ -f "$disk_lock" ]] || fail "release removed the disk lock"
exec {probe}<"$disk_lock"
/usr/bin/flock -n "$probe" || fail "release kept the disk lock"
exec {probe}<&-
start_disk_holder
expect 4 "release after a drain already unmounted the disk" "$helper" release "$label"
stop_disk_holder

# prepare-disk admission: only the legacy lock held exclusively on fd 9.
exec 10<&-
unset VANPI_BACKUP_JOB_LOCK_FD
export OFFSITE_ADMISSION=prepare
exec 9<"$VANPI_BACKUP_JOB_LOCK"
/usr/bin/flock -n -s 9 || fail "cannot hold the legacy lock shared"
expect 1 "prepare mount with a shared legacy hold" "$helper" mount "$label"
exec 9<&-
expect 1 "prepare mount without fd 9" "$helper" mount "$label"
exec 9<"$VANPI_BACKUP_JOB_LOCK"
/usr/bin/flock -n 9 || fail "cannot hold the legacy lock"
expect 0 "prepare mount" "$helper" mount "$label"
mounted "$label" || fail "prepare mount left the disk unmounted"
expect 0 "prepare release" "$helper" release "$label"
mounted "$label" && fail "prepare release left the disk mounted"
expect 1 "job mode is not satisfied by the legacy lock" env OFFSITE_ADMISSION=job VANPI_BACKUP_JOB_LOCK_FD=9 \
  "$helper" release "$label"
exec 9<&-

echo "PASS: offsite mount helper admission modes and release outcomes"
