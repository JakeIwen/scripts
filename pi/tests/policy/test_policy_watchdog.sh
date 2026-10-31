#!/bin/bash

set -u

if [[ ! -x /usr/bin/flock ]]; then
  echo "SKIP: policy watchdog test requires /usr/bin/flock"
  exit 0
fi

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)
test_root=$(mktemp -d)
holder=
cleanup() {
  touch "$test_root/release" 2>/dev/null || true
  [[ -z "$holder" ]] || wait "$holder" 2>/dev/null || true
  rm -rf "$test_root"
}
trap cleanup EXIT

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

cat > "$test_root/notify" <<'HELPER'
#!/bin/bash
printf 'notify\n' >> "$TEST_NOTIFY_LOG"
printf '%s\n' "$2" > "$TEST_NOTIFY_LOG.body"
HELPER
chmod +x "$test_root/notify"

export ISW_SCRIPT="$repo_root/pi/scripts/internet_switches.sh"
export ISW_LOCK_FILE="$test_root/policy.lock"
export ISW_CANARY_DIR="$test_root/canary"
export ISW_JOURNALCTL=/bin/true
export ISW_NTFY_SEND="$test_root/notify"
export TEST_NOTIFY_LOG="$test_root/notifications"
export POLICY_STALL_SECONDS=2
watchdog="$repo_root/pi/scripts/policy_watchdog.sh"

bash "$watchdog" || fail "an absent lock was reported as stalled"
[[ ! -e "$TEST_NOTIFY_LOG" ]] || fail "an absent lock sent an alert"

mkdir -p "$ISW_CANARY_DIR"

start_holder() { # <record-owner 0|1>
  rm -f "$test_root/ready" "$test_root/release"
  (
    exec 9>"$ISW_LOCK_FILE"
    /usr/bin/flock 9
    if (( $1 )); then
      printf '%s %s\n' "$BASHPID" "$(/usr/bin/date +%s)" > "$ISW_CANARY_DIR/owner"
    fi
    touch "$test_root/ready"
    while [[ ! -e "$test_root/release" ]]; do
      /usr/bin/sleep 0.05
    done
  ) &
  holder=$!
  for _ in {1..100}; do
    [[ -e "$test_root/ready" ]] && return 0
    /usr/bin/sleep 0.05
  done
  fail "lock holder did not start"
}

stop_holder() {
  touch "$test_root/release"
  wait "$holder"
  holder=
}

# Holders other than internet_switches record no owner. A brief one (the
# metering reconciler's tick) must not be aged from the lock file's mtime,
# which only says when someone last opened it for writing.
start_holder 0
/usr/bin/touch -d '1 hour ago' "$ISW_LOCK_FILE"
( /usr/bin/sleep 1; touch "$test_root/release" ) &
bash "$watchdog" >/dev/null || fail "a brief ownerless holder failed inspection"
[[ ! -e "$TEST_NOTIFY_LOG" ]] || fail "a brief ownerless holder sent an alert"
wait "$holder"
holder=

start_holder 0
bash "$watchdog" >/dev/null || fail "a stuck ownerless holder alert failed"
[[ "$(wc -l < "$TEST_NOTIFY_LOG" | tr -d ' ')" == 1 ]] \
  || fail "a stuck ownerless holder did not send exactly one alert"
grep -q "held for at least 2s" "$TEST_NOTIFY_LOG.body" \
  || fail "the ownerless alert did not state the observed hold"
if [[ -x /usr/bin/lslocks ]]; then
  /usr/bin/awk -v pid="$holder" -v ppid="$$" '$1 == pid && $2 == ppid { found = 1 }
    END { exit !found }' "$TEST_NOTIFY_LOG.body" \
    || fail "the ownerless alert did not show the holder's process"
fi
stop_holder
rm -f "$TEST_NOTIFY_LOG" "$ISW_CANARY_DIR/alerted"

start_holder 1

bash "$watchdog" || fail "a recent lock holder was reported as stalled"
[[ ! -e "$TEST_NOTIFY_LOG" ]] || fail "a recent lock holder sent an alert"

printf '%s %s\n' "$holder" "$(( $(/usr/bin/date +%s) - 10 ))" \
  > "$ISW_CANARY_DIR/owner"
bash "$watchdog" >/dev/null || fail "a stale lock alert failed"
[[ "$(wc -l < "$TEST_NOTIFY_LOG" | tr -d ' ')" == 1 ]] \
  || fail "a stale lock did not send exactly one alert"

bash "$watchdog" >/dev/null || fail "repeat stale-lock inspection failed"
[[ "$(wc -l < "$TEST_NOTIFY_LOG" | tr -d ' ')" == 1 ]] \
  || fail "a repeated stale lock sent a duplicate alert"

stop_holder
bash "$watchdog" || fail "a released lock was reported as stalled"

echo "PASS: independent policy stall watchdog"
