#!/bin/sh
# Owns Starlink power-off disconnection and immediate alternative selection.

starlink_power_off() {
    local off_configured=""
    local off_profile=""
    local off_candidate=""
    local off_result=""
    off_configured=$(effective_ssid "$SYSTEM_CFG")
    if [ "$off_configured" != denlink ] && ! link_is_target denlink; then
        log_message "Starlink powered off; antenna already targets another network"
        return 0
    fi
    # Leave denlink's profile intact. Only the live wireless selection changes;
    # use the current config to preserve management addressing and admin login.
    log_message "Starlink powered off; releasing denlink immediately"
    write_provision_config "$SYSTEM_CFG" "$APPLY_CFG" \
        "vanpi-disconnected-$$" none '02:00:00:00:00:00' '' || return 1
    apply_config || return 1
    rm -f "$MANUAL_HOLD_FILE" "$TRANSITION_FILE" "$STATE_DIR/last_auto_scan"
    clear_gui_transition
    clear_failures
    for off_profile in "$PROFILE_DIR"/*; do
        [ -f "$off_profile" ] || continue
        if [ "$(effective_ssid "$off_profile")" = denlink ]; then
            # Let the powered-down AP disappear before cron can select it.
            # Explicit power-on/connect requests bypass this short cooldown.
            set_cooldown "${off_profile##*/}" 120
        fi
    done
    [ ! -f "$PAUSE_FILE" ] || {
        log_message "denlink released; explicit maintenance pause retained"
        return 0
    }
    scan_networks || return 0
    off_candidate=$(choose_candidate denlink) || {
        log_message "denlink released; no other saved network visible"
        return 0
    }
    log_message "Starlink power-off roaming selected profile=$off_candidate"
    connect_profile "$off_candidate" yes
    off_result=$?
    [ "$off_result" -eq 0 ] || set_cooldown "$off_candidate"
    # Releasing denlink succeeded even if the alternative is unavailable.
    return 0
}
