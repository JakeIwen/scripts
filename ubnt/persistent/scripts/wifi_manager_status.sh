#!/bin/sh
# Owns human-readable status and credential-free dashboard records.

hex_encode() {
    printf '%s' "$1" | "$HEXDUMP" -v -e '1/1 "%02x"'
}

emit_dashboard_snapshot() {
    local dashboard_configured=""
    local dashboard_associated=""
    local dashboard_radio_status=""
    local dashboard_ccq=""
    local dashboard_signal=""
    local dashboard_noise=""
    local dashboard_hold_remaining=""
    local dashboard_paused=""
    local dashboard_running=""
    local dashboard_profile_path=""
    local dashboard_profile=""
    local dashboard_wireless_ssid=""
    local dashboard_wireless_bssid=""
    local dashboard_wireless_security=""
    local dashboard_wpa_status=""
    local dashboard_wpa_device_status=""
    local dashboard_wpa_ssid=""
    local dashboard_wpa_bssid=""
    local dashboard_has_password=""
    local dashboard_txpower=""
    local dashboard_rate_module=""
    local dashboard_rate_auto=""
    local dashboard_rate_mcs=""
    local dashboard_line=""
    local dashboard_key=""
    local dashboard_value=""
    local dashboard_ssid=""
    local dashboard_bssid=""
    local dashboard_security=""
    local dashboard_priority=""
    local dashboard_quality=""
    local dashboard_frequency=""
    local dashboard_channel=""
    dashboard_configured=$(effective_ssid "$SYSTEM_CFG")
    dashboard_associated=$(associated_ssid)
    dashboard_radio_status=$("$MCA_STATUS" 2>/dev/null)
    dashboard_ccq=$(printf '%s\n' "$dashboard_radio_status" | \
        awk -F= '$1 == "ccq" {print $2; exit}' | tr -d '\r')
    dashboard_signal=$(printf '%s\n' "$dashboard_radio_status" | \
        awk -F= '$1 == "signal" {print $2; exit}' | tr -d '\r')
    dashboard_noise=$(printf '%s\n' "$dashboard_radio_status" | \
        awk -F= '$1 == "noise" {print $2; exit}' | tr -d '\r')
    dashboard_hold_remaining=$(manual_hold_remaining)
    dashboard_paused=no
    if [ -f "$PAUSE_FILE" ] || [ "$dashboard_hold_remaining" -gt 0 ]; then
        dashboard_paused=yes
    fi
    [ -d "$LOCK_DIR" ] && dashboard_running=yes || dashboard_running=no
    printf 'state|%s|%s|%s|%s|%s|%s|%s|%s\n' \
        "$(hex_encode "$dashboard_configured")" \
        "$(hex_encode "$dashboard_associated")" \
        "$dashboard_ccq" "$dashboard_paused" "$dashboard_running" \
        "$dashboard_signal" "$dashboard_noise" "$dashboard_hold_remaining"

    for dashboard_profile_path in "$PROFILE_DIR"/*; do
        [ -f "$dashboard_profile_path" ] || continue
        dashboard_profile=${dashboard_profile_path##*/}
        case $dashboard_profile in
            system.cfg|reset|*.backup.*) continue ;;
        esac
        dashboard_wireless_ssid=
        dashboard_wireless_bssid=
        dashboard_wireless_security=
        dashboard_wpa_status=
        dashboard_wpa_device_status=
        dashboard_wpa_ssid=
        dashboard_wpa_bssid=
        dashboard_has_password=no
        dashboard_txpower=
        dashboard_rate_module=
        dashboard_rate_auto=
        dashboard_rate_mcs=
        while IFS= read -r dashboard_line || [ -n "$dashboard_line" ]; do
            dashboard_key=${dashboard_line%%=*}
            dashboard_value=${dashboard_line#*=}
            case $dashboard_key in
                wireless.1.ssid) dashboard_wireless_ssid=$dashboard_value ;;
                wireless.1.ap) dashboard_wireless_bssid=$dashboard_value ;;
                wireless.1.security.type) dashboard_wireless_security=$dashboard_value ;;
                wpasupplicant.status) dashboard_wpa_status=$dashboard_value ;;
                wpasupplicant.device.1.status) dashboard_wpa_device_status=$dashboard_value ;;
                wpasupplicant.profile.1.network.1.ssid) dashboard_wpa_ssid=$dashboard_value ;;
                wpasupplicant.profile.1.network.1.bssid) dashboard_wpa_bssid=$dashboard_value ;;
                aaa.1.wpa.psk|wpasupplicant.profile.1.network.1.psk)
                    [ -z "$dashboard_value" ] || dashboard_has_password=yes
                    ;;
                radio.1.txpower) dashboard_txpower=$dashboard_value ;;
                radio.rate_module) dashboard_rate_module=$dashboard_value ;;
                radio.1.rate.auto) dashboard_rate_auto=$dashboard_value ;;
                radio.1.rate.mcs) dashboard_rate_mcs=$dashboard_value ;;
            esac
        done < "$dashboard_profile_path"
        if [ "$dashboard_wpa_status" = enabled ] && \
            [ "$dashboard_wpa_device_status" = enabled ]; then
            dashboard_ssid=$dashboard_wpa_ssid
            dashboard_bssid=$dashboard_wpa_bssid
            dashboard_security=wpa
        else
            dashboard_ssid=$dashboard_wireless_ssid
            dashboard_bssid=$dashboard_wireless_bssid
            case $dashboard_wireless_security in
                wep*) dashboard_security=wep ;;
                wpa*) dashboard_security=wpa ;;
                *) dashboard_security=none ;;
            esac
        fi
        [ -n "$dashboard_ssid" ] || continue
        dashboard_priority=$(profile_priority "$dashboard_profile")
        printf 'profile|%s|%s|%s|%s|%s|%s|%s|%s|%s|%s\n' \
            "$(hex_encode "$dashboard_profile")" \
            "$(hex_encode "$dashboard_ssid")" \
            "$dashboard_security" "$dashboard_priority" \
            "$dashboard_bssid" "$dashboard_has_password" \
            "$dashboard_txpower" "$dashboard_rate_module" \
            "$dashboard_rate_auto" "$dashboard_rate_mcs"
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

show_status() {
    local status_configured=""
    local status_associated=""
    local status_ccq=""
    local status_paused=""
    local status_running=""
    status_configured=$(effective_ssid "$SYSTEM_CFG")
    status_associated=$(associated_ssid)
    status_ccq=$(current_ccq)
    [ -f "$PAUSE_FILE" ] && status_paused=yes || status_paused=no
    [ -d "$LOCK_DIR" ] && status_running=yes || status_running=no
    printf 'configured_ssid=%s\nassociated_ssid=%s\nccq=%s\npaused=%s\nselector_running=%s\nmanual_hold_remaining_seconds=%s\n' \
        "$status_configured" "$status_associated" "$status_ccq" "$status_paused" "$status_running" \
        "$(manual_hold_remaining)"
}
