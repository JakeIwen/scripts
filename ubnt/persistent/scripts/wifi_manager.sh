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

LOCK_DIR="$STATE_DIR/lock"
PAUSE_FILE="$STATE_DIR/paused"
TRANSITION_FILE="$STATE_DIR/transition_started"
SCAN_FILE="$STATE_DIR/scan.results"
SCAN_RAW="$STATE_DIR/scan.raw"
APPLY_CFG="$STATE_DIR/apply.cfg"
PRIORITY_FILE="$CONFIG_DIR/wifi-priority"
PREFER_DENLINK_FLAG="$CONFIG_DIR/prefer_denlink"
PENDING_PROFILE=

MANUAL_GRACE_SECONDS=${UBNT_MANUAL_GRACE_SECONDS:-120}
AUTO_SCAN_INTERVAL=${UBNT_AUTO_SCAN_INTERVAL:-120}
SCAN_PASSES=${UBNT_SCAN_PASSES:-3}
SCAN_SETTLE_SECONDS=${UBNT_SCAN_SETTLE_SECONDS:-2}
ASSOCIATE_FAST_SECONDS=${UBNT_ASSOCIATE_FAST_SECONDS:-24}
ASSOCIATE_FALLBACK_SECONDS=${UBNT_ASSOCIATE_FALLBACK_SECONDS:-35}
DHCP_SECONDS=${UBNT_DHCP_SECONDS:-30}
FAILURES_BEFORE_SWITCH=${UBNT_FAILURES_BEFORE_SWITCH:-3}
COOLDOWN_SECONDS=${UBNT_COOLDOWN_SECONDS:-3600}
HEALTHY_LOG_INTERVAL=${UBNT_HEALTHY_LOG_INTERVAL:-3600}
MAX_LOG_BYTES=${UBNT_MAX_LOG_BYTES:-262144}
LOG_KEEP_LINES=${UBNT_LOG_KEEP_LINES:-1000}

umask 077
mkdir -p "$STATE_DIR" "$(dirname "$LOG_FILE")"
chmod 700 "$STATE_DIR" 2>/dev/null || true

uptime_seconds() {
    awk '{split($1, value, "."); print value[1]}' /proc/uptime 2>/dev/null
}

log_message() {
    rotate_log_if_needed
    log_uptime=$(uptime_seconds)
    [ -n "$log_uptime" ] || log_uptime=unknown
    printf 'uptime=%s %s\n' "$log_uptime" "$*"
    printf 'uptime=%s %s\n' "$log_uptime" "$*" >> "$LOG_FILE" 2>/dev/null || true
}

rotate_log_if_needed() {
    [ -f "$LOG_FILE" ] || return 0
    log_size=$(wc -c < "$LOG_FILE" 2>/dev/null | tr -d '[:space:]')
    case $log_size in
        ''|*[!0-9]*) return 0 ;;
    esac
    [ "$log_size" -lt "$MAX_LOG_BYTES" ] || {
        rotation_file="$STATE_DIR/log.rotate.$$"
        tail -n "$LOG_KEEP_LINES" "$LOG_FILE" > "$rotation_file" 2>/dev/null || return 0
        mv "$rotation_file" "$LOG_FILE"
    }
}

log_healthy_connection() {
    healthy_ssid=$1
    healthy_ccq=$2
    healthy_now=$(uptime_seconds)
    [ -n "$healthy_now" ] || healthy_now=0
    healthy_last=$(sed -n '1p' "$STATE_DIR/last_healthy_log" 2>/dev/null)
    healthy_last_ssid=$(sed -n '1p' "$STATE_DIR/last_healthy_ssid" 2>/dev/null)
    case $healthy_last in
        ''|*[!0-9]*) healthy_last=0 ;;
    esac
    if [ "$healthy_ssid" != "$healthy_last_ssid" ] || \
        [ $((healthy_now - healthy_last)) -ge "$HEALTHY_LOG_INTERVAL" ]; then
        printf '%s\n' "$healthy_now" > "$STATE_DIR/last_healthy_log"
        printf '%s\n' "$healthy_ssid" > "$STATE_DIR/last_healthy_ssid"
        log_message "current connection healthy ssid=$healthy_ssid ccq=$healthy_ccq"
    fi
}

config_value() {
    config_file=$1
    config_key=$2
    awk -F= -v wanted="$config_key" '
        $1 == wanted {
            sub(/^[^=]*=/, "")
            print
            exit
        }
    ' "$config_file" 2>/dev/null
}

effective_ssid() {
    profile_file=$1
    wpa_status=$(config_value "$profile_file" wpasupplicant.status)
    wpa_device_status=$(config_value "$profile_file" wpasupplicant.device.1.status)
    if [ "$wpa_status" = enabled ] && [ "$wpa_device_status" = enabled ]; then
        config_value "$profile_file" wpasupplicant.profile.1.network.1.ssid
    else
        config_value "$profile_file" wireless.1.ssid
    fi
}

