#!/bin/bash

set -u

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)
test_root=$(mktemp -d)
trap 'rm -rf "$test_root"' EXIT

# shellcheck source=../lib.sh
source "$repo_root/pi/tests/lib.sh"
tidy="$repo_root/pi/scripts/rootfs_tidy.sh"
fake_bin="$test_root/bin"
log="$test_root/calls.log"
mkdir -p "$fake_bin"

# Every fake appends its argv to one log so tests can assert on call order.
cat > "$fake_bin/id" <<'EOF'
#!/bin/bash
printf '%s\n' "$TEST_UID"
EOF
cat > "$fake_bin/python3" <<'EOF'
#!/bin/bash
printf 'python3 %s\n' "$*" >> "$TEST_LOG"
exit "${TEST_CLEANUP_STATUS:-0}"
EOF
cat > "$fake_bin/apt-get" <<'EOF'
#!/bin/bash
printf 'apt-get %s\n' "$*" >> "$TEST_LOG"
EOF
cat > "$fake_bin/sudo" <<'EOF'
#!/bin/bash
exit 1
EOF
chmod +x "$fake_bin"/*

run_tidy() {
  : > "$log"
  ROOTFS_TIDY_CODEX_CLEANUP=/fixture/cleanup_codex_releases.py \
  ROOTFS_TIDY_PYTHON="$fake_bin/python3" \
  ROOTFS_TIDY_ID="$fake_bin/id" \
  ROOTFS_TIDY_SUDO="$fake_bin/sudo" \
  ROOTFS_TIDY_APT_GET="$fake_bin/apt-get" \
  TEST_UID="${TEST_UID:-0}" \
  TEST_CLEANUP_STATUS="${TEST_CLEANUP_STATUS:-0}" \
  TEST_LOG="$log" \
    bash "$tidy" "$@"
}

cleanup_call="python3 -I -B /fixture/cleanup_codex_releases.py --owner pi"

run_tidy > "$test_root/out" 2>&1 || fail "root dry-run failed"
grep -Fxq "$cleanup_call --dry-run" "$log" ||
  fail "root dry-run did not run the shared cleanup for pi"
grep -Fq "apt-get" "$log" && fail "dry-run ran apt-get"
grep -Fq "would run apt-get clean as root" "$test_root/out" ||
  fail "root dry-run did not describe apt cleanup"

run_tidy --apply > "$test_root/out" 2>&1 || fail "root apply failed"
grep -Fxq "$cleanup_call --apply" "$log" || fail "apply did not pass --apply to the cleanup"
assert_eq "apt-get clean" "$(tail -1 "$log")" "apply did not clean apt after Codex releases"

TEST_UID=1000 run_tidy --apply > "$test_root/out" 2>&1 || fail "non-root run failed"
grep -Fq "python3" "$log" && fail "a non-root run attempted the Codex cleanup"
grep -Fq "Codex releases: skipped (run as root" "$test_root/out" ||
  fail "non-root skip was not explicit"

TEST_CLEANUP_STATUS=75 run_tidy --apply > "$test_root/out" 2>&1 ||
  fail "a held installer lock failed the tidy run"
grep -Fq "skipped (installer lock held)" "$test_root/out" || fail "lock skip was not explicit"
assert_eq "apt-get clean" "$(tail -1 "$log")" "lock skip prevented apt cleanup"

if TEST_CLEANUP_STATUS=1 run_tidy --apply > "$test_root/out" 2>&1; then
  fail "a failed Codex cleanup was reported as success"
fi
grep -Fq "cleanup failed with status 1" "$test_root/out" || fail "cleanup failure was not explicit"
assert_eq "apt-get clean" "$(tail -1 "$log")" "cleanup failure prevented apt cleanup"

if run_tidy --bogus > /dev/null 2>&1; then
  fail "tidy accepted an unknown option"
fi

echo "PASS: rootfs tidy delegates Codex retention and cleans apt"
