#!/bin/sh
# Owns observed configuration, GUI stabilization, and bounded manual holds.

system_config_digest() {
    [ -f "$SYSTEM_CFG" ] || return 1
    "$MD5SUM" "$SYSTEM_CFG" 2>/dev/null | awk 'NR == 1 {print $1}'
}

record_observed_config() {
    local observed_digest=""
    local observed_digest_new=""
    observed_digest=$(system_config_digest)
    [ -n "$observed_digest" ] || return 1
    observed_digest_new="$OBSERVED_CONFIG_DIGEST_FILE.new.$$"
    printf '%s\n' "$observed_digest" > "$observed_digest_new" || return 1
    mv "$observed_digest_new" "$OBSERVED_CONFIG_DIGEST_FILE"
}

clear_gui_transition() {
    rm -f "$GUI_TRANSITION_FILE" "$GUI_TARGET_FILE"
}

observe_external_config_change() {
    local current_digest=""
    local previous_digest=""
    local gui_started=""
    current_digest=$(system_config_digest)
    [ -n "$current_digest" ] || return 1
    previous_digest=$(sed -n '1p' "$OBSERVED_CONFIG_DIGEST_FILE" 2>/dev/null)
    if [ -z "$previous_digest" ]; then
        record_observed_config
        return 1
    fi
    [ "$current_digest" != "$previous_digest" ] || return 1

    record_observed_config || return 1
    gui_target=$(effective_ssid "$SYSTEM_CFG")
    if [ -z "$gui_target" ]; then
        clear_gui_transition
        log_message "external airOS configuration detected without an SSID"
        return 1
    fi

    gui_started=$(uptime_seconds)
    [ -n "$gui_started" ] || gui_started=0
    printf '%s\n' "$gui_started" > "$GUI_TRANSITION_FILE"
    printf '%s\n' "$gui_target" > "$GUI_TARGET_FILE"
    rm -f "$TRANSITION_FILE"
    clear_failures
    log_message "external airOS configuration detected target=$gui_target grace=${GUI_GRACE_SECONDS}s"
    return 0
}

begin_transition() {
    local existing_transition=""
    local transition_uptime=""
    existing_transition=$(sed -n '1p' "$TRANSITION_FILE" 2>/dev/null)
    case $existing_transition in
        *[!0-9]*|'') ;;
        *) return 0 ;;
    esac
    transition_uptime=$(uptime_seconds)
    [ -n "$transition_uptime" ] || transition_uptime=0
    printf '%s\n' "$transition_uptime" > "$TRANSITION_FILE"
}

start_manual_hold() {
    local hold_started=""
    hold_started=$(uptime_seconds)
    [ -n "$hold_started" ] && [ -n "$1" ] || return 1
    printf '%s\n%s\n' "$hold_started" "$1" > "$MANUAL_HOLD_FILE.new.$$" || return 1
    mv "$MANUAL_HOLD_FILE.new.$$" "$MANUAL_HOLD_FILE" || return 1
    clear_failures
    log_message "manual connection protection target=$1 portal_grace=${PORTAL_GRACE_SECONDS}s"
}

manual_hold_remaining() {
    local hold_start=""
    local hold_now=""
    hold_start=$(sed -n '1p' "$MANUAL_HOLD_FILE" 2>/dev/null)
    hold_target=$(sed -n '2p' "$MANUAL_HOLD_FILE" 2>/dev/null)
    hold_now=$(uptime_seconds)
    case $hold_start:$hold_now in
        *[!0-9:]*|:*|*:) printf '0\n'; return ;;
    esac
    hold_age=$((hold_now - hold_start))
    if [ "$hold_age" -ge 0 ] && [ "$hold_age" -lt "$PORTAL_GRACE_SECONDS" ] && \
        [ -n "$hold_target" ] && [ "$hold_target" = "$(effective_ssid "$SYSTEM_CFG")" ]; then
        printf '%s\n' $((PORTAL_GRACE_SECONDS - hold_age))
    else
        printf '0\n'
    fi
}

manual_hold_active() {
    local hold_remaining=""
    [ -f "$MANUAL_HOLD_FILE" ] || return 1
    hold_remaining=$(manual_hold_remaining)
    if [ "$hold_remaining" -le 0 ]; then
        rm -f "$MANUAL_HOLD_FILE"
        log_message "manual connection protection expired; automatic selection resumed"
        return 2
    fi
    hold_target=$(sed -n '2p' "$MANUAL_HOLD_FILE" 2>/dev/null)
    if link_is_target "$hold_target"; then
        if has_dhcp_and_route && internet_reachable; then
            rm -f "$MANUAL_HOLD_FILE"
            log_message "manual connection online; automatic selection resumed"
            return 1
        fi
        log_message "captive portal protection target=$hold_target remaining=${hold_remaining}s"
        return 0
    fi
    # The association/reload grace is shorter than the captive-portal grace.
    # Losing an established network must not strand the selector for ten minutes.
    hold_age=$((PORTAL_GRACE_SECONDS - hold_remaining))
    if [ "$hold_age" -lt "$MANUAL_GRACE_SECONDS" ]; then
        log_message "manual connection protection target=$hold_target age=${hold_age}s"
        return 0
    fi
    rm -f "$MANUAL_HOLD_FILE" "$TRANSITION_FILE"
    log_message "manual target unavailable; automatic selection resumed"
    return 2
}