profile_name_is_valid() {
    case $1 in
        ''|.|..|*/*) return 1 ;;
        *) return 0 ;;
    esac
}

acquire_lock() {
    if mkdir "$LOCK_DIR" 2>/dev/null; then
        printf '%s\n' "$$" > "$LOCK_DIR/pid"
        trap 'release_lock' EXIT HUP INT TERM
        return 0
    fi

    lock_pid=$(sed -n '1p' "$LOCK_DIR/pid" 2>/dev/null)
    case $lock_pid in
        ''|*[!0-9]*) lock_pid= ;;
    esac
    if [ -n "$lock_pid" ] && kill -0 "$lock_pid" 2>/dev/null; then
        log_message "selector already running pid=$lock_pid"
        return 1
    fi

    rm -f "$LOCK_DIR/pid"
    if rmdir "$LOCK_DIR" 2>/dev/null && mkdir "$LOCK_DIR" 2>/dev/null; then
        printf '%s\n' "$$" > "$LOCK_DIR/pid"
        trap 'release_lock' EXIT HUP INT TERM
        log_message "removed stale selector lock"
        return 0
    fi

    log_message "unable to acquire selector lock"
    return 1
}

release_lock() {
    if [ -n "${PENDING_PROFILE:-}" ]; then
        rm -f "$PENDING_PROFILE"
    fi
    rm -f "$APPLY_CFG" "$STATE_DIR/softrestart.$$.log"
    rm -f "$LOCK_DIR/pid"
    rmdir "$LOCK_DIR" 2>/dev/null || true
    trap - EXIT HUP INT TERM
}

associated_ssid() {
    "$IWGETID" ath0 -r 2>/dev/null || "$IWGETID" -r 2>/dev/null
}

current_ccq() {
    "$MCA_STATUS" 2>/dev/null | awk -F= '$1 == "ccq" {print $2; exit}' | tr -d '\r'
}

link_is_target() {
    wanted_ssid=$1
    actual_ssid=$(associated_ssid)
    link_ccq=$(current_ccq)
    case $link_ccq in
        ''|*[!0-9]*) return 1 ;;
    esac
    [ "$actual_ssid" = "$wanted_ssid" ] && [ "$link_ccq" -gt 0 ]
}

has_dhcp_and_route() {
    "$IP_CMD" -4 addr show dev ath0 2>/dev/null | grep -q ' inet ' || return 1
    "$IP_CMD" -4 route show default 2>/dev/null | grep -q '^default' || return 1
}

internet_reachable() {
    "$PING" -c 1 -W 2 1.1.1.1 >/dev/null 2>&1 && return 0
    "$PING" -c 1 -W 2 8.8.8.8 >/dev/null 2>&1
}

wait_for_link() {
    wait_ssid=$1
    wait_seconds=$2
    waited=0
    next_wait_message=15
    log_message "waiting for association ssid=$wait_ssid timeout=${wait_seconds}s"
    while [ "$waited" -lt "$wait_seconds" ]; do
        link_is_target "$wait_ssid" && return 0
        sleep 2
        waited=$((waited + 2))
        if [ "$waited" -ge "$next_wait_message" ] && [ "$waited" -lt "$wait_seconds" ]; then
            log_message "still waiting for association ssid=$wait_ssid elapsed=${waited}s"
            next_wait_message=$((next_wait_message + 15))
        fi
    done
    return 1
}

wait_for_dhcp() {
    waited=0
    while [ "$waited" -lt "$DHCP_SECONDS" ]; do
        has_dhcp_and_route && return 0
        sleep 2
        waited=$((waited + 2))
    done
    return 1
}

scan_networks() {
    : > "$SCAN_FILE"
    scan_pass=1
    log_message "starting multi-pass site scan passes=$SCAN_PASSES"
    while [ "$scan_pass" -le "$SCAN_PASSES" ]; do
        scan_pass_raw="$SCAN_RAW.$scan_pass"
        : > "$scan_pass_raw"
        "$IWLIST" ath0 scan > "$scan_pass_raw" 2>/dev/null || true
        if [ -s "$scan_pass_raw" ]; then
            awk -f "$PARSER" "$scan_pass_raw" >> "$SCAN_FILE"
        fi
        if [ "$scan_pass" -lt "$SCAN_PASSES" ]; then
            sleep "$SCAN_SETTLE_SECONDS"
        fi
        scan_pass=$((scan_pass + 1))
    done
    if [ ! -s "$SCAN_FILE" ]; then
        log_message "multi-pass site scan contained no visible SSIDs"
        return 1
    fi
    return 0
}

hex_encode() {
    printf '%s' "$1" | "$HEXDUMP" -v -e '1/1 "%02x"'
}

emit_dashboard_snapshot() {
    dashboard_configured=$(effective_ssid "$SYSTEM_CFG")
    dashboard_associated=$(associated_ssid)
    dashboard_ccq=$(current_ccq)
    [ -f "$PAUSE_FILE" ] && dashboard_paused=yes || dashboard_paused=no
    [ -d "$LOCK_DIR" ] && dashboard_running=yes || dashboard_running=no
    printf 'state|%s|%s|%s|%s|%s\n' \
        "$(hex_encode "$dashboard_configured")" \
        "$(hex_encode "$dashboard_associated")" \
        "$dashboard_ccq" "$dashboard_paused" "$dashboard_running"

    for dashboard_profile_path in "$PROFILE_DIR"/*; do
        [ -f "$dashboard_profile_path" ] || continue
        dashboard_profile=${dashboard_profile_path##*/}
        case $dashboard_profile in
            system.cfg|reset|*.backup.*) continue ;;
        esac
        dashboard_ssid=$(effective_ssid "$dashboard_profile_path")
        [ -n "$dashboard_ssid" ] || continue
        dashboard_security=$(profile_security "$dashboard_profile_path")
        printf 'profile|%s|%s|%s\n' \
            "$(hex_encode "$dashboard_profile")" \
            "$(hex_encode "$dashboard_ssid")" \
            "$dashboard_security"
    done

    if [ -s "$SCAN_FILE" ]; then
        while IFS='|' read -r dashboard_quality dashboard_ssid dashboard_security \
            dashboard_frequency dashboard_channel dashboard_bssid dashboard_signal; do
            [ -n "$dashboard_ssid" ] || continue
            printf 'network|%s|%s|%s|%s|%s|%s|%s\n' \
                "$dashboard_quality" "$(hex_encode "$dashboard_ssid")" \
                "$dashboard_security" "$dashboard_frequency" "$dashboard_channel" \
                "$dashboard_bssid" "$dashboard_signal"
        done < "$SCAN_FILE"
    fi
}

