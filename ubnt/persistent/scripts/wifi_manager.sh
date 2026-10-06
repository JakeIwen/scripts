#!/bin/sh

# BusyBox/airOS Wi-Fi profile selector. Saved profiles are read-only during
# automatic operation; frequency restrictions are applied to a temporary copy.

PROFILE_DIR=${UBNT_PROFILE_DIR:-/etc/persistent/profiles}
CONFIG_DIR=${UBNT_CONFIG_DIR:-/etc/persistent/config}
STATE_DIR=${UBNT_STATE_DIR:-/tmp/ubnt-wifi}
LOG_FILE=${UBNT_LOG_FILE:-/var/log/ubnt-wifi.log}
SYSTEM_CFG=${UBNT_SYSTEM_CFG:-/tmp/system.cfg}
PARSER=${UBNT_SCAN_PARSER:-/etc/persistent/scripts/parse-iwlist.awk}
SOFTRESTART=${UBNT_SOFTRESTART:-/usr/etc/rc.d/rc.softrestart}
IWLIST=${UBNT_IWLIST:-/usr/bin/iwlist}
IWGETID=${UBNT_IWGETID:-/usr/bin/iwgetid}
MCA_STATUS=${UBNT_MCA_STATUS:-/usr/bin/mca-status}
IP_CMD=${UBNT_IP_CMD:-/usr/bin/ip}
PING=${UBNT_PING:-/bin/ping}
CFGMTD=${UBNT_CFGMTD:-/sbin/cfgmtd}
HEXDUMP=${UBNT_HEXDUMP:-/usr/bin/hexdump}
MD5SUM=${UBNT_MD5SUM:-/usr/bin/md5sum}
UPTIME_FILE=${UBNT_UPTIME_FILE:-/proc/uptime}
SSH_KEY_INSTALLER=${UBNT_SSH_KEY_INSTALLER:-/etc/persistent/scripts/ensure_ssh_keys.sh}

LOCK_DIR="$STATE_DIR/lock"
PAUSE_FILE="$STATE_DIR/paused"
MANUAL_HOLD_FILE="$STATE_DIR/manual-hold"
TRANSITION_FILE="$STATE_DIR/transition_started"
OBSERVED_CONFIG_DIGEST_FILE="$STATE_DIR/observed-system-config.md5"
GUI_TRANSITION_FILE="$STATE_DIR/gui-transition-started"
GUI_TARGET_FILE="$STATE_DIR/gui-transition-target"
SCAN_FILE="$STATE_DIR/scan.results"
SCAN_RAW="$STATE_DIR/scan.raw"
SCAN_COMPLETED_FILE="$STATE_DIR/scan-completed"
APPLY_CFG="$STATE_DIR/apply.cfg"
PRIORITY_FILE="$CONFIG_DIR/wifi-priority"
PREFER_DENLINK_FLAG="$CONFIG_DIR/prefer_denlink"
PENDING_PROFILE=

MANUAL_GRACE_SECONDS=${UBNT_MANUAL_GRACE_SECONDS:-120}
PORTAL_GRACE_SECONDS=${UBNT_PORTAL_GRACE_SECONDS:-600}
GUI_GRACE_SECONDS=${UBNT_GUI_GRACE_SECONDS:-600}
STANDARD_SCAN_FREQUENCIES=${UBNT_STANDARD_SCAN_FREQUENCIES:-"2412, 2417, 2422, 2427, 2432, 2437, 2442, 2447, 2452, 2457, 2462"}
AUTO_SCAN_INTERVAL=${UBNT_AUTO_SCAN_INTERVAL:-120}
SCAN_PASSES=${UBNT_SCAN_PASSES:-3}
SCAN_SETTLE_SECONDS=${UBNT_SCAN_SETTLE_SECONDS:-2}
ASSOCIATE_FAST_SECONDS=${UBNT_ASSOCIATE_FAST_SECONDS:-15}
ASSOCIATE_FALLBACK_SECONDS=${UBNT_ASSOCIATE_FALLBACK_SECONDS:-60}
SCAN_MAX_AGE_SECONDS=${UBNT_SCAN_MAX_AGE_SECONDS:-300}
DHCP_SECONDS=${UBNT_DHCP_SECONDS:-30}
FAILURES_BEFORE_SWITCH=${UBNT_FAILURES_BEFORE_SWITCH:-3}
COOLDOWN_SECONDS=${UBNT_COOLDOWN_SECONDS:-3600}
HEALTHY_LOG_INTERVAL=${UBNT_HEALTHY_LOG_INTERVAL:-3600}
MAX_LOG_BYTES=${UBNT_MAX_LOG_BYTES:-262144}
LOG_KEEP_LINES=${UBNT_LOG_KEEP_LINES:-1000}

