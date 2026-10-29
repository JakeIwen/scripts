#!/bin/bash

set -euo pipefail

repo_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
test_root=$(mktemp -d "${TMPDIR:-/tmp}/display-control-test.XXXXXX")
trap 'rm -rf -- "$test_root"' EXIT

bin="$test_root/bin"
xset_only="$test_root/xset-only"
calls="$test_root/calls"
mkdir -p "$bin" "$xset_only"
: > "$calls"

cat > "$bin/wlopm" <<'HELPER'
#!/bin/bash
printf 'wlopm:%s\n' "$*" >> "$DISPLAY_CONTROL_CALLS"
if (( $# == 0 )); then
  printf '%b' "${TEST_WLOPM_OUTPUT-}"
  exit "${TEST_WLOPM_STATUS:-0}"
fi
exit "${TEST_WLOPM_ACTION_STATUS:-0}"
HELPER
cat > "$bin/xset" <<'HELPER'
#!/bin/bash
printf 'xset:%s\n' "$*" >> "$DISPLAY_CONTROL_CALLS"
if [[ ${1-} == q ]]; then
  printf '%b' "${TEST_XSET_OUTPUT-}"
  exit "${TEST_XSET_STATUS:-0}"
fi
exit "${TEST_XSET_ACTION_STATUS:-0}"
HELPER
cat > "$test_root/safe_reboot.sh" <<'HELPER'
#!/bin/bash
printf 'safe_reboot\n' >> "$DISPLAY_CONTROL_CALLS"
HELPER
chmod +x "$bin/wlopm" "$bin/xset" "$test_root/safe_reboot.sh"
cp "$bin/xset" "$xset_only/xset"
cp "$(command -v grep)" "$xset_only/grep"
cp "$(command -v date)" "$xset_only/date"
chmod +x "$xset_only/xset" "$xset_only/grep" "$xset_only/date"

set_env() {
  DISPLAY_CONTROL_CALLS="$calls" \
    WAYLAND_DISPLAY=wayland-test \
    XDG_RUNTIME_DIR="$test_root/runtime" \
    PATH="$bin:/usr/bin:/bin" \
    "$@"
}

toggle="$repo_root/pi/scripts/toggle_display.sh"
reboot="$repo_root/pi/scripts/reboot.sh"
reboot_copy="$test_root/reboot.sh"
cp "$reboot" "$reboot_copy"

: > "$calls"
set_env /bin/bash "$toggle" on >/dev/null
[[ $(cat "$calls") == 'wlopm:--on *' ]] || exit 1

: > "$calls"
TEST_WLOPM_OUTPUT=$'HDMI-A-1 on\nDP-1 off' set_env /bin/bash "$toggle" >/dev/null
[[ $(cat "$calls") == $'wlopm:\nwlopm:--off *' ]] || exit 1

: > "$calls"
TEST_WLOPM_OUTPUT=$'HDMI-A-1 off' set_env /bin/bash "$toggle" >/dev/null
[[ $(cat "$calls") == $'wlopm:\nwlopm:--on *' ]] || exit 1

: > "$calls"
TEST_WLOPM_STATUS=1 TEST_XSET_OUTPUT='Monitor is On\n' \
  set_env /bin/bash "$toggle" >/dev/null
[[ $(cat "$calls") == $'wlopm:\nxset:q\nxset:dpms force off' ]] || exit 1

: > "$calls"
if set_env /bin/bash "$toggle" sideways >/dev/null 2>&1; then
  exit 1
fi
[[ ! -s "$calls" ]] || exit 1

: > "$calls"
TEST_WLOPM_OUTPUT=$'HDMI-A-1 off\nDP-1 off' set_env /bin/bash "$reboot_copy" >/dev/null
[[ $(cat "$calls") == $'wlopm:\nsafe_reboot' ]] || exit 1

: > "$calls"
TEST_WLOPM_OUTPUT='HDMI-A-1 on' set_env /bin/bash "$reboot_copy" >/dev/null
[[ $(cat "$calls") == 'wlopm:' ]] || exit 1

: > "$calls"
reboot_output=$(TEST_WLOPM_STATUS=1 set_env /bin/bash "$reboot_copy")
[[ $reboot_output == *'cannot read monitor state, skipping scheduled reboot'* ]] || exit 1
[[ $(cat "$calls") == 'wlopm:' ]] || exit 1

: > "$calls"
reboot_output=$(TEST_WLOPM_OUTPUT='' set_env /bin/bash "$reboot_copy")
[[ $reboot_output == *'cannot read monitor state, skipping scheduled reboot'* ]] || exit 1
[[ $(cat "$calls") == 'wlopm:' ]] || exit 1

: > "$calls"
reboot_output=$(DISPLAY_CONTROL_CALLS="$calls" WAYLAND_DISPLAY=wayland-test \
  XDG_RUNTIME_DIR="$test_root/runtime" PATH="$xset_only" /bin/bash "$reboot_copy")
[[ $reboot_output == *'cannot read monitor state, skipping scheduled reboot'* ]] || exit 1
[[ ! -s "$calls" ]] || exit 1

echo 'Display toggle and scheduled reboot tests passed'