profile_security() {
    security_profile=$1
    security_wpa=$(config_value "$security_profile" wpasupplicant.status)
    security_wpa_device=$(config_value "$security_profile" wpasupplicant.device.1.status)
    if [ "$security_wpa" = enabled ] && [ "$security_wpa_device" = enabled ]; then
        printf '%s\n' wpa
        return
    fi
    security_type=$(config_value "$security_profile" wireless.1.security.type)
    case $security_type in
        wep*) printf '%s\n' wep ;;
        wpa*) printf '%s\n' wpa ;;
        *) printf '%s\n' none ;;
    esac
}

scan_field_for_profile() {
    scan_profile=$1
    scan_profile_field=$2
    scan_profile_ssid=$(effective_ssid "$scan_profile")
    scan_profile_bssid=$(config_value "$scan_profile" wireless.1.ap)
    scan_profile_security=$(profile_security "$scan_profile")
    awk -F'|' \
        -v wanted_ssid="$scan_profile_ssid" \
        -v wanted_bssid="$scan_profile_bssid" \
        -v wanted_security="$scan_profile_security" \
        -v field="$scan_profile_field" '
        $2 == wanted_ssid &&
        $3 == wanted_security &&
        (wanted_bssid == "" || toupper($6) == toupper(wanted_bssid)) &&
        ($1 + 0) > best {
            best = $1 + 0
            value = $field
            found = 1
        }
        END { if (found) print value }
    ' "$SCAN_FILE"
}

set_scan_list() {
    config_path=$1
    scan_status=$2
    scan_frequency=$3
    config_rewrite="$config_path.rewrite.$$"
    awk -F= -v status="$scan_status" -v frequency="$scan_frequency" '
        $1 == "wireless.1.scan_list.status" {
            print "wireless.1.scan_list.status=" status
            saw_status = 1
            next
        }
        $1 == "wireless.1.scan_list.channels" {
            print "wireless.1.scan_list.channels=" frequency
            saw_channels = 1
            next
        }
        { print }
        END {
            if (!saw_status) print "wireless.1.scan_list.status=" status
            if (!saw_channels) print "wireless.1.scan_list.channels=" frequency
        }
    ' "$config_path" > "$config_rewrite" || return 1
    mv "$config_rewrite" "$config_path"
}

begin_transition() {
    existing_transition=$(sed -n '1p' "$TRANSITION_FILE" 2>/dev/null)
    case $existing_transition in
        *[!0-9]*|'') ;;
        *) return 0 ;;
    esac
    transition_uptime=$(uptime_seconds)
    [ -n "$transition_uptime" ] || transition_uptime=0
    printf '%s\n' "$transition_uptime" > "$TRANSITION_FILE"
}