# Validate every sibling before runtime initialization or command dispatch.
WIFI_MANAGER_SCRIPT_DIR=$(CDPATH= cd -- "$(dirname "$0")" && pwd) || {
    printf 'Wi-Fi manager: cannot locate modules beside %s\n' "$0" >&2
    exit 1
}
for WIFI_MANAGER_MODULE in runtime profiles scanning connect transitions provision status starlink; do
    if [ ! -f "$WIFI_MANAGER_SCRIPT_DIR/wifi_manager_$WIFI_MANAGER_MODULE.sh" ] || \
        [ ! -r "$WIFI_MANAGER_SCRIPT_DIR/wifi_manager_$WIFI_MANAGER_MODULE.sh" ]; then
        printf 'Wi-Fi manager: missing or unreadable module %s/wifi_manager_%s.sh\n' \
            "$WIFI_MANAGER_SCRIPT_DIR" "$WIFI_MANAGER_MODULE" >&2
        exit 1
    fi
done
for WIFI_MANAGER_MODULE in runtime profiles scanning connect transitions provision status starlink; do
    . "$WIFI_MANAGER_SCRIPT_DIR/wifi_manager_$WIFI_MANAGER_MODULE.sh" || exit 1
done
unset WIFI_MANAGER_MODULE WIFI_MANAGER_SCRIPT_DIR

umask 077
mkdir -p "$STATE_DIR" "$(dirname "$LOG_FILE")"
chmod 700 "$STATE_DIR" 2>/dev/null || true

usage() {
    printf 'Usage: %s auto|connect PROFILE|status|pause|resume|save-current PROFILE|disable PROFILE|dashboard-status|dashboard-scan|manual-connect-stdin|provision-stdin|update-profile-stdin|forget-stdin|starlink-off\n' "$0" >&2
}

main() {
    command_name=${1:-}
    case $command_name in
        auto)
            acquire_lock || exit 0
            auto_select
            ;;
        connect)
            [ "$#" -eq 2 ] || { usage; exit 1; }
            acquire_lock || exit 1
            run_requested_connect "$2"
            exit $?
            ;;
        status)
            show_status
            ;;
        pause)
            : > "$PAUSE_FILE"
            log_message "automatic selection paused"
            ;;
        resume)
            rm -f "$PAUSE_FILE" "$MANUAL_HOLD_FILE"
            log_message "automatic selection resumed"
            ;;
        save-current)
            [ "$#" -eq 2 ] || { usage; exit 1; }
            acquire_lock || exit 1
            save_current_profile "$2"
            ;;
        disable)
            [ "$#" -eq 2 ] || { usage; exit 1; }
            acquire_lock || exit 1
            disable_profile "$2"
            ;;
        dashboard-status)
            [ "$#" -eq 1 ] || { usage; exit 1; }
            emit_dashboard_snapshot
            ;;
        dashboard-scan)
            [ "$#" -eq 1 ] || { usage; exit 1; }
            acquire_lock || exit 1
            scan_networks || true
            emit_dashboard_snapshot
            ;;
        starlink-off)
            [ "$#" -eq 1 ] || { usage; exit 1; }
            off_lock_wait=0
            until acquire_lock; do
                [ "$off_lock_wait" -lt 120 ] || exit 1
                sleep 2
                off_lock_wait=$((off_lock_wait + 2))
            done
            starlink_power_off
            exit $?
            ;;
        manual-connect-stdin)
            [ "$#" -eq 1 ] || { usage; exit 1; }
            IFS= read -r manual_profile || {
                log_message "manual profile was not provided"
                exit 1
            }
            profile_name_is_valid "$manual_profile" && [ -f "$PROFILE_DIR/$manual_profile" ] || {
                log_message "unknown manual profile"
                exit 1
            }
            acquire_lock || exit 1
            run_requested_connect "$manual_profile"
            exit $?
            ;;
        forget-stdin)
            [ "$#" -eq 1 ] || { usage; exit 1; }
            IFS= read -r input_profile || exit 1
            acquire_lock || exit 1
            forget_profile "$input_profile"
            exit $?
            ;;
        provision-stdin)
            [ "$#" -eq 1 ] || { usage; exit 1; }
            IFS= read -r input_ssid || exit 1
            IFS= read -r input_security || exit 1
            IFS= read -r input_bssid || exit 1
            IFS= read -r input_password || exit 1
            acquire_lock || exit 1
            provision_profile "$input_ssid" "$input_security" "$input_bssid" "$input_password"
            exit $?
            ;;
        update-profile-stdin)
            [ "$#" -eq 1 ] || { usage; exit 1; }
            IFS= read -r input_profile || exit 1
            IFS= read -r input_password_action || exit 1
            IFS= read -r input_password || exit 1
            IFS= read -r input_bssid || exit 1
            IFS= read -r input_txpower || exit 1
            IFS= read -r input_rate_module || exit 1
            IFS= read -r input_rate_auto || exit 1
            IFS= read -r input_rate_mcs || exit 1
            IFS= read -r input_apply || exit 1
            acquire_lock || exit 1
            update_profile_settings "$input_profile" "$input_password_action" \
                "$input_password" "$input_bssid" "$input_txpower" \
                "$input_rate_module" "$input_rate_auto" "$input_rate_mcs" \
                "$input_apply"
            exit $?
            ;;
        *)
            usage
            exit 1
            ;;
    esac
}

main "$@"