manual_transition_active() {
    local configured_ssid=""
    local now_uptime=""
    local transition_start=""
    local transition_age=""
    configured_ssid=$(effective_ssid "$SYSTEM_CFG")
    [ -n "$configured_ssid" ] || return 1
    case $configured_ssid in
        vanpi-disconnected-*) return 1 ;;
    esac
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

handle_gui_transition() {
    local gui_start=""
    local gui_now=""
    local gui_age=""
    local gui_configured=""
    local gui_current_digest=""
    local gui_observed_digest=""
    local gui_connection_ready=""
    local gui_name_valid=""
    [ -f "$GUI_TRANSITION_FILE" ] && [ -f "$GUI_TARGET_FILE" ] || return 1

    gui_target=$(sed -n '1p' "$GUI_TARGET_FILE" 2>/dev/null)
    gui_start=$(sed -n '1p' "$GUI_TRANSITION_FILE" 2>/dev/null)
    [ -n "$gui_target" ] || {
        clear_gui_transition
        return 1
    }
    gui_now=$(uptime_seconds)
    [ -n "$gui_now" ] || gui_now=0
    case $gui_start in
        ''|*[!0-9]*)
            gui_start=$gui_now
            printf '%s\n' "$gui_start" > "$GUI_TRANSITION_FILE"
            ;;
    esac
    gui_age=$((gui_now - gui_start))
    if [ "$gui_age" -lt 0 ]; then
        gui_age=0
        printf '%s\n' "$gui_now" > "$GUI_TRANSITION_FILE"
    fi

    # Do not persist a stale target if airOS changes the configuration while
    # this cron invocation is inspecting it. The next invocation will record
    # the newer change and restart its grace window.
    gui_configured=$(effective_ssid "$SYSTEM_CFG")
    gui_current_digest=$(system_config_digest)
    gui_observed_digest=$(sed -n '1p' "$OBSERVED_CONFIG_DIGEST_FILE" 2>/dev/null)
    if [ "$gui_configured" != "$gui_target" ] || \
        [ -z "$gui_current_digest" ] || \
        [ "$gui_current_digest" != "$gui_observed_digest" ]; then
        log_message "external airOS configuration changed again; deferring profile save"
        return 0
    fi

    gui_connection_ready=no
    if link_is_target "$gui_target" && has_dhcp_and_route && internet_reachable; then
        gui_connection_ready=yes
        if runtime_scan_list_needs_normalization; then
            gui_connection_ready=no
            if normalize_runtime_scan_list external-gui preserve-gui && \
                wait_for_link "$gui_target" "$ASSOCIATE_FALLBACK_SECONDS" && \
                wait_for_dhcp && internet_reachable; then
                gui_connection_ready=yes
                log_message "external airOS connection stable after clearing scan-frequency restriction target=$gui_target"
            else
                log_message "external airOS connection not ready after clearing scan-frequency restriction target=$gui_target"
            fi
        fi
    fi

    if [ "$gui_connection_ready" = yes ]; then
        case $gui_target in
            .*) gui_name_valid=no ;;
            *) if profile_name_is_valid "$gui_target"; then gui_name_valid=yes; else gui_name_valid=no; fi ;;
        esac
        if [ "$gui_name_valid" != yes ]; then
            clear_gui_transition
            clear_failures
            rm -f "$TRANSITION_FILE"
            log_message "external airOS connection ready but SSID cannot be a profile name"
            return 0
        fi
        if save_current_profile "$gui_target" gui; then
            clear_gui_transition
            clear_failures
            rm -f "$TRANSITION_FILE"
            log_message "external airOS connection saved target=$gui_target"
            return 0
        fi
        log_message "external airOS connection ready but profile save failed target=$gui_target"
    fi

    if [ -f "$PAUSE_FILE" ]; then
        log_message "external airOS transition protected target=$gui_target age=$gui_age paused=yes"
        return 0
    fi
    if [ "$gui_age" -lt "$GUI_GRACE_SECONDS" ]; then
        log_message "external airOS transition protected target=$gui_target age=$gui_age"
        return 0
    fi

    clear_gui_transition
    rm -f "$TRANSITION_FILE"
    log_message "external airOS transition grace expired target=$gui_target age=$gui_age"
    return 2
}
