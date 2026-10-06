#!/bin/sh
# Owns new-network configuration generation and profile provisioning.

write_provision_config() {
    local provision_output=""
    local provision_ssid=""
    local provision_security=""
    local provision_bssid=""
    local provision_password=""
    local saw_wireless_ssid=""
    local saw_wireless_ap=""
    local saw_wireless_security=""
    local saw_scan_status=""
    local saw_scan_channels=""
    local saw_wpa_status=""
    local saw_wpa_device_status=""
    local saw_wpa_ssid=""
    local saw_wpa_bssid=""
    local saw_wpa_psk=""
    local provision_line=""
    local provision_key=""
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
                printf 'wireless.1.scan_list.status=enabled\n'
                saw_scan_status=yes
                ;;
            wireless.1.scan_list.channels)
                printf 'wireless.1.scan_list.channels=%s\n' "$STANDARD_SCAN_FREQUENCIES"
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
    [ "$saw_scan_status" = yes ] || printf 'wireless.1.scan_list.status=enabled\n' >> "$provision_output"
    [ "$saw_scan_channels" = yes ] || \
        printf 'wireless.1.scan_list.channels=%s\n' "$STANDARD_SCAN_FREQUENCIES" >> "$provision_output"
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
    preserve_current_login "$provision_output"
}

provision_profile() {
    local new_ssid=""
    local new_security=""
    local new_bssid=""
    local new_password=""
    local ssid_length=""
    local password_length=""
    local pending_name=""
    local provision_status=""
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
    start_manual_hold "$new_ssid" || return 1
    pending_name=.dashboard-new.$$
    PENDING_PROFILE="$PROFILE_DIR/$pending_name"
    write_provision_config "$provision_template" "$PENDING_PROFILE" \
        "$new_ssid" "$new_security" "$new_bssid" "$new_password" || return 1
    chmod 750 "$PENDING_PROFILE"

    connect_profile "$pending_name"
    provision_status=$?
    case $provision_status in
        0|2)
            save_current_profile "$new_ssid" || return 1
            [ "$provision_status" -ne 0 ] || rm -f "$MANUAL_HOLD_FILE"
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
