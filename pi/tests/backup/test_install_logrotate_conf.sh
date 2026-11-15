#!/bin/bash
# install_logrotate_conf.sh must install only after logrotate -d exits 0 on a
# root-owned copy of the effective configuration.

set -u

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)
test_root=$(mktemp -d)
trap 'rm -rf "$test_root"' EXIT
# shellcheck source=../lib.sh
source "$repo_root/pi/tests/lib.sh"
installer="$repo_root/pi/scripts/install_logrotate_conf.sh"
candidate="$repo_root/pi/configs/logrotate-rsyslog"

bin="$test_root/bin"; mkdir -p "$bin"
# Fake sudo: runs the command, dropping install's -o/-g (the test is not root);
# TEST_SUDO_FAIL=1 simulates a sudo that refuses. TEST_PUBLISH_FAIL breaks the
# publication: `short` writes 16 bytes of the staged copy and fails, as a full
# disk does; `rename` fails the mv into place.
cat > "$bin/sudo" <<'EOF2'
#!/bin/bash
[[ ${TEST_SUDO_FAIL:-0} == 1 ]] && exit 1
shift
args=()
while [[ $# -gt 0 ]]; do
  case $1 in -o|-g) shift 2 ;; *) args+=("$1"); shift ;; esac
done
dest=${args[${#args[@]}-1]}
if [[ ${args[0]} == */install && $dest == */.logrotate-rsyslog.* && ${TEST_PUBLISH_FAIL:-} == short ]]; then
  head -c 16 "${args[${#args[@]}-2]}" > "$dest"; exit 1
fi
[[ ${args[0]} == */mv && ${TEST_PUBLISH_FAIL:-} == rename ]] && exit 1
exec "${args[@]}"
EOF2
# Fake sync: records what was flushed (macOS sync takes no file arguments).
cat > "$bin/sync" <<'EOF2'
#!/bin/bash
echo "$*" >> "$TEST_SYNC_LOG"
EOF2
# Fake logrotate: exit status from TEST_LR_STATUS; prints the stanza it was
# given, as the real -d does, and records the config it loaded.
cat > "$bin/logrotate" <<'EOF2'
#!/bin/bash
cat "$2" > "$TEST_LR_SEEN"; cat "$(sed -n 's/^include //p' "$2")"/rsyslog >> "$TEST_LR_SEEN"
echo "rotating pattern: /var/log/syslog"
exit "${TEST_LR_STATUS:-0}"
EOF2
chmod +x "$bin"/*

reset() {
  rm -rf "$test_root/logrotate.d" "$test_root/main"
  mkdir -p "$test_root/logrotate.d"
  echo "old stanza" > "$test_root/logrotate.d/rsyslog"; echo "other" > "$test_root/logrotate.d/samba"
  printf 'weekly\ninclude %s/logrotate.d\n' "$test_root" > "$test_root/main"
  : > "$test_root/seen"; : > "$test_root/sync.log"
}
run() {
  INSTALL_LOGROTATE_SUDO="$bin/sudo" INSTALL_LOGROTATE_BIN="${LR_BIN:-$bin/logrotate}" \
  INSTALL_LOGROTATE_MAIN="$test_root/main" INSTALL_LOGROTATE_DIR="$test_root/logrotate.d" \
  INSTALL_LOGROTATE_SYNC="$bin/sync" TEST_SYNC_LOG="$test_root/sync.log" \
  TEST_LR_SEEN="$test_root/seen" bash "$installer" "$candidate"
}
no_staged_copies() {
  ! ls -a "$test_root" | grep -q '^\.logrotate-rsyslog\.' || fail "staged copy left behind: $1"
}

dbg() { cat "$test_root/out" >&2; }
reset; run > "$test_root/out" 2>&1 || { dbg; fail "valid candidate refused"; }
cmp -s "$candidate" "$test_root/logrotate.d/rsyslog" || fail "valid candidate not installed"
grep -q "maxsize 50M" "$test_root/seen" || fail "logrotate did not load the candidate"
grep -q "^other" "$test_root/logrotate.d/samba" || fail "sibling config disturbed"
grep -q '/\.logrotate-rsyslog\.' "$test_root/sync.log" || fail "staged copy not flushed before the rename"
grep -qx -- "-- $test_root/logrotate.d" "$test_root/sync.log" || fail "directory not flushed after the rename"
no_staged_copies "after a successful install"
run > "$test_root/out" 2>&1 || fail "unchanged rerun failed"
grep -q "already current" "$test_root/out" || fail "unchanged rerun did not no-op"

# A failed publication keeps the old file whole and cleans up its staged copy.
for mode in short rename; do
  reset
  if TEST_PUBLISH_FAIL=$mode run > "$test_root/out" 2>&1; then fail "publication failure ($mode) reported success"; fi
  assert_eq "old stanza" "$(cat "$test_root/logrotate.d/rsyslog")" "live file damaged by a failed publication ($mode)"
  no_staged_copies "after a failed publication ($mode)"
  ls "$test_root/logrotate.d" | grep -vqx 'rsyslog\|samba' && fail "stray file in logrotate.d ($mode)"
done

reset; if TEST_LR_STATUS=1 run > /dev/null 2>&1; then fail "logrotate failure installed"; fi
assert_eq "old stanza" "$(cat "$test_root/logrotate.d/rsyslog")" "installed despite a failing logrotate -d"

reset; if LR_BIN="$test_root/missing" run > /dev/null 2>&1; then fail "missing logrotate installed"; fi
assert_eq "old stanza" "$(cat "$test_root/logrotate.d/rsyslog")" "installed with no logrotate binary"

reset; if TEST_SUDO_FAIL=1 run > /dev/null 2>&1; then fail "sudo failure installed"; fi
assert_eq "old stanza" "$(cat "$test_root/logrotate.d/rsyslog")" "installed despite sudo failure"

reset; echo "weekly" > "$test_root/main"
if run > /dev/null 2>&1; then fail "main conf without the include installed"; fi
assert_eq "old stanza" "$(cat "$test_root/logrotate.d/rsyslog")" "installed without validating the include"

ls /tmp 2>/dev/null | grep -q '^vanpi-logrotate-check\.' && fail "work dir left behind"
echo "PASS: logrotate installer validates the effective config by exit status"
