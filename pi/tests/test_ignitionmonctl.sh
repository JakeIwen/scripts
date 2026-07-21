#!/bin/bash

set -u

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
test_root=$(mktemp -d)
trap 'rm -rf "$test_root"' EXIT
control="$repo_root/pi/scripts/ignitionmonctl"
state="$test_root/hooks/inactive/ignition"
fake_flock="$test_root/flock"

cat > "$fake_flock" <<'EOF'
#!/bin/bash
[[ $1 == -x && $2 == 9 ]]
EOF
chmod +x "$fake_flock"

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

run_control() {
  IGNITIONMON_INACTIVE_FILE="$state" IGNITIONMON_NOW="$TEST_NOW" \
    IGNITIONMON_FLOCK="$fake_flock" \
    bash "$control" "$@"
}

TEST_NOW=1000000
output=$(run_control disable 30m) || fail "30m duration was rejected"
[[ $output == *"disabled for 30m"* ]] || fail "disable output omitted duration"
[[ $(cat "$state") == 1001800 ]] || fail "30m deadline was calculated incorrectly"
run_control check >/dev/null || fail "fresh override was not active"

TEST_NOW=1001799
run_control check >/dev/null || fail "override expired one second early"
TEST_NOW=1001800
run_control check >/dev/null 2>&1 && fail "override remained active at its deadline"
[[ ! -e $state ]] || fail "expired state was not removed"

TEST_NOW=2000000
run_control disable 2 hours >/dev/null || fail "two-word hours duration was rejected"
[[ $(cat "$state") == 2007200 ]] || fail "hours deadline was calculated incorrectly"
status=$(run_control status) || fail "status failed for active override"
[[ $status == disabled:*remaining* ]] || fail "status did not report remaining time"
run_control enable >/dev/null || fail "manual reactivation failed"
[[ ! -e $state ]] || fail "manual reactivation left state behind"

for invalid in 0 -1 nope 2d; do
  run_control disable "$invalid" >/dev/null 2>&1 &&
    fail "invalid duration '$invalid' was accepted"
done

mkdir -p "${state%/*}"
printf 'partial-or-legacy-state\n' > "$state"
run_control check >/dev/null 2>&1 && fail "malformed state disabled the monitor"
[[ ! -e $state ]] || fail "malformed state was not removed fail-active"

TEST_NOW=3000000
run_control disable 10m >/dev/null || fail "initial override failed"
run_control disable 1h >/dev/null || fail "replacement override failed"
[[ $(cat "$state") == 3003600 ]] || fail "replacement did not atomically update deadline"

echo "PASS: durable ignition-monitor override parsing and expiry"