apply_config() {
    cp "$APPLY_CFG" "$SYSTEM_CFG" || return 1
    reload_output="$STATE_DIR/softrestart.$$.log"
    : > "$reload_output"
    chmod 600 "$reload_output"
    log_message "applying airOS wireless configuration"
    "$SOFTRESTART" save > "$reload_output" 2>&1 &
    reload_pid=$!
    reload_elapsed=0
    while kill -0 "$reload_pid" 2>/dev/null; do
        sleep 1
        reload_elapsed=$((reload_elapsed + 1))
        if [ $((reload_elapsed % 15)) -eq 0 ]; then
            log_message "airOS reload still running elapsed=${reload_elapsed}s"
        fi
    done
    wait "$reload_pid"
    reload_status=$?
    rm -f "$reload_output"
    if [ "$reload_status" -eq 0 ]; then
        log_message "airOS wireless configuration applied elapsed=${reload_elapsed}s"
        return 0
    fi
    log_message "airOS wireless configuration failed status=$reload_status"
    return "$reload_status"
}

clear_failures() {
    rm -f "$STATE_DIR/failure.ssid" "$STATE_DIR/failure.count"
}

connect_profile() {
    requested_profile=$1
    reuse_scan=${2:-no}
    profile_name_is_valid "$requested_profile" || {
        log_message "invalid profile name"
        return 1
    }
    profile_path="$PROFILE_DIR/$requested_profile"
    [ -f "$profile_path" ] || {
        log_message "missing profile=$requested_profile"
        return 1
    }
    target_ssid=$(effective_ssid "$profile_path")
    [ -n "$target_ssid" ] || {
        log_message "profile has no effective SSID profile=$requested_profile"
        return 1
    }

    if link_is_target "$target_ssid" && has_dhcp_and_route && internet_reachable; then
        clear_failures
        rm -f "$TRANSITION_FILE"
        log_message "requested profile already ready profile=$requested_profile ssid=$target_ssid"
        return 0
    fi

    begin_transition

    selected_frequency=
    if [ "$reuse_scan" = yes ] && [ -s "$SCAN_FILE" ]; then
        selected_frequency=$(scan_field_for_profile "$profile_path" 4)
    else
        scan_networks && selected_frequency=$(scan_field_for_profile "$profile_path" 4)
    fi
    cp "$profile_path" "$APPLY_CFG" || return 1
    case $selected_frequency in
        ''|*[!0-9]*) selected_frequency= ;;
    esac

    if [ -n "$selected_frequency" ]; then
        set_scan_list "$APPLY_CFG" enabled "$selected_frequency"
        log_message "connecting profile=$requested_profile ssid=$target_ssid fast_frequency=$selected_frequency"
        begin_transition
        if apply_config && wait_for_link "$target_ssid" "$ASSOCIATE_FAST_SECONDS"; then
            link_result=success
        else
            link_result=failed
        fi
    else
        link_result=failed
        log_message "target absent from scan; using unrestricted fallback profile=$requested_profile"
    fi

    if [ "$link_result" != success ]; then
        cp "$profile_path" "$APPLY_CFG" || return 1
        set_scan_list "$APPLY_CFG" disabled ""
        log_message "retrying unrestricted profile=$requested_profile ssid=$target_ssid"
        begin_transition
        apply_config || {
            log_message "soft restart failed profile=$requested_profile"
            return 1
        }
        if ! wait_for_link "$target_ssid" "$ASSOCIATE_FALLBACK_SECONDS"; then
            log_message "association timeout profile=$requested_profile ssid=$target_ssid"
            return 1
        fi
    fi

    rm -f "$TRANSITION_FILE"
    if ! wait_for_dhcp; then
        log_message "associated but DHCP/default route timed out profile=$requested_profile"
        return 2
    fi
    if ! internet_reachable; then
        log_message "associated with route but internet check failed profile=$requested_profile"
        return 2
    fi
    clear_failures
    log_message "connection ready profile=$requested_profile ssid=$target_ssid"
    return 0
}

run_requested_connect() {
    requested_name=$1
    connect_profile "$requested_name"
    requested_status=$?
    if [ "$requested_status" -eq 1 ] && \
        profile_name_is_valid "$requested_name" && [ -f "$PROFILE_DIR/$requested_name" ]; then
        recover_after_failed_manual_switch "$requested_name" || true
    fi
    return "$requested_status"
}

profile_template_for_security() {
    wanted_security=$1
    for template_path in "$PROFILE_DIR"/*; do
        [ -f "$template_path" ] || continue
        template_name=${template_path##*/}
        case $template_name in
            system.cfg|reset|*.backup.*) continue ;;
        esac
        [ "$(profile_security "$template_path")" = "$wanted_security" ] || continue
        printf '%s\n' "$template_path"
        return 0
    done
    return 1
}

