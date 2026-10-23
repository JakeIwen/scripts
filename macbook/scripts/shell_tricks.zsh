# Source from ~/.zshrc; `tricks` explains this deliberately small tool set.
# Definitions/paths only at load time: no subprocesses, output or network calls.
_SHELL_TRICKS_REPO="$HOME/dev/scripts"
_SHELL_TRICKS_DOC="$_SHELL_TRICKS_REPO/shared/sh/shell_tricks.md"
_SHELL_TRICKS_NOTIFY="$_SHELL_TRICKS_REPO/macbook/scripts/claude_ntfy_notify.py"
_SHELL_TRICKS_MOUNT='/Volumes/pihome'
_SHELL_TRICKS_SHARE="$_SHELL_TRICKS_MOUNT/Documents/agent-shared"
source "$_SHELL_TRICKS_REPO/shared/sh/shell_tricks_common.sh"

cshare() {
    [ "$#" -eq 0 ] || { printf 'usage: cshare\n' >&2; return 2; }
    # A leftover /Volumes directory is not a mounted share. Never create one or
    # silently switch to SSH/Tailscale; the latter may use a metered uplink.
    command mount | command grep -Fq " on $_SHELL_TRICKS_MOUNT (smbfs" || {
        printf 'cshare: pihome SMB share is not mounted; connect on the van LAN first\n' >&2
        return 1
    }
    builtin cd -- "$_SHELL_TRICKS_SHARE"
}

clipfile() {
    [ "$#" -eq 1 ] && [ -f "$1" ] && [ -r "$1" ] || {
        printf 'usage: clipfile READABLE_TEXT_FILE\n' >&2; return 2;
    }
    # Redirection preserves quotes, newlines and a leading dash in the filename.
    command pbcopy < "$1"
}

copypath() {
    [ "$#" -le 1 ] || { printf 'usage: copypath [EXISTING_PATH]\n' >&2; return 2; }
    local target="${1:-.}"
    [ -e "$target" ] || { printf 'copypath: no such path: %s\n' "$target" >&2; return 1; }
    # :A is Zsh's absolute/canonical path modifier (resolves symlinks). Unlike
    # command substitution it preserves trailing newlines in a filename.
    printf '%s' "${target:A}" | command pbcopy
}

reveal() {
    [ "$#" -le 1 ] || { printf 'usage: reveal [EXISTING_PATH]\n' >&2; return 2; }
    local target="${1:-.}"
    [ -e "$target" ] || { printf 'reveal: no such path: %s\n' "$target" >&2; return 1; }
    # -R selects the item in Finder rather than opening it in its default app.
    command open -R "${target:A}"
}
