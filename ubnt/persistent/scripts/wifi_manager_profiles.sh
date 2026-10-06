#!/bin/sh
# Owns profile inspection, admin-login preservation, editing, and persistence.

config_value() {
    local config_file=""
    local config_key=""
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
    local profile_file=""
    local wpa_status=""
    local wpa_device_status=""
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

profile_security() {
    local security_profile=""
    local security_wpa=""
    local security_wpa_device=""
    local security_type=""
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

preserve_current_login() {
    local login_destination=""
    local login_rewrite=""
    login_destination=$1
    [ "$login_destination" != "$SYSTEM_CFG" ] || return 1
    login_rewrite="$login_destination.login.$$"
    # Pass paths only: password hashes must never enter command arguments or
    # logs. The live device owns its admin login; network templates do not.
    if ! awk -F= '
        FILENAME == ARGV[1] {
            if ($1 == "users.1.name") { name = substr($0, index($0, "=") + 1); names++ }
            if ($1 == "users.1.password") { password = substr($0, index($0, "=") + 1); passwords++ }
            next
        }
        {
            if (names != 1 || passwords != 1 || name == "" || password == "") exit 1
            if ($1 == "users.1.name") {
                if (!wrote_name++) print "users.1.name=" name
            } else if ($1 == "users.1.password") {
                if (!wrote_password++) print "users.1.password=" password
            } else print
        }
        END {
            if (names != 1 || passwords != 1 || name == "" || password == "") exit 1
            if (!wrote_name) print "users.1.name=" name
            if (!wrote_password) print "users.1.password=" password
        }
    ' "$SYSTEM_CFG" "$login_destination" > "$login_rewrite"; then
        rm -f "$login_rewrite"
        log_message "cannot preserve live admin login; configuration left unchanged"
        return 1
    fi
    chmod 600 "$login_rewrite" || return 1
    mv "$login_rewrite" "$login_destination"
}

profile_template_for_security() {
    local wanted_security=""
    local template_path=""
    local template_name=""
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

update_profile_settings() {
    validate_profile_settings "$@" || return 1
    # PENDING_PROFILE stays global so exit-trap cleanup owns pending output.
    PENDING_PROFILE="$PROFILE_DIR/.dashboard-profile.$$"
    rewrite_profile_settings "$PROFILE_DIR/$1" "$2" "$3" "$4" "$5" "$6" "$7" "$8"
    commit_profile_settings "$1" "$4" "$5" "$6" "$7" "$8" "$9"
}

validate_profile_settings() {
    local update_name=""
    local update_password_action=""
    local update_password=""
    local update_bssid=""
    local update_txpower=""
    local update_rate_module=""
    local update_rate_auto=""
    local update_rate_mcs=""
    local update_apply=""
    local update_source=""
    local update_password_length=""
    update_name=$1
    update_password_action=$2
    update_password=$3
    update_bssid=$4
    update_txpower=$5
    update_rate_module=$6
    update_rate_auto=$7
    update_rate_mcs=$8
    update_apply=$9

    profile_name_is_valid "$update_name" || {
        log_message "invalid profile-update name"
        return 1
    }
    update_source="$PROFILE_DIR/$update_name"
    [ -f "$update_source" ] || {
        log_message "unknown profile-update profile=$update_name"
        return 1
    }
    case $update_password_action in
        keep) ;;
        change)
            [ "$(profile_security "$update_source")" = wpa ] || {
                log_message "password update requires WPA profile=$update_name"
                return 1
            }
            update_password_length=$(printf '%s' "$update_password" | wc -c | tr -d '[:space:]')
            case $update_password_length in
                ''|*[!0-9]*) return 1 ;;
            esac
            [ "$update_password_length" -ge 8 ] && [ "$update_password_length" -le 63 ] || {
                log_message "WPA password must be 8 to 63 bytes"
                return 1
            }
            if printf '%s' "$update_password" | LC_ALL=C grep -q '[[:cntrl:]]'; then
                log_message "WPA password contains a control character"
                return 1
            fi
            ;;
        *) log_message "invalid password-update action"; return 1 ;;
    esac
    [ "$update_password_action" = change ] || [ -z "$update_password" ] || return 1
    [ -z "$update_bssid" ] || printf '%s\n' "$update_bssid" | \
        grep -Eq '^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$' || {
            log_message "invalid profile lock-to-AP address"
            return 1
        }
    case $update_txpower in
        ''|*[!0-9]*) log_message "invalid profile output power"; return 1 ;;
    esac
    [ "$update_txpower" -ge 0 ] && [ "$update_txpower" -le 23 ] || {
        log_message "profile output power must be 0 to 23 dBm"
        return 1
    }
    case $update_rate_module in
        atheros|ewma_ht) ;;
        *) log_message "invalid profile data-rate module"; return 1 ;;
    esac
    case $update_rate_auto in
        enabled|disabled) ;;
        *) log_message "invalid profile rate-auto setting"; return 1 ;;
    esac
    case $update_rate_mcs in
        ''|*[!0-9]*) log_message "invalid profile MCS rate"; return 1 ;;
    esac
    [ "$update_rate_mcs" -ge 0 ] && [ "$update_rate_mcs" -le 15 ] || {
        log_message "profile MCS rate must be 0 to 15"
        return 1
    }
    case $update_apply in
        yes|no) ;;
        *) log_message "invalid profile apply setting"; return 1 ;;
    esac
    return 0
}

