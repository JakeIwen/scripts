#!/usr/bin/env bash

# Grep by default so substrings, thinking and tool output are not missed;
# --index opts into Switchboard's faster whole-word index of prompts and replies.

set -u -o pipefail

llmsearch_scripts=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
source "$llmsearch_scripts/session_search_header.sh"

llmsearch_usage() {
    cat <<'EOF'
Usage: llmsearch [flags] [--] <query>

Search Claude Code and Codex session events for one literal, case-insensitive
substring, including thinking, tool calls and output. All query arguments form
one term: llmsearch dim auto finds "dim auto" only. Results show one result per
session, newest activity first, with the match count and latest excerpt.
Directory roll-ups (-d) update live when grep output goes to a terminal.

Index queries (--index only):
  Search this machine's transcript index (LLM Switchboard's, on the Mac or the
  Pi): your prompts and visible replies only. If the command is missing or no
  index build has finished, search everything with grep instead.
  All words must be in one message, in any order, as whole words. Case and
  accents are ignored, and punctuation splits words: pcan finds pcan_view, not
  PCANView. There are no wildcards: dim* finds dim, not dimming.
  "a phrase" matches a phrase, and -word excludes a word.
  Filters: tool:claude|codex  dir:<part>  by:me|reply  age:<2d
           after:YYYY-MM-DD  before:YYYY-MM-DD
  The shell strips quotes, so wrap a query with phrases in single quotes:
    llmsearch --index '"dim mode" "auto mode"'   both phrases, in one message
    llmsearch --index "dim mode" "auto mode"     just the words dim, mode and auto

Separate substrings:
  For separate substrings anywhere in one session, use rg on the transcripts:
    rg -0li dim ~/.claude/projects ~/.codex/sessions | xargs -0 rg -li auto

Flags:
  -t, --title, -n, --name
               Search saved session titles only (literal, case-insensitive).
               Uses the session stores directly, without the transcript index.
  -s, --session
               Look up sessions by id: the whole id or its start (a6ac05ee).
               Uses the session stores directly, like -t.
  -f, --forks  Show each matching session's fork family instead: the session
               it was forked or branched from and its own forks, as a tree.
               Codex subagent threads are left out.
  -e, --single Show every matching excerpt instead of grouping them.
  -g, --group  One result per session, with the match count and latest
               excerpt (the default; undoes an earlier -e or -d).
  -d, --dirs   Group by directory and tool, with total matches and the latest
               hit. The last -d, -g or -e flag wins.
  --index      Use the fast whole-word index of prompts and visible replies.
  --grep       Use the default full substring scan (undoes an earlier --index).
               The last --index or --grep flag wins.
  -N           Show N message lines before and after each match (e.g. -2).
  -a N, -aN    Show N lines after; later context flags override each side.
  -b N, -bN    Show N lines before (non-negative integers).
  -h, --help   Show this help and exit.
  --           Search the remaining arguments as one phrase.
EOF
}

session_search_grep=true
session_search_dirs=false
if ! session_search_parse_args "$@"; then
    llmsearch_usage >&2
    exit 2
fi
if [ "$session_search_help" = true ]; then
    llmsearch_usage
    exit 0
fi

llmsearch_claude=${CLAUDESEARCH_ROOT:-${CLAUDE_CONFIG_DIR:-"$HOME/.claude"}}
llmsearch_codex=${CODEXSEARCH_ROOT:-"$HOME/.codex"}

llmsearch_sources() {
    session_search_run claude "$llmsearch_claude" codex "$llmsearch_codex"
}

llmsearch_grep() {
    local dependency
    for dependency in grep awk python3; do
        if ! command -v "$dependency" >/dev/null 2>&1; then
            echo "llmsearch: required command not found: $dependency" >&2
            return 127
        fi
    done
    if [ ! -d "$llmsearch_claude/projects" ] && [ ! -d "$llmsearch_codex/sessions" ]; then
        echo 'llmsearch: no Claude or Codex session directories found' >&2
        return 1
    fi
    # Directory roll-ups need the machine records; per-session results already
    # come from the extractor. Keep oracle records ungrouped in either mode.
    if [ "$session_search_dirs" = true ] && [ "${SESSION_SEARCH_RECORDS:-}" != 1 ] &&
        [ "$session_search_forks" = false ]; then
        SESSION_SEARCH_RECORDS=1 llmsearch_sources |
            python3 "$llmsearch_scripts/session_search_display.py" --group
    else
        llmsearch_sources
    fi
}

