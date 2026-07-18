alias l='ls -lah'
alias ..='cd ..'
alias ...='cd ../..'
alias ....='cd ../../..'
alias .....='cd ../../../..'

manager=/etc/persistent/scripts/wifi_manager.sh

ccq() {
    mca-status | awk -F= '$1 == "ccq" {print $2; exit}'
}

is_connected() {
    current_ccq=$(ccq)
    case $current_ccq in
        ''|*[!0-9]*) return 1 ;;
    esac
    [ "$current_ccq" -gt 0 ]
}

save_current() {
    profile_name=${1:-$(iwgetid ath0 -r 2>/dev/null || iwgetid -r 2>/dev/null)}
    "$manager" save-current "$profile_name"
}

set_ap() {
    [ "$#" -eq 1 ] || {
        echo 'Usage: set_ap PROFILE' >&2
        return 1
    }
    "$manager" connect "$1"
}

nh_set_ap() {
    [ "$#" -eq 1 ] || {
        echo 'Usage: nh_set_ap PROFILE' >&2
        return 1
    }
    "$manager" connect "$1" > /tmp/set_ap_result.txt 2>&1 &
}

pause_wifi_auto() {
    "$manager" pause
}

resume_wifi_auto() {
    "$manager" resume
}

disable_ap() {
    [ "$#" -eq 1 ] || {
        echo 'Usage: disable_ap PROFILE' >&2
        return 1
    }
    "$manager" disable "$1"
}

delete_ap() {
    echo 'Profiles are retained. Use disable_ap PROFILE for a recoverable removal.' >&2
    return 1
}

del_current() {
    echo 'Refusing to infer and delete a profile. Use disable_ap PROFILE.' >&2
    return 1
}

reset() {
    "$manager" connect reset
}
