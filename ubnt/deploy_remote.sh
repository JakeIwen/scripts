#!/bin/sh
# Sent over SSH by scp_to_device.sh; all work before install lives in /tmp.
set -eu
umask 077
export LC_ALL=C

phase=$1
stage=$2
rollback_name=$3
keep=$4
budget=$5
activate=$6
backup_pruned=$7
persistent=/etc/persistent
candidate="$stage/candidate/persistent"

fail() { printf 'Deploy refused: %s\n' "$*" >&2; exit 1; }
digest() {
    md5_result=$(md5sum "$1") || return 1
    printf '%s\n' "${md5_result%% *}"
}

# Never let a destination symlink redirect writes/deletion outside this tree.
check_layout() {
    for directory in "$1" "$1/config" "$1/scripts"; do
        [ -d "$directory" ] && [ ! -L "$directory" ] || fail "unsafe code directory: $directory"
    done
    [ ! -L "$1/rollback" ] || fail 'rollback must not be a symlink'
    if [ -e "$1/rollback" ]; then
        [ -d "$1/rollback" ] || fail 'rollback must be a directory'
    fi
}

copy_code_rollback() {
    destination="$candidate/rollback/$rollback_name"
    [ ! -e "$destination" ] && [ ! -L "$destination" ] || fail 'rollback name already exists'
    mkdir -p "$destination/config" "$destination/scripts"
    cp -p "$candidate/rc.postsysinit" "$destination/"
    if [ -f "$candidate/profile" ]; then
        cp -p "$candidate/profile" "$destination/profile"
    else
        : > "$destination/profile.absent"
    fi
    cp -p "$candidate/config/cron" "$candidate/config/.profile" "$destination/config/"
    cp -p "$candidate/scripts/"*.sh "$candidate/scripts/"*.awk "$destination/scripts/"
}

plan_pruning() {
    : > "$stage/old-code.list"
    count=1
    for directory in "$candidate"/rollback/code-*; do
        [ ! -L "$directory" ] || fail 'code rollback must not be a symlink'
        [ -d "$directory" ] || continue
        name=${directory##*/}
        [ "$name" != "$rollback_name" ] || continue
        case $name in
            code-[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]T[0-9][0-9][0-9][0-9][0-9][0-9]Z) ;;
            *) fail 'unrecognized code rollback name; review it before deploying' ;;
        esac
        printf '%s\n' "$name" >> "$stage/old-code.list"
        count=$((count + 1))
    done
    # The C-locale glob above is chronological for code-YYYYMMDDTHHMMSSZ names.
    : > "$stage/prune.list"
    while IFS= read -r name; do
        [ "$count" -gt "$keep" ] || break
        printf '%s\n' "$name" >> "$stage/prune.list"
        rm -rf "$candidate/rollback/$name"
        count=$((count - 1))
    done < "$stage/old-code.list"
}

