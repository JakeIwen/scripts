#!/bin/sh
# Owns Starlink power-off disconnection and immediate alternative selection.

clear_starlink_transition() {
    rm -f "$MANUAL_HOLD_FILE" "$TRANSITION_FILE" "$STATE_DIR/last_auto_scan"
    clear_gui_transition
    clear_failures
}

cooldown_starlink_profiles() {
    local off_profile=""
    for off_profile in "$PROFILE_DIR"/*; do
        [ -f "$off_profile" ] || continue
        if [ "$(effective_ssid "$off_profile")" = denlink ]; then
            # Let the powered-down AP disappear before cron can select it.
            # Explicit power-on/connect requests bypass this short cooldown.
            set_cooldown "${off_profile##*/}" 120
        fi
    done
}

release_denlink() {
    # Leave saved profiles intact and preserve current Ethernet/admin settings.
    log_message "Starlink powered off; releasing denlink immediately"
    write_provision_config "$SYSTEM_CFG" "$APPLY_CFG" \
        "vanpi-disconnected-$$" none '02:00:00:00:00:00' '' || return 1
    apply_config || return 1
    clear_starlink_transition
}

starlink_direct_candidate() {
    # Only scan_networks publishes the completion marker, after a full survey.
    # A pinned radio can reuse that survey, but cannot start another full one
    # without reloading. Return 2 when the disconnect/full-scan path is needed,
    # or 1 when a fresh survey found no eligible alternative.
    if scan_results_are_recent; then
        choose_candidate denlink && return 0
    fi
    runtime_scan_list_needs_normalization && return 2
    scan_networks >&2 || return 1
    choose_candidate denlink
}

starlink_power_off() {
    local off_configured=""
    local off_candidate=""
    local off_result=""
    local off_scan_status=""
    off_configured=$(effective_ssid "$SYSTEM_CFG")
    if [ "$off_configured" != denlink ] && ! link_is_target denlink; then
        log_message "Starlink powered off; antenna already targets another network"
        return 0
    fi
    # Pause does not take the selector lock; recheck it after discovery and
    # profile inspection, immediately before admitting either connection.
    off_scan_status=2
    if [ ! -f "$PAUSE_FILE" ]; then
        off_candidate=$(starlink_direct_candidate)
        off_scan_status=$?
        if [ "$off_scan_status" -eq 0 ]; then
            clear_starlink_transition
            cooldown_starlink_profiles
            if [ -f "$PAUSE_FILE" ]; then
                release_denlink
                return $?
            fi
            log_message "Starlink power-off switching directly to profile=$off_candidate"
            connect_profile "$off_candidate" yes
            off_result=$?
            [ "$off_result" -ne 0 ] || return 0
            set_cooldown "$off_candidate"
            # A radio link needing DHCP or portal login has still left denlink.
            [ "$off_result" -ne 2 ] || return 0
            log_message "Starlink direct association failed; releasing and surveying again"
            off_scan_status=2
        fi
    fi
    release_denlink || return 1
    cooldown_starlink_profiles
    [ ! -f "$PAUSE_FILE" ] || {
        log_message "denlink released; explicit maintenance pause retained"
        return 0
    }
    [ "$off_scan_status" -ne 1 ] || {
        log_message "denlink released; no other saved network visible"
        return 0
    }
    scan_networks || return 0
    off_candidate=$(choose_candidate denlink) || {
        log_message "denlink released; no other saved network visible"
        return 0
    }
    [ ! -f "$PAUSE_FILE" ] || return 0
    log_message "Starlink power-off roaming selected profile=$off_candidate"
    connect_profile "$off_candidate" yes
    off_result=$?
    [ "$off_result" -eq 0 ] || set_cooldown "$off_candidate"
    # Releasing denlink succeeded even if the alternative is unavailable.
    return 0
}
