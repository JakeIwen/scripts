#!/usr/bin/env bash

# Search LLM Switchboard's transcript index of prompts and replies when it can
# answer, and otherwise grep every transcript with claudesearch and codexsearch.

set -u -o pipefail

llmsearch_scripts=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
source "$llmsearch_scripts/session_search_header.sh"

llmsearch_usage() {
    cat <<'EOF'
Usage: llmsearch [flags] [--] <query>

Search this machine's transcript index (LLM Switchboard's, on the Mac or the
Pi): your prompts and the visible replies of its Claude Code and Codex sessions.
Where there is no index (no llm-switchboard command, or before its first build),
search everything with grep instead. Results are shown newest first; grouped
grep results update live when output goes to a terminal.

Index queries:
  All words must be in one message, as whole words. Case and accents are
  ignored, and punctuation splits words: pcan finds pcan_view, not PCANView.
  "a phrase" matches a phrase, and -word excludes a word.
  Filters: tool:claude|codex  dir:<part>  by:me|reply  age:<2d
           after:YYYY-MM-DD  before:YYYY-MM-DD

Flags:
  -t, --title  Search saved session titles only (literal, case-insensitive).
               Uses the session stores directly, without the transcript index.
  -g, --group  Group by directory and tool, with total matches and the latest hit.
  --grep       Search everything, slowly, including thinking and tool calls and
               output, for a literal, case-insensitive term.
  -N           Show N message lines before and after each match (e.g. -2).
  -a N, -aN    Show N lines after; later context flags override each side.
  -b N, -bN    Show N lines before (non-negative integers).
  -h, --help   Show this help and exit.
  --           Search the remaining arguments as one phrase.
EOF
}

session_search_grep=false
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
    session_search_files claude "$llmsearch_claude" codex "$llmsearch_codex" |
        session_search_extract
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
    if [ "$session_search_group" = true ]; then
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
    jq -nj -L "$llmsearch_scripts" --argjson group "$session_search_group" '
        include "session_search_records"; session_search_index_fields($group)
    ' | llmsearch_print_results
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
    local query=$session_search_words quote='"' results status
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
        0) [ -z "$results" ] || printf '%s\n' "$results" | llmsearch_print_index || return 1 ;;
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
