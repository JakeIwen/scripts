#!/bin/bash
set -u
(( BASH_VERSINFO[0] >= 4 )) || {
  echo "SKIP: smart_watch.sh needs bash 4+ for its disk-lock table, as on the Pi"
  exit 0
}

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)
. "$repo_root/pi/tests/lib.sh"
watch="$repo_root/pi/scripts/smart_watch.sh"
test_root=$(mktemp -d)
trap 'rm -rf "$test_root"' EXIT
[[ -n ${SMART_WATCH_TEST_KEEP:-} ]] && trap 'cat "$test_root/watch.log" >&2' EXIT
jq_command=$(command -v jq) || fail "jq is required"

# alpha is mounted, beta is attached but parked, gamma is detached.
cat > "$test_root/policy.sh" <<'STUB'
HDD_LABELS=(alpha beta gamma)
disk_policy_resolve_exact_label() {
  case $1 in
    alpha) DISK_POLICY_RESOLVED_DEVICE=/dev/sda1 ;;
    beta) DISK_POLICY_RESOLVED_DEVICE=/dev/sdb1 ;;
    *) return 1 ;;
  esac
}
STUB
cat > "$test_root/locks.sh" <<STUB
declare -A BACKUP_DISK_LOCK_FD=()
hold_backup_disk() {
  [[ -e "$test_root/busy-\$1" ]] && return 1
  local fd
  exec {fd}</dev/null
  BACKUP_DISK_LOCK_FD[\$1]=\$fd
}
STUB
cat > "$test_root/findmnt" <<'STUB'
#!/bin/bash
[[ "${@: -1}" == /dev/sda1 ]] && echo /mnt/alpha
exit 0
STUB
cat > "$test_root/lsblk" <<'STUB'
#!/bin/bash
part=${@: -1}; part=${part#/dev/}; echo "${part%%[0-9]*}"
STUB
cat > "$test_root/smartctl" <<STUB
#!/bin/bash
disk=\${@: -1}
echo "\${disk#/dev/}" >> "$test_root/smartctl-calls"
cat "$test_root/report.json"
exit "\$(cat "$test_root/smartctl-rc")"
STUB
cat > "$test_root/notify" <<STUB
#!/bin/bash
printf '%s|%s|%s|%s\n' "\$NTFY_TOPIC_VAR" "\$1" "\$3" "\$2" >> "$test_root/alerts"
exit "\${TEST_NOTIFY_STATUS:-0}"
STUB
chmod +x "$test_root"/{findmnt,lsblk,smartctl,notify}
touch "$test_root/alerts" "$test_root/smartctl-calls"

report() { # <passed> <reallocated> <pending> <uncorrectable> <temp> [smartctl rc]
  cat > "$test_root/report.json" <<JSON
{"smart_status": {"passed": $1}, "temperature": {"current": $5},
 "ata_smart_attributes": {"table": [
   {"id": 5, "raw": {"value": $2}}, {"id": 197, "raw": {"value": $3}},
   {"id": 198, "raw": {"value": $4}}]}}
JSON
  echo "${6:-0}" > "$test_root/smartctl-rc"
}

run_watch() {
  SMART_WATCH_POLICY="$test_root/policy.sh" SMART_WATCH_LOCKS="$test_root/locks.sh" \
    SMART_WATCH_SMARTCTL="$test_root/smartctl" SMART_WATCH_FINDMNT="$test_root/findmnt" \
    SMART_WATCH_LSBLK="$test_root/lsblk" SMART_WATCH_JQ="$jq_command" \
    SMART_WATCH_NOTIFY="$test_root/notify" SMART_WATCH_STATE_DIR="$test_root/state" \
    TEST_NOTIFY_STATUS="${TEST_NOTIFY_STATUS:-0}" bash "$watch" >> "$test_root/watch.log" 2>&1
}
alert_count() { wc -l < "$test_root/alerts" | tr -d ' '; }
last_alert() { tail -1 "$test_root/alerts"; }

report true 32 0 0 44
run_watch || fail "a healthy first run failed"
assert_eq 0 "$(alert_count)" "an existing reallocated count is a silent baseline"
assert_eq sda "$(cat "$test_root/smartctl-calls")" "only the mounted disk was queried"

report true 40 0 0 44
run_watch
assert_eq 1 "$(alert_count)" "a reallocated-sector increase alerts"
assert_eq "NTFY_WARNING_URL|SMART: alpha|high|alpha reallocated sectors rose from 32 to 40." \
  "$(last_alert)" "the alert names the disk, the change and the warning topic"
run_watch
assert_eq 1 "$(alert_count)" "an unchanged count does not repeat the alert"

report true 40 3 0 44
run_watch
assert_eq 2 "$(alert_count)" "new pending sectors alert"
run_watch
assert_eq 2 "$(alert_count)" "a steady pending count does not repeat"

report true 40 3 0 61
run_watch
assert_eq 3 "$(alert_count)" "reaching the temperature limit alerts"
report true 40 3 0 62
run_watch
assert_eq 3 "$(alert_count)" "staying hot does not repeat"
report true 40 3 0 50
run_watch
report true 40 3 0 61
run_watch
assert_eq 4 "$(alert_count)" "cooling below the re-arm point arms the next temperature alert"

report false 40 3 0 44
TEST_NOTIFY_STATUS=1 run_watch && fail "an undelivered alert was reported as success"
TEST_NOTIFY_STATUS=0 run_watch
assert_eq 6 "$(alert_count)" "an undelivered alert is retried on the next run"
[[ "$(last_alert)" == *"failed its SMART health check"* ]] ||
  fail "a failed health check did not alert"

calls_before=$(wc -l < "$test_root/smartctl-calls")
touch "$test_root/busy-alpha"
run_watch
assert_eq "$calls_before" "$(wc -l < "$test_root/smartctl-calls")" \
  "a disk whose lock is held for unmount or spin-down is not queried"
rm "$test_root/busy-alpha"

report true 99 3 0 44 2
run_watch
assert_eq 6 "$(alert_count)" "a disk that reports standby is skipped without alerts"

echo "PASS: SMART is read only from mounted disks, and each change alerts once"
