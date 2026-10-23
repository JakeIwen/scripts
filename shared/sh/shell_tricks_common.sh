# Shared definitions for shell_tricks.zsh and shell_tricks.bash.
# Portable to Bash 3.2 and Zsh; host files supply the documentation/sender paths.
# `command` bypasses existing aliases/functions; nothing here runs at load time.

tricks() {
    [ "$#" -le 1 ] || { printf 'usage: tricks [NAME|-l]\n' >&2; return 2; }
    [ -r "$_SHELL_TRICKS_DOC" ] || {
        printf 'tricks: missing help file: %s\n' "$_SHELL_TRICKS_DOC" >&2; return 1;
    }
    if [ "$#" -eq 0 ]; then
        # Do not launch a pager in a pipe, agent session or redirected output.
        if [ -t 1 ] && command -v less >/dev/null 2>&1; then
            command less -FRX -- "$_SHELL_TRICKS_DOC"
        else
            command cat -- "$_SHELL_TRICKS_DOC"
        fi
        return
    fi
    if [ "$1" = -l ]; then
        command awk '/^## / {name=$2} /^Summary: / {
            sub(/^Summary: /, ""); printf "%-14s %s\n", name, $0
        }' "$_SHELL_TRICKS_DOC"
        return
    fi
    # Compare complete headings literally, not as user-supplied regular expressions.
    local rc=0
    SHELL_TRICKS_QUERY="$1" command awk '
        BEGIN {tool=ENVIRON["SHELL_TRICKS_QUERY"]}
        /^## / {selected=($0 == "## " tool); if (selected) found=1}
        selected {print}
        END {if (!found) exit 3}
    ' "$_SHELL_TRICKS_DOC" || rc=$?
    [ "$rc" -eq 3 ] || return "$rc"
    printf 'tricks: unknown tool %s. Valid names:\n' "$1" >&2
    tricks -l >&2
    return 2
}

croot() {
    [ "$#" -eq 0 ] || { printf 'usage: croot\n' >&2; return 2; }
    local root
    root=$(command git rev-parse --show-toplevel) || return
    builtin cd -- "$root"
}

cproj() {
    [ "$#" -le 1 ] || { printf 'usage: cproj [PROJECT]\n' >&2; return 2; }
    # One directory name only: a typo such as ../config must not leave ~/dev.
    case "${1-}" in .|..|*/*)
        printf 'cproj: use one directory name under ~/dev\n' >&2; return 2 ;;
    esac
    builtin cd -- "$HOME/dev${1:+/$1}"
}

ctmp() {
    [ "$#" -eq 0 ] || { printf 'usage: ctmp\n' >&2; return 2; }
    local directory
    # mktemp makes a private, unique directory; no shared predictable /tmp name.
    directory=$(command mktemp -d "${TMPDIR:-/tmp}/shell-tricks.XXXXXXXX") || return
    builtin cd -- "$directory" || { command rmdir -- "$directory"; return 1; }
    builtin pwd -P
}

gsummary() {
    [ "$#" -eq 0 ] || { printf 'usage: gsummary\n' >&2; return 2; }
    command git status --short --branch || return
    printf '\nUnstaged change sizes:\n'
    command git --no-pager diff --stat -- || return
    printf '\nStaged change sizes:\n'
    command git --no-pager diff --cached --stat --
}

gworktrees() {
    [ "$#" -eq 0 ] || { printf 'usage: gworktrees\n' >&2; return 2; }
    # Listing only: no prune, remove, checkout or fetch.
    command git worktree list
}

gpickaxe() {
    [ "$#" -ge 1 ] && [ -n "$1" ] || {
        printf 'usage: gpickaxe LITERAL [PATHSPEC ...]\n' >&2; return 2;
    }
    local needle="$1"
    shift
    # Git -S finds commits where the number of occurrences changes, not every
    # commit touching a matching line. -- keeps filenames out of option parsing.
    command git --no-pager log --all --oneline --decorate "-S$needle" -- "$@"
}

gignored() {
    [ "$#" -ge 1 ] || { printf 'usage: gignored PATH ...\n' >&2; return 2; }
    # -v names the ignore file, rule line number, pattern, and matching path.
    command git check-ignore -v -- "$@"
}

aftercmd() (
    [ "$#" -ge 1 ] || { printf 'usage: aftercmd COMMAND [ARG ...]\n' >&2; return 2; }
    local rc=0
    # A subshell isolates cd/variables in the wrapped command; argv stays argv,
    # never eval. Notification failures must not hide the command's exit status.
    "$@" || rc=$?
    command python3 "$_SHELL_TRICKS_NOTIFY" --send 'Shell task' \
        "Finished (exit $rc)." default </dev/null ||
        printf 'aftercmd: notification failed (command exited %s)\n' "$rc" >&2
    return "$rc"
)

pathlines() {
    [ "$#" -eq 0 ] || { printf 'usage: pathlines\n' >&2; return 2; }
    local remaining="${PATH-}" component number=1
    # Split at the first colon using shell expansion, even if PATH is empty
    # and external tools cannot be found. Empty components mean this directory.
    while :; do
        component="${remaining%%:*}"
        printf '%2d  %s\n' "$number" "${component:-. (current directory)}"
        case "$remaining" in
            *:*) remaining="${remaining#*:}" ;;
            *) break ;;
        esac
        number=$((number + 1))
    done
}
