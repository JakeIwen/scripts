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
HELPER
chmod +x "$test_root/notify"

export ISW_SCRIPT="$repo_root/pi/scripts/internet_switches.sh"
export ISW_LOCK_FILE="$test_root/policy.lock"
export ISW_CANARY_DIR="$test_root/canary"
export ISW_LOG_FILE="$test_root/policy.log"
export ISW_NTFY_SEND="$test_root/notify"
export TEST_NOTIFY_LOG="$test_root/notifications"
export POLICY_STALL_SECONDS=2
watchdog="$repo_root/pi/scripts/policy_watchdog.sh"

bash "$watchdog" || fail "an absent lock was reported as stalled"
[[ ! -e "$TEST_NOTIFY_LOG" ]] || fail "an absent lock sent an alert"

mkdir -p "$ISW_CANARY_DIR"
: > "$ISW_LOG_FILE"
(
  exec 9>"$ISW_LOCK_FILE"
  /usr/bin/flock 9
  printf '%s %s\n' "$BASHPID" "$(/usr/bin/date +%s)" > "$ISW_CANARY_DIR/owner"
  touch "$test_root/ready"
  while [[ ! -e "$test_root/release" ]]; do
    /usr/bin/sleep 0.05
  done
) &
holder=$!

for _ in {1..100}; do
  [[ -e "$test_root/ready" ]] && break
  /usr/bin/sleep 0.05
done
[[ -e "$test_root/ready" ]] || fail "lock holder did not start"

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

touch "$test_root/release"
wait "$holder"
holder=
bash "$watchdog" || fail "a released lock was reported as stalled"

echo "PASS: independent policy stall watchdog"
