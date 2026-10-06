#!/bin/sh
# Owns surveys, channel hints, candidate ranking, and automatic selection.

scan_networks() {
    local scan_pass=""
    local scan_pass_raw=""
    rm -f "$SCAN_COMPLETED_FILE"
    if ! normalize_runtime_scan_list full-scan preserve-gui; then
        log_message "unable to apply standard scan-frequency allowlist"
        return 1
    fi
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
    uptime_seconds > "$SCAN_COMPLETED_FILE"
    return 0
}

scan_field_for_profile() {
    local scan_profile=""
    local scan_profile_field=""
    local scan_profile_ssid=""
    local scan_profile_bssid=""
    local scan_profile_security=""
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
    local config_path=""
    local scan_status=""
    local scan_frequency=""
    local config_rewrite=""
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

recent_frequency_for_profile() {
    local frequency_scan_time=""
    local frequency_now=""
    local frequency_age=""
    local frequency_hint=""
    [ -s "$SCAN_FILE" ] || return 1
    frequency_scan_time=$(sed -n '1p' "$SCAN_COMPLETED_FILE" 2>/dev/null)
    frequency_now=$(uptime_seconds)
    case $frequency_scan_time:$frequency_now in
        *[!0-9:]*|:*|*:) return 1 ;;
    esac
    frequency_age=$((frequency_now - frequency_scan_time))
    [ "$frequency_age" -ge 0 ] && [ "$frequency_age" -le "$SCAN_MAX_AGE_SECONDS" ] || return 1
    frequency_hint=$(scan_field_for_profile "$1" 4)
    case $frequency_hint in
        2412|2417|2422|2427|2432|2437|2442|2447|2452|2457|2462)
            printf '%s\n' "$frequency_hint" ;;
        *) return 1 ;;
    esac
}

runtime_scan_list_needs_normalization() {
    local runtime_scan_status=""
    local runtime_scan_channels=""
    runtime_scan_status=$(config_value "$SYSTEM_CFG" wireless.1.scan_list.status)
    runtime_scan_channels=$(config_value "$SYSTEM_CFG" wireless.1.scan_list.channels)
    [ "$runtime_scan_status" != enabled ] || \
        [ "$runtime_scan_channels" != "$STANDARD_SCAN_FREQUENCIES" ]
}

normalize_runtime_scan_list() {
    local normalize_reason=""
    local normalize_mode=""
    normalize_reason=$1
    normalize_mode=${2:-manager}
    runtime_scan_list_needs_normalization || return 0
    cp "$SYSTEM_CFG" "$APPLY_CFG" || return 1
    set_scan_list "$APPLY_CFG" enabled "$STANDARD_SCAN_FREQUENCIES" || return 1
    log_message "applying standard scan-frequency allowlist reason=$normalize_reason"
    apply_config "$normalize_mode"
}

profile_priority() {
    local priority_profile=""
    local configured_priority=""
    local priority_value=""
    local priority_name=""
    priority_profile=$1
    configured_priority=
    if [ -f "$PRIORITY_FILE" ]; then
        while IFS='|' read -r priority_value priority_name; do
            case $priority_value in
                ''|'#'*) continue ;;
            esac
            if [ "$priority_name" = "$priority_profile" ]; then
                configured_priority=$priority_value
                break
            fi
        done < "$PRIORITY_FILE"
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

choose_candidate() {
    local candidate_current_ssid=""
    local best_score=""
    local best_profile=""
    local candidate_path=""
    local candidate_profile=""
    local candidate_ssid=""
    local candidate_quality=""
    local candidate_priority=""
    local candidate_score=""
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
    local scan_now=""
    local last_scan=""
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
    local gui_grace_expired=""
    local gui_transition_status=""
    local manual_hold_status=""
    local auto_ssid=""
    local auto_ccq=""
    local selected_profile=""
    local connect_status=""
    observe_external_config_change || true
    gui_grace_expired=no
    handle_gui_transition
    gui_transition_status=$?
    case $gui_transition_status in
        0) return 0 ;;
        2) gui_grace_expired=yes ;;
    esac

    [ ! -f "$PAUSE_FILE" ] || {
        log_message "automatic selection paused"
        return 0
    }
    manual_hold_active
    manual_hold_status=$?
    [ "$manual_hold_status" -ne 0 ] || return 0
    if [ "$gui_grace_expired" != yes ] && [ "$manual_hold_status" -ne 2 ]; then
        manual_transition_active && return 0
    fi
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