write_provision_config() {
    provision_template=$1
    provision_output=$2
    provision_ssid=$3
    provision_security=$4
    provision_bssid=$5
    provision_password=$6
    saw_wireless_ssid=no
    saw_wireless_ap=no
    saw_wireless_security=no
    saw_scan_status=no
    saw_scan_channels=no
    saw_wpa_status=no
    saw_wpa_device_status=no
    saw_wpa_ssid=no
    saw_wpa_bssid=no
    saw_wpa_psk=no

    : > "$provision_output"
    while IFS= read -r provision_line || [ -n "$provision_line" ]; do
        provision_key=${provision_line%%=*}
        case $provision_key in
            wireless.1.ssid)
                printf 'wireless.1.ssid=%s\n' "$provision_ssid"
                saw_wireless_ssid=yes
                ;;
            wireless.1.ap)
                printf 'wireless.1.ap=%s\n' "$provision_bssid"
                saw_wireless_ap=yes
                ;;
            wireless.1.security.type)
                printf 'wireless.1.security.type=none\n'
                saw_wireless_security=yes
                ;;
            wireless.1.scan_list.status)
                printf 'wireless.1.scan_list.status=disabled\n'
                saw_scan_status=yes
                ;;
            wireless.1.scan_list.channels)
                printf 'wireless.1.scan_list.channels=\n'
                saw_scan_channels=yes
                ;;
            wpasupplicant.status)
                if [ "$provision_security" = wpa ]; then
                    printf 'wpasupplicant.status=enabled\n'
                else
                    printf 'wpasupplicant.status=disabled\n'
                fi
                saw_wpa_status=yes
                ;;
            wpasupplicant.device.1.status)
                if [ "$provision_security" = wpa ]; then
                    printf 'wpasupplicant.device.1.status=enabled\n'
                else
                    printf 'wpasupplicant.device.1.status=disabled\n'
                fi
                saw_wpa_device_status=yes
                ;;
            wpasupplicant.profile.1.network.1.ssid)
                printf 'wpasupplicant.profile.1.network.1.ssid=%s\n' "$provision_ssid"
                saw_wpa_ssid=yes
                ;;
            wpasupplicant.profile.1.network.1.bssid)
                printf 'wpasupplicant.profile.1.network.1.bssid=%s\n' "$provision_bssid"
                saw_wpa_bssid=yes
                ;;
            wpasupplicant.profile.1.network.1.psk)
                if [ "$provision_security" = wpa ]; then
                    printf 'wpasupplicant.profile.1.network.1.psk=%s\n' "$provision_password"
                    saw_wpa_psk=yes
                fi
                ;;
            *) printf '%s\n' "$provision_line" ;;
        esac
    done < "$provision_template" >> "$provision_output"

    [ "$saw_wireless_ssid" = yes ] || printf 'wireless.1.ssid=%s\n' "$provision_ssid" >> "$provision_output"
    [ "$saw_wireless_ap" = yes ] || printf 'wireless.1.ap=%s\n' "$provision_bssid" >> "$provision_output"
    [ "$saw_wireless_security" = yes ] || printf 'wireless.1.security.type=none\n' >> "$provision_output"
    [ "$saw_scan_status" = yes ] || printf 'wireless.1.scan_list.status=disabled\n' >> "$provision_output"
    [ "$saw_scan_channels" = yes ] || printf 'wireless.1.scan_list.channels=\n' >> "$provision_output"
    if [ "$provision_security" = wpa ]; then
        [ "$saw_wpa_status" = yes ] || printf 'wpasupplicant.status=enabled\n' >> "$provision_output"
        [ "$saw_wpa_device_status" = yes ] || printf 'wpasupplicant.device.1.status=enabled\n' >> "$provision_output"
        [ "$saw_wpa_ssid" = yes ] || printf 'wpasupplicant.profile.1.network.1.ssid=%s\n' "$provision_ssid" >> "$provision_output"
        [ "$saw_wpa_bssid" = yes ] || printf 'wpasupplicant.profile.1.network.1.bssid=%s\n' "$provision_bssid" >> "$provision_output"
        [ "$saw_wpa_psk" = yes ] || printf 'wpasupplicant.profile.1.network.1.psk=%s\n' "$provision_password" >> "$provision_output"
    else
        [ "$saw_wpa_status" = yes ] || printf 'wpasupplicant.status=disabled\n' >> "$provision_output"
        [ "$saw_wpa_device_status" = yes ] || printf 'wpasupplicant.device.1.status=disabled\n' >> "$provision_output"
    fi
}