rewrite_profile_settings() {
    local update_source=""
    local update_password_action=""
    local update_password=""
    local update_bssid=""
    local update_txpower=""
    local update_rate_module=""
    local update_rate_auto=""
    local update_rate_mcs=""
    local saw_aaa_password=""
    local saw_supplicant_password=""
    local saw_wireless_bssid=""
    local saw_supplicant_bssid=""
    local saw_txpower=""
    local saw_rate_module=""
    local saw_rate_auto=""
    local saw_rate_mcs=""
    local update_line=""
    local update_key=""
    update_source=$1
    update_password_action=$2
    update_password=$3
    update_bssid=$4
    update_txpower=$5
    update_rate_module=$6
    update_rate_auto=$7
    update_rate_mcs=$8

    saw_aaa_password=no
    saw_supplicant_password=no
    saw_wireless_bssid=no
    saw_supplicant_bssid=no
    saw_txpower=no
    saw_rate_module=no
    saw_rate_auto=no
    saw_rate_mcs=no
    : > "$PENDING_PROFILE"
    while IFS= read -r update_line || [ -n "$update_line" ]; do
        update_key=${update_line%%=*}
        case $update_key in
            aaa.1.wpa.psk)
                if [ "$update_password_action" = change ]; then
                    printf 'aaa.1.wpa.psk=%s\n' "$update_password"
                else
                    printf '%s\n' "$update_line"
                fi
                saw_aaa_password=yes
                ;;
            wpasupplicant.profile.1.network.1.psk)
                if [ "$update_password_action" = change ]; then
                    printf 'wpasupplicant.profile.1.network.1.psk=%s\n' "$update_password"
                else
                    printf '%s\n' "$update_line"
                fi
                saw_supplicant_password=yes
                ;;
            wireless.1.ap)
                printf 'wireless.1.ap=%s\n' "$update_bssid"
                saw_wireless_bssid=yes
                ;;
            wpasupplicant.profile.1.network.1.bssid)
                printf 'wpasupplicant.profile.1.network.1.bssid=%s\n' "$update_bssid"
                saw_supplicant_bssid=yes
                ;;
            radio.1.txpower)
                printf 'radio.1.txpower=%s\n' "$update_txpower"
                saw_txpower=yes
                ;;
            radio.rate_module)
                printf 'radio.rate_module=%s\n' "$update_rate_module"
                saw_rate_module=yes
                ;;
            radio.1.rate.auto)
                printf 'radio.1.rate.auto=%s\n' "$update_rate_auto"
                saw_rate_auto=yes
                ;;
            radio.1.rate.mcs)
                printf 'radio.1.rate.mcs=%s\n' "$update_rate_mcs"
                saw_rate_mcs=yes
                ;;
            *) printf '%s\n' "$update_line" ;;
        esac
    done < "$update_source" >> "$PENDING_PROFILE"
    if [ "$update_password_action" = change ]; then
        [ "$saw_aaa_password" = yes ] || printf 'aaa.1.wpa.psk=%s\n' "$update_password" >> "$PENDING_PROFILE"
        [ "$saw_supplicant_password" = yes ] || \
            printf 'wpasupplicant.profile.1.network.1.psk=%s\n' "$update_password" >> "$PENDING_PROFILE"
    fi
    [ "$saw_wireless_bssid" = yes ] || printf 'wireless.1.ap=%s\n' "$update_bssid" >> "$PENDING_PROFILE"
    [ "$saw_supplicant_bssid" = yes ] || \
        printf 'wpasupplicant.profile.1.network.1.bssid=%s\n' "$update_bssid" >> "$PENDING_PROFILE"
    [ "$saw_txpower" = yes ] || printf 'radio.1.txpower=%s\n' "$update_txpower" >> "$PENDING_PROFILE"
    [ "$saw_rate_module" = yes ] || printf 'radio.rate_module=%s\n' "$update_rate_module" >> "$PENDING_PROFILE"
    [ "$saw_rate_auto" = yes ] || printf 'radio.1.rate.auto=%s\n' "$update_rate_auto" >> "$PENDING_PROFILE"
    [ "$saw_rate_mcs" = yes ] || printf 'radio.1.rate.mcs=%s\n' "$update_rate_mcs" >> "$PENDING_PROFILE"
    chmod 750 "$PENDING_PROFILE"
}

