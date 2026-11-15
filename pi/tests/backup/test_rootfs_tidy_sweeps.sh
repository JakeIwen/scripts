#!/bin/bash
# Runs rootfs_tidy.sh against a fixture home and archive mount.

set -u

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)
test_root=$(mktemp -d)
trap 'rm -rf "$test_root"' EXIT
# shellcheck source=../lib.sh
source "$repo_root/pi/tests/lib.sh"
tidy="$repo_root/pi/scripts/rootfs_tidy.sh"

fake_bin="$test_root/bin"
home="$test_root/home"
mount_dir="$test_root/mnt"
mkdir -p "$fake_bin"
printf '#!/bin/bash\necho 0\n' > "$fake_bin/id"
printf '#!/bin/bash\nexit 0\n' > "$fake_bin/noop"
printf '#!/bin/bash\nexit "${TEST_MOUNT_STATUS:-0}"\n' > "$fake_bin/mountpoint"
chmod +x "$fake_bin"/*

OLD=202001010000
build_fixture() {
  rm -rf "$home" "$mount_dir"
  mkdir -p "$mount_dir" "$home/.codex/archived_sessions/sub" \
    "$home/.codex/migration-backups/b1" "$home/.codex/sessions" \
    "$home/dev/obd-things/tmp/suite-old/inner" "$home/dev/obd-things/tmp/suite-new" \
    "$home/dev/obd-things/tmp/suite-busy/inner" \
    "$home/dev/obd-things/tmp/playwright" "$home/dev/obd-things/tmp/playwright-old" \
    "$home/dev/obd-things/tmp/compute/job" "$home/.npm/_cacache" "$home/.cache/pip" \
    "$home/.cache/mozilla" "$home/Documents/agent-shared"
  echo old > "$home/.codex/archived_sessions/old.jsonl"
  echo nested > "$home/.codex/archived_sessions/sub/nested.jsonl"
  echo new > "$home/.codex/archived_sessions/new.jsonl"
  echo mig > "$home/.codex/migration-backups/b1/state.sqlite"
  echo live > "$home/.codex/sessions/live.jsonl"
  ln -s ../sessions/live.jsonl "$home/.codex/archived_sessions/link.jsonl"
  touch -t $OLD "$home/.codex/archived_sessions/old.jsonl" \
    "$home/.codex/archived_sessions/sub/nested.jsonl" \
    "$home/.codex/migration-backups/b1/state.sqlite" "$home/.codex/sessions/live.jsonl"
  echo x > "$home/dev/obd-things/tmp/suite-old/inner/f"
  echo x > "$home/dev/obd-things/tmp/suite-busy/inner/f"
  touch -t $OLD "$home/dev/obd-things/tmp/suite-old/inner/f" \
    "$home/dev/obd-things/tmp/suite-old/inner" "$home/dev/obd-things/tmp/suite-old" \
    "$home/dev/obd-things/tmp/suite-busy" "$home/dev/obd-things/tmp/suite-busy/inner" \
    "$home/dev/obd-things/tmp/playwright-old" "$home/dev/obd-things/tmp/playwright" \
    "$home/dev/obd-things/tmp/compute/job"
  echo x > "$home/dev/obd-things/tmp/suite-new/f"
  echo c > "$home/.npm/_cacache/oldfile"; echo c > "$home/.npm/_cacache/newfile"
  echo c > "$home/.cache/pip/oldfile"; echo c > "$home/.cache/mozilla/oldfile"
  touch -t $OLD "$home/.npm/_cacache/oldfile" "$home/.cache/pip/oldfile" "$home/.cache/mozilla/oldfile"
  head -c 2000 /dev/zero > "$home/Documents/agent-shared/bigold.bin"
  head -c 2000 /dev/zero > "$home/Documents/agent-shared/bignew.bin"
  head -c 10 /dev/zero > "$home/Documents/agent-shared/smallold.bin"
  touch -t $OLD "$home/Documents/agent-shared/bigold.bin" "$home/Documents/agent-shared/smallold.bin"
}

run_tidy() {
  ROOTFS_TIDY_CODEX_CLEANUP=/fixture/none ROOTFS_TIDY_PYTHON="$fake_bin/noop" \
  ROOTFS_TIDY_ID="$fake_bin/id" ROOTFS_TIDY_SUDO="$fake_bin/noop" \
  ROOTFS_TIDY_APT_GET="$fake_bin/noop" ROOTFS_TIDY_HOME="$home" \
  ROOTFS_TIDY_ARCHIVE_MOUNT="$mount_dir" ROOTFS_TIDY_MOUNTPOINT="$fake_bin/mountpoint" \
  ROOTFS_TIDY_BIG_BYTES=1000 \
    bash "$tidy" "$@"
}
dbg() { cat "$test_root/out" >&2; }
snapshot() { (cd "$test_root" && find home mnt | sort); }
exists() { [[ -e "$1" || -L "$1" ]]; }

# Dry run changes nothing and names what it would do.
build_fixture
before=$(snapshot)
run_tidy > "$test_root/out" 2>&1 || fail "dry-run failed"
assert_eq "$before" "$(snapshot)" "dry-run changed the tree"
grep -Fq "would move archived_sessions/old.jsonl" "$test_root/out" || { dbg; fail "dry-run missed old session"; }
grep -Fq "would remove suite-old" "$test_root/out" || fail "dry-run missed suite dir"

# Apply.
run_tidy --apply > "$test_root/out" 2>&1 || fail "apply failed"
archive="$mount_dir/vanpi-root-archive/codex"
exists "$home/.codex/archived_sessions/old.jsonl" && fail "old session left in place"
exists "$home/.codex/archived_sessions/sub/nested.jsonl" && fail "nested session left in place"
exists "$home/.codex/migration-backups/b1/state.sqlite" && fail "old migration backup left"
assert_eq old "$(cat "$archive/archived_sessions/old.jsonl")" "archived copy content"
assert_eq nested "$(cat "$archive/archived_sessions/sub/nested.jsonl")" "nested copy content"
assert_eq mig "$(cat "$archive/migration-backups/b1/state.sqlite")" "migration copy content"
exists "$archive/archived_sessions/old.jsonl.tidy-part" && fail "temp copy left behind"
exists "$home/.codex/archived_sessions/new.jsonl" || fail "recent session was moved"
exists "$home/.codex/archived_sessions/link.jsonl" || fail "symlink was touched"
exists "$home/.codex/sessions/live.jsonl" || fail "live sessions touched"
exists "$archive/archived_sessions/new.jsonl" && fail "recent session archived"
tmp="$home/dev/obd-things/tmp"
exists "$tmp/suite-old" && fail "old suite dir kept"
exists "$tmp/suite-busy" || fail "suite dir with recent write inside was removed"
exists "$tmp/suite-new" || fail "recent suite dir removed"
exists "$tmp/playwright-old" && fail "old playwright cache kept"
exists "$tmp/playwright" || fail "tmp/playwright itself must never be swept"
exists "$tmp/compute/job" || fail "compute touched"
exists "$home/.npm/_cacache/oldfile" && fail "old npm cache file kept"
exists "$home/.npm/_cacache/newfile" || fail "new npm cache file removed"
exists "$home/.cache/pip/oldfile" && fail "old pip cache file kept"
exists "$home/.cache/mozilla/oldfile" || fail "mozilla cache must not be swept"
for f in bigold bignew smallold; do exists "$home/Documents/agent-shared/$f.bin" || fail "agent-shared $f deleted"; done
grep -Fq "bigold.bin" "$test_root/out" || fail "big old file not reported"
grep -Fq "bignew.bin" "$test_root/out" && fail "recent big file reported"
grep -Fq "smallold.bin" "$test_root/out" && fail "small file reported"

# A checksum mismatch keeps the source and fails the run.
build_fixture
printf '#!/bin/bash\nfor a; do :; done\nif [[ $a == *.tidy-part ]]; then echo bad; else echo good; fi\n' > "$fake_bin/sha"
chmod +x "$fake_bin/sha"
if ROOTFS_TIDY_SHA256="$fake_bin/sha" run_tidy --apply > "$test_root/out" 2>&1; then
  fail "checksum mismatch reported success"
fi
exists "$home/.codex/archived_sessions/old.jsonl" || fail "source deleted despite mismatch"
exists "$mount_dir/vanpi-root-archive/codex/archived_sessions/old.jsonl" && fail "unverified copy published"

# Unmounted or unknown mount state skips the Codex move only.
for status in 1 2; do
  build_fixture
  TEST_MOUNT_STATUS=$status run_tidy --apply > "$test_root/out" 2>&1 || fail "unmounted run failed ($status)"
  grep -Fq "Codex archive: skipped" "$test_root/out" || fail "skip not logged ($status)"
  exists "$home/.codex/archived_sessions/old.jsonl" || fail "moved while unmounted ($status)"
  exists "$mount_dir/vanpi-root-archive" && fail "wrote archive while unmounted ($status)"
  exists "$tmp/suite-old" && fail "other sweeps skipped with unmounted disk ($status)"
done

echo "PASS: rootfs tidy sweeps move, verify, age-filter and report safely"
