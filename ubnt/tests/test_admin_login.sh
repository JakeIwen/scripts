#!/bin/sh
set -eu
script_dir=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
test_root=$(mktemp -d "${TMPDIR:-/tmp}/ubnt-admin-test.XXXXXX")
trap 'rm -rf "$test_root"' EXIT HUP INT TERM
export UBNT_SYSTEM_CFG="$test_root/system.cfg"
export UBNT_STATE_DIR="$test_root/state"
export UBNT_LOG_FILE="$test_root/manager.log"
export UBNT_UPTIME_FILE="$test_root/uptime"
printf '1000.0 0.0\n' > "$UBNT_UPTIME_FILE"
# Source definitions only; the unique final main "$@" line is the dispatch boundary.
sed '/^main "\$@"$/,$d' "$script_dir/../persistent/scripts/wifi_manager.sh" > "$test_root/functions.sh"
. "$test_root/functions.sh"

printf '%s\n' 'users.1.name=ubnt' 'users.1.password=preferred-test-hash' > "$SYSTEM_CFG"
printf '%s\n' 'users.1.name=old-account' 'users.1.password=obsolete-test-hash' \
    'wpasupplicant.profile.1.network.1.psk=upstream-test-password' \
    'wireless.1.ssid=Example Network' > "$test_root/template.cfg"
preserve_current_login "$test_root/template.cfg"
grep -qx 'users.1.name=ubnt' "$test_root/template.cfg"
grep -qx 'users.1.password=preferred-test-hash' "$test_root/template.cfg"
grep -qx 'wpasupplicant.profile.1.network.1.psk=upstream-test-password' "$test_root/template.cfg"
grep -qx 'wireless.1.ssid=Example Network' "$test_root/template.cfg"

# A later native password change takes precedence over every saved template.
printf '%s\n' 'users.1.name=ubnt' 'users.1.password=changed-again-test-hash' > "$SYSTEM_CFG"
preserve_current_login "$test_root/template.cfg"
grep -qx 'users.1.password=changed-again-test-hash' "$test_root/template.cfg"

# Never partially rewrite a template when the live credential is ambiguous.
cp "$test_root/template.cfg" "$test_root/expected.cfg"
for invalid in missing empty duplicate; do
    printf '%s\n' 'users.1.name=ubnt' > "$SYSTEM_CFG"
    case $invalid in
        empty) printf 'users.1.password=\n' >> "$SYSTEM_CFG" ;;
        duplicate) printf '%s\n' 'users.1.password=one-test-hash' 'users.1.password=two-test-hash' >> "$SYSTEM_CFG" ;;
    esac
    if preserve_current_login "$test_root/template.cfg" >/dev/null; then
        echo 'Invalid live credential unexpectedly accepted' >&2
        exit 1
    fi
    [ "$(md5sum "$test_root/template.cfg" | awk '{print $1}')" = "$(md5sum "$test_root/expected.cfg" | awk '{print $1}')" ]
done
! grep -qE 'test-hash|test-password' "$LOG_FILE"
if preserve_current_login "$SYSTEM_CFG"; then
    echo 'Refusing to overwrite the credential source was expected' >&2
    exit 1
fi
printf 'admin-login: ok\n'