provision_profile() {
    new_ssid=$1
    new_security=$2
    new_bssid=$3
    new_password=$4

    profile_name_is_valid "$new_ssid" || {
        log_message "new SSID cannot be used as a profile filename"
        return 1
    }
    case $new_ssid in
        .*) log_message "new SSID cannot begin with a dot"; return 1 ;;
    esac
    ssid_length=$(printf '%s' "$new_ssid" | wc -c | tr -d '[:space:]')
    case $ssid_length in
        ''|*[!0-9]*) return 1 ;;
    esac
    [ "$ssid_length" -ge 1 ] && [ "$ssid_length" -le 32 ] || {
        log_message "new SSID must be 1 to 32 bytes"
        return 1
    }
    if printf '%s' "$new_ssid" | LC_ALL=C grep -q '[[:cntrl:]]'; then
        log_message "new SSID contains a control character"
        return 1
    fi
    case $new_security in
        wpa|none) ;;
        *) log_message "unsupported new-network security=$new_security"; return 1 ;;
    esac
    if ! printf '%s\n' "$new_bssid" | grep -Eq '^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$'; then
        log_message "invalid access-point address"
        return 1
    fi
    if [ "$new_security" = wpa ]; then
        password_length=$(printf '%s' "$new_password" | wc -c | tr -d '[:space:]')
        case $password_length in
            ''|*[!0-9]*) return 1 ;;
        esac
        [ "$password_length" -ge 8 ] && [ "$password_length" -le 63 ] || {
            log_message "WPA password must be 8 to 63 bytes"
            return 1
        }
        if printf '%s' "$new_password" | LC_ALL=C grep -q '[[:cntrl:]]'; then
            log_message "WPA password contains a control character"
            return 1
        fi
    elif [ -n "$new_password" ]; then
        log_message "open networks do not accept a password"
        return 1
    fi
    [ ! -e "$PROFILE_DIR/$new_ssid" ] || {
        log_message "profile already exists profile=$new_ssid"
        return 1
    }

    provision_template=$(profile_template_for_security "$new_security") || {
        log_message "no saved template for security=$new_security"
        return 1
    }
    : > "$PAUSE_FILE"
    log_message "automatic selection paused for new profile=$new_ssid"
    pending_name=.dashboard-new.$$
    PENDING_PROFILE="$PROFILE_DIR/$pending_name"
    write_provision_config "$provision_template" "$PENDING_PROFILE" \
        "$new_ssid" "$new_security" "$new_bssid" "$new_password" || return 1
    chmod 750 "$PENDING_PROFILE"

    connect_profile "$pending_name"
    provision_status=$?
    case $provision_status in
        0|2)
            set_scan_list "$SYSTEM_CFG" disabled "" || return 1
            save_current_profile "$new_ssid" || return 1
            rm -f "$PENDING_PROFILE"
            PENDING_PROFILE=
            log_message "provisioned profile=$new_ssid security=$new_security"
            return "$provision_status"
            ;;
        *)
            recover_after_failed_manual_switch "$pending_name" || true
            rm -f "$PENDING_PROFILE"
            PENDING_PROFILE=
            return "$provision_status"
            ;;
    esac
}

manual_transition_active() {
    configured_ssid=$(effective_ssid "$SYSTEM_CFG")
    [ -n "$configured_ssid" ] || return 1
    if link_is_target "$configured_ssid"; then
        rm -f "$TRANSITION_FILE"
        return 1
    fi

    now_uptime=$(uptime_seconds)
    [ -n "$now_uptime" ] || now_uptime=0
    transition_start=$(sed -n '1p' "$TRANSITION_FILE" 2>/dev/null)
    case $transition_start in
        ''|*[!0-9]*)
            transition_start=$now_uptime
            printf '%s\n' "$transition_start" > "$TRANSITION_FILE"
            ;;
    esac
    transition_age=$((now_uptime - transition_start))
    if [ "$transition_age" -lt "$MANUAL_GRACE_SECONDS" ]; then
        log_message "manual/config transition protected target=$configured_ssid age=$transition_age"
        return 0
    fi
    log_message "transition grace expired target=$configured_ssid age=$transition_age"
    rm -f "$TRANSITION_FILE"
    return 1
}

