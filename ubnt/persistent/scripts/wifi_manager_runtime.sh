#!/bin/sh
# Owns uptime, logging, locks, failure counters, and cooldown state.

uptime_seconds() {
    awk '{split($1, value, "."); print value[1]}' "$UPTIME_FILE" 2>/dev/null
}

log_message() {
    local log_uptime=""
    rotate_log_if_needed
    log_uptime=$(uptime_seconds)
    [ -n "$log_uptime" ] || log_uptime=unknown
    printf 'uptime=%s %s\n' "$log_uptime" "$*"
    printf 'uptime=%s %s\n' "$log_uptime" "$*" >> "$LOG_FILE" 2>/dev/null || true
}

rotate_log_if_needed() {
    local log_size=""
    local rotation_file=""
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
    local healthy_ssid=""
    local healthy_ccq=""
    local healthy_now=""
    local healthy_last=""
    local healthy_last_ssid=""
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

acquire_lock() {
    local lock_pid=""
    if mkdir "$LOCK_DIR" 2>/dev/null; then
        printf '%s\n' "$$" > "$LOCK_DIR/pid"
        install_lock_traps
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
        install_lock_traps
        log_message "removed stale selector lock"
        return 0
    fi

    log_message "unable to acquire selector lock"
    return 1
}

install_lock_traps() {
    trap 'release_lock' EXIT
    trap 'exit 130' INT
    trap 'exit 143' TERM
    case ${command_name:-} in
        manual-connect-stdin|provision-stdin|update-profile-stdin|forget-stdin|starlink-off)
            # Wireless reloads can close the initiating SSH channel. Finish
            # saving credentials and restoring keys even after that hangup.
            trap '' HUP PIPE
            exec >/dev/null 2>&1
            ;;
        *) trap 'exit 129' HUP ;;
    esac
}

release_lock() {
    if [ -n "${PENDING_PROFILE:-}" ]; then
        rm -f "$PENDING_PROFILE"
    fi
    rm -f "$APPLY_CFG" "$STATE_DIR/softrestart.$$.log"
    rm -f "$LOCK_DIR/pid"
    rmdir "$LOCK_DIR" 2>/dev/null || true
    trap - EXIT HUP INT TERM PIPE
}

clear_failures() {
    rm -f "$STATE_DIR/failure.ssid" "$STATE_DIR/failure.count"
}

record_failure() {
    local previous_ssid=""
    local previous_count=""
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

cooldown_active() {
    local cooldown_until=""
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
    printf '%s\n' $((cooldown_now + ${2:-$COOLDOWN_SECONDS})) > "$STATE_DIR/cooldown.$cooldown_profile"
}
