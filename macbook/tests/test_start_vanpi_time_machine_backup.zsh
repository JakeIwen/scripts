#!/bin/zsh
set -u

repo_root="${0:A:h:h:h}"
runner="$repo_root/macbook/scripts/start_vanpi_time_machine_backup.zsh"
test_root=$(/usr/bin/mktemp -d)
trap '/bin/rm -rf -- "$test_root"' EXIT

fail() {
  print -u2 -- "FAIL: $*"
  exit 1
}

fake_bin="$test_root/bin"
call_log="$test_root/calls"
/bin/mkdir -p "$fake_bin"
: > "$call_log"

cat > "$fake_bin/nc" <<'EOF'
#!/bin/zsh
exit "${TEST_NC_STATUS:-0}"
EOF

cat > "$fake_bin/ssh" <<'EOF'
#!/bin/zsh
exit "${TEST_SSH_STATUS:-0}"
EOF

cat > "$fake_bin/tmutil" <<'EOF'
#!/bin/zsh
[[ "$1" == "startbackup" ]] || exit 2
print -r -- "$*" >> "$TEST_CALL_LOG"
EOF

/bin/chmod +x "$fake_bin/nc" "$fake_bin/ssh" "$fake_bin/tmutil"

run_runner() {
  TM_PREFLIGHT_NC="$fake_bin/nc" \
  TM_PREFLIGHT_SSH="$fake_bin/ssh" \
  TM_PREFLIGHT_TMUTIL="$fake_bin/tmutil" \
  TEST_CALL_LOG="$call_log" \
  TEST_NC_STATUS="${TEST_NC_STATUS:-0}" \
  TEST_SSH_STATUS="${TEST_SSH_STATUS:-0}" \
  /bin/zsh "$runner" "$@"
}

run_runner || fail "ready destination did not start a backup"
[[ "$(<"$call_log")" == "startbackup --auto" ]] ||
  fail "tmutil was called with unexpected arguments"

: > "$call_log"
ready_output=$(run_runner --check) || fail "ready check failed"
[[ "$ready_output" == ready:* ]] || fail "ready check lacks diagnostics"
[[ ! -s "$call_log" ]] || fail "check mode started a backup"

: > "$call_log"
TEST_NC_STATUS=1 run_runner ||
  fail "an unreachable server should be a quiet launchd skip"
[[ ! -s "$call_log" ]] || fail "unreachable SMB started a backup"
TEST_NC_STATUS=1 run_runner --check >/dev/null 2>&1 &&
  fail "check mode accepted unreachable SMB"

: > "$call_log"
TEST_SSH_STATUS=1 run_runner ||
  fail "an unmounted disk should be a quiet launchd skip"
[[ ! -s "$call_log" ]] || fail "an unmounted disk started a backup"

echo "PASS: guarded Time Machine scheduling fails closed"