recover_after_failed_manual_switch() {
    failed_profile=$1
    failed_path="$PROFILE_DIR/$failed_profile"
    failed_ssid=$(effective_ssid "$failed_path")
    set_cooldown "$failed_profile"

    recovery_start=$(sed -n '1p' "$TRANSITION_FILE" 2>/dev/null)
    recovery_now=$(uptime_seconds)
    [ -n "$recovery_now" ] || recovery_now=0
    case $recovery_start in
        ''|*[!0-9]*) recovery_start=$recovery_now ;;
    esac
    recovery_elapsed=$((recovery_now - recovery_start))
    recovery_remaining=$((MANUAL_GRACE_SECONDS - recovery_elapsed))
    if [ "$recovery_remaining" -gt 0 ]; then
        log_message "requested switch failed profile=$failed_profile; automatic recovery in ${recovery_remaining}s"
        while [ "$recovery_remaining" -gt 0 ]; do
            recovery_sleep=15
            [ "$recovery_remaining" -ge "$recovery_sleep" ] || recovery_sleep=$recovery_remaining
            sleep "$recovery_sleep"
            recovery_remaining=$((recovery_remaining - recovery_sleep))
            if [ "$recovery_remaining" -gt 0 ]; then
                log_message "manual switch protection active recovery_in=${recovery_remaining}s"
            fi
        done
    fi
    rm -f "$TRANSITION_FILE"
    log_message "manual switch protection expired; recovering best available saved network"

    if [ ! -s "$SCAN_FILE" ]; then
        scan_networks || {
            log_message "automatic recovery scan found no visible networks"
            return 1
        }
    fi
    recovery_profile=$(choose_candidate "$failed_ssid") || {
        log_message "automatic recovery found no eligible saved profile"
        return 1
    }
    log_message "automatic recovery selected profile=$recovery_profile"
    connect_profile "$recovery_profile" yes
    recovery_status=$?
    if [ "$recovery_status" -eq 0 ]; then
        log_message "automatic recovery completed profile=$recovery_profile"
        return 0
    fi
    set_cooldown "$recovery_profile"
    log_message "automatic recovery failed profile=$recovery_profile status=$recovery_status"
    return "$recovery_status"
}

record_failure() {
    failed_ssid=$1
    previous_ssid=$(sed -n '1p' "$STATE_DIR/failure.ssid" 2>/dev/null)
    previous_count=$(sed -n '1p' "$STATE_DIR/failure.count" 2>/dev/null)
    case $previous_count in
        ''|*[!0-9]*) previous_count=0 ;;
    esac
    [ "$previous_ssid" = "$failed_ssid" ] || previous_count=0
    failure_count=$((previous_count + 1))
    printf '%s\n' "$failed_ssid" > "$STATE_DIR/failure.ssid"
    printf '%s\n' "$failure_count" > "$STATE_DIR/failure.count"
    printf '%s\n' "$failure_count"
}

profile_priority() {
    priority_profile=$1
    configured_priority=
    if [ -f "$PRIORITY_FILE" ]; then
        configured_priority=$(awk -F'|' -v wanted="$priority_profile" '
            /^[[:space:]]*#/ { next }
            $2 == wanted { print $1; exit }
        ' "$PRIORITY_FILE")
    fi
    case $configured_priority in
        ''|*[!0-9]*) configured_priority= ;;
    esac
    if [ -n "$configured_priority" ]; then
        printf '%s\n' "$configured_priority"
    elif [ "$priority_profile" = denlink ]; then
        if [ -f "$PREFER_DENLINK_FLAG" ]; then printf '%s\n' 1000; else printf '%s\n' 10; fi
    else
        printf '%s\n' 100
    fi
}

cooldown_active() {
    cooldown_profile=$1
    cooldown_until=$(sed -n '1p' "$STATE_DIR/cooldown.$cooldown_profile" 2>/dev/null)
    case $cooldown_until in
        ''|*[!0-9]*) return 1 ;;
    esac
    cooldown_now=$(uptime_seconds)
    [ -n "$cooldown_now" ] || cooldown_now=0
    [ "$cooldown_now" -lt "$cooldown_until" ]
}

set_cooldown() {
    cooldown_profile=$1
    cooldown_now=$(uptime_seconds)
    [ -n "$cooldown_now" ] || cooldown_now=0
    printf '%s\n' $((cooldown_now + COOLDOWN_SECONDS)) > "$STATE_DIR/cooldown.$cooldown_profile"
}

choose_candidate() {
    candidate_current_ssid=$1
    best_score=-1
    best_profile=
    for candidate_path in "$PROFILE_DIR"/*; do
        [ -f "$candidate_path" ] || continue
        candidate_profile=${candidate_path##*/}
        case $candidate_profile in
            system.cfg|reset|*.backup.*) continue ;;
        esac
        cooldown_active "$candidate_profile" && continue
        candidate_ssid=$(effective_ssid "$candidate_path")
        [ -n "$candidate_ssid" ] || continue
        [ "$candidate_ssid" = "$candidate_current_ssid" ] && continue
        candidate_quality=$(scan_field_for_profile "$candidate_path" 1)
        case $candidate_quality in
            ''|*[!0-9]*) continue ;;
        esac
        candidate_priority=$(profile_priority "$candidate_profile")
        candidate_score=$((candidate_priority * 1000 + candidate_quality))
        if [ "$candidate_score" -gt "$best_score" ]; then
            best_score=$candidate_score
            best_profile=$candidate_profile
        fi
    done
    [ -n "$best_profile" ] || return 1
    printf '%s\n' "$best_profile"
}

