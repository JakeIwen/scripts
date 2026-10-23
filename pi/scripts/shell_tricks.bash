# Source from ~/.bashrc; Bash 3.2+ syntax also lets the Mac run portable tests.
# Deployed with shell_tricks_common.sh and shell_tricks.md in ~/scripts/.
# Definitions/paths only at load time: no subprocesses, output or network calls.
_SHELL_TRICKS_SCRIPTS="$HOME/scripts"
_SHELL_TRICKS_DOC="$_SHELL_TRICKS_SCRIPTS/shell_tricks.md"
_SHELL_TRICKS_NOTIFY="$_SHELL_TRICKS_SCRIPTS/claude_ntfy_notify.py"
_SHELL_TRICKS_SHARE="$HOME/Documents/agent-shared"
source "$_SHELL_TRICKS_SCRIPTS/shell_tricks_common.sh"

cshare() {
    [ "$#" -eq 0 ] || { printf 'usage: cshare\n' >&2; return 2; }
    # On the Pi this is local storage, not the Mac's SMB mount. Do not create it
    # if missing: a failed cd must not leave a plausible but incorrect inbox.
    builtin cd -- "$_SHELL_TRICKS_SHARE"
}

junit() {
    [ "$#" -ge 1 ] && [ "$#" -le 2 ] && [ -n "$1" ] || {
        printf 'usage: junit UNIT [LINES|follow]\n' >&2; return 2;
    }
    local lines="${2:-100}"
    if [ "$lines" = follow ]; then
        command journalctl --boot --unit="$1" --lines=100 --follow --no-pager
    else
        case "$lines" in ''|*[!0-9]*)
            printf 'junit: LINES must be an integer from 1 to 10000, or follow\n' >&2; return 2 ;;
        esac
        [ "${#lines}" -le 5 ] && [ "$lines" -ge 1 ] && [ "$lines" -le 10000 ] || {
            printf 'junit: LINES must be from 1 to 10000\n' >&2; return 2;
        }
        # Current boot only; no sudo, pager, restart, or unbounded log dump.
        command journalctl --boot --unit="$1" --lines="$lines" --no-pager
    fi
}

failedunits() {
    [ "$#" -le 1 ] && { [ "$#" -eq 0 ] || [ "$1" = --user ]; } || {
        printf 'usage: failedunits [--user]\n' >&2; return 2;
    }
    # --user selects the per-user manager; a listing never resets failed state.
    command systemctl "$@" --failed --no-pager --full
}

pitherm() {
    [ "$#" -eq 0 ] || { printf 'usage: pitherm\n' >&2; return 2; }
    command vcgencmd measure_temp || return
    # get_throttled is a bitmask: low bits mean NOW, bits 16+ mean since boot.
    # Keep the raw mask so sticky historical warnings cannot look like live ones.
    command vcgencmd get_throttled
}
