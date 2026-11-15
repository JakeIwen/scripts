#!/bin/bash
# Runs rootfs_tidy.sh against a fixture home.

set -u

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)
test_root=$(cd -P "$(mktemp -d)" && pwd -P)
trap 'chmod -R u+rwx "$test_root" 2>/dev/null; rm -rf "$test_root"' EXIT
# shellcheck source=../lib.sh
source "$repo_root/pi/tests/lib.sh"
tidy="$repo_root/pi/scripts/rootfs_tidy.sh"

fake_bin="$test_root/bin"
home="$test_root/home"
mkdir -p "$fake_bin"
printf '#!/bin/bash\necho 0\n' > "$fake_bin/id"
printf '#!/bin/bash\nexit 0\n' > "$fake_bin/noop"
# A fake setpriv records the identity.
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
  rm -rf "$home" "$test_root/outside"
  : > "$test_root/setpriv.log"
  mkdir -p "$home/.codex/archived_sessions/sub" \
    "$home/.codex/migration-backups/b1" "$home/.codex/sessions" \
    "$home/dev/obd-things/tmp/suite-old/inner" "$home/dev/obd-things/tmp/suite-new" \
    "$home/dev/obd-things/tmp/suite-busy/inner" \
    "$home/dev/obd-things/tmp/playwright" "$home/dev/obd-things/tmp/playwright-old" \
    "$home/dev/obd-things/tmp/playwrightNotes" "$home/dev/obd-things/tmp/compute/job" "$home/.npm/_cacache" "$home/.cache/pip" \
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
    "$home/dev/obd-things/tmp/playwrightNotes" "$home/dev/obd-things/tmp/compute/job"
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
  ROOTFS_TIDY_SWEEPS_PYTHON="$python" \
  ROOTFS_TIDY_SETPRIV="$fake_bin/setpriv" TEST_SETPRIV_LOG="$test_root/setpriv.log" \
  ROOTFS_TIDY_BIG_BYTES=1000 \
    bash "$tidy" "$@"
}
dbg() { cat "$test_root/out" >&2; }
snapshot() { (cd "$test_root" && find home | sort); }
exists() { [[ -e "$1" || -L "$1" ]]; }

# Dry run changes nothing and names what it would do.
build_fixture
before=$(snapshot)
run_tidy > "$test_root/out" 2>&1 || fail "dry-run failed"
assert_eq "$before" "$(snapshot)" "dry-run changed the tree"
grep -Fq "would delete archived_sessions/old.jsonl" "$test_root/out" || { dbg; fail "dry-run missed old session"; }
grep -Fq "would remove suite-old" "$test_root/out" || fail "dry-run missed suite dir"

# Apply.
run_tidy --apply > "$test_root/out" 2>&1 || fail "apply failed"
exists "$home/.codex/archived_sessions/old.jsonl" && fail "old session left in place"
exists "$home/.codex/archived_sessions/sub/nested.jsonl" && fail "nested session left in place"
grep -Fq "Codex sessions: 2 file(s) older than 30 days, deleted 2, 11 bytes" "$test_root/out" || { dbg; fail "delete summary"; }
exists "$home/.codex/archived_sessions/new.jsonl" || fail "recent session was deleted"
exists "$home/.codex/archived_sessions/link.jsonl" || fail "symlink was touched"
exists "$home/.codex/sessions/live.jsonl" || fail "live sessions touched"
exists "$home/.codex/migration-backups/b1/state.sqlite" || fail "migration backups are not swept"
tmp="$home/dev/obd-things/tmp"
exists "$tmp/suite-old" && fail "old suite dir kept"
exists "$tmp/suite-busy" || fail "suite dir with recent write inside was removed"
exists "$tmp/suite-new" || fail "recent suite dir removed"
exists "$tmp/playwright-old" && fail "old playwright cache kept"
exists "$tmp/playwright" || fail "tmp/playwright itself must never be swept"
exists "$tmp/playwrightNotes" || fail "only playwright-* siblings are swept"
exists "$tmp/compute/job" || fail "compute touched"
exists "$home/.npm/_cacache/oldfile" && fail "old npm cache file kept"
exists "$home/.npm/_cacache/newfile" || fail "new npm cache file removed"
exists "$home/.cache/pip/oldfile" && fail "old pip cache file kept"
exists "$home/.cache/mozilla/oldfile" || fail "mozilla cache must not be swept"
for f in bigold bignew smallold; do exists "$home/Documents/agent-shared/$f.bin" || fail "agent-shared $f deleted"; done
grep -Fq "bigold.bin" "$test_root/out" || fail "big old file not reported"
grep -Fq "bignew.bin" "$test_root/out" && fail "recent big file reported"
grep -Fq "smallold.bin" "$test_root/out" && fail "small file reported"

# A failed sweep fails the whole run but not the other sweeps.
if [[ $(id -u) != 0 ]]; then
  build_fixture
  chmod 000 "$home/.codex/archived_sessions"
  if run_tidy --apply > "$test_root/out" 2>&1; then fail "an unreadable Codex archive reported success"; fi
  chmod 700 "$home/.codex/archived_sessions"
  exists "$home/.codex/archived_sessions/old.jsonl" || fail "deleted by a failed sweep"
  exists "$tmp/suite-old" && fail "other sweeps did not run after a Codex sweep failure"
fi

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

echo "PASS: rootfs tidy sweeps delete, age-filter and report safely"
