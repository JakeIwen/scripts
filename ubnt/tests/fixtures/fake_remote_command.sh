#!/bin/sh
set -eu

name=${0##*/}
log() {
    printf '%s\t%s\n' "$name" "$*" >> "$FAKE_EVENT_LOG"
}
mark_once() {
    marker="$FAKE_DEVICE_ROOT/state/$1"
    if [ ! -e "$marker" ]; then
        : > "$marker"
        return 0
    fi
    return 1
}

case $name in
    sh)
        if [ "${FAKE_HOOK_FAIL:-}" = 1 ] && [ "${1:-}" = "$FAKE_DEVICE_ROOT/etc/persistent/rc.postsysinit" ]; then
            log 'injected boot-hook failure'
            exit 81
        fi
        exec "$FAKE_PYTHON" "$FAKE_TRANSPORT_HELPER" remote-sh "$@"
        ;;
    pkill)
        log "$*"
        [ "${1:-}" = crond ] && rm -f "$FAKE_DEVICE_ROOT/state/crond.running"
        exit 0
        ;;
    pgrep)
        log "$*"
        if [ "${1:-}" = crond ] || [ "${2:-}" = crond ]; then
            [ -e "$FAKE_DEVICE_ROOT/state/crond.running" ]
            exit
        fi
        exit 1
        ;;
    crond)
        log "$*"
        if ! "$FAKE_PYTHON" -c 'import signal, sys; sys.exit(signal.getsignal(signal.SIGTERM) == signal.SIG_IGN)'; then
            log 'inherited ignored SIGTERM'
            exit 80
        fi
        if [ "${FAKE_CROND_FAIL_ONCE:-}" = 1 ] && mark_once crond-failed; then
            exit 75
        fi
        : > "$FAKE_DEVICE_ROOT/state/crond.running"
        exit 0
        ;;
    cfgmtd)
        log "$*"
        operation=
        destination=
        config_file=
        slot=
        while [ "$#" -gt 0 ]; do
            case $1 in
                -w|-r) operation=$1; shift ;;
                -p) destination=$2; shift 2 ;;
                -f) config_file=$2; shift 2 ;;
                -t) slot=$2; shift 2 ;;
                *) shift ;;
            esac
        done
        case $operation in
            -w)
                case ${FAKE_CFGMTD_WRITE_FAIL:-} in
                    exit)
                        if mark_once cfgmtd-write-failed; then exit 74; fi
                        ;;
                    segv)
                        if mark_once cfgmtd-write-failed; then kill -SEGV $$; fi
                        ;;
                    HUP|TERM|PIPE)
                        kill -"$FAKE_CFGMTD_WRITE_FAIL" "$PPID"
                        exit 74
                        ;;
                esac
                rm -rf "$FAKE_DEVICE_ROOT/flash/persistent.new"
                cp -pR "$FAKE_DEVICE_ROOT/etc/persistent" "$FAKE_DEVICE_ROOT/flash/persistent.new"
                rm -rf "$FAKE_DEVICE_ROOT/flash/persistent"
                mv "$FAKE_DEVICE_ROOT/flash/persistent.new" "$FAKE_DEVICE_ROOT/flash/persistent"
                cp -p "$FAKE_DEVICE_ROOT/tmp/system.cfg" "$FAKE_DEVICE_ROOT/flash/system.cfg"
                ;;
            -r)
                if [ "${FAKE_CFGMTD_READ_FAIL:-}" = 1 ] && mark_once cfgmtd-read-failed; then
                    exit 76
                fi
                [ -n "$destination" ] && [ -n "$config_file" ] && [ "$slot" = 1 ]
                mkdir -p "$destination"
                cp -pR "$FAKE_DEVICE_ROOT/flash/persistent" "$destination/persistent"
                cp -p "$FAKE_DEVICE_ROOT/flash/system.cfg" "$config_file"
                readback_root="$destination/persistent"
                case ${FAKE_CFGMTD_LAYOUT:-} in
                    direct)
                        cp -pR "$readback_root/." "$destination"
                        rm -rf "$readback_root"
                        readback_root=$destination
                        ;;
                    ambiguous) cp -pR "$readback_root/." "$destination" ;;
                esac
                if [ -n "${FAKE_CFGMTD_READBACK_CORRUPT:-}" ] && mark_once cfgmtd-readback-corrupted; then
                    printf '\ncorrupted-by-test\n' >> "$readback_root/$FAKE_CFGMTD_READBACK_CORRUPT"
                fi
                if [ -n "${FAKE_CFGMTD_READBACK_MISSING:-}" ] && mark_once cfgmtd-readback-removed; then
                    rm -f "$readback_root/$FAKE_CFGMTD_READBACK_MISSING"
                fi
                if [ -n "${FAKE_CFGMTD_READBACK_SYMLINK:-}" ] && mark_once cfgmtd-readback-symlinked; then
                    link_path=$FAKE_CFGMTD_READBACK_SYMLINK
                    [ "$link_path" != 1 ] || link_path=scripts
                    rm -rf "$readback_root/$link_path"
                    ln -s "$FAKE_DEVICE_ROOT/etc/persistent/$link_path" "$readback_root/$link_path"
                fi
                ;;
            *) exit 64 ;;
        esac
        ;;
    md5sum)
        log "$*"
        exec /sbin/md5sum "$@"
        ;;
    cp)
        log "$*"
        if [ -n "${FAKE_CP_FAIL_MATCH:-}" ]; then
            case " $* " in
                *"$FAKE_CP_FAIL_MATCH"*)
                    if mark_once cp-failed; then exit 77; fi
                    ;;
            esac
        fi
        exec /bin/cp "$@"
        ;;
    mv)
        log "$*"
        if [ -n "${FAKE_MV_FAIL_MATCH:-}" ]; then
            case " $* " in
                *"$FAKE_MV_FAIL_MATCH"*)
                    if mark_once mv-failed; then exit 78; fi
                    ;;
            esac
        fi
        exec /bin/mv "$@"
        ;;
    rm)
        log "$*"
        exec /bin/rm "$@"
        ;;
    tar)
        log "$*"
        fail_tar=no
        case ${FAKE_TAR_FAIL:-} in
            1) fail_tar=yes ;;
            compress) case " $* " in *" -czf "*) fail_tar=yes ;; esac ;;
        esac
        if [ "$fail_tar" = yes ] && mark_once tar-failed; then
            exit 79
        fi
        case " $* " in
            *prospective.tar.gz*)
                /usr/bin/tar "$@"
                /bin/cp "$2" "$FAKE_DEVICE_ROOT/state/prospective.tar.gz"
                exit 0
                ;;
        esac
        exec /usr/bin/tar "$@"
        ;;
    gzip)
        log "$*"
        if { [ "${FAKE_TAR_FAIL:-}" = 1 ] || [ "${FAKE_TAR_FAIL:-}" = compress ]; } && mark_once gzip-failed; then
            exit 79
        fi
        exec /usr/bin/gzip "$@"
        ;;
    *)
        printf 'Unknown fake remote command: %s\n' "$name" >&2
        exit 127
        ;;
esac