code_files() {
    for source in "$stage/persistent/rc.postsysinit" "$stage/persistent/profile" \
        "$stage"/persistent/config/* "$stage/persistent/config/.profile" \
        "$stage"/persistent/scripts/*; do
        printf '%s\n' "${source#"$stage/persistent/"}"
    done
}

install_code() {
    root=$1
    code_files > "$stage/code-files.list"
    while IFS= read -r relative; do
        [ ! -d "$root/$relative" ] && [ ! -L "$root/$relative" ] || fail "unsafe code destination: $relative"
        # Remove only our temporary file; never follow a stale .new symlink.
        rm -f "$root/$relative.new"
        cp -p "$stage/persistent/$relative" "$root/$relative.new"
        case $relative in rc.postsysinit|scripts/*) chmod 750 "$root/$relative.new" ;; esac
        mv "$root/$relative.new" "$root/$relative"
    done < "$stage/code-files.list"
}

prepare() {
    check_layout "$persistent"
    tar -cf "$stage/live.tar" -C /etc persistent
    digest "$stage/live.tar" > "$stage/live.md5"
    mkdir "$stage/candidate"
    tar -xf "$stage/live.tar" -C "$stage/candidate"
    check_layout "$candidate"
    copy_code_rollback
    plan_pruning
    install_code "$candidate"
    # Account for the system configuration too, not only persistent files.
    cp -p /tmp/system.cfg "$stage/candidate/system.cfg"
    tar -czf "$stage/prospective.tar.gz" -C "$stage/candidate" persistent system.cfg
    bytes=$(wc -c < "$stage/prospective.tar.gz")
    [ "$bytes" -le "$budget" ] || fail "prospective flash archive is $bytes bytes; budget is $budget bytes (112 KiB maximum). Live files and cron unchanged."
    printf 'Flash preflight: %s bytes (budget %s).\n' "$bytes" "$budget"
    # Export only on request; a large old history need not be duplicated again.
    if [ "$backup_pruned" = yes ]; then
        mkdir "$stage/pruned"
        while IFS= read -r name; do
            tar -xf "$stage/live.tar" -C "$stage/pruned" "persistent/rollback/$name"
        done < "$stage/prune.list"
        tar -czf "$stage/pruned-rollbacks.tar.gz" -C "$stage/pruned" .
        digest "$stage/pruned-rollbacks.tar.gz" > "$stage/pruned-rollbacks.md5"
    fi
}

cron_stopped=no
lock_owned=no
pause_owned=no
finish() {
    status=$?
    trap - 0
    # Catch repeated signals without exporting ignored TERM to the new daemon.
    trap ':' HUP INT TERM PIPE
    set +e
    if [ "$cron_stopped" = yes ]; then
        printf 'Deploy failed after stopping cron; restoring cron before exiting.\n' >&2
        [ "$pause_owned" = no ] || rm -f /tmp/ubnt-wifi/paused
        if /bin/sh "$persistent/rc.postsysinit" && pgrep crond >/dev/null 2>&1; then
            printf 'Cron restored via rc.postsysinit.\n' >&2
        elif crond && pgrep crond >/dev/null 2>&1; then
            printf 'Cron restored via crond fallback.\n' >&2
        else
            printf 'ERROR: cron recovery failed; manual recovery is required.\n' >&2
        fi
        [ "$status" -ne 0 ] || status=1
    fi
    if [ "$lock_owned" = yes ]; then
        rm -f /tmp/ubnt-wifi/lock/pid
        rmdir /tmp/ubnt-wifi/lock
    fi
    rm -rf "$stage"
    exit "$status"
}

install() {
    trap finish 0
    trap 'exit 129' HUP
    trap 'exit 130' INT
    trap 'exit 143' TERM
    trap 'exit 141' PIPE
    # Share the manager's lock: stopping cron alone cannot stop an active job.
    mkdir -p /tmp/ubnt-wifi
    mkdir /tmp/ubnt-wifi/lock || fail 'Wi-Fi manager or another deploy is busy; retry when idle'
    lock_owned=yes
    printf '%s\n' "$$" > /tmp/ubnt-wifi/lock/pid
    check_layout "$persistent"
    tar -cf "$stage/live-check.tar" -C /etc persistent
    expected=$(cat "$stage/live.md5")
    [ "$(digest "$stage/live-check.tar")" = "$expected" ] || fail 'persistent tree changed since preflight; retry deployment'
    # Also guard configuration growth during a potentially slow Mac backup.
    current_config=$(digest /tmp/system.cfg)
    expected_config=$(digest "$stage/candidate/system.cfg")
    [ "$current_config" = "$expected_config" ] || fail 'system.cfg changed since preflight; retry deployment'
    expected_wifi=$(digest "$stage/persistent/scripts/wifi_manager.sh")

    # Arm recovery BEFORE pkill, including interruption while pkill is running.
    cron_stopped=yes
    pkill crond 2>/dev/null || true
    mkdir -p "$persistent/rollback"
    cp -pR "$candidate/rollback/$rollback_name" "$persistent/rollback/$rollback_name"
    while IFS= read -r name; do
        [ -d "$persistent/rollback/$name" ] && [ ! -L "$persistent/rollback/$name" ] || fail 'rollback changed before pruning'
        rm -rf "$persistent/rollback/$name"
    done < "$stage/prune.list"
    install_code "$persistent"
    /sbin/cfgmtd -w -p /etc/

    mkdir "$stage/readback"
    /sbin/cfgmtd -r -t 1 -p "$stage/readback/" -f "$stage/readback/system.cfg"
    for directory in "$stage/readback/persistent" "$stage/readback/persistent/scripts" "$stage/readback/scripts"; do
        [ ! -L "$directory" ] || fail 'active flash readback contains a symlinked code directory'
    done
    readback="$stage/readback/persistent/scripts/wifi_manager.sh"
    # Allow an archive rooted at persistent or at its parent directory.
    # Accept only one exact deployed path, never a recursive rollback match.
    if [ -f "$stage/readback/scripts/wifi_manager.sh" ]; then
        [ ! -e "$readback" ] || fail 'ambiguous active flash readback layout'
        readback="$stage/readback/scripts/wifi_manager.sh"
    fi
    [ -f "$readback" ] && [ ! -L "$readback" ] || fail 'active flash readback is missing wifi_manager.sh'
    [ "$(digest "$readback")" = "$expected_wifi" ] || fail 'active flash wifi_manager.sh md5 mismatch'
    printf 'Active flash wifi_manager.sh md5 verified.\n'

    if [ "$activate" = yes ]; then
        /bin/sh "$persistent/rc.postsysinit"
        pgrep crond >/dev/null 2>&1 || fail 'activation did not start cron'
    else
        if [ ! -e /tmp/ubnt-wifi/paused ]; then
            pause_owned=yes
            : > /tmp/ubnt-wifi/paused
        fi
    fi
    # Paused success deliberately leaves cron stopped, unlike ANY failure.
    cron_stopped=no
}

case $phase in
    prepare) prepare ;;
    install) install ;;
    *) fail 'unknown deploy phase' ;;
esac