commit_profile_settings() {
    local update_name=""
    local update_bssid=""
    local update_txpower=""
    local update_rate_module=""
    local update_rate_auto=""
    local update_rate_mcs=""
    local update_apply=""
    local update_source=""
    local update_uptime=""
    update_name=$1
    update_bssid=$2
    update_txpower=$3
    update_rate_module=$4
    update_rate_auto=$5
    update_rate_mcs=$6
    update_apply=$7
    update_source="$PROFILE_DIR/$update_name"

    mkdir -p "$PROFILE_DIR/.disabled"
    update_uptime=$(uptime_seconds)
    [ -n "$update_uptime" ] || update_uptime=0
    cp "$update_source" \
        "$PROFILE_DIR/.disabled/$update_name.settings-backup.$update_uptime.$$" || return 1
    mv "$PENDING_PROFILE" "$update_source" || return 1
    PENDING_PROFILE=
    persist_profiles || return 1
    log_message "updated saved profile=$update_name lock_to_ap=${update_bssid:-any} txpower=$update_txpower rate_module=$update_rate_module rate_auto=$update_rate_auto rate_mcs=$update_rate_mcs"
    if [ "$update_apply" = yes ]; then
        run_requested_connect "$update_name" yes
        return $?
    fi
}

persist_profiles() {
    local persist_status=""
    log_message "saving profiles to flash"
    "$CFGMTD" -w -p /etc/
    persist_status=$?
    # airOS may regenerate authorized_keys during configuration persistence.
    "$SSH_KEY_INSTALLER" || return 1
    [ "$persist_status" -eq 0 ] || {
        log_message "profile persistence failed status=$persist_status"
        return "$persist_status"
    }
    log_message "profiles saved to flash"
}

save_current_profile() {
    local save_name=""
    local save_source=""
    local save_destination=""
    local save_uptime=""
    save_name=$1
    save_source=${2:-explicit}
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
    # Normalize the saved copy only. The live config must continue to describe
    # the actual radio so the next survey detects and clears a fast-connect pin.
    set_scan_list "$PROFILE_DIR/.new.$$.cfg" enabled "$STANDARD_SCAN_FREQUENCIES" || return 1
    chmod 750 "$PROFILE_DIR/.new.$$.cfg"
    mv "$PROFILE_DIR/.new.$$.cfg" "$save_destination" || return 1
    persist_profiles || return 1
    record_observed_config || return 1
    if [ "$save_source" = explicit ]; then
        clear_gui_transition
        rm -f "$TRANSITION_FILE"
    fi
    log_message "saved profile source=$save_source profile=$save_name"
}

disable_profile() {
    local disable_name=""
    local disable_source=""
    local disable_uptime=""
    disable_name=$1
    profile_name_is_valid "$disable_name" || return 1
    disable_source="$PROFILE_DIR/$disable_name"
    [ -f "$disable_source" ] || return 1
    mkdir -p "$PROFILE_DIR/.disabled"
    disable_uptime=$(uptime_seconds)
    [ -n "$disable_uptime" ] || disable_uptime=0
    mv "$disable_source" "$PROFILE_DIR/.disabled/$disable_name.$disable_uptime.$$" || return 1
    persist_profiles || return 1
    log_message "disabled profile recoverably profile=$disable_name"
}

forget_profile() {
    local forget_name=""
    local forget_path=""
    local forget_ssid=""
    local forget_active=""
    local forget_backup=""
    local roam_profile=""
    local roam_status=""
    forget_name=$1
    case $forget_name in
        ''|.*|*/*|reset|system.cfg|*.backup.*)
            log_message "cannot forget an internal profile"; return 1 ;;
    esac
    forget_path="$PROFILE_DIR/$forget_name"
    [ -f "$forget_path" ] && [ ! -L "$forget_path" ] || return 1
    forget_ssid=$(effective_ssid "$forget_path")
    [ -n "$forget_ssid" ] || return 1
    forget_active=no
    if [ "$(effective_ssid "$SYSTEM_CFG")" = "$forget_ssid" ] || \
        [ "$(associated_ssid)" = "$forget_ssid" ]; then
        forget_active=yes
    fi
    mkdir -p "$PROFILE_DIR/.disabled" || return 1
    forget_backup="$PROFILE_DIR/.disabled/$forget_name.forgotten.$(uptime_seconds).$$"
    mv "$forget_path" "$forget_backup" || return 1
    if [ "$forget_active" = yes ]; then
        # Preserve current Ethernet/management settings. The historical reset
        # profile is a complete unrelated AP configuration, not a disconnect.
        write_provision_config "$SYSTEM_CFG" "$APPLY_CFG" \
            "vanpi-disconnected-$$" none '02:00:00:00:00:00' '' || return 1
        apply_config || return 1
        rm -f "$PAUSE_FILE" "$MANUAL_HOLD_FILE" "$TRANSITION_FILE" "$STATE_DIR/last_auto_scan"
        clear_gui_transition
        clear_failures
    fi
    persist_profiles || return 1
    log_message "forgot profile=$forget_name active=$forget_active backup=$forget_backup"
    if [ "$forget_active" = yes ]; then
        scan_networks || return 0
        roam_profile=$(choose_candidate "$forget_ssid") || return 0
        connect_profile "$roam_profile" yes
        roam_status=$?
        [ "$roam_status" -eq 0 ] || set_cooldown "$roam_profile"
        # Forgetting succeeded even if no other uplink can be joined yet.
    fi
    return 0
}