# The index covers only the default ~/.claude and ~/.codex, and records mode is
# the grep scripts' own format; llm-switchboard's oracle test relies on both.
llmsearch_index_applies() {
    [ "$session_search_grep" = false ] &&
        [ "$session_search_title" = false ] &&
        [ "$session_search_session" = false ] &&
        [ -z "${CLAUDESEARCH_ROOT:-}${CODEXSEARCH_ROOT:-}${CLAUDE_CONFIG_DIR:-}" ] &&
        [ "${SESSION_SEARCH_RECORDS:-}" != 1 ] &&
        command -v llm-switchboard >/dev/null 2>&1
}

# Labels and prints the results with the shared helpers in this shell: there
# can be 200 of them, and a fork per result costs milliseconds on the Pi.
llmsearch_print_results() {
    local tool cwd id name modified count excerpt command
    while IFS= read -r -d '' tool && IFS= read -r -d '' cwd && IFS= read -r -d '' id &&
        IFS= read -r -d '' name && IFS= read -r -d '' modified &&
        IFS= read -r -d '' count && IFS= read -r -d '' excerpt; do
        case "$tool" in
            claude) command=claudepr ;;
            codex) command=codexpr ;;
            *) continue ;;
        esac
        session_search_header_at "$name" "$modified" "$count matches | "
        session_search_resume_command "$cwd" "$command" "$id"
        printf '%s\n\n' "$excerpt"
    done
}

# One jq run orders or groups the results: jq 1.6 takes a fifth of a second to
# start on a busy Pi.
llmsearch_print_index() {
    jq -nj -L "$llmsearch_scripts" --argjson group "$session_search_group" \
        --argjson dirs "$session_search_dirs" '
        include "session_search_records"; session_search_index_fields($group; $dirs)
    ' | llmsearch_print_results
}

# -f: the matching sessions' fork families instead of their matches.
llmsearch_print_forks() {
    jq -nj -L "$llmsearch_scripts" 'include "session_search_records"; session_search_index_ids' |
        session_search_fork_families claude "$llmsearch_claude" codex "$llmsearch_codex"
}

llmsearch_ask_index() {
    local flags=()
    if [ "$session_search_context_requested" = true ]; then
        flags+=(--before "$session_search_before" --after "$session_search_after")
    fi
    llm-switchboard search --json --limit 200 ${flags[@]+"${flags[@]}"} -- "$1"
}

# Returns llm-switchboard's status: 3 means no index build has finished, and
# 4 (ours) that it lacks the context flags. The index also answers 3 while its
# periodic update holds the database lock, so ask once more before settling
# for grep.
llmsearch_index() {
    local query=$session_search_words quote='"' results status print
    # Words before -- keep the index's syntax; the text after it is one phrase.
    # A phrase can't hold a quote, and the index splits words at quotes anyway.
    if [ -n "$session_search_literal" ]; then
        query="${query:+$query }\"${session_search_literal//$quote/ }\""
    fi
    llmsearch_errors=$(mktemp "${TMPDIR:-/tmp}/llmsearch.XXXXXX") || return 1
    trap 'rm -f -- "$llmsearch_errors"' EXIT
    results=$(llmsearch_ask_index "$query" 2>"$llmsearch_errors")
    status=$?
    if [ "$status" -eq 3 ]; then
        sleep 1
        results=$(llmsearch_ask_index "$query" 2>"$llmsearch_errors")
        status=$?
    fi
    case "$status" in
        0)
            print=llmsearch_print_index
            [ "$session_search_forks" = false ] || print=llmsearch_print_forks
            [ -z "$results" ] || printf '%s\n' "$results" | "$print" || return 1
            ;;
        3) ;;
        *)
            # An llm-switchboard older than --before/--after rejects them as
            # usage errors; any other failure, a bad query included, is shown.
            if [ "$status" -eq 2 ] && [ "$session_search_context_requested" = true ] &&
                grep -q 'unrecognized arguments' -- "$llmsearch_errors"; then
                return 4
            fi
            cat -- "$llmsearch_errors" >&2
            ;;
    esac
    return "$status"
}

if llmsearch_index_applies; then
    llmsearch_index
    llmsearch_status=$?
    case "$llmsearch_status" in
        3) echo 'llmsearch: the transcript index is not built yet, so searching with grep (slow)' >&2 ;;
        4)
            echo 'llmsearch: the transcript index rejected context flags, so searching with grep (slow)' >&2
            ;;
        *) exit "$llmsearch_status" ;;
    esac
fi
llmsearch_grep