auto_scan_due() {
    scan_now=$(uptime_seconds)
    [ -n "$scan_now" ] || scan_now=0
    last_scan=$(sed -n '1p' "$STATE_DIR/last_auto_scan" 2>/dev/null)
    case $last_scan in
        ''|*[!0-9]*) last_scan=0 ;;
    esac
    [ $((scan_now - last_scan)) -ge "$AUTO_SCAN_INTERVAL" ] || return 1
    printf '%s\n' "$scan_now" > "$STATE_DIR/last_auto_scan"
}

auto_select() {
    [ ! -f "$PAUSE_FILE" ] || {
        log_message "automatic selection paused"
        return 0
    }
    manual_transition_active && return 0
    auto_ssid=$(associated_ssid)
    auto_ccq=$(current_ccq)
    case $auto_ccq in
        ''|*[!0-9]*) auto_ccq=0 ;;
    esac

    if [ -n "$auto_ssid" ] && [ "$auto_ccq" -gt 300 ]; then
        if internet_reachable; then
            clear_failures
            if [ "$auto_ssid" != denlink ] || [ -f "$PREFER_DENLINK_FLAG" ]; then
                log_healthy_connection "$auto_ssid" "$auto_ccq"
                return 0
            fi
            auto_scan_due || return 0
        else
            failure_count=$(record_failure "$auto_ssid")
            log_message "internet failure ssid=$auto_ssid count=$failure_count/$FAILURES_BEFORE_SWITCH"
            [ "$failure_count" -ge "$FAILURES_BEFORE_SWITCH" ] || return 0
        fi
    fi

    scan_networks || return 0
    selected_profile=$(choose_candidate "$auto_ssid") || {
        log_message "no eligible saved profile found in scan"
        return 0
    }
    log_message "automatic candidate profile=$selected_profile"
    connect_profile "$selected_profile" yes
    connect_status=$?
    [ "$connect_status" -ne 0 ] || return 0
    set_cooldown "$selected_profile"
    log_message "candidate failed profile=$selected_profile status=$connect_status cooldown=$COOLDOWN_SECONDS"
    return 0
}

save_current_profile() {
    save_name=$1
    profile_name_is_valid "$save_name" || return 1
    [ -f "$SYSTEM_CFG" ] || return 1
    mkdir -p "$PROFILE_DIR/.disabled"
    save_destination="$PROFILE_DIR/$save_name"
    if [ -f "$save_destination" ]; then
        save_uptime=$(uptime_seconds)
        [ -n "$save_uptime" ] || save_uptime=0
        cp "$save_destination" "$PROFILE_DIR/.disabled/$save_name.backup.$save_uptime.$$" || return 1
    fi
    cp "$SYSTEM_CFG" "$PROFILE_DIR/.new.$$.cfg" || return 1
    chmod 750 "$PROFILE_DIR/.new.$$.cfg"
    mv "$PROFILE_DIR/.new.$$.cfg" "$save_destination" || return 1
    "$CFGMTD" -w -p /etc/
    log_message "saved profile explicitly profile=$save_name"
}

disable_profile() {
    disable_name=$1
    profile_name_is_valid "$disable_name" || return 1
    disable_source="$PROFILE_DIR/$disable_name"
    [ -f "$disable_source" ] || return 1
    mkdir -p "$PROFILE_DIR/.disabled"
    disable_uptime=$(uptime_seconds)
    [ -n "$disable_uptime" ] || disable_uptime=0
    mv "$disable_source" "$PROFILE_DIR/.disabled/$disable_name.$disable_uptime.$$" || return 1
    "$CFGMTD" -w -p /etc/
    log_message "disabled profile recoverably profile=$disable_name"
}

show_status() {
    status_configured=$(effective_ssid "$SYSTEM_CFG")
    status_associated=$(associated_ssid)
    status_ccq=$(current_ccq)
    [ -f "$PAUSE_FILE" ] && status_paused=yes || status_paused=no
    [ -d "$LOCK_DIR" ] && status_running=yes || status_running=no
    printf 'configured_ssid=%s\nassociated_ssid=%s\nccq=%s\npaused=%s\nselector_running=%s\n' \
        "$status_configured" "$status_associated" "$status_ccq" "$status_paused" "$status_running"
}

usage() {
    printf 'Usage: %s auto|connect PROFILE|status|pause|resume|save-current PROFILE|disable PROFILE|dashboard-status|dashboard-scan|manual-connect-stdin|provision-stdin\n' "$0" >&2
}

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
        rm -f "$PAUSE_FILE"
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
        : > "$PAUSE_FILE"
        log_message "automatic selection paused for manual profile=$manual_profile"
        acquire_lock || exit 1
        run_requested_connect "$manual_profile"
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
    *)
        usage
        exit 1
        ;;
esac
