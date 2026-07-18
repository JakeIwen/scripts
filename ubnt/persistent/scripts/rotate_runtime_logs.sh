#!/bin/sh

max_bytes=${UBNT_MAX_LOG_BYTES:-262144}
keep_lines=${UBNT_LOG_KEEP_LINES:-1000}
state_dir=/tmp/ubnt-wifi

mkdir -p "$state_dir"

rotate_log() {
    runtime_log=$1
    [ -f "$runtime_log" ] || return 0
    runtime_size=$(wc -c < "$runtime_log" 2>/dev/null | tr -d '[:space:]')
    case $runtime_size in
        ''|*[!0-9]*) return 0 ;;
    esac
    [ "$runtime_size" -lt "$max_bytes" ] || {
        runtime_tmp="$state_dir/rotate.${runtime_log##*/}.$$"
        tail -n "$keep_lines" "$runtime_log" > "$runtime_tmp" 2>/dev/null || return 0
        mv "$runtime_tmp" "$runtime_log"
    }
}

rotate_log /var/log/ubnt-wifi.log
rotate_log /var/log/ubnt-manager-cron-errors.log
