#!/bin/bash
set -u

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
watchdog="$repo_root/pi/scripts/usb_controller_watchdog.sh"
service="$repo_root/pi/services/vanpi-usb-controller-watchdog.service"
timer="$repo_root/pi/services/vanpi-usb-controller-watchdog.timer"
test_root=$(mktemp -d)
trap 'rm -rf "$test_root"' EXIT

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

fake_journal="$test_root/journalctl"
fake_notify="$test_root/notify"
calls="$test_root/calls"
state_dir="$test_root/state"
touch "$calls"

cat > "$fake_journal" <<'EOF'
#!/bin/bash
if [[ "${TEST_CONTROLLER_DEAD:-0}" == 1 ]]; then
  echo "xhci_hcd 0000:01:00.0: xHCI host controller not responding, assume dead"
  exit 0
fi
echo "-- No entries --"
exit 1
EOF
cat > "$fake_notify" <<EOF
#!/bin/bash
printf '%s\\n' "\$*" >> "$calls"
exit "\${TEST_NOTIFY_STATUS:-0}"
EOF
chmod +x "$fake_journal" "$fake_notify"

run_watchdog() {
  USB_CONTROLLER_WATCH_JOURNALCTL="$fake_journal" \
  USB_CONTROLLER_WATCH_NOTIFY="$fake_notify" \
  USB_CONTROLLER_WATCH_STATE_DIR="$state_dir" \
  USB_CONTROLLER_WATCH_DATE=/bin/date \
  TEST_CONTROLLER_DEAD="${TEST_CONTROLLER_DEAD:-0}" \
  TEST_NOTIFY_STATUS="${TEST_NOTIFY_STATUS:-0}" \
  bash "$watchdog"
}

run_watchdog || fail "healthy controller state was reported as a failure"
[[ ! -s "$calls" ]] || fail "healthy controller state sent an alert"

TEST_CONTROLLER_DEAD=1 run_watchdog >/dev/null ||
  fail "controller-death alert failed"
(( $(wc -l < "$calls") == 1 )) || fail "controller death did not send one alert"
grep -Fq "safe_reboot.sh" "$calls" ||
  fail "controller-death alert omitted the safe recovery command"

TEST_CONTROLLER_DEAD=1 run_watchdog >/dev/null ||
  fail "repeat controller-death check failed"
(( $(wc -l < "$calls") == 1 )) || fail "controller-death alert was not rate limited"

rm -f "$state_dir/alerted"
TEST_CONTROLLER_DEAD=1 TEST_NOTIFY_STATUS=1 run_watchdog >/dev/null 2>&1 &&
  fail "notification failure was reported as success"
[[ ! -e "$state_dir/alerted" ]] ||
  fail "failed notification incorrectly created a rate-limit marker"

grep -Fq "ExecStart=/home/pi/scripts/usb_controller_watchdog.sh" "$service" ||
  fail "USB-controller watchdog service does not run the watchdog"
grep -Fq "OnUnitInactiveSec=1min" "$timer" ||
  fail "USB-controller watchdog timer is not periodic"
grep -Fq "Unit=vanpi-usb-controller-watchdog.service" "$timer" ||
  fail "USB-controller watchdog timer targets the wrong service"

echo "PASS: fatal USB-controller failures produce one actionable alert per boot"
