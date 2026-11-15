#!/bin/bash
# Runs rootfs_tidy.sh against a fixture home and archive mount.

set -u

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)
test_root=$(cd -P "$(mktemp -d)" && pwd -P)
trap 'chmod -R u+rwx "$test_root" 2>/dev/null; rm -rf "$test_root"' EXIT
# shellcheck source=../lib.sh
source "$repo_root/pi/tests/lib.sh"
tidy="$repo_root/pi/scripts/rootfs_tidy.sh"

fake_bin="$test_root/bin"
home="$test_root/home"
mount_dir="$test_root/mnt"
mkdir -p "$fake_bin"
printf '#!/bin/bash\necho 0\n' > "$fake_bin/id"
printf '#!/bin/bash\nexit 0\n' > "$fake_bin/noop"
# A fake setpriv records the identity. The archive volume (fake findmnt and
# mountinfo, printing util-linux's real formats) comes from the fixture module.
python=$(command -v python3) || fail "python3 is required"
cat > "$fake_bin/setpriv" <<'EOF2'
#!/bin/bash
echo "$*" >> "$TEST_SETPRIV_LOG"
while [[ $# -gt 0 && $1 != -- ]]; do shift; done
shift
exec "$@"
EOF2
chmod +x "$fake_bin"/*

OLD=202001010000
build_fixture() {
  chmod -R u+rwx "$home" 2>/dev/null
  rm -rf "$home" "$mount_dir" "$test_root/outside"
  : > "$test_root/setpriv.log"
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
  "$python" "$repo_root/pi/tests/backup/rootfs_archive_fixtures.py" "$fake_bin" "$mount_dir" \
    "${TEST_UUID:-122C-A219}" || fail "volume fixture"
}

run_tidy() {
  ROOTFS_TIDY_CODEX_CLEANUP=/fixture/none ROOTFS_TIDY_PYTHON="$fake_bin/noop" \
  ROOTFS_TIDY_ID="$fake_bin/id" ROOTFS_TIDY_SUDO="$fake_bin/noop" \
  ROOTFS_TIDY_APT_GET="$fake_bin/noop" ROOTFS_TIDY_HOME="$home" \
  ROOTFS_TIDY_ARCHIVE_MOUNT="$mount_dir" ROOTFS_TIDY_FINDMNT="$fake_bin/findmnt" \
  ROOTFS_TIDY_MOUNTINFO="$fake_bin/mountinfo" ROOTFS_TIDY_ARCHIVE_PYTHON="$python" \
  ROOTFS_TIDY_SETPRIV="$fake_bin/setpriv" TEST_SETPRIV_LOG="$test_root/setpriv.log" \
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
ls "$mount_dir/vanpi-root-archive/.tidy-tmp" | grep -q . && fail "temp copy left behind"
grep -Fq "Codex archive: 3 file(s) older than 30 days, 3 moved" "$test_root/out" || { dbg; fail "archive summary"; }
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

# A wrong volume skips the Codex move only and writes nothing to it; the
# module's own tests cover every identity check.
TEST_UUID=OTHER-UUID build_fixture
run_tidy --apply > "$test_root/out" 2>&1 || { dbg; fail "wrong-volume run failed"; }
grep -Fq "Codex archive: skipped" "$test_root/out" || fail "skip not logged"
exists "$home/.codex/archived_sessions/old.jsonl" || fail "moved to the wrong volume"
exists "$mount_dir/vanpi-root-archive" && fail "wrote the archive on the wrong volume"
exists "$tmp/suite-old" && fail "other sweeps skipped on the wrong volume"

# A failed archive step fails the whole run but not the other sweeps.
build_fixture
mkdir -p "$mount_dir/vanpi-root-archive/codex/archived_sessions"
echo other > "$mount_dir/vanpi-root-archive/codex/archived_sessions/old.jsonl"
if run_tidy --apply > "$test_root/out" 2>&1; then fail "a kept archive file reported success"; fi
exists "$home/.codex/archived_sessions/old.jsonl" || fail "source deleted over a different archive copy"
exists "$tmp/suite-old" && fail "other sweeps did not run after an archive failure"

# Root runs the sweeps as pi, not as root.
build_fixture
run_tidy --apply > /dev/null 2>&1
grep -Fq -- "--reuid=pi" "$test_root/setpriv.log" || fail "sweeps did not drop to pi"

# A symlinked ancestor is refused; nothing outside the home is deleted.
build_fixture
mkdir -p "$test_root/outside/pip" "$test_root/outside/aux"
echo c > "$test_root/outside/pip/victim"; touch -t $OLD "$test_root/outside/pip/victim"
rm -rf "$home/.cache"; ln -s "$test_root/outside" "$home/.cache"
run_tidy --apply > "$test_root/out" 2>&1 || fail "symlinked-ancestor run failed"
exists "$test_root/outside/pip/victim" || fail "deleted through a symlinked ancestor"
grep -Fq "cache .cache/pip: skipped" "$test_root/out" || fail "symlinked ancestor not reported"

# An idle check that cannot traverse a dir keeps it and fails the run.
if [[ $(id -u) != 0 ]]; then
  build_fixture
  mkdir -p "$tmp/suite-old/locked"; touch -t $OLD "$tmp/suite-old/locked" "$tmp/suite-old"
  chmod 000 "$tmp/suite-old/locked"
  if run_tidy --apply > "$test_root/out" 2>&1; then fail "traversal error reported success"; fi
  exists "$tmp/suite-old" || fail "dir removed despite a traversal error"
  grep -Fq "could not inspect" "$test_root/out" || fail "traversal error not reported"
  chmod 700 "$tmp/suite-old/locked"
fi

echo "PASS: rootfs tidy sweeps move, verify, age-filter and report safely"
