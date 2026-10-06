#!/bin/sh
# Owns link checks, radio reloads, requested connections, and recovery.

associated_ssid() {
    "$IWGETID" ath0 -r 2>/dev/null || "$IWGETID" -r 2>/dev/null
}

current_ccq() {
    "$MCA_STATUS" 2>/dev/null | awk -F= '$1 == "ccq" {print $2; exit}' | tr -d '\r'
}

link_is_target() {
    local wanted_ssid=""
    local actual_ssid=""
    local link_ccq=""
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
    local wait_ssid=""
    local wait_seconds=""
    local next_wait_message=""
    wait_ssid=$1
    wait_seconds=$2
    waited=0
    next_wait_message=15
    log_message "waiting for association ssid=$wait_ssid timeout=${wait_seconds}s"
    link_is_target "$wait_ssid" && return 0
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

apply_config() {
    local apply_mode=""
    local reload_output=""
    local reload_pid=""
    local reload_elapsed=""
    local reload_status=""
    apply_mode=${1:-manager}
    preserve_current_login "$APPLY_CFG" || return 1
    # Manager-driven configuration changes must not be mistaken for native
    # airOS GUI changes by the next automatic cron invocation.
    [ "$apply_mode" = preserve-gui ] || clear_gui_transition
    cp "$APPLY_CFG" "$SYSTEM_CFG" || return 1
    record_observed_config || return 1
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
    record_observed_config || {
        log_message "unable to record airOS configuration after reload"
        return 1
    }
    if ! "$SSH_KEY_INSTALLER"; then
        log_message "persistent SSH key restore failed after airOS reload"
        return 1
    fi
    rm -f "$reload_output"
    if [ "$reload_status" -eq 0 ]; then
        log_message "airOS wireless configuration applied elapsed=${reload_elapsed}s"
        return 0
    fi
    log_message "airOS wireless configuration failed status=$reload_status"
    return "$reload_status"
}

connect_profile() {
    local requested_profile=""
    local force_connect=""
    local profile_path=""
    local target_ssid=""
    local selected_frequency=""
    local link_result=""
    requested_profile=$1
    force_connect=${2:-no}
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

    if [ "$force_connect" != yes ] && \
        link_is_target "$target_ssid" && has_dhcp_and_route && internet_reachable; then
        clear_failures
        rm -f "$TRANSITION_FILE"
        log_message "requested profile already ready profile=$requested_profile ssid=$target_ssid"
        return 0
    fi

    begin_transition
    selected_frequency=$(recent_frequency_for_profile "$profile_path") || selected_frequency=
    link_result=failed
    if [ -n "$selected_frequency" ]; then
        cp "$profile_path" "$APPLY_CFG" || return 1
        set_scan_list "$APPLY_CFG" enabled "$selected_frequency" || return 1
        log_message "connecting scanned-frequency profile=$requested_profile ssid=$target_ssid frequency=$selected_frequency"
        if apply_config && wait_for_link "$target_ssid" "$ASSOCIATE_FAST_SECONDS"; then
            link_result=success
        fi
    fi
    if [ "$link_result" != success ]; then
        cp "$profile_path" "$APPLY_CFG" || return 1
        set_scan_list "$APPLY_CFG" enabled "$STANDARD_SCAN_FREQUENCIES" || return 1
        log_message "connecting standard-frequency fallback profile=$requested_profile ssid=$target_ssid"
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
    local requested_name=""
    local requested_force=""
    local requested_status=""
    requested_name=$1
    requested_force=${2:-no}
    profile_name_is_valid "$requested_name" && [ -f "$PROFILE_DIR/$requested_name" ] || return 1
    start_manual_hold "$(effective_ssid "$PROFILE_DIR/$requested_name")" || return 1
    connect_profile "$requested_name" "$requested_force"
    requested_status=$?
    [ "$requested_status" -ne 0 ] || rm -f "$MANUAL_HOLD_FILE"
    if [ "$requested_status" -eq 1 ] && \
        profile_name_is_valid "$requested_name" && [ -f "$PROFILE_DIR/$requested_name" ]; then
        recover_after_failed_manual_switch "$requested_name" || true
    fi
    return "$requested_status"
}

recover_after_failed_manual_switch() {
    local failed_profile=""
    local failed_path=""
    local recovery_start=""
    local recovery_now=""
    local recovery_elapsed=""
    local recovery_remaining=""
    local recovery_sleep=""
    local recovery_profile=""
    local recovery_status=""
    rm -f "$MANUAL_HOLD_FILE"
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
